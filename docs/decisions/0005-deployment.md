# 0005: Deployment details: time zone, clock offset, site and camera

- **Status:** proposed
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
- **Daylight saving:** the time zone is an IANA name, not a fixed UTC offset, because it changes with daylight saving. Python's `zoneinfo` reads it. On Windows that needs the small `tzdata` package, which becomes the pipeline's only dependency (`; sys_platform == "win32"`) and is bundled in the app.

### Kept with the outputs

The settings are saved in `<output>/deployment.json` the first time they're given, and read from there on later runs. So a later `crittervid merge` or `validate` on the same output folder groups days the same way without the options being given again. An option given on a later run replaces the saved value and is saved, with a line in the run's output saying so.

### Without a time zone

Days are grouped in the computer's time zone as today, with one warning per run. It names that time zone and says how to set the camera's.

### A check that the time zone fits the card

The card's folder dates are the camera's local dates. So for each event, the date its time gives in the chosen time zone should be the date of its card folder. The conversion counts the events where it isn't, and warns, with an example: "12 of 340 events fall on a different day in Europe/London than on the card; is the time zone right?" This catches a wrong or missing time zone at once, without the user knowing the camera's settings.

### Changing them later

- **Changing the time zone or clock offset** regroups the days. The merge then makes the new days' files, because their contents changed (#88).
- **Merged files for days that no longer have events** are reported, as validation does today for leftover days. They aren't deleted, because the contents record shows what they hold but not that the footage is merged elsewhere. A later change can delete them once every event they hold is in a current merged file.

### Not in this record

- **Correcting cameras whose clock wasn't set (#73):** each power-up starts its own series counting from 0, so it needs its own correction, such as "the first event of this series started at …". The clock offset here is for clocks that were set but wrong. #73 builds on it.
- **Several cameras on one card or output folder (#34):** the camera setting makes room for it.
- **Writing the site and camera into the MP4 files:** see question 3.

## Plan: three PRs

1. **Settings and days.**
   - The four fields, `deployment.json` (read, save, override), and the options on the commands.
   - `tzdata` on Windows.
   - Day grouping, window splitting and the merged files' names in the camera's time zone, with the clock offset.
   - The warning when no time zone is set, and the check against the card's folder dates.
2. **Clock offset everywhere else:** `creation_time` and the logs' local times, and leftover merged days reported after a change.
3. **GUI and docs:** the four fields in the GUI (filled from `deployment.json` when the output folder has one), and README and DEPLOYMENT sections.

## Questions for you

1. **Save the settings in `<output>/deployment.json` and reuse them?** Recommended. Every step on an output folder then agrees without retyping. The alternative is giving the options on every command.
2. **Without a time zone, keep using the computer's, with a warning?** Recommended. Existing setups keep working. The alternative is refusing to merge until a time zone is set.
3. **Keep the site and camera out of the MP4 files?** Recommended. They'd be in `deployment.json`, the run log and later exports, but not in the videos, because clips get shared and a site name can reveal where a rare species lives. The alternative is writing them into each file's metadata, maybe behind an option.
