"""The steps of a run, in order (decision record 0004).

Each Step says what `crittervid run`, the crittervid command and the run's
summary need to know about it. Adding a step is a module with run(config) (and
main(argv) for its own command) plus one Step in STEPS at its place; nothing
else changes. Modules are named, not imported, so listing the steps loads none
of them.
"""
import importlib
import os
from dataclasses import dataclass
from typing import Callable, Optional

from . import deployment


@dataclass(frozen=True)
class Step:
    name: str                    # its command (crittervid <name>) and its logger's name
    module: str                  # the module with run(config) and main(argv)
    title: str                   # the heading: === Step N: <title> ===
    options: Callable            # options(config): its command-line options doing what the config says
    skipped_title: Optional[str] = None      # the heading when it doesn't run
    enabled: Callable = lambda config, choices: True   # enabled(config, choices): whether it runs
    prepare: Optional[Callable] = None       # prepare(config): before it runs, never in a dry run
    summary: Optional[Callable] = None       # summary(config, ran, dry_run): its lines in the summary

    def run(self, config):
        """Run the step: its module's run(config), looked up now."""
        return importlib.import_module(self.module).run(config)


# --- convert ---------------------------------------------------------------------

def _convert_options(config):
    args = ['--input-dir', str(config.input_dir), '--output-dir', str(config.output_dir)]
    if config.accel:
        args += ['--accel', config.accel]
    args += ['--workers', str(config.workers)]
    if config.copy:
        args.append('--copy')
    if config.retry_failed:
        args.append('--retry-failed')
    return args + ['--max-retries', str(config.max_retries)] + deployment.options(config)


def _convert_summary(config, ran, dry_run):
    return [f"Conversion summary: {config.conversion_summary}"]


# --- merge -----------------------------------------------------------------------

def _merge_options(config):
    args = ['--output-dir', str(config.output_dir), '--splits-per-day', str(config.splits_per_day)]
    return args + (['--force'] if config.force_merge else []) + deployment.options(config)


def _merge_prepare(config):
    # Remove the previous run's report first, so it can't be mistaken for this
    # run's if the merge stops before writing one
    try:
        os.remove(config.merge_summary)
    except OSError:
        pass


def _merge_summary(config, ran, dry_run):
    lines = [f"Merging summary: {config.merge_summary}"]
    # Repeat the merge's warnings (e.g. older merged files it kept), which are
    # otherwise easy to miss among the step's output. The report is this run's:
    # an earlier one was removed before the step
    if not dry_run and os.path.isfile(config.merge_summary):
        # Paths in it can be in any encoding (Linux)
        with open(config.merge_summary, encoding='utf-8', errors='replace') as f:
            lines += [f"Merge {line.rstrip()}" for line in f if line.startswith('WARNING')]
    return lines


# --- validate --------------------------------------------------------------------

def _validate_options(config):
    args = ['--output-dir', str(config.output_dir), '--workers', str(config.workers)]
    if config.deep_validate:
        args.append('--deep')
    return args + (['--accel', config.accel] if config.accel else []) + deployment.options(config)


def _validate_summary(config, ran, dry_run):
    return ["Validation: see console output above" if ran else "Validation: skipped"]


STEPS = (
    Step('convert', 'crittervid.convert', 'Convert .media to .mp4',
         options=_convert_options, summary=_convert_summary),
    Step('merge', 'crittervid.merge', 'Merge .mp4 files into longer clips',
         options=_merge_options, prepare=_merge_prepare, summary=_merge_summary),
    Step('validate', 'crittervid.validate', 'Validate output and directory structure',
         options=_validate_options, summary=_validate_summary,
         skipped_title='Validation skipped (use --validate to enable)',
         enabled=lambda config, choices: choices.get('validate', True)),
)


def get(name):
    """The step called `name`."""
    for step in STEPS:
        if step.name == name:
            return step
    raise KeyError(name)
