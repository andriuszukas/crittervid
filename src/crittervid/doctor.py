"""crittervid doctor: checks what CritterVid needs and prints a short report.

- FFmpeg and ffprobe: found, and their versions;
- the encoders --accel can use: each tried on a tiny test clip, and which one
  Auto would pick;
- the output folder: whether it can be written;
- disk space: free space where the output goes, against the input's size;
- the time zone and clock offset saved for the output folder (decision record 0005).

Each line starts with OK, WARNING or ERROR; the exit code is 1 if any check
found an error. It only reads (and writes one small test file it removes).
"""
import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

from . import __version__, deployment, log
from .config import PipelineConfig
from .ffmpeg import ACCEL_ORDER, encoder_works, get_ffmpeg_path, get_ffprobe_path, tool_version

_log = log.get('doctor')

# --accel choice -> what it needs, for the report
ACCELS = {
    'cpu': 'libx264 (CPU)',
    'cuda': 'NVIDIA NVENC (h264_nvenc)',
    'qsv': 'Intel Quick Sync (h264_qsv)',
    'amf': 'AMD AMF (h264_amf)',
    'vaapi': 'VAAPI (h264_vaapi, Linux)',
}


def say(message, **extra):
    log.say(_log, message, **extra)


def _size(folder):
    """The total size of the .media files under a folder, in bytes."""
    total = 0
    for dirpath, _, names in os.walk(folder):
        for name in names:
            if name.endswith('.media'):
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
    return total


def _gb(n):
    return f'{n / 1e9:.1f} GB'


def _existing(path):
    """The path, or its nearest folder that exists."""
    path = Path(os.path.abspath(path))
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def check_ffmpeg():
    """Problems found (True if FFmpeg is missing)."""
    ffmpeg = get_ffmpeg_path()
    found = shutil.which(ffmpeg) or (os.path.isfile(ffmpeg) and ffmpeg)
    if not found:
        say("ERROR: FFmpeg not found. Install it and put it on PATH, or put ffmpeg(.exe) in the "
            "project folder (_internal in the packaged app), or name it with CRITTERVID_FFMPEG.")
        return True
    say(f'OK: FFmpeg {tool_version(found) or "(version unknown)"} at {found}')
    ffprobe = get_ffprobe_path()
    if ffprobe:
        say(f'OK: ffprobe {tool_version(ffprobe) or "(version unknown)"} at {ffprobe}')
    else:
        say("WARNING: ffprobe not found (it comes with FFmpeg): merged files may drift slightly, "
            "and validation can only decode every file in full.")
    return False


def check_encoders():
    """Problems found (True if not even the CPU encoder works)."""
    working = []
    for accel, label in ACCELS.items():
        if accel == 'vaapi' and platform.system() != 'Linux':
            continue
        ok, reason = encoder_works(accel)
        if ok:
            working.append(accel)
            say(f'OK: encoder {label} works (--accel {accel})')
        else:
            say(f'WARNING: encoder {label} not available (--accel {accel}): {reason}' if accel != 'cpu'
                else f'ERROR: the CPU encoder {label} failed: {reason}')
    # What detect_accel would pick, from the tries above
    auto = next((accel for accel in ACCEL_ORDER if accel in working), 'cpu')
    say(f'OK: --accel auto picks {auto} (the first that works of {", ".join(ACCEL_ORDER)}, then the CPU)')
    return 'cpu' not in working


def check_output(output_dir):
    """Problems found (True if the output folder can't be written)."""
    target = Path(output_dir)
    where = target if target.is_dir() else _existing(target)
    if target.exists() and not target.is_dir():
        say(f'ERROR: {target} is a file, not a folder: the output can\'t go there')
        return True
    # One file, created and removed: Python's temporary-file helper takes a
    # refused file on Windows for a name in use and retries thousands of times
    test_file = where / f'.crittervid-check-{os.getpid()}'
    try:
        with open(test_file, 'xb'):
            pass
        test_file.unlink()
    except OSError as e:
        say(f'ERROR: can\'t write to {where} ({e})' + ('' if where == target else f', where the output folder {target} would be created'))
        return True
    say(f'OK: {target} can be written' + ('' if target.is_dir() else f' (it will be created in {where})'))
    return False


def check_space(input_dir, output_dir):
    """Problems found (True if there's less free space than the input's size)."""
    where = _existing(output_dir)
    try:
        free = shutil.disk_usage(where).free
    except OSError as e:
        say(f'WARNING: free space at {where} unknown ({e})')
        return False
    if Path(input_dir).is_dir():
        say(f'Measuring the .media files in {input_dir}...')
        needed = _size(input_dir)
    else:
        needed = 0
    if not needed:
        say(f'OK: {_gb(free)} free at {where} (no .media files found in {input_dir} to compare with)')
        return False
    # Converted files take about as much as the input, and merged days as much again
    if free < needed:
        say(f'ERROR: {_gb(free)} free at {where}, but the input is {_gb(needed)}: the converted files '
            f'alone need about that much')
        return True
    if free < 2 * needed:
        say(f'WARNING: {_gb(free)} free at {where}; the input is {_gb(needed)}, and converting and '
            f'merging need about {_gb(2 * needed)}')
        return False
    say(f'OK: {_gb(free)} free at {where}, enough for {_gb(needed)} of input (about {_gb(2 * needed)} '
        f'with merged days)')
    return False


def check_deployment(config):
    """The time zone and clock offset saved for the output folder, if any."""
    try:
        saved = deployment.read(config)
    except deployment.Invalid as e:
        say(f'ERROR: {e}')
        return True
    if 'timezone' not in saved:
        say(f"WARNING: no time zone saved for {config.output_dir}: days will be grouped in this computer's "
            f"({deployment.computer_zone()}). Give the camera's with --timezone when you run CritterVid")
    else:
        offset = saved.get('clock_offset')
        say(f"OK: time zone {saved['timezone']}" + (f", clock offset {offset:+d} s" if offset else '')
            + f" (saved in {deployment.path(config)})")
    return False


def run(config):
    """Run every check; returns 0, or 1 if one found an error."""
    get_ffmpeg_path.cache_clear()   # look again, as things may have changed since
    get_ffprobe_path.cache_clear()
    say(f'CritterVid {__version__}, Python {platform.python_version()}, '
        f'{platform.system()} {platform.release()} ({platform.machine()})')
    problems = [check_ffmpeg()]
    if not problems[0]:
        problems.append(check_encoders())
    problems += [check_output(config.output_dir), check_space(config.input_dir, config.output_dir),
                 check_deployment(config)]
    if any(problems):
        say('Some checks failed; see the ERROR lines above.')
        return 1
    say('All checks passed.')
    return 0


def main(argv=None):
    log.setup_streams()
    parser = argparse.ArgumentParser(prog='crittervid doctor', description='Check what CritterVid needs: FFmpeg, '
                                     'encoders, the output folder and disk space.')
    parser.add_argument('--input-dir', default='input/DCIM', help='The card folder to compare free space with (default: input/DCIM)')
    parser.add_argument('--output-dir', default='output', help='Where the output will go (default: output)')
    parser.add_argument('--verbose', action='store_true', help='Also show details on the console (they are always in the logs).')
    args = parser.parse_args(argv)
    config = PipelineConfig(input_dir=args.input_dir, output_dir=args.output_dir)
    log.start_run(config, 'doctor', verbose=args.verbose, run_log=False)   # it writes no output
    return run(config)


if __name__ == '__main__':
    sys.exit(main())
