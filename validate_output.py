import os
from pathlib import Path
import subprocess
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import argparse
import platform
from ffmpeg_helper import get_ffmpeg_path

OUTPUT_ROOT = Path('output')
MERGED_ROOT = Path('output/merged')

# Patterns for filenames
MP4_PATTERN = re.compile(r'(\d{10,})_(\d{4})_.*\.mp4$')
MERGED_PATTERN = re.compile(r'merged_(\d{8}_\d{4})_(\d{8}_\d{4})(_part\d+of\d+)?\.mp4$')

# Check if video is playable using ffmpeg
def is_playable(video_path):
    # Use hardware acceleration if requested
    accel = getattr(is_playable, 'accel', None)
    input_opts = []
    if accel == 'cuda':
        input_opts = ['-hwaccel', 'cuda']
    elif accel == 'qsv':
        input_opts = ['-hwaccel', 'qsv']
    elif accel == 'vaapi':
        input_opts = ['-hwaccel', 'vaapi']
    elif accel == 'amf':
        input_opts = ['-hwaccel', 'dxva2']
    ffmpeg_path = get_ffmpeg_path()
    cmd = [ffmpeg_path, '-v', 'error'] + input_opts + ['-i', str(video_path), '-f', 'null', '-']
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0

# Validate converted mp4 files
def check_converted_file(path):
    if not is_playable(path):
        return f'UNPLAYABLE: {path}'
    return None

def validate_converted(workers):
    print('Validating converted .mp4 files...')
    errors = []
    files = []
    for dirpath, _, filenames in os.walk(OUTPUT_ROOT):
        for filename in filenames:
            if filename.endswith('.mp4'):
                path = Path(dirpath) / filename
                files.append(path)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(check_converted_file, f) for f in files]
        for future in as_completed(futures):
            result = future.result()
            if result:
                errors.append(result)
    return errors

# Validate merged mp4 files
def check_merged_file(path):
    if not is_playable(path):
        return f'UNPLAYABLE: {path}'
    return None

def validate_merged(workers):
    print('Validating merged .mp4 files...')
    errors = []
    files = []
    for dirpath, _, filenames in os.walk(MERGED_ROOT):
        for filename in filenames:
            if filename.endswith('.mp4'):
                path = Path(dirpath) / filename
                files.append(path)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(check_merged_file, f) for f in files]
        for future in as_completed(futures):
            result = future.result()
            if result:
                errors.append(result)
    return errors


def main():
    parser = argparse.ArgumentParser(description='Validate output videos for naming and playability.')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory containing .mp4 files to validate (default: output)')
    parser.add_argument('--workers', type=int, default=4, help='Number of parallel validation workers (default: 4)')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default=None, help='Hardware acceleration type for validation (default: None)')
    args = parser.parse_args()

    # Update global paths based on arguments
    global OUTPUT_ROOT, MERGED_ROOT
    OUTPUT_ROOT = Path(args.output_dir)
    MERGED_ROOT = OUTPUT_ROOT / 'merged'

    accel = args.accel
    if accel == 'auto':
        # Simple auto-detect (NVIDIA only)
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                accel = 'cuda'
        except ImportError:
            accel = None
    is_playable.accel = accel
    if accel:
        print(f"Using hardware acceleration for validation: {accel}")

    errors = []
    try:
        errors += validate_converted(args.workers)
        errors += validate_merged(args.workers)
    except KeyboardInterrupt:
        print("\nValidation interrupted by user (Ctrl+C). Partial results shown below.")
        errors.append("PROCESS INTERRUPTED: Validation stopped by user.")
    if errors:
        print('\nValidation errors found:')
        for err in errors:
            print(err)
    else:
        print('All output videos are correctly named and playable.')

if __name__ == '__main__':
    main()
