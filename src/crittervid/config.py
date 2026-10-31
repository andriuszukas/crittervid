"""Settings for one run of the pipeline, and the token that stops it.

Each step takes a PipelineConfig, which the crittervid command builds from its
options; settings a step doesn't use are ignored by it. Each step's options
for a config are in crittervid.steps.
"""
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

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
    # Deployment details (crittervid.deployment): None when not given, which
    # settling fills from <output>/deployment.json
    timezone: Optional[str] = None   # an IANA name; None after settling: the computer's
    clock_offset: Optional[int] = None   # seconds added to times of events whose clock was set
    site: Optional[str] = None
    camera: Optional[str] = None
    # ((series name, its real start), ...) for series of events recorded before the
    # camera's clock was set (decision record 0006); after settling, with the
    # saved ones and each start as an ISO time with its UTC offset
    series: Optional[tuple] = None
    deployment_settled: bool = field(default=False, compare=False, repr=False)
    cancel: CancelToken = field(default_factory=CancelToken, compare=False, repr=False)

    def __post_init__(self):
        # Accept strings for the folders, as the command line gives them
        object.__setattr__(self, 'input_dir', Path(self.input_dir))
        object.__setattr__(self, 'output_dir', Path(self.output_dir))

    @property
    def tz(self):
        """The camera's time zone, or None for the computer's."""
        return ZoneInfo(self.timezone) if self.timezone else None

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
