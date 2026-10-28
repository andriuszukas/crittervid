# 0003: Logging with run IDs, a log per run and an app log

- **Status:** proposed
- **Issues:** #26 (structured logging). It's the base for #45 (diagnostics zip), #29 (`crittervid doctor`) and #57 (optional OpenTelemetry), and it helps #25 (step interface).

## Context

All output today is `print`: about 60 calls across `convert`, `merge`, `validate` and `pipeline`. A few other paths write output too:
- `print(..., file=sys.stderr)` for option errors;
- `traceback.print_exc()` when a step of `crittervid run` fails unexpectedly;
- the CLI's "Stopped by user" line;
- validation's loop that prints the problems it found.

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

A new module, `crittervid/log.py`.

**Where it's set up:** `log.start_run(config, command)` is called in exactly one place, the command entry point (`cli.main` and each step's `main`). It's never called in a step's `run()`. So a whole `crittervid run` is one run, even though it calls `convert.run`, `merge.run` and `validate.run` directly.

**Guarantees:**
- **Calling it again** in the same process, as tests and later the GUI may do, replaces the earlier setup instead of adding a second set of outputs.
- **Without it,** the `crittervid` logger has a `NullHandler` and doesn't propagate, so a step used as a library works and prints nothing it wasn't asked to.

**It does three things:**

1. **Creates a run ID:** 8 random hex characters, such as `3f9a2c1e`, plus the start time in UTC.
2. **Attaches three outputs** to the `crittervid` logger:
   - **Console:** standard output, readable text, described below.
   - **Run log:** `<output folder>/logs/<start time>_<run ID>.jsonl`, one JSON object per line, written as UTF-8 and flushed after each record.
     - **Which commands:** only the ones that write output (`run`, `convert`, `merge`), and not with `--dry-run`. `validate` on its own reads the output folder and doesn't create anything in it.
     - **When it's created:** only when the first record is written (`delay=True`), so a run that fails at once doesn't leave an empty `logs/` folder, or an output folder, behind.
   - **App log:** `crittervid.log` in the OS's usual folder for app logs:
     - Windows: `%LOCALAPPDATA%\CritterVid\Logs`
     - macOS: `~/Library/Logs/CritterVid`
     - Linux: `$XDG_STATE_HOME/crittervid`, by default `~/.local/state/crittervid`

     This is where a failure before the output folder exists ends up, such as a bad option or a missing input folder.

     Two processes can write it at once, such as a run started from the GUI and one in a terminal. Rotating while the other has it open fails on Windows, so it never rotates mid-run. Instead, `start_run` moves it aside if it's over 1 MB, keeping five old files, and ignores a failure to do so; the next run tries again.
3. **Prints `Run 3f9a2c1e started; log: <run log path>`** as the first line. The GUI only learns the output folder through the child process it starts, so it reads the ID and the path from this line, which makes the line's format part of the contract. A bug report can quote the ID.

**Standard output and error:** setting up the streams moves into `log.py`: UTF-8, line-buffered, and `errors='replace'` for characters the console can't show. Today `pipeline` and each step do this themselves. When there's no console at all (`sys.stdout` is `None`, for example under `pythonw`), the console output is left out and the logs still work.

**Ctrl+C and shutdown:** `cli.main` calls `logging.shutdown()` in a `finally`, so records written while a stopped conversion cleans up still reach the files. The JSON formatter never raises: a value it can't encode becomes a string (`default=str`).

The run's settings are a `PipelineConfig` (decision record 0002). Logging uses the standard library only, so the pipeline still needs no packages.

### What each record carries

| Field | Example | Where it comes from |
|---|---|---|
| `schema` | `1` | the record format's version, raised when fields change meaning |
| `time` | `2026-10-28T09:14:03.211Z` | UTC, from the record's creation time |
| `run` | `3f9a2c1e` | `start_run` |
| `level` | `INFO`, `WARNING`, `ERROR` | the call |
| `step` | `convert`, `merge`, `validate`, `run` | the logger's name (`crittervid.convert`, …) |
| `message` | `SUCCESS: Converted … -> …` | the call; the same text as today |
| `elapsed_s` | `12.84` | seconds since the run started (`time.monotonic`) |
| `file` | the event folder, segment or merged file | `extra=` on per-file records |
| `duration_s` | `3.07` | time taken for that file, on its result record |
| `ffmpeg_stderr` | the last 20 lines | on records of a failed FFmpeg call |
| `traceback` | the formatted exception | on records of unexpected errors |

