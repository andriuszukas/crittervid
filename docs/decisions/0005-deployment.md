# 0005: Deployment details: time zone, clock offset, site and camera

- **Status:** accepted
- **Issues:** #36 (deployment details), the remaining part of #8 (days grouped in the computer's time zone); it prepares #33's camera and deployment metadata, #32 (merged files mapped to real time), #34 (several cameras) and #73's clock correction

## Context

- **What the card records:**
  - **Start times in UTC:** an event folder's name starts with its start time in UTC (`1739110704_0602`), and every output file's `creation_time` (#89) uses it.
  - **Local dates:** the card's `YYYY/MM/DD` folders are the camera's local dates. So the camera knows its time zone, but the card doesn't record it.
- **How days are grouped today:** the merge, and validation which mirrors it, put events into days and parts of days with `datetime.fromtimestamp()`. That's the time zone of the computer doing the work. Process a card on a laptop set to another time zone, and events move to the neighbouring day: late-evening footage merges into the next day's file, and the merged file names show the wrong hours.
- **Wrong camera clocks:** a camera's clock can be off, and nothing lets a user say so. Cameras whose clock wasn't set at all (#73) are only kept apart today.
- **Who and where:** for later exports (Camtrap DP, #50; Timelapse, #51) and for keeping several cameras apart (#34), a run needs to know which deployment it is: a site and a camera.

## Decision

### Four settings

| Setting | Option | Example | Used for |
|---|---|---|---|
| time zone | `--timezone` | `Europe/Vilnius` (an IANA name) | days and parts of days, the merged files' names, local times in reports |
| clock offset | `--clock-offset` | `+75` or `-3600` (seconds) | added to every event time whose clock was set: days, names, `creation_time` |
| site | `--site` | free text | recorded with the outputs; later exports |
| camera | `--camera` | free text | recorded with the outputs; later exports and #34 |

- **Where they apply:** they're fields of `PipelineConfig`. The options go on `run`, `convert`, `merge` and `validate`, and the GUI gets matching fields.
- **Daylight saving:** the time zone is an IANA name, not a fixed UTC offset, because it changes with daylight saving. Python's `zoneinfo` reads it. Windows, and Linux containers without `/usr/share/zoneinfo`, don't have the time zone data. So the small `tzdata` package becomes the pipeline's only dependency, on every system. The app bundles its data (`collect_data_files('tzdata')` in the spec). An unknown name is an error that names it, before anything runs.

### Clock offset: raw times decide, corrected times are used

- **Whether the clock was set** is decided on the raw time, before the offset is added, so an offset can't move an event across that line.
- **The offset is added** only to events whose clock was set. Days, the merged files' names and the merged files' `creation_time` all use the corrected time.
- **Converted files keep the camera's own time** as their `creation_time`, the one in their names (a change made while implementing PR 2). They aren't converted again when the offset changes, so a corrected time in them would go stale. Merged files record the offset each was made with, in the day's contents record, so a changed offset merges them again.
- **The check against the card** (below) uses the raw time, because the camera wrote its folders by its own clock.

### Days and parts of days, with daylight saving

- **A day** is a local date in the camera's time zone: from that date's local midnight to the next. On the days the clocks change, it's 23 or 25 hours long.
- **Parts of a day** (#86) split its real length into equal windows. The bounds are worked out as UTC instants, and events are compared as instants, not as local clock times.
- **Names** show local clock times. When the clocks go back, a window in the repeated hour could get the same name as another. In that case the name also gets the UTC offset (such as `+0200`).
- **The functions in `names.py`** (`group_files_by_day`, `split_day`, `part_name`) are the ones the merge and validation already share, so both change together. Tests cover both daylight-saving days.

### Kept with the outputs

The settings are saved in `<output>/deployment.json` the first time they're given, and read from there on later runs. So a later `crittervid merge` or `validate` on the same output folder groups days the same way without the options being given again.

- **Changing a value:** an option given on a later run replaces the saved value and is saved. The run warns, naming the old and new value, and says that the days will be regrouped (see below).
- **When it's written:** at the start of a run that converts or merges. A dry run and a validation-only run don't write it; they use the options given, and otherwise the saved values.
- **How it's written:** to a temporary file next to it, which is then renamed into place, so a run stopped halfway leaves the old file. If two runs on one output folder change it at once, the later one wins.
- **What's in it:** a `schema` number, the four settings, and when each was set. The site and camera are optional.
- **A broken file** (not JSON, an unknown time zone, an offset that isn't a whole number of seconds) stops the run with an error naming the file and the problem. The run doesn't fall back to the computer's time zone, because that would regroup the days without anyone noticing.
- **Shown to the user:** the first lines of each run, the run log's settings record, the summary and `crittervid doctor` all show the time zone and offset in use, and where they came from (an option, `deployment.json` or the computer).

### Without a time zone

Days are grouped in the computer's time zone as today, with one warning per run. It names that time zone and says how to set the camera's.

### A check that the time zone fits the card

The card's folder dates are the camera's local dates. So for each event, the date its raw time gives in the chosen time zone should be the date of its card folder. The conversion counts the events where it isn't, and warns, with an example: "12 of 340 events fall on a different day in Europe/London than on the card; is the time zone right?" This catches a wrong or missing time zone at once, without the user knowing the camera's settings.

- **Left out:** events whose clock wasn't set (their 1970 and 2106 folders never match), and cards whose layout has no day folders above the events.
- **Checked on the real card copy:** every 2025 event's folder date matches its folder-name time in one fixed time zone, so the check holds there.

### Changing them later

- **Changing the time zone or clock offset** regroups the days. The merge then makes the new days' files, because their contents changed (#88).
- **Merged files for days that no longer have events** are reported, as validation does today for leftover days. They aren't deleted, because the contents record shows what they hold but not that the footage is merged elsewhere. A later change can delete them once every event they hold is in a current merged file.

### Not in this record

- **Correcting cameras whose clock wasn't set (#73):** each power-up starts its own series counting from 0, so it needs its own correction, such as "the first event of this series started at …". The clock offset here is for clocks that were set but wrong. #73 builds on it.
- **Several cameras on one card or output folder (#34):** the camera setting makes room for it.
- **Writing the site and camera into the MP4 files:** see question 3.
- **Events pushed across midnight by a clock offset** belong to the day their corrected time gives. That's the point of the offset, so it's accepted.
- **The UTC offset that Camtrap DP needs** comes from the time zone and each event's time when that export is written (#50). It isn't stored.

## Plan: three PRs

1. **Settings and days.**
   - The four fields, `deployment.json` (read, save, override), and the options on the commands.
   - `tzdata` as a dependency, and in the app's spec.
   - Day grouping, window splitting and the merged files' names in the camera's time zone, with the clock offset.
   - The warning when no time zone is set, and the check against the card's folder dates.
2. **Clock offset everywhere else:** `creation_time` and the logs' local times; the time zone in `doctor`, the summary and the run log; and leftover merged days reported after a change.
3. **GUI and docs:** the four fields in the GUI, next to the output folder. They're filled from `deployment.json` when an output folder with one is chosen. The time zone is picked from a list of names, so a typo can't get through. The README's dependency notes and the DEPLOYMENT guide are updated.

## Questions (answered: yes to all three)

1. **Save the settings in `<output>/deployment.json` and reuse them?** Recommended. Every step on an output folder then agrees without retyping. The alternative is giving the options on every command.
2. **Without a time zone, keep using the computer's, with a warning?** Recommended. Existing setups keep working. The alternative is refusing to merge until a time zone is set.
3. **Keep the site and camera out of the MP4 files?** Recommended. They'd be in `deployment.json`, the run log and later exports, but not in the videos, because clips get shared and a site name can reveal where a rare species lives. The alternative is writing them into each file's metadata, maybe behind an option. Either way, the docs say that `deployment.json` and the logs hold the site, so they should be left out of shared copies of an output folder.
