import os

import subprocess
from pathlib import Path
import argparse
import sys
import re
import signal
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import platform
from ffmpeg_helper import get_ffmpeg_path
import media_format

INPUT_ROOT = Path('input/DCIM')
OUTPUT_ROOT = Path('output')
FFMPEG_PATH = get_ffmpeg_path()  # Auto-detect ffmpeg location
SUMMARY_REPORT = OUTPUT_ROOT / 'conversion_summary.txt'
FAILED_FILES = OUTPUT_ROOT / 'conversion_failed.txt'

class Stopped(BaseException):
    """The run was stopped (Ctrl+C) before this conversion could finish. Not an
    Exception, so the per-file error handling doesn't report it as a failure."""


# Set on Ctrl+C: no new FFmpeg starts, and the running ones are ended
STOP = threading.Event()
_running = set()
_running_lock = threading.Lock()


def run_ffmpeg(cmd):
    """Run FFmpeg like subprocess.run(cmd, capture_output=True), but so that
    stop_ffmpeg() can end it. Raises Stopped instead of starting it once the run
    is stopping, and if it fails because the run was stopped."""
    with _running_lock:
        if STOP.is_set():
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
    if proc.returncode != 0 and STOP.wait(0.6):
        raise Stopped
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def stop_ffmpeg():
    """Stop starting FFmpeg and end the running ones. A console's Ctrl+C reaches
    them too, but not when the run is stopped some other way."""
    with _running_lock:
        STOP.set()
        for proc in _running:
            proc.terminate()


# Hardware acceleration options for FFmpeg: (options before -i, options after -i)
ACCEL_OPTIONS = {
    'cpu': ([], []),
    'cuda': (['-hwaccel', 'cuda'], ['-c:v', 'h264_nvenc']),
    'qsv': (['-hwaccel', 'qsv'], ['-c:v', 'h264_qsv']),
    'vaapi': (['-hwaccel', 'vaapi'], ['-c:v', 'h264_vaapi']),
    'amf': (['-hwaccel', 'dxva2'], ['-c:v', 'h264_amf']),
}

def detect_accel():
    # Try to auto-detect GPU type
    try:
        import GPUtil
        gpus = GPUtil.getGPUs()
        if gpus:
            # NVIDIA
            return 'cuda'
    except ImportError:
        pass
    # Try to detect Intel QSV
    if platform.system() == 'Windows':
        try:
            import wmi
            c = wmi.WMI()
            for gpu in c.Win32_VideoController():
                if 'Intel' in gpu.Name:
                    return 'qsv'
        except ImportError:
            pass
    # Try to detect AMD (AMF)
    try:
        import wmi
        c = wmi.WMI()
        for gpu in c.Win32_VideoController():
            if 'AMD' in gpu.Name:
                return 'amf'
    except ImportError:
        pass
    # Try to detect VAAPI (Linux)
    if platform.system() == 'Linux':
        return 'vaapi'
    return 'cpu'

# Recursively scan for .media files
def find_media_files(root):
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if filename.endswith('.media'):
                yield Path(dirpath) / filename

EVENT_FOLDER = re.compile(r'\d{10,}_\d{4}')


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


def record_format_command(video, audio, out_path, workdir):
    """FFmpeg command that writes video frames and audio records into an MP4
    without re-encoding the video.

    Each frame keeps the timestamp the camera recorded. The audio, placed by its
    own timestamps from the first frame to the end of the last one, is encoded as
    AAC; with no audio recorded it's silence, so every event file has audio."""
    video_path = workdir / 'video.ts'
    video_path.write_bytes(media_format.video_ts(video))
    last_frame = video[-1][0] - video[-2][0] if len(video) > 1 else 66_667
    audio_path = workdir / 'audio.pcm'
    audio_path.write_bytes(media_format.audio_pcm(audio, video[0][0], video[-1][0] + last_frame))
    # FFmpeg treats jumps of more than 10 s in MPEG-TS timestamps as errors and
    # closes them up; a raised threshold keeps real pauses in the recording
    return [FFMPEG_PATH, '-y', '-dts_delta_threshold', '1000000', '-i', str(video_path),
            '-f', 's16le', '-ar', str(media_format.AUDIO_SAMPLE_RATE), '-i', str(audio_path),
            '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-f', 'mp4', str(out_path)]


