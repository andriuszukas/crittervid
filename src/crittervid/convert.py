import os

import subprocess
from pathlib import Path
import argparse
import logging
import sys
import re
import signal
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from . import deployment, log, media_format, names
from .config import ACCEL_CHOICES, PipelineConfig
from .ffmpeg import ACCEL_OPTIONS, PATH_TEXT, detect_accel, get_ffmpeg_path, media_arg


class Stopped(BaseException):
    """The run was stopped (Ctrl+C) before this conversion could finish. Not an
    Exception, so the per-file error handling doesn't report it as a failure."""


# Set on Ctrl+C: no new FFmpeg starts, and the running ones are ended
_running = set()
_running_lock = threading.Lock()


_log = log.get('convert')


def say(message, level=None, **extra):
    log.say(_log, message, level, **extra)


def run_ffmpeg(cmd, cancel):
    """Run FFmpeg like subprocess.run(cmd, capture_output=True), but so that
    stop_ffmpeg() can end it. Raises Stopped instead of starting it once the run
    is stopping (the cancel token is set), and if it fails because of that."""
    with _running_lock:
        if cancel.is_cancelled():
            raise Stopped
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        _running.add(proc)
    try:
        out, err = proc.communicate()
    finally:
        with _running_lock:
            _running.discard(proc)
    # A console's Ctrl+C reaches FFmpeg slightly before the run starts stopping
    # (the main thread checks every 0.5 s), so give a failure that long to turn
    # out to be the stop rather than a failed conversion
    if proc.returncode != 0 and cancel.wait(0.6):
        raise Stopped
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def stop_ffmpeg(cancel):
    """Stop the run: no new FFmpeg starts, and the running ones are ended. A
    console's Ctrl+C reaches them too, but not when the run is stopped some other
    way."""
    with _running_lock:
        cancel.cancel()
        for proc in _running:
            proc.terminate()


# Hardware acceleration options for FFmpeg: (options before -i, options after -i)
# Recursively scan for .media files
def find_media_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.media'):
                yield Path(dirpath) / filename

EVENT_FOLDER = re.compile(r'\d{10,}_\d+')


def read_event(segments):
    """The video frames and audio records of an event's .media segments, in order,
    plus warnings about anything that had to be skipped.

    Damaged segments don't stop the event: empty files are skipped, an incomplete
    final record (as left by power loss) is ignored, and segments that aren't in
    the record format are skipped. Raises media_format.MediaFormatError only if no
    segment is in the record format. Video frames that don't move forward in time
    (repeated or garbage records) are dropped, since every frame needs a later
    timestamp than the one before; audio_pcm handles the audio equivalent. Frames
    before the first keyframe can't be decoded and are dropped too, as is what's
    left of earlier pictures in front of that keyframe."""
    video, audio, notes, usable, dropped = [], [], [], 0, 0
    for segment in sorted(segments, key=lambda p: p.name):
        data = segment.read_bytes()
        if not data:
            notes.append(f'{segment.name} is empty and was skipped')
            continue
        try:
            streams = media_format.split_streams(data)
        except media_format.MediaFormatError:
            notes.append(f"{segment.name} isn't in the cameras' record format and was skipped")
            continue
        usable += 1
        if streams.trailing_bytes:
            notes.append(f'{segment.name} ends with {streams.trailing_bytes} bytes of an '
                         f'incomplete record, which were ignored')
        for frame in streams.video:
            if not video or frame[0] > video[-1][0]:
                video.append(frame)
            else:
                dropped += 1
        audio += streams.audio
    if not usable:
        raise media_format.MediaFormatError('no segment is in the record format')
    if dropped:
        notes.append(f"{dropped} video frames that didn't move forward in time were dropped")
    # Frames before the first keyframe can't be decoded, so the event starts there,
    # and without what's left of earlier pictures in front of that keyframe (#77)
    start = media_format.decodable_start(video)
    if start is None:   # converted as it is, in case it's something not seen on cards so far
        notes.append("no video frame is a keyframe with its parameter sets, so the video may not play")
        return video, audio, notes
    if start:
        notes.append(f"{start} video frames at the start (and any audio before the first keyframe) "
                     f"were dropped: they can't be decoded without an earlier keyframe that isn't on the card")
    t_us, payload, key = video[start]
    keyframe = media_format.from_parameter_sets(payload)
    if len(keyframe) < len(payload):
        notes.append(f"the first keyframe began with {len(payload) - len(keyframe)} bytes of earlier "
                     f"pictures that can't be decoded; they were removed")
    video = [(t_us, keyframe, key)] + video[start + 1:]
    return video, audio, notes