A filter on the handlers adds `run`, `step` and `elapsed_s`, so a call site only passes what it knows. The converter's worker threads log through the same loggers, and `run` is the same for the whole process.

The record format is documented with the README's "Logs" section, and a test fixes its field names and types.

### The console stays as it is

- **Same text:** the console handler prints `message` alone, exactly as today's `print` does. The `SUCCESS:` / `WARNING:` / `ERROR:` / `SKIPPED:` prefixes and the `=== Step` lines stay, so the GUI, the summary files and every existing test keep working.
- **One deliberate change, FFmpeg failures:** the console and the summary show FFmpeg's last error line, followed by `(full FFmpeg output in the run log)`. The run log keeps the last 20 lines.
- **Tracebacks:** unexpected errors print their message on the console. The traceback goes to the run log and the app log. This replaces `traceback.print_exc()` in `crittervid run`.
- **Option errors** stay as they are: argparse's usage and errors go to stderr before a run starts. They also reach the app log only when they come from a step's own checks, such as `--splits-per-day 0` inside a run.
- **`--verbose`** on every command also shows `DEBUG` records on the console. These are new, more detailed lines, such as each FFmpeg command line. The run log and the app log always include them.

### What stays the same

- **The summary files** (`conversion_summary.txt`, `merge_summary.txt`) are written as now, from the same messages. They're retired once the run manifest (#37) replaces them, not here.
- **Exit codes and the GUI's way of running the pipeline** don't change.
- **The output folder:** the merge and validation only look at `.mp4` files, so `logs/` doesn't affect them.

### Privacy

The logs contain file paths and nothing else about the user. They stay on the user's computer: nothing is sent anywhere, and no option to send them is added. The diagnostics zip (#45) will blank out paths and locations before anything leaves the machine, and the optional OpenTelemetry add-on (#57) attaches to these same loggers.

## Plan: four PRs

1. **`log.py` on its own.**
   - Run ID, the three outputs, setting up the streams, `start_run` (including calling it twice), the app log's move-aside, the JSON formatter and `--verbose`.
   - Unit tests: record fields and types, UTF-8 and paths that aren't UTF-8, no console, an unwritable log folder, and no handlers before `start_run`.
   - Nothing calls it yet.
2. **Use it everywhere.**
   - Each command's entry point calls `start_run`.
   - Every `print`, `report()` and the other output paths listed above become logger calls with the same text. The console output doesn't change, which the existing tests check.
   - New tests: the run log exists and has one valid JSON object per line, all with the same run ID for a whole `crittervid run`. The app log gets the records, a dry run or `validate` alone writes no run log, and the first line names the run log.
3. **Detail.**
   - `file` and `duration_s` on per-file results.
   - The FFmpeg stderr tail, with the shorter console line.
   - Tracebacks for unexpected errors, and `DEBUG` records for the FFmpeg command lines.
   - Tests: a failed conversion's record holds FFmpeg's last lines, and a crash's traceback reaches the run log. The summary file's `ERROR:` lines get the shorter text too, so tests that read it are checked.
4. **Showing it.**
   - The GUI shows the run ID and where the run log is when a run ends.
   - README and DEPLOYMENT get a short "Logs" section: where the logs are, what's in them, and that nothing is sent.

## Risks

- **Output order:** the converter's worker threads log at the same time, as they print today. Log records are written whole, so lines won't interleave mid-line any more than they do now.
- **Log files growing:** a run log is one line per file and step, a few hundred KB for a whole card. Run logs aren't deleted automatically; they're small, and they belong with the output. The app log keeps five old files.
- **Unwritable log folders:** if the app log's folder can't be created, a warning appears on the console and the run continues without it. If the run log can't be written, the same happens; the run doesn't fail.
- **Paths in messages:** messages contain file paths, as the console does today. Paths that aren't UTF-8 (Linux) are written as escaped surrogates in the JSON, so they can be read back exactly. The diagnostics zip (#45) is where paths get blanked out.

Not planned for now: a `--log-file` override or `--log-level` (`--verbose` covers the need), and a log level choice in the GUI.

## Questions for you

1. **Run logs in `<output>/logs/`?** Recommended. They stay next to the footage they describe, so a run's outputs and its log travel together. The alternative is the app log folder only.
2. **Keep the summary files until the manifest (#37)?** Recommended. They're what users look at today, and `process_all` reads the merge's.
3. **Shorten FFmpeg errors on the console** to the last line, with the full output in the run log? Recommended. Today a single failure can push everything else off the screen.
