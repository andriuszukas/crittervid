import subprocess
import sys
import argparse
import os

# Get the correct script directory (works both in dev and frozen exe)
if getattr(sys, 'frozen', False):
    # Running in PyInstaller bundle
    SCRIPT_DIR = sys._MEIPASS
else:
    # Running in normal Python
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CONVERT_SCRIPT = os.path.join(SCRIPT_DIR, 'convert_media.py')
MERGE_SCRIPT = os.path.join(SCRIPT_DIR, 'merge_videos.py')
VALIDATE_SCRIPT = os.path.join(SCRIPT_DIR, 'validate_output.py')


def run_script(script, dry_run):
    if dry_run:
        print(f"[DRY RUN] Would run: python {script}")
        return 0
    print(f"Running: python {script}")
    result = subprocess.run([sys.executable, script])
    if result.returncode != 0:
        print(f"ERROR: {script} failed with exit code {result.returncode}")
    return result.returncode


def main():
    # Flush each line so step headers stay in order with the child scripts' output
    # when stdout is a pipe (e.g. the GUI or `| tee`)
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(description="Convert and merge Green Feathers camera videos.")
    parser.add_argument('--input-dir', type=str, default='input/DCIM', help='Input directory containing .media files (default: input/DCIM)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory for converted .mp4 files (default: output)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without running FFmpeg or modifying files.')
    parser.add_argument('--accel', choices=['cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto'], default=None, help='Hardware acceleration type for conversion (default: auto)')
    parser.add_argument('--workers', type=int, default=None, help='Number of parallel conversions (default: 4)')
    parser.add_argument('--copy', action='store_true', help='Copy video stream without re-encoding (fast, requires compatible input)')
    parser.add_argument('--retry-failed', action='store_true', help='Retry only files that failed in previous conversion run')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum number of retry attempts for failed files (default: 3)')
    parser.add_argument('--validate', dest='validate', action='store_true', help='Run output validation after merging (default: enabled).')
    parser.add_argument('--no-validate', dest='validate', action='store_false', help='Skip output validation step (faster, not recommended for final results).')
    parser.add_argument('--splits-per-day', type=int, default=None, help='Number of merged .mp4 files to create per day per group (forwarded to merge_videos.py).')
    parser.set_defaults(validate=True)
    args = parser.parse_args()


    convert_args = []
    if args.input_dir:
        convert_args += ['--input-dir', args.input_dir]
    if args.output_dir:
        convert_args += ['--output-dir', args.output_dir]
    if args.accel:
        convert_args += ['--accel', args.accel]
    if args.workers:
        convert_args += ['--workers', str(args.workers)]
    if args.copy:
        convert_args += ['--copy']
    if args.retry_failed:
        convert_args += ['--retry-failed']
    if args.max_retries:
        convert_args += ['--max-retries', str(args.max_retries)]

    print("=== Step 1: Convert .media to .mp4 ===")
    if args.dry_run:
        print(f"[DRY RUN] Would run: python {CONVERT_SCRIPT} {' '.join(convert_args)}")
        rc1 = 0
    else:
        rc1 = subprocess.run([sys.executable, CONVERT_SCRIPT] + convert_args).returncode

    print("=== Step 2: Merge .mp4 files into longer clips ===")
    merge_args = []
    if args.output_dir:
        merge_args += ['--output-dir', args.output_dir]
    if args.splits_per_day:
        merge_args += ['--splits-per-day', str(args.splits_per_day)]
    if args.dry_run:
        print(f"[DRY RUN] Would run: python {MERGE_SCRIPT} {' '.join(merge_args)}")
        rc2 = 0
    else:
        rc2 = subprocess.run([sys.executable, MERGE_SCRIPT] + merge_args).returncode

    if args.validate:
        print("=== Step 3: Validate output and directory structure ===")
        validate_args = []
        if args.output_dir:
            validate_args += ['--output-dir', args.output_dir]
        if args.workers:
            validate_args += ['--workers', str(args.workers)]
        if args.accel:
            validate_args += ['--accel', args.accel]
        if args.dry_run:
            print(f"[DRY RUN] Would run: python validate_output.py {' '.join(validate_args)}")
            rc3 = 0
        else:
            rc3 = subprocess.run([sys.executable, VALIDATE_SCRIPT] + validate_args).returncode
    else:
        print("=== Step 3: Validation skipped (use --validate to enable) ===")
        rc3 = 0

    print("=== Summary ===")
    print(f"Conversion summary: {os.path.join(args.output_dir, 'conversion_summary.txt')}")
    print(f"Merging summary: {os.path.join(args.output_dir, 'merged', 'merge_summary.txt')}")
    if args.validate:
        print("Validation: see console output above")
    else:
        print("Validation: skipped")
    if rc1 == 0 and rc2 == 0 and rc3 == 0:
        print("All steps completed successfully.")
        return 0
    print("Some steps failed. Check logs above and summary reports.")
    return 1

if __name__ == '__main__':
    sys.exit(main())