def record_format_command(video, audio, out_path, workdir, start=None):
    """FFmpeg command that writes video frames and audio records into an MP4
    without re-encoding the video.

    Each frame keeps the timestamp the camera recorded. The audio, placed by its
    own timestamps from the first frame to the end of the last one, is encoded as
    AAC; with no audio recorded it's silence, so every event file has audio.
    `start` (Unix seconds) is recorded as the file's creation_time: it comes from
    the event folder's name, as the frames' timestamps count from a point of the
    camera's own, not from 1970."""
    video_path = workdir / 'video.ts'
    video_path.write_bytes(media_format.video_ts(video))
    last_frame = video[-1][0] - video[-2][0] if len(video) > 1 else 66_667
    audio_path = workdir / 'audio.pcm'
    audio_path.write_bytes(media_format.audio_pcm(audio, video[0][0], video[-1][0] + last_frame))
    # FFmpeg treats jumps of more than 10 s in MPEG-TS timestamps as errors and
    # closes them up; a raised threshold keeps real pauses in the recording
    return [get_ffmpeg_path(), '-y', '-dts_delta_threshold', '1000000', '-i', str(video_path),
            '-f', 's16le', '-ar', str(media_format.AUDIO_SAMPLE_RATE), '-i', str(audio_path),
            '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac',
            *([] if start is None else media_format.creation_time_args(start)), '-f', 'mp4', media_arg(out_path)]


def report(summary, msg, **extra):
    say(msg, **extra)
    summary.append(msg)


def convert_event(event_dir, segments, summary, failed_files, config):
    """Convert an event folder's .media segments into one MP4 for the whole event.

    Returns True or False for success, or None if no segment is in the cameras'
    record format (the caller then converts them one by one). One file per event
    keeps the camera's timeline exact; joining per-segment files would add an
    estimated frame duration at every join."""
    folder = event_dir.name   # <unix timestamp>_<duration in seconds>
    yyyy, mm, dd = event_dir.parts[-4:-1]
    out_dir = config.output_dir / yyyy / mm / dd
    out_path = out_dir / f'{folder}.mp4'
    tmp_path = out_dir / f'{folder}.mp4.part'
    started = time.monotonic()

    def took():
        return {'file': str(event_dir), 'duration_s': round(time.monotonic() - started, 3)}

    try:
        if out_path.exists():
            report(summary, f"SKIPPED: {event_dir} -> {out_path} (already exists)", file=str(event_dir))
            return True
        try:
            video, audio, notes = read_event(segments)
        except media_format.MediaFormatError:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='crittervid-') as workdir:
            cmd = record_format_command(video, audio, tmp_path, Path(workdir), start=int(folder.split('_')[0]))
            _log.debug(f'FFmpeg: {log.command_line(cmd)}', extra={'file': str(event_dir)})
            result = run_ffmpeg(cmd, config.cancel)
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            last, tail = log.ffmpeg_failure(result.stderr)
            report(summary, f"ERROR: Failed to convert {event_dir}: {last} (full FFmpeg output in the run log)",
                   ffmpeg_stderr=tail, **took())
            failed_files.append(str(event_dir))
            return False
        tmp_path.replace(out_path)
        sound = 'audio AAC' if audio else 'silent audio (none recorded)'
        report(summary, f"SUCCESS: Converted {event_dir} ({len(segments)} segments) -> {out_path} "
                        f"[video copied with camera timestamps, {sound}]", **took())
        for note in notes:
            report(summary, f"WARNING: {event_dir}: {note}")
        # The folder name gives the event's length; footage that doesn't match it
        # points to missing segments or a timing problem worth a look. A length of
        # 0 means the camera didn't record one (e.g. the event was cut short).
        span = (video[-1][0] - video[0][0]) / 1e6
        _, _, expected = folder.partition('_')
        if int(expected) > 0 and abs(span - int(expected)) > max(2, int(expected) * 0.02):
            report(summary, f"WARNING: {event_dir}: the footage spans {span:.1f} s, "
                            f"but the folder name says {int(expected)} s")
        return True
    except Stopped:
        tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        report(summary, f"ERROR: Error processing {event_dir}: {e}")
        failed_files.append(str(event_dir))
        return False


