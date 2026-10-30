# 0006: Dating events recorded before the camera's clock was set

- **Status:** accepted
- **Issues:** #73 (events recorded before the camera clock was set); builds on 0005 (time zone and clock offset)

## Context

- **What happens today:** a camera whose clock wasn't set counts from 0 after power-up. Its events land in `1970/…` or `2106/…` folders on the card, with folder names like `0000000001_0602`. CritterVid converts them, but doesn't merge them into days, and warns (#86).
- **On the real card copy:** 36 of 2,198 events are like this. They're one run of about 6 hours of back-to-back 10-minute recordings, from one power-up. The run starts in a `2106/…` folder and carries on in a `1970/…` one where the date wraps: each event starts exactly where the one before ended. That's footage worth having in the right days.
- **The card can't date them by itself.** I checked whether a counter in the records keeps running when the clock gets set, which would let the real time be worked out:
  - The millisecond field in bytes 8–11 is the wall clock: Unix time in milliseconds, truncated to 32 bits. For dated events, start minus that field is constant across the card.
  - The microsecond field in bytes 16–23 also follows the wall clock.
  - For the unset events, both count from 0 together with the folder name.
  - So nothing on the card ties a run of unset events to real time. The user has to say when it started, for example from what they remember or from what the footage shows.

## Decision

### Series

- **A series** is a run of unset events from one power-up. CritterVid finds them among the converted files:
  - It sorts all unset events by their camera time, whatever folder they're in. The folders aren't used, because one power-up can span a `2106/…` and a `1970/…` folder where the date wraps.
  - A new series starts when an event starts before the previous one ended. A camera that powers up again counts from 0 again.
  - An event converted per segment counts as one event, by its event folder, as in day grouping.
  - **Limit:** a later power-up whose events happen to fall after the previous series' end, by camera time, joins that series. The warning lists each series' length, which shows when that has happened.
- **A series is named** after its first event, such as `0000000001_0602`. The name holds no real date and doesn't change when more files are converted.

### The correction

- **The user gives the real start of a series' first event,** with `--series-start NAME=TIME`. It can be given more than once, once per series.
- **TIME** is an ISO 8601 date and time, such as `2025-06-01T14:30`.
  - **Without a UTC offset** it's the camera's local time, in the time zone from 0005. Then the time zone must be set; otherwise it's an error.
  - **With an offset** (`…Z`, `…+02:00`) it's taken as given.
  - **Daylight saving:** a local time that doesn't exist, in the hour skipped when the clocks go forward, is an error. One that happens twice, when they go back, is the first of the two. Giving an offset picks the other.
  - **Saved with its UTC offset resolved,** so a later change of time zone doesn't move it.
- **Every event in the series moves by the same amount:** the real start minus the first event's camera time. The deployment's clock offset doesn't apply to these events, because their correction replaces it.
- **Corrected events count as dated:**
  - they're merged into their days;
  - merged files get their corrected `creation_time`;
  - validation expects them in those days.
  - Converted files keep the camera's own time, as for every event (0005). They have no `creation_time`, because their camera time isn't a real one.
  - The check against the card (0005) skips them, as now: their card folders' dates mean nothing.
- **How it reaches the code:** `names.py` gets a map of `{event: shift in seconds}`, built from the converted files and the corrections. It's used wherever an event's time is worked out:
  - `event_time`, day grouping and part windows, which validation shares;
  - each merged file's `creation_time`, from its first event's corrected time.
  - The day's contents record (0005's `#clock_offsets`) generalizes to the shift of each merged file's first event. That's the only time that sets the file's `creation_time`, so a changed correction makes the merge rebuild the file. A changed correction that moves events to other windows changes their contents too, which triggers a rebuild already.
  - Days left without events after a correction changes are reported, as in 0005.

### Saved, listed and checked

- **Saved in `deployment.json`** under `series`, as `{name: the time given, with its UTC offset}`, and reused like the other settings, with a `set` time for each.
  - Its `schema` becomes 2 only when a series is saved, so an older version refuses that file instead of rewriting it without the series. Files without series stay at 1.
  - Reading it checks `series` like the other fields: a map of names to times with offsets.
  - A correction for a series that isn't among the converted files is kept, with a warning.
- **Listed in the warnings:** the merge's warning about unset events (it sees all the converted files) lists each series with no correction: its name, number of events and length from first start to last end. It also gives the option to copy, such as `--series-start 0000000001_0602=<its real start>`. The conversion gives one warning instead of one per event, naming the series without a start, found as the merge finds them (among the events it converts and those converted already).
- **Checks:**
  - A corrected start that isn't a time a set clock could give (the 2000–2100 range of `clock_was_set`), or that's in the future, is an error.
  - A corrected series that overlaps dated footage gives a warning, because the camera records one thing at a time.

## Plan: two PRs

1. **Series and corrections** (CLI):
   - series detection in `names.py`, shared by the merge and validation;
   - `--series-start` on `run`, `convert`, `merge` and `validate`, saved in `deployment.json` (schema 2 when present);
   - the shift map in day grouping, part windows, merged files' `creation_time`, the contents record and validation;
   - the series listed in the warnings, and the checks above.
2. **GUI and docs:** a table of the series in the GUI, each with a start time field, plus README and DEPLOYMENT sections.

## Questions (answered: yes to all three)

1. **Give the real start of each series' first event?** Recommended: it's the one time a person can usually tell. The alternative is a clock offset per series in seconds, which is harder to work out.
2. **Read a time without an offset as the camera's local time?** Recommended: that's how people remember times. The alternative is always UTC.
3. **Keep uncorrected series out of merges, as now?** Recommended: their days are unknown. The alternative is merging them into one "undated" file per series.