def report(summary, msg):
    print(msg)
    summary.append(msg)


def convert_event(event_dir, segments, summary, failed_files):
    """Convert an event folder's .media segments into one MP4 for the whole event.

    Returns True or False for success, or None if no segment is in the cameras'
    record format (the caller then converts them one by one). One file per event
    keeps the camera's timeline exact; joining per-segment files would add an
    estimated frame duration at every join."""
    folder = event_dir.name   # <unix timestamp>_<duration in seconds>
    yyyy, mm, dd = event_dir.parts[-4:-1]
    out_dir = OUTPUT_ROOT / yyyy / mm / dd
    out_path = out_dir / f'{folder}.mp4'
    tmp_path = out_dir / f'{folder}.mp4.part'
    try:
        if out_path.exists():
            report(summary, f"SKIPPED: {event_dir} -> {out_path} (already exists)")
            return True
        try:
            video, audio, notes = read_event(segments)
        except media_format.MediaFormatError:
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='crittervid-') as workdir:
            cmd = record_format_command(video, audio, tmp_path, Path(workdir))
            result = run_ffmpeg(cmd)
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            report(summary, f"ERROR: Failed to convert {event_dir}: {result.stderr.decode('utf-8', errors='replace')}")
            failed_files.append(str(event_dir))
            return False
        tmp_path.replace(out_path)
        sound = 'audio AAC' if audio else 'silent audio (none recorded)'
        report(summary, f"SUCCESS: Converted {event_dir} ({len(segments)} segments) -> {out_path} "
                        f"[video copied with camera timestamps, {sound}]")
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


# Convert .media to .mp4, preserving timestamp and group ID
def convert_media_file(media_path, summary, accel, failed_files, copy_mode=False):
    parts = media_path.parts
    tmp_path = None
    try:
        yyyy, mm, dd = parts[-5], parts[-4], parts[-3]
        folder = parts[-2]  # unix_timestamp_groupid
        timestamp, group_id = folder.split('_')
        out_dir = OUTPUT_ROOT / yyyy / mm / dd / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{timestamp}_{group_id}_{media_path.stem}.mp4"
        out_path = out_dir / out_name
        # Write to a temp file and rename on success, so an interrupted or failed
        # conversion never leaves a partial .mp4 that later runs would skip
        tmp_path = out_dir / (out_name + '.part')
        if out_path.exists():
            skip_msg = f"SKIPPED: {media_path} -> {out_path} (already exists)"
            print(skip_msg)
            summary.append(skip_msg)
            return True
        # Files that aren't in the cameras' record format: let FFmpeg read them directly
        if copy_mode:
            cmd = [FFMPEG_PATH, '-y', '-i', str(media_path), '-c:v', 'copy'] + FALLBACK_OUTPUT + [str(tmp_path)]
            method = 'copy'
        else:
            input_opts, output_opts = ACCEL_OPTIONS[accel]
            cmd = [FFMPEG_PATH, '-y'] + input_opts + ['-i', str(media_path)] + output_opts + FALLBACK_OUTPUT + [str(tmp_path)]
            method = accel
        result = run_ffmpeg(cmd)
        if result.returncode != 0:
            tmp_path.unlink(missing_ok=True)
            error_msg = f"ERROR: Failed to convert {media_path}: {result.stderr.decode('utf-8', errors='replace')}"
            print(error_msg)
            summary.append(error_msg)
            failed_files.append(str(media_path))
            return False
        else:
            tmp_path.replace(out_path)
            verb = 'Copied' if method == 'copy' else 'Converted'
            success_msg = f"SUCCESS: {verb} {media_path} -> {out_path} [{method}]"
            print(success_msg)
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
        print(error_msg)
        summary.append(error_msg)
        failed_files.append(str(media_path))
        return False

