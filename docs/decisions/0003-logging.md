# 0003: Logging with run IDs, a log per run and an app log

- **Status:** proposed
- **Issues:** #26 (structured logging). It's the base for #45 (diagnostics zip), #29 (`crittervid doctor`) and #57 (optional OpenTelemetry), and it helps #25 (step interface).

## Context

All output today is `print`: about 60 calls across `convert`, `merge`, `validate` and `pipeline`.

- **What reads that output:**
  - **The console** shows it as it is.
  - **The GUI** streams it into its output box.
  - **The tests** check it for lines such as `SUCCESS: …`, `WARNING: kept 1 merged files`, `ERROR: …` and `=== Step 2`.
  - **The summary files:** the converter and the merge also collect the same lines into `conversion_summary.txt` and `merged/merge_summary.txt`. `process_all` repeats the merge's warnings from that file.
- **What's missing:**
  - **Nothing ties a run's lines together,** and nothing records when they happened or how long each file took.
  - **An FFmpeg failure prints FFmpeg's entire stderr,** often hundreds of lines, into the console and the summary.
  - **An unexpected error's traceback** reaches only the console. Once the console window is closed, it's gone.
- **For bug reports,** someone helping a user needs one file that says what happened in a run. #45 will collect it into a diagnostics zip.

The agreed approach (2026-10-14): the standard `logging` module, local only, nothing sent anywhere. OpenTelemetry stays an optional add-on, off by default (#57).

## Decision

### One logging setup per run

A new module, `crittervid/log.py`. Every command calls `log.start_run(config, command)` once, so a whole `crittervid run` is one run. It does three things:

1. **Creates a run ID:** 8 random hex characters, such as `3f9a2c1e`, plus the start time in UTC.
2. **Attaches three outputs** to the `crittervid` logger:
   - **Console:** standard output, readable text, described below.
   - **Run log:** `<output folder>/logs/<start time>_<run ID>.jsonl`, one JSON object per line. Only when the run writes output, so not for `--dry-run`.
   - **App log:** `crittervid.log` in the OS's usual folder for app logs: `%LOCALAPPDATA%\CritterVid\Logs` on Windows, `~/Library/Logs/CritterVid` on macOS, and `$XDG_STATE_HOME/crittervid` (default `~/.local/state/crittervid`) on Linux. It rotates, keeping five files of 1 MB. This is where a failure before the output folder exists ends up, such as a bad option or a missing input folder.
3. **Prints `Run 3f9a2c1e started`** as the first line, so the GUI shows the ID and a bug report can quote it.

The run's settings are a `PipelineConfig` (decision record 0002). Logging uses the standard library only, so the pipeline still needs no packages.

### What each record carries

| Field | Example | Where it comes from |
|---|---|---|
| `time` | `2026-10-28T09:14:03.211Z` | UTC |
| `run` | `3f9a2c1e` | `start_run` |
| `level` | `INFO`, `WARNING`, `ERROR` | the call |
| `step` | `convert`, `merge`, `validate`, `run` | the logger's name (`crittervid.convert`, …) |
| `message` | `SUCCESS: Converted … -> …` | the call; the same text as today |
| `elapsed_s` | `12.84` | seconds since the run started |
| `file` | the event folder, segment or merged file | `extra=` on per-file records |
| `duration_s` | `3.07` | time taken for that file, on its result record |
| `ffmpeg_stderr` | the last 20 lines | on records of a failed FFmpeg call |
| `traceback` | the formatted exception | on records of unexpected errors |

A filter on the handlers adds `run`, `step` and `elapsed_s`, so a call site only passes what it knows. The converter's worker threads log through the same loggers, and `run` is the same for the whole process.

### The console stays as it is

- **Same text:** the console handler prints `message` alone, exactly as today's `print` does. The `SUCCESS:` / `WARNING:` / `ERROR:` / `SKIPPED:` prefixes and the `=== Step` lines stay, so the GUI, the summary files and every existing test keep working.
- **One deliberate change, FFmpeg failures:** the console and the summary show FFmpeg's last error line, followed by `(full FFmpeg output in the run log)`. The run log keeps the last 20 lines.
- **Tracebacks:** unexpected errors print their message on the console. The traceback goes to the run log and the app log.
- **`--verbose`** on every command also shows `DEBUG` records on the console. These are new, more detailed lines, such as each FFmpeg command line. The run log and the app log always include them.

### What stays the same

- **The summary files** (`conversion_summary.txt`, `merge_summary.txt`) are written as now, from the same messages. They're retired once the run manifest (#37) replaces them, not here.
- **Exit codes and the GUI's way of running the pipeline** don't change.
- **The output folder:** the merge and validation only look at `.mp4` files, so `logs/` doesn't affect them.

### Privacy

The logs contain file paths and nothing else about the user. They stay on the user's computer: nothing is sent anywhere, and no option to send them is added. The diagnostics zip (#45) will blank out paths and locations before anything leaves the machine, and the optional OpenTelemetry add-on (#57) attaches to these same loggers.

## Plan: three PRs

1. **Foundation.**
   - `log.py`: run ID, the three outputs, `start_run`, `--verbose`.
   - Each command calls `start_run`.
   - Every `print` and `report()` in the steps becomes a logger call with the same text. The console output doesn't change, which the existing tests check.
   - New tests: the run log exists and has one valid JSON object per line, all with the same run ID for a whole `crittervid run`. The app log gets the records too, and a dry run writes no run log.
2. **Detail.**
   - `file` and `duration_s` on per-file results.
   - The FFmpeg stderr tail, with the shorter console line.
   - Tracebacks for unexpected errors, and `DEBUG` records for the FFmpeg command lines.
   - Tests: a failed conversion's record holds FFmpeg's last lines, and a crash's traceback reaches the run log.
3. **Showing it.**
   - The GUI shows the run ID and where the run log is when a run ends.
   - README and DEPLOYMENT get a short "Logs" section: where the logs are, what's in them, and that nothing is sent.

## Risks

- **Output order:** the converter's worker threads log at the same time, as they print today. Log records are written whole, so lines won't interleave mid-line any more than they do now.
- **Log files growing:** a run log is one line per file and step, a few hundred KB for a whole card. The app log rotates.
- **Unwritable log folders:** if the app log's folder can't be created, a warning appears on the console and the run continues without it. If the run log can't be written, the same happens; the run doesn't fail.

## Questions for you

1. **Run logs in `<output>/logs/`?** Recommended. They stay next to the footage they describe, so a run's outputs and its log travel together. The alternative is the app log folder only.
2. **Keep the summary files until the manifest (#37)?** Recommended. They're what users look at today, and `process_all` reads the merge's.
3. **Shorten FFmpeg errors on the console** to the last line, with the full output in the run log? Recommended. Today a single failure can push everything else off the screen.