# Per-segment output uses the same video time base as per-event files (90 kHz), so
# the two kinds can be joined in one merged day without the video being squashed
FALLBACK_OUTPUT = ['-video_track_timescale', '90000', '-f', 'mp4']


# Convert one .media segment to .mp4, named after its event and offset
def convert_media_file(media_path, summary, accel, failed_files, config):
    parts = media_path.parts
    tmp_path = None
    try:
        yyyy, mm, dd = parts[-5], parts[-4], parts[-3]
        folder = parts[-2]  # <start time>_<event length in seconds>
        timestamp, duration = folder.split('_')
        out_dir = config.output_dir / yyyy / mm / dd / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{timestamp}_{duration}_{media_path.stem}.mp4"
        out_path = out_dir / out_name
        # Write to a temp file and rename on success, so an interrupted or failed
        # conversion never leaves a partial .mp4 that later runs would skip
        tmp_path = out_dir / (out_name + '.part')
        if out_path.exists():
            skip_msg = f"SKIPPED: {media_path} -> {out_path} (already exists)"
            say(skip_msg, file=str(media_path))
            summary.append(skip_msg)
            return True
        # Files that aren't in the cameras' record format: let FFmpeg read them directly.
        # A segment starts at the event's start plus its offset (its file name)
        offset = int(media_path.stem) if media_path.stem.isdigit() else 0
        output = media_format.creation_time_args(int(timestamp) + offset) + FALLBACK_OUTPUT
        chosen_by_auto = accel == 'auto' and not config.copy
        if chosen_by_auto:
            accel = _auto_accel()

        def command(accel):
            if config.copy:
                return [get_ffmpeg_path(), '-y', '-i', media_arg(media_path), '-c:v', 'copy'] + output + [media_arg(tmp_path)]
            input_opts, output_opts = ACCEL_OPTIONS[accel]
            return [get_ffmpeg_path(), '-y'] + input_opts + ['-i', media_arg(media_path)] + output_opts + output + [media_arg(tmp_path)]

        method = 'copy' if config.copy else accel
        started = time.monotonic()
        cmd = command(accel)
        _log.debug(f'FFmpeg: {log.command_line(cmd)}', extra={'file': str(media_path)})
        result = run_ffmpeg(cmd, config.cancel)
        if result.returncode != 0 and method not in ('copy', 'cpu'):
            # Maybe the GPU or its driver can't do this file, which the CPU can
            reason = log.ffmpeg_failure(result.stderr)[0]
            say(f"WARNING: {media_path}: failed with {accel} ({reason}); trying the CPU",
                file=str(media_path))
            tmp_path.unlink(missing_ok=True)
            cmd = command('cpu')
            _log.debug(f'FFmpeg: {log.command_line(cmd)}', extra={'file': str(media_path)})
            result = run_ffmpeg(cmd, config.cancel)
            method = f'cpu, after {accel} failed'
            if result.returncode == 0 and chosen_by_auto:
                # The CPU managed what the GPU didn't: it's the GPU, so Auto stops
                # trying it, rather than failing on it once for every file
                _auto_gpu_failed(accel)
        took = {'file': str(media_path), 'duration_s': round(time.monotonic() - started, 3)}
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            last, tail = log.ffmpeg_failure(result.stderr)
            error_msg = f"ERROR: Failed to convert {media_path}: {last} (full FFmpeg output in the run log)"
            say(error_msg, ffmpeg_stderr=tail, **took)
            summary.append(error_msg)
            failed_files.append(str(media_path))
            return False
        else:
            tmp_path.replace(out_path)
            verb = 'Copied' if method == 'copy' else 'Converted'
            success_msg = f"SUCCESS: {verb} {media_path} -> {out_path} [{method}]"
            say(success_msg, **took)
            summary.append(success_msg)
            return True
    except Stopped:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        error_msg = f"ERROR: Error processing {media_path}: {e}"
        say(error_msg)
        summary.append(error_msg)
        failed_files.append(str(media_path))
        return False

