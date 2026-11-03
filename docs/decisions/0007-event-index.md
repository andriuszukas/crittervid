# 0007: An index of the events in each merged file

- **Status:** accepted (2026-11-03)
- **Issues:** #32 (merged files lose the mapping to real time across gaps); it prepares #50 (Camtrap DP export) and #51 (Timelapse)
- **Principles:** written against the project's dev principles ("Growing without weaving"); the last section says how

## Context

- **The problem:** a merged file joins a day's events back to back, without the gaps between them. A position in the file no longer tells you the real time, or which event you're watching.
- **What exists:** each day's `merged_contents.json` records which converted files each merged file holds, but not where each one sits in it. That record is the merge's own bookkeeping: it decides which files are up to date (#88) and which can be removed (#78). It isn't a document for people or other tools.
- **What the merge already knows:**
  - It ends each converted file where its video ends, measured with ffprobe (`video_end`: the video's start plus its length).
  - The concat list gives each file `inpoint 0`, `outpoint` and `duration`, so each file starts where the one before ended.
  - A merged file starts at its first frame, as validation already assumes.
  - So an event's position is the sum of the earlier files' `video_end` values, minus the first file's lead-in (its video's start, when that's above 0).
  - It's accurate to about ±20 ms: that's the drift validation allows for merged days. Audio can differ by up to 0.2 s, as now.
  - Without ffprobe the lengths aren't known.

## Decision

### One public document per merged file: the event index

- **Where it goes:** next to each merged file, `merged_….mp4` gets `merged_….events.csv`, with one row per event in the order they play.
- **It's public.** People, validation and later exports (#50, #51) read it. It stays separate from `merged_contents.json`, which remains the merge's private record, so either can change without breaking the other.
- **Format: CSV with a Frictionless Table Schema,** the standard Camtrap DP is built on.
  - The schema is `docs/schemas/event-index.json`. Its fields are the contract: a reader checks the CSV's header against them. It has a version, raised when a column's meaning changes.
  - Column names follow Camtrap DP's style, so #50 can map them directly:

| Column | Type | Meaning |
|---|---|---|
| `eventStart` | datetime | when the event started: ISO 8601 with its UTC offset, in the camera's time zone (0005), such as `2025-10-01T08:26:41+03:00` |
| `offsetSeconds` | number | where it starts in the merged file, in seconds from the file's first frame |
| `durationSeconds` | number | how long it plays there |
| `filePath` | string | the converted file, relative to the output folder |
| `shiftSeconds` | integer | what was added to the camera's time: the clock offset (0005), or the series' shift (0006). It's data, so a reader can tell corrected times apart without knowing why. |

- **Positions** aren't stored a second time, such as a W3C Media Fragment (`merged_….mp4#t=a,b`). A reader that needs one makes it from `offsetSeconds` and `durationSeconds`, so there's one source of truth.
- **When a length isn't known** (no ffprobe, or a file it can't read), every later position is unknown too. So the index is written with `offsetSeconds` and `durationSeconds` empty for all rows, which the schema declares as missing values. The merge warns once. An empty value is truthful; a guessed one wouldn't be.

### MP4 chapters, rendered from the index

- **What they are:** when the merge writes a merged file, it can embed one chapter per event, from the same rows. Players such as VLC list them and let you jump between events. Each chapter is named by its local start time, such as `08:26:41`.
- **How FFmpeg gets them:**
  - The rows go into an FFmpeg metadata file, passed as an extra input with `-map_chapters 1`.
  - It uses `TIMEBASE=1/1000`, with each chapter's end after its start, and escapes the special characters in titles.
  - The global metadata isn't copied from it, so the merged file's `creation_time` stays as it is. A test checks that.
- **Optional:** `--no-chapters` (a GUI checkbox) leaves them out. Whether they're on by default is question 2.
- **No index, no chapters:** without known lengths, the merged file gets no chapters.

### Decisions apart from effects

- **The pure core:** a new module, `crittervid/index.py`, holds pure functions:
  - `build(starts, lengths, lead_in, shifts)` takes the events' corrected start times (the merge already has them from `group_files_by_day`), their measured lengths, the first file's lead-in and their shifts, and returns the index rows.
  - `to_csv(rows)` and `to_chapters(rows)` render them.
  - They don't touch files, FFmpeg or the clock, and they don't redo time correction, so the same inputs always give the same index.
  - `index.py` imports only the standard library.
- **The shell:** the merge stays the shell.
  - It keeps the `video_end` values it already measures for the concat list, so a new merge needs no extra probing.
  - It calls `build`, writes the CSV next to the merged file, and passes the chapters to FFmpeg.
- **Validation** reads the CSV, the public document, rather than calling the merge.

### Kept current, without needless work

- **Made with the merged file:** the index is written after its merged file, through a temporary file renamed into place. If the run stops in between, the merged file has no index, and the next run writes it.
- **When an index counts as current:** it exists, its header matches the schema, and it's newer than its merged file and its sources, the same test as for merged files (#88). That makes the CSV its own cache, so up-to-date merged files cost no probing on later runs.
- **Missing or outdated indexes** are written again from the converted files' lengths, without merging again. Existing outputs get their indexes on the next run, without re-encoding.
- **Chapters** are only in files merged from now on. `--force` remakes merged files to add them.
- **Cleanup and names:**
  - A merged file the merge removes takes its index with it, and the index is forgotten when its merged file is gone.
  - Validation's name check and the regrouped-days report recognize `.events.csv` files, so they aren't reported as unknown.

### Validation

- **Each merged file's index is checked,** using the probes validation already makes:
  - its rows are the merged file's sources, in order;
  - its lengths add up to the merged file's length, within the existing tolerance;
  - its starts match the corrected event times.
- **A missing or outdated index** is a warning: the next merge writes it.

## Plan: two PRs

1. **Index:**
   - `index.py` with its tests, which run on plain data with no files;
   - the schema;
   - the merge writing and refreshing indexes;
   - the cleanup;
   - validation's checks;
   - docs.
2. **Chapters:**
   - the rendering;
   - the merge passing it to FFmpeg;
   - `--no-chapters` and the GUI checkbox;
   - real-FFmpeg tests that the chapters are in the file, end at the video's end, and leave `creation_time` as it was.

## How this follows the dev principles

- **Shared representation:** one public document per merged file, in a standard format (a Frictionless table, as in Camtrap DP, with its column style) and with standard times (ISO 8601 with offset). Its schema is the contract. The merge's private record stays private.
- **Meaning as data:** corrections show up as `shiftSeconds`. Chapters are a rendering of the same rows, not a second source of truth. Positions are stored once.
- **Decisions apart from effects:** `index.py` is pure and takes the facts it needs as inputs. The merge performs the effects.
- **Executable rules:** a test in the suite checks that `index.py` imports only the standard library. Another checks every written index's header against the schema file.
- **Optional costs:** chapters can be turned off, and nothing adds a dependency. The schema check is a short hand-written test, not Frictionless, which can wait until #50 needs it. That also keeps the set of pinned inputs small.
- **Compatibility:** an additive change. Existing outputs get indexes without being merged again, and the schema's version covers later changes.

## Questions (answered: yes to all three)

1. **One index per merged file, next to it (`merged_….events.csv`)?** Recommended: it travels with its video. The alternative is one index per day, covering all of that day's merged files, with a column for the merged file.
2. **Chapters on by default, with `--no-chapters` to leave them out?** Recommended: they make long merged files easy to move around in, and they cost nothing at merge time. The alternative is off by default (`--chapters`) until they've been tried in more players.
3. **Check the CSV against its schema with a hand-written test, and leave Frictionless until the Camtrap DP export (#50)?** Recommended: no new dependency for now. The alternative is adding Frictionless as a dev dependency now.
