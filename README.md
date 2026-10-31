# crittervid

A pipeline for processing wildlife / trail-camera footage. It converts proprietary
`.media` segments to `.mp4`, merges them into longer per-day clips, and validates that
the results are playable. Runs as a command-line tool or a simple browser-based GUI,
and can be bundled into a Windows executable (which currently still needs Python
installed, see [#4](https://github.com/andriuszukas/crittervid/issues/4)).

## Features

- **Convert** each recorded event's `.media` segments into one `.mp4`, in parallel. The camera's
  video is copied without re-encoding, every frame keeps the time the camera recorded (including
  pauses in the recording), and the audio is placed by its own timestamps so it stays in sync.
  Damaged segments (empty files, a cut-off end after power loss) are skipped or trimmed with a
  warning instead of spoiling the event. Each event's length is checked against its folder name.
- **Hardware acceleration** — only used for `.media` files that aren't in the cameras' usual
  record format, which FFmpeg has to re-encode: NVIDIA (CUDA), AMD (AMF), Intel (QSV), VAAPI
  (Linux), or CPU. Auto, the default, tries each GPU encoder on a tiny test clip the first time a
  file needs encoding and uses the first that works, otherwise the CPU. A GPU encode that fails
  is done again on the CPU. `crittervid doctor` shows which encoders work on your computer.
- **Merge** converted clips into one or more files per day. `--splits-per-day N` divides each day
  into N equal time windows (to the minute; 2 gives 00:00–12:00 and 12:00–24:00), and each window
  with events becomes one file named after its window. An event belongs to the window it starts in.
  A part whose events haven't changed since it was merged is skipped on later runs. To merge
  everything again, e.g. after upgrading to a version that merges differently, use `--force`
  (`--force-merge` with `crittervid run`, "Merge everything again" in the app); a merged file
  replaced or edited by hand isn't noticed otherwise. Merging a day again with a
  different number replaces that day's earlier merged files, unless they hold
  footage the new ones don't (e.g. after deleting converted files to save space). Each day's
  `merged_contents.json` records which events each merged file holds; merged files made by
  earlier versions have no record, so they're kept and you're told about them.
- **Validate** the output in seconds: every event file has video and audio of matching length,
  each merged day is as long as its events, and every file has an expected name. `--deep-validate`
  also decodes everything in full (about as long as the footage).
- **Robust** — skips already-converted files, retries failures, handles Ctrl+C gracefully, and writes summary reports.
- **GUI or CLI** — a Streamlit web UI, or the `crittervid` command in a terminal.

## Requirements

- Python 3.10 to 3.13 (3.14 isn't supported yet: the pinned PyInstaller doesn't install on it)
- [FFmpeg](https://ffmpeg.org/download.html) — either on your `PATH`, or place `ffmpeg.exe` (Windows) / `ffmpeg` (macOS, Linux) in the project folder.
  > **Note:** FFmpeg is intentionally **not** committed to this repo (it exceeds GitHub's file-size limit). Download it separately.
- Python dependencies, on any system, run from the project folder (it installs CritterVid itself
  too, as an editable package):
  ```bash
  pip install -r requirements.txt
  ```
  This installs the `crittervid` command and the GUI. The pipeline itself
  needs only `tzdata`, the time zone data (Windows has none of its own). To build the app or run the tests, install `requirements-dev.txt` instead.

## Input layout

The pipeline expects the camera's folder structure under the input directory:

```
input/DCIM/YYYY/MM/DD/<unixtimestamp>_<duration>/*.media
```

Each event folder is named after the event's start (Unix time, UTC) and its length in seconds
(`0000` if the camera didn't record one). Output files keep these names, and the converter warns
when an event's footage doesn't match its length.

## Usage

### GUI (recommended)

Run `crittervid gui`, or just `crittervid`, and open the address it prints (usually
http://localhost:8501). On Windows you can also double-click `run_app.bat`. In the packaged app,
double-click `CritterVid.exe`.

Select the input and output folders, choose hardware acceleration, and click **Run**.

### Command line

```bash
# Full pipeline: convert -> merge -> validate
crittervid run --input-dir input/DCIM --output-dir output --accel cuda

# The same, giving the camera's time zone (saved in the output folder for later runs)
crittervid run --input-dir input/DCIM --output-dir output --timezone Europe/Vilnius

# Individual steps
crittervid convert --input-dir input/DCIM --output-dir output --accel cuda
crittervid merge --output-dir output --splits-per-day 2
crittervid validate --output-dir output            # add --deep to decode every file

# Retry only the files that failed in the previous run
crittervid convert --retry-failed --max-retries 5

# Preview without running FFmpeg
crittervid run --dry-run

# Check FFmpeg, which hardware encoders work, the output folder, disk space and the time zone
crittervid doctor --input-dir input/DCIM --output-dir output
```

`crittervid` is installed by `pip install -r requirements.txt`; `python -m crittervid` does the
same. `crittervid --help` lists the commands, and `crittervid <command> --help` their options. The
older script names (`python process_all.py`, `python convert_media.py`, …) still work for now,
and run from a checkout even without installing.

Key options: `--accel {cpu,cuda,qsv,vaapi,amf,auto}`, `--workers N`, `--copy`
(stream-copy without re-encoding), `--splits-per-day N`, `--no-validate`, `--deep-validate`,
and the camera details below.

## Camera time zone and other details

Footage is grouped into the camera's days, so CritterVid needs to know the time zone the camera's
clock was set to. The card doesn't record it.

| Option | GUI field | What it's for |
|---|---|---|
| `--timezone NAME` | Camera's time zone | An IANA name such as `Europe/Vilnius` or `America/New_York`. Days, parts of days and merged file names follow it, with daylight saving: a day is 23 or 25 hours when the clocks change. |
| `--clock-offset SECONDS` | Clock offset | Added to the camera's times if its clock was wrong: `75` if it was 75 s slow, `-3600` if it was an hour fast. Merged files get the corrected times; converted files keep the camera's own, which their names show. |
| `--site TEXT` | Site | Where the camera was. |
| `--camera TEXT` | Camera | Which camera it was. |

- **Saved for later runs:** the first run given them saves them in `<output>/deployment.json`.
  Later runs on that output folder use them without retyping, and the GUI fills them in. An
  option given again replaces the saved value, with a warning; days are then regrouped and merged
  again. A merged day left without events is reported, not deleted.
- **Without a time zone,** days are grouped in the computer's time zone, with a warning. That's
  right only if the camera's clock was set to the same one.
- **A check against the card:** the camera files each event under its own date, so the conversion
  warns when the time zone puts events on other days than the card does. `crittervid doctor` shows
  the time zone and clock offset saved for the output folder.
- **A broken `deployment.json`** (or an unknown time zone) stops the run with an error, rather
  than grouping days some other way. To unset a saved value, edit or delete that file.
- **Privacy:** the site and camera aren't written into the videos. They are in
  `deployment.json` and the run logs, so leave those out of copies you share if the site is
  sensitive.
- Events recorded before the camera's clock was set aren't affected by the offset; see below.

## Output

```
output/
  YYYY/MM/DD/<timestamp>_<duration>.mp4    # one file per recorded event
  merged/YYYY/MM/DD/merged_*.mp4           # merged daily clips, in the camera's days
  merged/YYYY/MM/DD/merged_contents.json    # which events each merged clip holds
  deployment.json                          # the camera's time zone and other details
  conversion_summary.txt                   # per-run reports
  merged/merge_summary.txt
```

Every event file has an audio track; it's silent if the camera recorded no sound. Every output
file records when its footage starts as its `creation_time` (UTC), which players and tools such as
Timelapse show: by the camera's clock for event files, and with the clock offset for merged files.
Events recorded before the camera's clock was set get none. Files made by earlier
versions get it once they're made again (convert after deleting them; merge with `--force`). Event
folders in which no file is in the cameras' record format are converted one segment at a time
instead, into `YYYY/MM/DD/<timestamp>_<duration>/` folders. An event file isn't redone if more
segments are added to its folder later; delete it to convert the event again.

### Events recorded before the camera's clock was set

A camera whose clock wasn't set counts from 0 when it's switched on, so its events show up on the
card in `1970/` or `2106/` folders. They're converted into the same folders, but their real time
is unknown, so they aren't merged into days until you say when they happened.

- **Series:** the events from one switch-on are one series, named after its first event, such as
  `0000000001_0602`. One series can span a `2106/` and a `1970/` folder. The merge lists each
  series with no start, with its number of events and length, and the option to give it one.
  The GUI lists them too, under "Camera and Site".
- **Giving a start:** `--series-start 0000000001_0602=2025-06-01T14:30` (or `2025-06-01 14:30`)
  is the real start of the series' first event, in the camera's local time, which needs the time
  zone set. Add `Z` or a UTC offset such as `+02:00` to give it another way. Every event of the
  series moves by the same amount. The events are then merged into their days, and merged files
  get the corrected `creation_time`. Converted files keep the camera's own time.
- **Saved** in `deployment.json`, like the other details. Giving a series another start later
  merges it again. A start that's in the future or before 2000 is refused, as is a local time
  skipped when the clocks go forward. A time that happens twice when they go back is the first
  of the two.
- **Checks:** the merge warns when a dated series overlaps other recordings, because the camera
  records one thing at a time, and when the camera was switched on more than once before its clock
  was set, because their events can then be mixed up.

> **Upgrading from an earlier version?** Earlier versions converted `.media` files at the wrong
> speed (about 25% too slow) and without sound
> ([details](docs/decisions/0001-media-format.md)). Convert your cards again, ideally into a new
> output folder. If you reuse an old one, the old per-segment files are left in place but are no
> longer included in merges.

## Logs

Every run gets an ID, shown in its first line of output: `Run 3f9a2c1e started; log: <path>`.
The GUI shows it again when the run ends. Quote it when reporting a problem.

- **Run log:** `<output>/logs/<start time>_<run ID>.jsonl`, written by `run`, `convert` and
  `merge`, but not by a dry run or by `validate` on its own. It has one JSON object per line, with
  these fields:
  - always: `schema`, `time` (UTC), `run`, `level`, `step`, `message` and `elapsed_s`;
  - for each file: `file`, plus `duration_s` once the file is done;
  - when something fails: `ffmpeg_stderr` (FFmpeg's last 20 lines of error output) or `traceback`;
  - on the first line: `command` and `settings`.
- **App log:** `crittervid.log` in `%LOCALAPPDATA%\CritterVid\Logs` (Windows),
  `~/Library/Logs/CritterVid` (macOS) or `~/.local/state/crittervid` (Linux). It covers every run,
  including problems before an output folder exists. It's moved aside when it passes 1 MB, and the
  last five old ones are kept.
- **Console:** the same messages as the run log. FFmpeg failures show only the line that says why,
  and errors that come with a traceback show one line; the full details are in the run log.
  `--verbose` also shows details such as each FFmpeg command line.

The logs contain file paths and stay on your computer: CritterVid never sends them anywhere.
See [decision record 0003](docs/decisions/0003-logging.md) for the details.

## Running tests

```bash
python -m pytest
```

Most tests use a stand-in for FFmpeg (`tests/fake_ffmpeg.py`) and run anywhere.
Some need more, and are skipped when it's missing:

- `tests/test_e2e.py` and `tests/test_real_ffmpeg.py` run the real FFmpeg (and
  ffprobe) on generated footage, including a simulated SD card, and check the
  outputs' durations. They need FFmpeg on your `PATH`.
- `tests/test_gui.py` drives the GUI with Streamlit's test harness. It needs
  Streamlit installed.

CI sets `CRITTERVID_REQUIRE_DEPS=1`, which makes those tests fail instead of skip
when a dependency is missing.

## Building a standalone executable

See [DEPLOYMENT.md](DEPLOYMENT.md) for full build and distribution instructions.

```bash
build.bat
```

## Acknowledgments

This tool exists thanks to the research of
[**Dr. Filipe Ribeiro da Cunha**](https://www.wur.nl/en/persons/fc-filipe-ribeiro-da-cunha-phd)
and his team at **Wageningen University**, whose work motivated and made possible
the wildlife-footage processing this pipeline automates. With gratitude for their
contribution to the field.

## License

[MIT](LICENSE) © 2026 Andrius Zukas
