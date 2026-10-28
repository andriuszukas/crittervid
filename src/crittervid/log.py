"""Logging for a run (decision record 0003).

start_run() is called once, where a command starts. It gives the run an ID and
sends the `crittervid` logger's records to three places:

- the console: each record's message alone, as the steps printed it before;
- the run log: <output folder>/logs/<start time>_<run ID>.jsonl, one JSON
  object per record (see JsonFormatter for the fields);
- the app log: crittervid.log in the operating system's folder for app logs
  (app_log_dir), for everything, including failures before an output folder
  exists.

Nothing is sent anywhere. Without start_run (a step used as a library), the
logger has no outputs and prints nothing.
"""
import dataclasses
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 1          # the run log's record format; raised when fields change meaning
LOGGER = 'crittervid'
APP_LOG_NAME = 'crittervid.log'
APP_LOG_MAX_BYTES = 1_000_000
APP_LOG_KEEP = 5
# Fields a call can add with extra={...}; they're written to the JSON when present
EXTRA_FIELDS = ('file', 'duration_s', 'ffmpeg_stderr', 'command', 'settings')

logging.getLogger(LOGGER).addHandler(logging.NullHandler())
logging.getLogger(LOGGER).propagate = False


def get(step):
    """The logger for a step: crittervid.convert, crittervid.merge, ..."""
    return logging.getLogger(f'{LOGGER}.{step}')


def level_of(message):
    """The level a message's own wording gives it: the steps' messages start
    with ERROR or WARNING when they are one ("Failed to ..." is an error too)."""
    text = message.lstrip()
    if text.startswith(('ERROR', 'Failed to ')):
        return logging.ERROR
    if text.startswith('WARNING'):
        return logging.WARNING
    return logging.INFO


def say(logger, message, level=None, **extra):
    """Log a message at `level`, or the level its wording gives it (see
    level_of); the console shows it exactly as written."""
    logger.log(level_of(message) if level is None else level, message, extra=extra or None)


FFMPEG_TAIL_LINES = 20
# Lines FFmpeg ends a failure with that say nothing about the cause
_FFMPEG_TRAILER = re.compile(r'(Conversion failed!|frame=|size=|Lsize=|video:|Nothing was written into output file'
                             r'|Task finished with error code|Terminating thread|Exiting normally)')
# Its summary of an input that couldn't be opened; the cause is in a line before it
_FFMPEG_SUMMARY = re.compile(r'Error opening (input|output) file')
_FFMPEG_ERROR = re.compile(r'error|could not|cannot|impossible|not found|no such|invalid|unknown encoder|'
                           r'unsupported|denied|failed', re.I)
_FFMPEG_PREFIX = re.compile(r'^(\[[^\]]*\]\s*)+')   # [enc:h264_nvenc @ 0x...] and the like


def ffmpeg_failure(stderr):
    """What to tell about a failed FFmpeg call from its error output (bytes):
    the line that best says why, for the console and the summary, and its last
    FFMPEG_TAIL_LINES lines, for the logs (`ffmpeg_stderr`).

    FFmpeg ends a failure with generic lines ("Conversion failed!", progress,
    "Error opening input files: ..."), so the best line is the last one that
    reads like a specific error, e.g. "Could not open encoder before EOF",
    "Impossible to open 'missing.mp4'" or "moov atom not found"."""
    lines = [line.strip() for line in stderr.decode('utf-8', errors='replace').splitlines() if line.strip()]
    tail = '\n'.join(lines[-FFMPEG_TAIL_LINES:])
    shown = [_FFMPEG_PREFIX.sub('', line) for line in lines]
    shown = [line for line in shown if line and not _FFMPEG_TRAILER.match(line)]
    if not shown:
        return (lines[-1] if lines else 'no error output'), tail
    errors = [line for line in shown if _FFMPEG_ERROR.search(line)]
    specific = [line for line in errors if not _FFMPEG_SUMMARY.match(line) and not line.startswith('Error opening input:')]
    return (specific or errors or shown)[-1], tail