def list_entries(paths):
    """Paths for the failed-files list: absolute, each once. Paths from lists of
    earlier versions are relative to where those ran, as before; making them
    absolute too lets them match this run's (case-insensitively on Windows).
    The list has one path per line, so a path with a line break is left out,
    with a warning."""
    entries = []
    for p in dict.fromkeys(Path(os.path.abspath(p)) for p in paths):
        if '\n' in str(p) or '\r' in str(p):
            say(f"WARNING: {p} can't be listed for --retry-failed: its path has a line break; rename it")
        else:
            entries.append(str(p))
    return entries


def group_by_event(paths):
    """Group .media files and event folders into {event folder: [segments]}.

    A file stands for its whole event folder: an event is converted as one file,
    so it needs all its segments (e.g. when --retry-failed lists single files
    written by older versions)."""
    folders = {path if path.is_dir() else path.parent for path in paths}
    return {folder: sorted(folder.glob('*.media')) for folder in sorted(folders)}


_auto_lock = threading.Lock()
_auto_said = False
_auto_cpu = False   # set once Auto's GPU failed on a file the CPU could do


def _auto_accel():
    """--accel auto's encoder (worked out once), said the first time it's used."""
    global _auto_said
    accel = 'cpu' if _auto_cpu else detect_accel()
    with _auto_lock:
        if not _auto_said:
            _auto_said = True
            say(f"Auto-detected acceleration: {accel}")
    return accel


def _auto_gpu_failed(accel):
    """Use the CPU for the rest of this run, after Auto's GPU encoder (accel)
    failed on a file the CPU converted."""
    global _auto_cpu
    with _auto_lock:
        if not _auto_cpu:
            _auto_cpu = True
            say(f"WARNING: {accel} failed on a file the CPU could convert; Auto uses the CPU for the rest of this run")


def convert_event_parallel(args):
    event_dir, segments, accel, config = args
    # Use a local summary and failed list for thread safety
    local_summary = []
    local_failed = []
    if not EVENT_FOLDER.fullmatch(event_dir.name):
        report(local_summary, f"ERROR: {event_dir} isn't an event folder: expected a name like "
                              f"1736453774_0021 (<start time>_<length in seconds>)")
        local_failed.append(str(event_dir))
        return local_summary, local_failed, False
    success = convert_event(event_dir, segments, local_summary, local_failed, config)
    if success is None:
        # Not in the record format: convert the segments one by one
        results = [convert_media_file(segment, local_summary, accel, local_failed, config)
                   for segment in sorted(segments)]
        success = all(results)
    return local_summary, local_failed, success

CARD_DAY = (4, 2, 2)   # the lengths of a card's YYYY/MM/DD folder names


def check_card_dates(event_dirs, config):
    """Warn when the time zone puts events on other days than the card does.

    The camera files each event under its own local date (DCIM/YYYY/MM/DD/<event>),
    so in the right time zone an event's start, by the camera's clock, falls on
    that date. Events whose clock wasn't set, and folders without dates above
    them, aren't checked."""
    checked, other = 0, []
    for event_dir in sorted(event_dirs):
        days = event_dir.parent.parts[-3:]
        if (not EVENT_FOLDER.fullmatch(event_dir.name) or tuple(map(len, days)) != CARD_DAY
                or not all(part.isdigit() for part in days)):
            continue
        seconds = int(event_dir.name.split('_')[0])
        if not media_format.clock_was_set(seconds):
            continue
        try:
            card_day = date(*map(int, days))
        except ValueError:
            continue
        checked += 1
        if names.local_time(seconds, config.tz).date() != card_day:   # the camera's own time
            other.append(event_dir)
    if other:
        where = config.timezone or "this computer's time zone"
        say(f"WARNING: {len(other)} of {checked} events fall on a different day in {where} than on the "
            f"card (e.g. {other[0]}); is the time zone right? Give the camera's with --timezone")


