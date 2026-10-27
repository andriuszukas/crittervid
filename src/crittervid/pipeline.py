import sys
import argparse
import os
import traceback

from . import convert, merge, validate


def run_step(step, args):
    """Run one step of the pipeline in this process and return its exit code.
    A step that rejects its options or fails unexpectedly fails only itself, as
    when the steps were separate programs; Ctrl+C stops the whole run."""
    try:
        return step.main(args) or 0
    except SystemExit as e:   # e.g. argparse rejected an option
        # As Python treats it at exit: None is success, a message is printed
        # and is a failure
        if e.code is None or isinstance(e.code, int):
            return e.code or 0
        print(e.code, file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 1


def main(argv=None):
    # Flush each line so step headers stay in order with the child scripts' output
    # when stdout is a pipe (e.g. the GUI or `| tee`). Write UTF-8 like the child
    # scripts, since paths can contain characters the console's code page lacks
    # The streams are None when there's no console at all (pythonw).
    if sys.stdout is not None:
        sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(prog='crittervid run', description="Convert, merge and validate camera-trap videos.")
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
    parser.add_argument('--deep-validate', action='store_true', help='Also decode every output in full during validation (slow: about as long as the footage).')
    parser.add_argument('--force-merge', action='store_true', help='Merge every part again, including ones '
                        'that are already up to date (forwarded to merge_videos.py as --force).')
    parser.add_argument('--splits-per-day', type=int, default=None, help='Number of equal time windows to divide each day into, one merged file per window with '
                             'events (1 to 1440; forwarded to merge_videos.py).')
    parser.set_defaults(validate=True)
    args = parser.parse_args(argv)


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
        print(f"[DRY RUN] Would run: crittervid convert {' '.join(convert_args)}")
        rc1 = 0
    else:
        rc1 = run_step(convert, convert_args)
        if convert.STOP.is_set():   # the conversion was stopped with Ctrl+C
            raise KeyboardInterrupt

    print("=== Step 2: Merge .mp4 files into longer clips ===")
    merge_summary = os.path.join(args.output_dir, 'merged', 'merge_summary.txt')
    merge_args = []
    if args.output_dir:
        merge_args += ['--output-dir', args.output_dir]
    if args.splits_per_day is not None:   # the merge rejects values it can't use
        merge_args += ['--splits-per-day', str(args.splits_per_day)]
    if args.force_merge:
        merge_args += ['--force']
    if args.dry_run:
        print(f"[DRY RUN] Would run: crittervid merge {' '.join(merge_args)}")
        rc2 = 0
    else:
        # Remove the previous run's report first, so it can't be mistaken for this
        # run's if the merge stops before writing one
        try:
            os.remove(merge_summary)
        except OSError:
            pass
        rc2 = run_step(merge, merge_args)

    if args.validate:
        print("=== Step 3: Validate output and directory structure ===")
        validate_args = []
        if args.output_dir:
            validate_args += ['--output-dir', args.output_dir]
        if args.workers:
            validate_args += ['--workers', str(args.workers)]
        if args.deep_validate:
            validate_args += ['--deep']
        if args.accel:
            validate_args += ['--accel', args.accel]
        if args.dry_run:
            print(f"[DRY RUN] Would run: crittervid validate {' '.join(validate_args)}")
            rc3 = 0
        else:
            rc3 = run_step(validate, validate_args)
    else:
        print("=== Step 3: Validation skipped (use --validate to enable) ===")
        rc3 = 0

    print("=== Summary ===")
    print(f"Conversion summary: {os.path.join(args.output_dir, 'conversion_summary.txt')}")
    print(f"Merging summary: {merge_summary}")
    # Repeat the merge's warnings (e.g. older merged files it kept), which are
    # otherwise easy to miss among the step's output. The report is this run's:
    # an earlier one was removed before the step
    if not args.dry_run and os.path.isfile(merge_summary):
        # Paths in it can be in any encoding (Linux)
        with open(merge_summary, encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('WARNING'):
                    print(f"Merge {line.rstrip()}")
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
    from .cli import main as crittervid
    sys.exit(crittervid(['run', *sys.argv[1:]]))
