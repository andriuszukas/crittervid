# 0006: Dating events recorded before the camera's clock was set

- **Status:** proposed
- **Issues:** #73 (events recorded before the camera clock was set); builds on 0005 (time zone and clock offset)

## Context

- **What happens today:** a camera whose clock wasn't set counts from 0 after power-up. Its events land in `1970/…` or `2106/…` folders on the card, with folder names like `0000000001_0602`. CritterVid converts them, but doesn't merge them into days, and warns (#86).
- **On the real card copy:** 36 of 2,198 events are like this, in two runs. Each run is about 3 hours of back-to-back 10-minute recordings. That's footage worth having in the right days.
- **The card can't date them by itself.** I checked whether a counter in the records keeps running when the clock gets set, which would let the real time be worked out:
  - The millisecond field in bytes 8–11 is the wall clock: Unix time in milliseconds, truncated to 32 bits. For dated events, start minus that field is constant across the card.
  - The microsecond field in bytes 16–23 also follows the wall clock.
  - For the unset events, both count from 0 together with the folder name.
  - So nothing on the card ties a run of unset events to real time. The user has to say when it started, for example from what they remember or from what the footage shows.

## Decision

### Series

- **A series** is a run of unset events from one power-up. CritterVid finds them among the converted files:
  - It sorts the unset events of each card day folder (such as `2106/02/07`) by their start.
  - A new series starts at another day folder, or when an event starts before the previous one ended. A camera that powers up again counts from 0 again.
- **A series is named** after its day folder and first event, such as `2106/02/07/0000000001_0602`. The name holds no real date and doesn't change when more files are converted.

### The correction

- **The user gives the real start of a series' first event,** with `--series-start NAME=TIME`. It can be given more than once, once per series.
- **TIME** is an ISO 8601 date and time, such as `2025-06-01T14:30`. Without a UTC offset it's the camera's local time, in the time zone from 0005, which must then be set. With an offset (`…Z`, `…+02:00`) it's taken as given.
- **Every event in the series moves by the same amount:** the real start minus the first event's camera time. The deployment's clock offset doesn't apply to these events, because their correction replaces it.
- **Corrected events count as dated:**
  - they're merged into their days;
  - merged files get their corrected `creation_time`;
  - validation expects them in those days.
  - Converted files keep the camera's own time, as for every event (0005).

### Saved, listed and checked

- **Saved in `deployment.json`** under `series`, as `{name: the time given, with its UTC offset}`, and reused like the other settings.
  - Its `schema` becomes 2 when a series is saved, so an older version refuses the file instead of rewriting it without the series.
  - A correction for a series no longer on the card is kept, with a warning.
- **Listed in the warnings:** the existing warning about unset events lists each series with no correction: its name, number of events and footage length. It also gives the option to copy, such as `--series-start 2106/02/07/0000000001_0602=<its real start>`.
- **Checks:**
  - A correction that puts a series in the future is an error.
  - So is one that puts it before 2000, and an unknown series name.
  - A corrected series that overlaps dated footage gives a warning, because the camera records one thing at a time.

## Plan: two PRs

1. **Series and corrections** (CLI):
   - series detection in `names.py`, shared by the merge and validation;
   - `--series-start` on `run`, `convert`, `merge` and `validate`, saved in `deployment.json` (schema 2);
   - corrections in day grouping, part windows, merged files' `creation_time` and validation;
   - the series listed in the warnings, and the checks above.
2. **GUI and docs:** a table of the series in the GUI, each with a start time field, plus README and DEPLOYMENT sections.

## Questions for you

1. **Give the real start of each series' first event?** Recommended: it's the one time a person can usually tell. The alternative is a clock offset per series in seconds, which is harder to work out.
2. **Read a time without an offset as the camera's local time?** Recommended: that's how people remember times. The alternative is always UTC.
3. **Keep uncorrected series out of merges, as now?** Recommended: their days are unknown. The alternative is merging them into one "undated" file per series.