def report_undated_series(event_dirs, config):
    """Warn about the events recorded before the camera's clock was set whose
    series has no real start (decision record 0006): they're converted, but the
    merge leaves them out of days until it's given."""
    # The series as the merge finds them, from the events' names
    events = [Path(d.name + '.mp4') for d in event_dirs if EVENT_FOLDER.fullmatch(d.name)]
    starts = deployment.series_starts(config)
    undated = [(name, found) for name, found in names.find_series(events) if name not in starts]
    if undated:
        say(f"WARNING: {sum(len(found) for _, found in undated)} events were recorded before the camera's clock "
            f"was set, in series {', '.join(name for name, _ in undated)}; they're merged into their days once "
            f"each series is given its real start with --series-start (the merge lists them)")


def main(argv=None):
    log.setup_streams()   # UTF-8 on the console, also for option errors
    parser = argparse.ArgumentParser(prog='crittervid convert', description="Convert .media files to .mp4 with optional hardware acceleration.")
    parser.add_argument('--input-dir', type=str, default='input/DCIM', help='Input directory containing .media files (default: input/DCIM)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory for converted .mp4 files (default: output)')
    parser.add_argument('--accel', choices=ACCEL_CHOICES, default='auto', help='Hardware acceleration type (default: auto)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel conversions (default: 4)')
    parser.add_argument('--copy', action='store_true', help='Copy video stream without re-encoding (fast, requires compatible input)')
    parser.add_argument('--retry-failed', action='store_true', help='Retry only files that failed in previous run')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum number of retry attempts for failed files (default: 3)')
    deployment.add_options(parser)
    parser.add_argument('--verbose', action='store_true', help='Also show details on the console (they are always in the logs).')
    args = parser.parse_args(argv)
    # On Windows, Ctrl+Break (how a program stops a console child) stops the run
    # like Ctrl+C
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    config = PipelineConfig(input_dir=args.input_dir, output_dir=args.output_dir, accel=args.accel,
                            workers=args.workers, copy=args.copy, retry_failed=args.retry_failed,
                            max_retries=args.max_retries, timezone=args.timezone,
                            clock_offset=args.clock_offset, site=args.site, camera=args.camera,
                            series=tuple(args.series_start) if args.series_start else None)
    log.start_run(config, 'convert', verbose=args.verbose)
    return run(config)


