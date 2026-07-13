import os

import subprocess
from pathlib import Path
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import platform
from ffmpeg_helper import get_ffmpeg_path

INPUT_ROOT = Path('input/DCIM')
OUTPUT_ROOT = Path('output')
FFMPEG_PATH = get_ffmpeg_path()  # Auto-detect ffmpeg location
SUMMARY_REPORT = OUTPUT_ROOT / 'conversion_summary.txt'
FAILED_FILES = OUTPUT_ROOT / 'conversion_failed.txt'

# Hardware acceleration options for FFmpeg
ACCEL_OPTIONS = {
    'cpu': [],
    'cuda': ['-hwaccel', 'cuda', '-c:v', 'h264_nvenc'],
    'qsv': ['-hwaccel', 'qsv', '-c:v', 'h264_qsv'],
    'vaapi': ['-hwaccel', 'vaapi', '-c:v', 'h264_vaapi'],
    'amf': ['-hwaccel', 'dxva2', '-c:v', 'h264_amf'],
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

# Convert .media to .mp4, preserving timestamp and group ID
def convert_media_file(media_path, summary, accel, failed_files):
    parts = media_path.parts
    try:
        yyyy, mm, dd = parts[-5], parts[-4], parts[-3]
        folder = parts[-2]  # unix_timestamp_groupid
        timestamp, group_id = folder.split('_')
        out_dir = OUTPUT_ROOT / yyyy / mm / dd / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = f"{timestamp}_{group_id}_{media_path.stem}.mp4"
        out_path = out_dir / out_name
        if out_path.exists():
            skip_msg = f"SKIPPED: {media_path} -> {out_path} (already exists)"
            print(skip_msg)
            summary.append(skip_msg)
            return True
        # Check for copy mode
        if hasattr(convert_media_file, 'copy_mode') and convert_media_file.copy_mode:
            cmd = [FFMPEG_PATH, '-y', '-i', str(media_path), '-c:v', 'copy', str(out_path)]
        else:
            input_opts = []
            output_opts = []
            if accel == 'cuda':
                input_opts = ['-hwaccel', 'cuda']
                output_opts = ['-c:v', 'h264_nvenc']
            elif accel == 'qsv':
                input_opts = ['-hwaccel', 'qsv']
                output_opts = ['-c:v', 'h264_qsv']
            elif accel == 'vaapi':
                input_opts = ['-hwaccel', 'vaapi']
                output_opts = ['-c:v', 'h264_vaapi']
            elif accel == 'amf':
                input_opts = ['-hwaccel', 'dxva2']
                output_opts = ['-c:v', 'h264_amf']
            # CPU: no extra options
            cmd = [FFMPEG_PATH, '-y'] + input_opts + ['-i', str(media_path)] + output_opts + [str(out_path)]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            error_msg = f"ERROR: Failed to convert {media_path}: {result.stderr.decode()}"
            print(error_msg)
            summary.append(error_msg)
            failed_files.append(str(media_path))
            return False
        else:
            if hasattr(convert_media_file, 'copy_mode') and convert_media_file.copy_mode:
                success_msg = f"SUCCESS: Copied {media_path} -> {out_path} [copy]"
            else:
                success_msg = f"SUCCESS: Converted {media_path} -> {out_path} [{accel}]"
            print(success_msg)
            summary.append(success_msg)
            return True
    except Exception as e:
        error_msg = f"ERROR: Error processing {media_path}: {e}"
        print(error_msg)
        summary.append(error_msg)
        failed_files.append(str(media_path))
        return False

def convert_media_file_parallel(args):
        media_path, accel, copy_mode = args
        # Use a local summary and failed list for thread safety
        local_summary = []
        local_failed = []
        convert_media_file.copy_mode = copy_mode
        success = convert_media_file(media_path, local_summary, accel, local_failed)
        return local_summary, local_failed, success

def main():
    parser = argparse.ArgumentParser(description="Convert .media files to .mp4 with optional hardware acceleration.")
    parser.add_argument('--input-dir', type=str, default='input/DCIM', help='Input directory containing .media files (default: input/DCIM)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory for converted .mp4 files (default: output)')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default='auto', help='Hardware acceleration type (default: auto)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel conversions (default: 4)')
    parser.add_argument('--copy', action='store_true', help='Copy video stream without re-encoding (fast, requires compatible input)')
    parser.add_argument('--retry-failed', action='store_true', help='Retry only files that failed in previous run')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum number of retry attempts for failed files (default: 3)')
    args = parser.parse_args()

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
        with open(FAILED_FILES, 'r') as f:
            media_files = [Path(line.strip()) for line in f if line.strip()]
        print(f"Found {len(media_files)} failed files to retry.")
    else:
        media_files = list(find_media_files(INPUT_ROOT))
        print(f"Found {len(media_files)} .media files to process.")

    summary = []
    failed_files = []
    retry_count = 0
    max_retries = args.max_retries if args.retry_failed else 1

    while retry_count < max_retries:
        if retry_count > 0:
            print(f"\n=== Retry attempt {retry_count}/{max_retries - 1} for {len(media_files)} failed files ===")
        else:
            print(f"Starting conversion with {args.workers} workers...")

        current_failed = []
        try:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(convert_media_file_parallel, (media_file, accel, args.copy)) for media_file in media_files]
                for future in as_completed(futures):
                    result_summary, result_failed, success = future.result()
                    summary.extend(result_summary)
                    current_failed.extend(result_failed)
        except KeyboardInterrupt:
            print("\nConversion interrupted by user (Ctrl+C). Writing partial summary...")
            summary.append("PROCESS INTERRUPTED: Conversion stopped by user.")
            break

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
    with open(SUMMARY_REPORT, 'w') as f:
        for line in summary:
            f.write(line + '\n')
    print(f"Summary report written to {SUMMARY_REPORT}")

    # Write failed files list
    if failed_files:
        with open(FAILED_FILES, 'w') as f:
            for failed in failed_files:
                f.write(failed + '\n')
        print(f"Failed files list written to {FAILED_FILES} ({len(failed_files)} files)")
        print(f"To retry failed files, run: python convert_media.py --retry-failed")
    else:
        # Remove failed files list if all succeeded
        if FAILED_FILES.exists():
            FAILED_FILES.unlink()
        print("All conversions completed successfully!")

if __name__ == '__main__':
    main()