def command_line(cmd):
    """A command for the logs, as one line that can be pasted into a shell."""
    parts = [str(part) for part in cmd]
    return subprocess.list2cmdline(parts) if sys.platform == 'win32' else shlex.join(parts)


def file_only(logger, level, message, **extra):
    """Log a message to the log files but not the console, for lines the
    console already shows another way (e.g. option errors on stderr)."""
    logger.log(level, message, extra={**extra, 'console': False})


class _ConsoleFormatter(logging.Formatter):
    """The message alone, as the steps printed it: no traceback, which goes to
    the log files instead."""

    def format(self, record):
        return record.getMessage()


def _for_console(record):
    return getattr(record, 'console', True)


@dataclasses.dataclass
class Run:
    id: str
    command: str
    started: float              # time.monotonic() at the start
    started_utc: datetime
    run_log: Path = None        # None when the command writes no output
    app_log: Path = None        # None when its folder can't be used


_run = None
_handlers = []


def current():
    """The run started by start_run, or None."""
    return _run


def app_log_dir():
    """Where the app log goes: CRITTERVID_LOG_DIR if set (tests), otherwise the
    usual folder for app logs on this system."""
    if os.environ.get('CRITTERVID_LOG_DIR'):
        return Path(os.environ['CRITTERVID_LOG_DIR'])
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local'
        return Path(base) / 'CritterVid' / 'Logs'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'CritterVid'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'crittervid'


def setup_streams():
    """Write UTF-8 to the console whatever its code page, replacing what it can't
    show, and flush each line so output stays in order when it's piped (the GUI,
    `| tee`). The streams are None when there's no console at all (pythonw)."""
    for stream, options in ((sys.stdout, {'line_buffering': True}), (sys.stderr, {})):
        # Streams without it (e.g. a StringIO in place of stdout) are left as they are
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace', **options)
            except (OSError, ValueError):
                pass


class _Context(logging.Filter):
    """Adds the run ID, the step and the time since the run started to every
    record, so a call only passes what it knows."""

    def filter(self, record):
        run = _run
        record.run = run.id if run else None
        name = record.name
        # crittervid.convert (or crittervid.convert.anything) -> convert; crittervid -> the command
        record.step = (name[len(LOGGER) + 1:].split('.')[0] if name.startswith(LOGGER + '.')
                       else (run.command if run else None))
        record.elapsed_s = round(time.monotonic() - run.started, 3) if run else None
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with these fields:

    schema, time (UTC, ISO 8601), run, level, step, message, elapsed_s, and
    when present: file, duration_s, ffmpeg_stderr (the last lines of FFmpeg's
    error output), command, settings, traceback.

    Never raises: a value JSON can't hold is written as a string. Paths that
    aren't UTF-8 (Linux) come through as escaped surrogates and read back
    exactly."""

    def format(self, record):
        data = {
            'schema': SCHEMA,
            'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds')
                            .replace('+00:00', 'Z'),
            'run': getattr(record, 'run', None),
            'level': record.levelname,
            'step': getattr(record, 'step', None),
            'message': None,
            'elapsed_s': getattr(record, 'elapsed_s', None),
        }
        try:
            data['message'] = record.getMessage()
        except Exception:   # bad %-arguments: keep what there is
            data['message'] = str(record.msg)
        for key in EXTRA_FIELDS:
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            try:
                data['traceback'] = self.formatException(record.exc_info)
            except Exception:
                data['traceback'] = repr(record.exc_info[1])
        try:
            return json.dumps(data, default=str)
        except Exception:   # e.g. a value whose str() fails: keep the fixed fields' types
            fixed = ('schema', 'time', 'run', 'level', 'step', 'elapsed_s')
            return json.dumps({k: (v if k in fixed else repr(v)) for k, v in data.items()}, default=repr)


class _LogFile(logging.FileHandler):
    """A log file created, with its folder, only when the first record arrives.
    If it can't be written, it says so once on the console and stops trying, so
    the run carries on without it."""

    def __init__(self, path, console):
        super().__init__(path, encoding='utf-8', delay=True)
        self._console = console
        self._failed = False

    def _open(self):
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()

    def emit(self, record):
        if self._failed:
            return
        try:
            super().emit(record)
        except Exception:   # opening the file isn't covered by FileHandler's own handling
            self.handleError(record)

    def handleError(self, record):
        if self._failed:
            return
        self._failed = True
        error = sys.exc_info()[1]
        try:   # logging must never raise, even if the console is gone by now
            self._console.write(f"WARNING: can't write the log {self.baseFilename} ({error}); "
                                f"carrying on without it\n")
            self._console.flush()
        except Exception:
            pass


def _move_aside(path):
    """Keep the app log small without rotating it mid-run: two programs can write
    it at once (a run from the GUI and one in a terminal), and renaming a file
    another program has open fails on Windows. So it's moved aside here, at the
    start of a run, and a failure to do so is ignored; the next run tries again."""
    try:
        if path.stat().st_size <= APP_LOG_MAX_BYTES:
            return
        for k in range(APP_LOG_KEEP - 1, 0, -1):
            older = path.with_name(f'{path.name}.{k}')
            if older.exists():
                os.replace(older, path.with_name(f'{path.name}.{k + 1}'))
        os.replace(path, path.with_name(f'{path.name}.1'))
    except OSError:
        pass


def _settings(config):
    """A config's settings for the log (not its cancel token)."""
    return {f.name: getattr(config, f.name) for f in dataclasses.fields(config) if f.name != 'cancel'}