def run(config):
    """Convert config.input_dir into config.output_dir; returns 0, or 1 if
    anything failed or the run was stopped (config.cancel is then set), or 2
    for settings it can't use."""
    if config.workers < 1 or config.max_retries < 1:
        message = 'crittervid convert: error: --workers and --max-retries must be at least 1'
        print(message, file=sys.stderr)
        log.file_only(_log, logging.ERROR, message)
        return 2
    try:
        config = deployment.settle(config, save=True)
    except deployment.Invalid as e:
        say(f"ERROR: {e}")
        return 2
    global _auto_said, _auto_cpu
    with _auto_lock:   # what Auto learnt in an earlier run in this process doesn't carry over
        _auto_said = _auto_cpu = False
    accel = config.accel or 'auto'
    if config.copy:
        say("Using stream copy mode (--copy): will not re-encode video.")
    elif accel == 'auto':
        # Worked out when a file needs encoding: files in the cameras' record
        # format are copied, so most runs never need to try the encoders
        say("Using acceleration: auto (the first encoder that works, when a file needs encoding)")
    else:
        say(f"Using acceleration: {accel}")
        if accel not in ACCEL_OPTIONS:
            say(f"Unknown acceleration type: {accel}. Falling back to CPU.", level=logging.WARNING)
            accel = 'cpu'

    # Determine which files to process
    if config.retry_failed and config.failed_list.exists():
        say(f"Retrying failed files from {config.failed_list}...")
        with open(config.failed_list, 'r', **PATH_TEXT) as f:
            media_files = [Path(p) for p in list_entries(line.strip() for line in f if line.strip())]
        say(f"Found {len(media_files)} failed files to retry.")
    else:
        media_files = list(find_media_files(config.input_dir))
        say(f"Found {len(media_files)} .media files to process.")
    check_card_dates({path.parent for path in media_files}, config)
    report_undated_series({path.parent for path in media_files}, config)

    summary = []
    failed_files = []
    retry_count = 0
    interrupted = False
    max_retries = config.max_retries if config.retry_failed else 1

    while retry_count < max_retries:
        if retry_count > 0:
            say(f"\n=== Retry attempt {retry_count}/{max_retries - 1} for {len(media_files)} failed files ===")
        else:
            say(f"Starting conversion with {config.workers} workers...")

        current_failed = []
        executor = ThreadPoolExecutor(max_workers=config.workers)
        events, futures, collected = None, {}, set()

        def collect(future):
            result_summary, result_failed, success = future.result()
            summary.extend(result_summary)
            current_failed.extend(result_failed)
            collected.add(future)

        try:
            events = group_by_event(media_files)
            for event_dir, segments in events.items():
                futures[executor.submit(convert_event_parallel, (event_dir, segments, accel, config))] = event_dir
            pending = set(futures)
            while pending:
                # Wait in short steps: on Windows, Ctrl+C doesn't interrupt a wait
                # without a timeout
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    collect(future)
        except KeyboardInterrupt:
            say("\nConversion interrupted by user (Ctrl+C). Stopping FFmpeg and writing partial summary...", level=logging.WARNING)
            # Queued events don't start, and the running ones stop and remove
            # their partial files; they're converted again on the next run
            stop_ffmpeg(config.cancel)
            executor.shutdown(wait=True, cancel_futures=True)
            # Keep the results of events that finished before the stop
            for future in futures:
                if future not in collected and not future.cancelled() and future.exception() is None:
                    collect(future)
            summary.append("PROCESS INTERRUPTED: Conversion stopped by user.")
            interrupted = True
            if config.retry_failed or retry_count:
                # Still to retry: this round's failures and the events it didn't
                # finish, including any it hadn't started on
                finished = {futures[future] for future in collected}
                failed_files = current_failed + ([str(p) for p in media_files] if events is None else
                                                 [str(e) for e in events if e not in finished])
            else:
                failed_files = current_failed
            break
        finally:
            executor.shutdown(wait=True)

        # Check if we should retry
        if not current_failed or retry_count >= max_retries - 1:
            failed_files = current_failed
            break

        # Prepare for retry
        media_files = [Path(f) for f in current_failed]
        failed_files = current_failed
        retry_count += 1

    # Write results
    config.output_dir.mkdir(parents=True, exist_ok=True)
    with open(config.conversion_summary, 'w', **PATH_TEXT) as f:
        for line in summary:
            f.write(line + '\n')
    say(f"Summary report written to {config.conversion_summary}")

    # Write failed files list, with absolute paths so --retry-failed finds them
    # from any folder (lists from earlier versions are relative to where they ran)
    if interrupted:
        # A retry lists what it didn't get to; another run keeps the earlier list
        # (it stopped before it could tell which of those are fine now) and adds
        # its own failures
        listed = failed_files
        if not (config.retry_failed or retry_count) and config.failed_list.exists():
            with open(config.failed_list, **PATH_TEXT) as f:
                listed = [line.strip() for line in f if line.strip()] + failed_files
        listed = list_entries(listed)
        if listed:
            with open(config.failed_list, 'w', **PATH_TEXT) as f:
                f.writelines(entry + '\n' for entry in listed)
            say(f"Failed files list written to {config.failed_list} ({len(listed)} files)")
        elif config.retry_failed or retry_count:
            config.failed_list.unlink(missing_ok=True)   # everything it was retrying is done
        say("Conversion stopped before it finished; run it again to convert the rest.", level=logging.WARNING)
    elif failed_files:
        with open(config.failed_list, 'w', **PATH_TEXT) as f:
            f.writelines(entry + '\n' for entry in list_entries(failed_files))
        say(f"Failed files list written to {config.failed_list} ({len(failed_files)} files)")
        say("To retry failed files, run: python -m crittervid convert --retry-failed (with the same folders)")
    else:
        # Remove failed files list if all succeeded
        if config.failed_list.exists():
            config.failed_list.unlink()
        say("All conversions completed successfully!")

    return 1 if failed_files or interrupted else 0

if __name__ == '__main__':
    sys.exit(main())