def list_entries(paths):
    """Paths for the failed-files list: absolute, each once. Paths from lists of
    earlier versions are relative to where those ran, as before; making them
    absolute too lets them match this run's (case-insensitively on Windows)."""
    return [str(p) for p in dict.fromkeys(Path(os.path.abspath(p)) for p in paths)]


def group_by_event(paths):
    """Group .media files and event folders into {event folder: [segments]}.

    A file stands for its whole event folder: an event is converted as one file,
    so it needs all its segments (e.g. when --retry-failed lists single files
    written by older versions)."""
    folders = {path if path.is_dir() else path.parent for path in paths}
    return {folder: sorted(folder.glob('*.media')) for folder in sorted(folders)}


def convert_event_parallel(args):
    event_dir, segments, accel, copy_mode = args
    # Use a local summary and failed list for thread safety
    local_summary = []
    local_failed = []
    if not EVENT_FOLDER.fullmatch(event_dir.name):
        report(local_summary, f"ERROR: {event_dir} isn't an event folder: expected a name like "
                              f"1736453774_0021 (<start time>_<length in seconds>)")
        local_failed.append(str(event_dir))
        return local_summary, local_failed, False
    success = convert_event(event_dir, segments, local_summary, local_failed)
    if success is None:
        # Not in the record format: convert the segments one by one
        results = [convert_media_file(segment, local_summary, accel, local_failed, copy_mode)
                   for segment in sorted(segments)]
        success = all(results)
    if success and not media_format.clock_was_set(int(event_dir.name.split('_')[0])):
        report(local_summary, f"WARNING: {event_dir}: recorded before the camera's clock was set, "
                              f"so its real time is unknown; it won't be merged with dated days")
    return local_summary, local_failed, success