def start_run(config=None, command='run', verbose=False, run_log=True):
    """Start logging a run of `command` and return it (see Run). Writes the run
    log only when run_log is true and there's a config (commands that write
    output, not dry runs). Calling it again replaces the earlier setup.

    The first record, also on the console, is `Run <id> started; log: <path>`,
    naming the run log (or the app log if there's no run log), or
    `Run <id> started (no log file)` if neither can be written. The GUI reads
    the ID and the path from it, so this format is fixed."""
    global _run
    stop_run()
    setup_streams()
    now = datetime.now(timezone.utc)
    _run = Run(id=secrets.token_hex(4), command=command, started=time.monotonic(), started_utc=now)
    logger = logging.getLogger(LOGGER)
    logger.setLevel(logging.DEBUG)
    context = _Context()
    console = sys.stdout

    if console is not None:
        handler = logging.StreamHandler(console)
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        handler.setFormatter(_ConsoleFormatter())
        handler.addFilter(_for_console)
        _handlers.append(handler)

    # Said after the first line, which must stay first (the GUI reads it)
    later = []
    try:
        folder = app_log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        _run.app_log = folder / APP_LOG_NAME
        _move_aside(_run.app_log)
        _handlers.append(_LogFile(_run.app_log, console))
    except OSError as e:
        later.append(f"WARNING: can't use the app log folder ({e}); carrying on without it")

    if run_log and config is not None:
        # Absolute, so whoever reads the first line finds it wherever they run
        _run.run_log = Path(os.path.abspath(config.output_dir / 'logs' / f"{now:%Y%m%dT%H%M%SZ}_{_run.id}.jsonl"))
        _handlers.append(_LogFile(_run.run_log, console))

    for handler in _handlers:
        handler.addFilter(context)
        if isinstance(handler, _LogFile):
            handler.setFormatter(JsonFormatter())
            handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)

    shown = _run.run_log or _run.app_log
    logger.info(f'Run {_run.id} started; log: {shown}' if shown else f'Run {_run.id} started (no log file)',
                extra={'command': command, 'settings': _settings(config) if config is not None else None})
    for message in later:
        logger.warning(message)
    return _run


_FIRST_LINE = re.compile(r'Run ([0-9a-f]{8}) started(?:; log: (.+)| \(no log file\))$')


def read_first_line(line):
    """The run ID and log path (or None) from the first line start_run prints,
    or None if the line isn't one; for programs that start a run and read its
    output, such as the GUI."""
    m = _FIRST_LINE.match(line.strip())
    return (m.group(1), m.group(2)) if m else None


def stop_run():
    """Close the outputs start_run added, writing out what's buffered."""
    global _run
    logger = logging.getLogger(LOGGER)
    for handler in _handlers:
        logger.removeHandler(handler)
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    _handlers.clear()
    _run = None
