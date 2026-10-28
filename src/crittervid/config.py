"""Settings for one run of the pipeline, and the token that stops it.

Each step takes a PipelineConfig, which the crittervid command builds from its
options; settings a step doesn't use are ignored by it.
"""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ACCEL_CHOICES = ('cpu', 'cuda', 'qsv', 'vaapi', 'amf', 'auto')


class CancelToken:
    """Set when a run is being stopped (Ctrl+C now; the GUI's Stop button later,
    #42). Steps check it to stop starting work and to end what's running. One
    token is shared by every step of a run; once set it stays set, so each run
    needs a new PipelineConfig."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def is_cancelled(self):
        return self._event.is_set()

    def wait(self, timeout):
        """Wait up to timeout seconds for the run to be stopped; True if it was."""
        return self._event.wait(timeout)


@dataclass(frozen=True)
class PipelineConfig:
    input_dir: Path = Path('input/DCIM')
    output_dir: Path = Path('output')
    # None: auto-detect for conversion, decode on the CPU for --deep validation
    accel: Optional[str] = None
    workers: int = 4
    copy: bool = False
    retry_failed: bool = False
    max_retries: int = 3
    splits_per_day: int = 1
    force_merge: bool = False
    deep_validate: bool = False
    cancel: CancelToken = field(default_factory=CancelToken, compare=False, repr=False)

    def __post_init__(self):
        # Accept strings for the folders, as the command line gives them
        object.__setattr__(self, 'input_dir', Path(self.input_dir))
        object.__setattr__(self, 'output_dir', Path(self.output_dir))

    @property
    def conversion_summary(self):
        return self.output_dir / 'conversion_summary.txt'

    @property
    def failed_list(self):
        """The converter's list of files to convert again with --retry-failed."""
        return self.output_dir / 'conversion_failed.txt'

    @property
    def merged_dir(self):
        return self.output_dir / 'merged'

    @property
    def merge_summary(self):
        return self.merged_dir / 'merge_summary.txt'

    def command_args(self, step):
        """The options of `crittervid <step>` that do what this config does, for
        showing what a run would do (and for running a step as its own program)."""
        if step == 'convert':
            args = ['--input-dir', str(self.input_dir), '--output-dir', str(self.output_dir)]
            if self.accel:
                args += ['--accel', self.accel]
            args += ['--workers', str(self.workers)]
            if self.copy:
                args.append('--copy')
            if self.retry_failed:
                args.append('--retry-failed')
            return args + ['--max-retries', str(self.max_retries)]
        if step == 'merge':
            args = ['--output-dir', str(self.output_dir), '--splits-per-day', str(self.splits_per_day)]
            return args + (['--force'] if self.force_merge else [])
        if step == 'validate':
            args = ['--output-dir', str(self.output_dir), '--workers', str(self.workers)]
            if self.deep_validate:
                args.append('--deep')
            return args + (['--accel', self.accel] if self.accel else [])
        raise ValueError(f'unknown step {step!r}')