def main():
    # Paths can contain characters the console's code page can't encode (e.g. the
    # Windows default when output is piped to the GUI), so always write UTF-8.
    # The streams are None when there's no console at all (pythonw).
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description="Convert .media files to .mp4 with optional hardware acceleration.")
    parser.add_argument('--input-dir', type=str, default='input/DCIM', help='Input directory containing .media files (default: input/DCIM)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory for converted .mp4 files (default: output)')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default='auto', help='Hardware acceleration type (default: auto)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel conversions (default: 4)')
    parser.add_argument('--copy', action='store_true', help='Copy video stream without re-encoding (fast, requires compatible input)')
    parser.add_argument('--retry-failed', action='store_true', help='Retry only files that failed in previous run')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum number of retry attempts for failed files (default: 3)')
    args = parser.parse_args()
    # On Windows, Ctrl+Break (how a program stops a console child) stops the run
    # like Ctrl+C
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)

    # Update global paths based on arguments
    global INPUT_ROOT, OUTPUT_ROOT, SUMMARY_REPORT, FAILED_FILES
    INPUT_ROOT = Path(args.input_dir)
    OUTPUT_ROOT = Path(args.output_dir)
    SUMMARY_REPORT = OUTPUT_ROOT / 'conversion_summary.txt'
    FAILED_FILES = OUTPUT_ROOT / 'conversion_failed.txt'

    accel = args.accel
    if args.copy:
        print("Using stream copy mode (--copy): will not re-encode video.")
    else:
        if accel == 'auto':
            accel = detect_accel()
            print(f"Auto-detected acceleration: {accel}")
        else:
            print(f"Using acceleration: {accel}")
        if accel not in ACCEL_OPTIONS:
            print(f"Unknown acceleration type: {accel}. Falling back to CPU.")
            accel = 'cpu'

    # Determine which files to process
    if args.retry_failed and FAILED_FILES.exists():
        print(f"Retrying failed files from {FAILED_FILES}...")
        with open(FAILED_FILES, 'r', encoding='utf-8') as f:
            media_files = [Path(p) for p in list_entries(line.strip() for line in f if line.strip())]
        print(f"Found {len(media_files)} failed files to retry.")
    else:
        media_files = list(find_media_files(INPUT_ROOT))
        print(f"Found {len(media_files)} .media files to process.")

    summary = []
    failed_files = []
    retry_count = 0
    interrupted = False
    max_retries = args.max_retries if args.retry_failed else 1

    while retry_count < max_retries:
        if retry_count > 0:
            print(f"\n=== Retry attempt {retry_count}/{max_retries - 1} for {len(media_files)} failed files ===")
        else:
            print(f"Starting conversion with {args.workers} workers...")

        current_failed = []
        executor = ThreadPoolExecutor(max_workers=args.workers)
        events, futures, collected = None, {}, set()

        def collect(future):
            result_summary, result_failed, success = future.result()
            summary.extend(result_summary)
            current_failed.extend(result_failed)
            collected.add(future)

        try:
            events = group_by_event(media_files)
            for event_dir, segments in events.items():
                futures[executor.submit(convert_event_parallel, (event_dir, segments, accel, args.copy))] = event_dir
            pending = set(futures)
            while pending:
                # Wait in short steps: on Windows, Ctrl+C doesn't interrupt a wait
                # without a timeout
                done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                for future in done:
                    collect(future)
        except KeyboardInterrupt:
            print("\nConversion interrupted by user (Ctrl+C). Stopping FFmpeg and writing partial summary...")
            # Queued events don't start, and the running ones stop and remove
            # their partial files; they're converted again on the next run
            stop_ffmpeg()
            executor.shutdown(wait=True, cancel_futures=True)
            # Keep the results of events that finished before the stop
            for future in futures:
                if future not in collected and not future.cancelled() and future.exception() is None:
                    collect(future)
            summary.append("PROCESS INTERRUPTED: Conversion stopped by user.")
            interrupted = True
            if args.retry_failed or retry_count:
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
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_REPORT, 'w', encoding='utf-8') as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {SUMMARY_REPORT}")

    # Write failed files list, with absolute paths so --retry-failed finds them
    # from any folder (lists from earlier versions are relative to where they ran)
    if interrupted:
        # A retry lists what it didn't get to; another run keeps the earlier list
        # (it stopped before it could tell which of those are fine now) and adds
        # its own failures
        listed = failed_files
        if not (args.retry_failed or retry_count) and FAILED_FILES.exists():
            with open(FAILED_FILES, encoding='utf-8') as f:
                listed = [line.strip() for line in f if line.strip()] + failed_files
        listed = list_entries(listed)
        if listed:
            with open(FAILED_FILES, 'w', encoding='utf-8') as f:
                f.writelines(entry + '\n' for entry in listed)
            print(f"Failed files list written to {FAILED_FILES} ({len(listed)} files)")
        elif args.retry_failed or retry_count:
            FAILED_FILES.unlink(missing_ok=True)   # everything it was retrying is done
        print("Conversion stopped before it finished; run it again to convert the rest.")
    elif failed_files:
        with open(FAILED_FILES, 'w', encoding='utf-8') as f:
            f.writelines(entry + '\n' for entry in list_entries(failed_files))
        print(f"Failed files list written to {FAILED_FILES} ({len(failed_files)} files)")
        print(f"To retry failed files, run: python convert_media.py --retry-failed")
    else:
        # Remove failed files list if all succeeded
        if FAILED_FILES.exists():
            FAILED_FILES.unlink()
        print("All conversions completed successfully!")

    return 1 if failed_files or interrupted else 0

if __name__ == '__main__':
    sys.exit(main())
