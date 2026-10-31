import sys
import argparse
import logging
import os

from . import deployment, log, steps
from .config import ACCEL_CHOICES, PipelineConfig


_log = log.get('run')


def say(message, level=None, **extra):
    log.say(_log, message, level, **extra)


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
        _log.error(f'ERROR: {step.name} failed unexpectedly: {e!r} (details in the log)',
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
    deployment.add_options(parser)
    parser.set_defaults(validate=True)
    parser.add_argument('--verbose', action='store_true', help='Also show details on the console (they are always in the logs).')
    args = parser.parse_args(argv)
    # Options left out take each step's default
    config = PipelineConfig(input_dir=args.input_dir, output_dir=args.output_dir, accel=args.accel,
                            workers=4 if args.workers is None else args.workers, copy=args.copy,
                            retry_failed=args.retry_failed, max_retries=args.max_retries,
                            splits_per_day=1 if args.splits_per_day is None else args.splits_per_day,
                            force_merge=args.force_merge, deep_validate=args.deep_validate,
                            timezone=args.timezone, clock_offset=args.clock_offset, site=args.site,
                            camera=args.camera,
                            series=tuple(args.series_start) if args.series_start else None)
    log.start_run(config, 'run', verbose=args.verbose, run_log=not args.dry_run)
    return run(config, validate_output=args.validate, dry_run=args.dry_run)


def run(config, validate_output=True, dry_run=False):
    """Run the steps (crittervid.steps.STEPS) in order, in this process, with one
    cancel token for all of them. A stop between steps ends the run."""
    # Worked out once for the whole run, and saved unless it's a dry run
    try:
        config = deployment.settle(config, save=not dry_run)
    except deployment.Invalid as e:
        say(f"ERROR: {e}")
        return 2
    choices = {'validate': validate_output}
    codes, ran = [], {}
    for number, step in enumerate(steps.STEPS, 1):
        ran[step.name] = step.enabled(config, choices)
        if not ran[step.name]:
            say(f"=== Step {number}: {step.skipped_title or step.title + ' (skipped)'} ===")
            continue
        say(f"=== Step {number}: {step.title} ===")
        if dry_run:
            say(f"[DRY RUN] Would run: {log.command_line(['crittervid', step.name, *step.options(config)])}")
            codes.append(0)
            continue
        if step.prepare:
            step.prepare(config)
        codes.append(run_step(step, config))
        if config.cancel.is_cancelled() and number < len(steps.STEPS):   # stopped (Ctrl+C, or a Stop button)
            raise KeyboardInterrupt

    say("=== Summary ===")
    say(deployment.summary(config))
    for step in steps.STEPS:
        if step.summary:
            for line in step.summary(config, ran[step.name], dry_run):
                say(line)
    if all(code == 0 for code in codes):
        say("All steps completed successfully.")
        return 0
    say("Some steps failed. Check logs above and summary reports.", level=logging.ERROR)
    return 1

if __name__ == '__main__':
    from .cli import main as crittervid
    sys.exit(crittervid(['run', *sys.argv[1:]]))
