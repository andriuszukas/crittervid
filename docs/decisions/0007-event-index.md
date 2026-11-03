# 0007: An index of the events in each merged file

- **Status:** proposed (2026-11-03)
- **Issues:** #32 (merged files lose the mapping to real time across gaps); it prepares #50 (Camtrap DP export) and #51 (Timelapse)
- **Principles:** written against the project's dev principles ("Growing without weaving"); the last section says how

## Context

- **The problem:** a merged file joins a day's events back to back, without the gaps between them. A position in the file no longer tells you the real time, or which event you're watching.
- **What exists:**
  - Each day's `merged_contents.json` records which converted files each merged file holds, but not where each one sits in it.
  - That record is the merge's own bookkeeping: it decides which files are up to date (#88) and which can be removed (#78). It isn't a document for people or other tools.
- **What the merge already knows:** it ends each converted file where its video ends, measured with ffprobe (`video_end`). So each event's position in the merged file is the sum of the lengths before it. Without ffprobe those lengths aren't known.

## Decision

### One public document per merged file: the event index

- **Where it goes:** next to each merged file, `merged_….mp4` gets `merged_….events.csv`. It has one row per event, in the order they play.
- **It's public.** People, validation and later exports (#50, #51) read it. It stays separate from `merged_contents.json`, which remains the merge's private record, so either can change without breaking the other.
- **Format: CSV described by a Frictionless Table Schema.** That's the standard Camtrap DP is built on, so the Camtrap DP export (#50) can use the rows directly. The schema is published as `docs/schemas/event-index.json`, versioned like the other documents.
- **Columns:**

| Column | Meaning |
|---|---|
| `fragment` | the event's place in the merged file, as a W3C Media Fragment: `merged_….mp4#t=1234.567,1836.890`. A standard way to address part of a file, which players and exporters understand. |
| `offset_s`, `duration_s` | the same, as numbers (seconds from the file's start, and length) |
| `start` | when the event started, in ISO 8601 with its UTC offset, in the camera's time zone (0005), such as `2025-10-01T08:26:41+03:00` |
| `source` | the converted file, relative to the output folder |
| `shift_s` | what was added to the camera's time: the clock offset (0005), or the series' shift (0006). It's data, so a reader can tell corrected times apart without knowing why. |

- **No ffprobe:** the index is still written, with `offset_s`, `duration_s` and `fragment` left empty, and the merge warns once. An empty value is truthful; a guessed one wouldn't be.

### MP4 chapters, derived from the index

- **What they are:** when the merge writes a merged file, it also embeds one chapter per event. Players such as VLC show them by name and let you jump between events. Each chapter is named by its local start time, such as `08:26:41`.
- **Same source:** they're rendered from the same index rows, so the two can't disagree. FFmpeg writes them from a metadata file, with no new dependency.
- **Optional:** `--no-chapters` (a GUI checkbox) leaves them out. Whether they're on by default is question 2.

### Decisions apart from effects

- **The pure core:** a new module, `crittervid/index.py`, holds pure functions:
  - `build(sources, lengths, shifts, tz)` returns index rows from the ordered sources, their measured lengths, the shifts and the time zone;
  - `to_csv(rows)` and `to_chapters(rows)` render them.
  - They don't touch files, FFmpeg or the clock, so the same inputs always give the same index.
- **The shell:** the merge stays the shell. It measures lengths (as now), calls `build`, writes the CSV next to the merged file, and passes the chapters to FFmpeg.
- **Dependencies:** `index.py` depends only on `names.py` (times) and the standard library, never on `merge.py`. Validation reads the CSV, the public document, rather than calling the merge.

### Kept current, without needless re-merging

- **Made with the merged file:** the index is written whenever its merged file is made, through a temporary file renamed into place, after the MP4.
- **Up-to-date merged files** that have no index, or an older-schema one, get one written. That needs only the converted files' lengths, not a new merge. Existing outputs get indexes on the next run, without re-encoding.
- **Chapters** are only in files made from now on. `--force` remakes the merged files to add them.
- **Removed together:** a merged file the merge removes takes its index with it.

### Validation

- **Each merged file's index is checked:**
  - its rows are the merged file's sources, in order;
  - the lengths add up to the merged file's length, within the tolerance used for merged days now;
  - its starts match the corrected event times.
- **A missing or stale index** is a warning: the next merge writes it.

## Plan: two PRs

1. **Index:**
   - `index.py` with its tests, which run on plain data with no files;
   - the schema;
   - the merge writing and refreshing indexes;
   - validation checking them;
   - docs.
2. **Chapters:** the rendering, the merge passing it to FFmpeg, `--no-chapters` and the GUI checkbox, and a real-FFmpeg test that the chapters are in the file.

## How this follows the dev principles

- **Shared representation:** one public document per merged file, in a standard format (a Frictionless table, as in Camtrap DP). It uses a standard address for a part of a file (Media Fragments) and standard time stamps (ISO 8601 with offset). The merge's private record stays private.
- **Meaning as data:** corrections show up as `shift_s`. Chapters are a rendering of the same data, not a second source of truth.
- **Decisions apart from effects:** `index.py` is pure; the merge performs the effects.
- **Dependencies:** `index.py` depends on `names.py` only. A test in the suite checks that it imports nothing from `merge`, `convert`, `validate`, the GUI or `subprocess`.
- **Optional costs:** chapters can be turned off; the index needs no new dependency. Frictionless is used only by the tests that check the CSV against its schema, as a dev dependency.
- **Compatibility:** an additive change. Existing outputs get indexes without being re-merged. The schema has a version for later changes.

## Questions for you

1. **One index per merged file, next to it (`merged_….events.csv`)?** Recommended: it travels with its video. The alternative is one index per day, covering all of that day's merged files.
2. **Chapters on by default, with `--no-chapters` to leave them out?** Recommended: they cost nothing at merge time and make long merged files easy to move around in. The alternative is off by default, on with `--chapters`.
3. **Frictionless as a dev dependency, to check the CSV against its schema in tests?** Recommended: it's what Camtrap DP uses, and #50 needs it anyway. The alternative is a hand-written check of the columns, with no new dependency.
