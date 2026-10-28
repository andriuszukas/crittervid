import sys
import argparse
import logging
import os

from . import convert, log, merge, validate
from .config import ACCEL_CHOICES, PipelineConfig


_log = log.get('run')


def say(message, **extra):
    log.say(_log, message, **extra)


def run_step(step, config):
    """Run one step of the pipeline in this process and return its exit code.
    A step that rejects its settings or fails unexpectedly fails only itself,
    as when the steps were separate programs; Ctrl+C stops the whole run."""
    try:
        return step.run(config) or 0
    except SystemExit as e:   # e.g. argparse rejected an option
        # As Python treats it at exit: None is success, a message is printed
        # and is a failure
        if e.code is None or isinstance(e.code, int):
            return e.code or 0
        print(e.code, file=sys.stderr)
        log.file_only(_log, logging.ERROR, str(e.code))
        return 1
    except Exception as e:
        # The traceback goes to the logs; the console gets one line
        _log.error(f'ERROR: {step.__name__.rsplit(".", 1)[-1]} failed unexpectedly: {e!r} (details in the log)',
                   exc_info=True)
        return 1


def main(argv=None):
    log.setup_streams()   # UTF-8 on the console, also for option errors
    parser = argparse.ArgumentParser(prog='crittervid run', description="Convert, merge and validate camera-trap videos.")
    parser.add_argument('--input-dir', type=str, default='input/DCIM', help='Input directory containing .media files (default: input/DCIM)')
    parser.add_argument('--output-dir', type=str, default='output', help='Output directory for converted .mp4 files (default: output)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without running FFmpeg or modifying files.')
    parser.add_argument('--accel', choices=ACCEL_CHOICES, default=None, help='Hardware acceleration type for conversion (default: auto)')
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
    parser.add_argument('--verbose', action='store_true', help='Also show details on the console (they are always in the logs).')
    args = parser.parse_args(argv)
    # Options left out take each step's default
    config = PipelineConfig(input_dir=args.input_dir, output_dir=args.output_dir, accel=args.accel,
                            workers=4 if args.workers is None else args.workers, copy=args.copy,
                            retry_failed=args.retry_failed, max_retries=args.max_retries,
                            splits_per_day=1 if args.splits_per_day is None else args.splits_per_day,
                            force_merge=args.force_merge, deep_validate=args.deep_validate)
    log.start_run(config, 'run', verbose=args.verbose, run_log=not args.dry_run)
    return run(config, validate_output=args.validate, dry_run=args.dry_run)


def run(config, validate_output=True, dry_run=False):
    """Convert, merge and (unless told not to) validate, in this process, with
    one cancel token for all three steps."""
    def step(name, module):
        if dry_run:
            say(f"[DRY RUN] Would run: crittervid {name} {' '.join(config.command_args(name))}")
            return 0
        return run_step(module, config)

    say("=== Step 1: Convert .media to .mp4 ===")
    rc1 = step('convert', convert)
    if config.cancel.is_cancelled():   # the conversion was stopped with Ctrl+C
        raise KeyboardInterrupt

    say("=== Step 2: Merge .mp4 files into longer clips ===")
    merge_summary = config.merge_summary
    if not dry_run:
        # Remove the previous run's report first, so it can't be mistaken for this
        # run's if the merge stops before writing one
        try:
            os.remove(merge_summary)
        except OSError:
            pass
    rc2 = step('merge', merge)

    if validate_output:
        say("=== Step 3: Validate output and directory structure ===")
        rc3 = step('validate', validate)
    else:
        say("=== Step 3: Validation skipped (use --validate to enable) ===")
        rc3 = 0

    say("=== Summary ===")
    say(f"Conversion summary: {config.conversion_summary}")
    say(f"Merging summary: {merge_summary}")
    # Repeat the merge's warnings (e.g. older merged files it kept), which are
    # otherwise easy to miss among the step's output. The report is this run's:
    # an earlier one was removed before the step
    if not dry_run and os.path.isfile(merge_summary):
        # Paths in it can be in any encoding (Linux)
        with open(merge_summary, encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('WARNING'):
                    say(f"Merge {line.rstrip()}")
    if validate_output:
        say("Validation: see console output above")
    else:
        say("Validation: skipped")
    if rc1 == 0 and rc2 == 0 and rc3 == 0:
        say("All steps completed successfully.")
        return 0
    say("Some steps failed. Check logs above and summary reports.")
    return 1

if __name__ == '__main__':
    from .cli import main as crittervid
    sys.exit(crittervid(['run', *sys.argv[1:]]))
