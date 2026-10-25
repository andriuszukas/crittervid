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
  record format, which FFmpeg has to re-encode: NVIDIA (CUDA), Intel (QSV), AMD (AMF), or CPU.
  Auto-detection can't detect NVIDIA on Python 3.12+ ([#2](https://github.com/andriuszukas/crittervid/issues/2)), so choose your GPU explicitly.
- **Merge** converted clips into one or more files per day. `--splits-per-day N` divides each day
  into N equal time windows (to the minute; 2 gives 00:00–12:00 and 12:00–24:00), and each window
  with events becomes one file named after its window. An event belongs to the window it starts in.
  A part whose events haven't changed since it was merged is skipped on later runs. To merge
  everything again, e.g. after upgrading to a version that merges differently, use `--force`
  (`--force-merge` in `process_all.py`, "Merge everything again" in the app); a merged file
  replaced or edited by hand isn't noticed otherwise. Merging a day again with a
  different number replaces that day's earlier merged files, unless they hold
  footage the new ones don't (e.g. after deleting converted files to save space). Each day's
  `merged_contents.json` records which events each merged file holds; merged files made by
  earlier versions have no record, so they're kept and you're told about them.
- **Validate** the output in seconds: every event file has video and audio of matching length,
  each merged day is as long as its events, and every file has an expected name. `--deep-validate`
  also decodes everything in full (about as long as the footage).
- **Robust** — skips already-converted files, retries failures, handles Ctrl+C gracefully, and writes summary reports.
- **GUI or CLI** — a Streamlit web UI, or run each stage directly from the terminal.

## Requirements

- Python 3.10 to 3.13 (3.14 isn't supported yet: the pinned PyInstaller doesn't install on it)
- [FFmpeg](https://ffmpeg.org/download.html) — either on your `PATH`, or place `ffmpeg.exe` (Windows) / `ffmpeg` (macOS, Linux) in the project folder.
  > **Note:** FFmpeg is intentionally **not** committed to this repo (it exceeds GitHub's file-size limit). Download it separately.
- Python dependencies, on any system:
  ```bash
  pip install -r requirements.txt
  ```
  This installs the GUI and the optional GPU detection. The command-line scripts need no packages. To build the app or run the tests, install `requirements-dev.txt` instead.

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

On Windows, double-click `run_app.bat` and open the address it prints (usually http://localhost:8501).
On any platform, run this from the project folder; it opens your browser:

```bash
streamlit run app.py
```

The first time, Streamlit asks for an email address in the terminal; press Enter to skip.

Select the input and output folders, choose hardware acceleration, and click **Run**.

### Command line

```bash
# Full pipeline: convert -> merge -> validate
python process_all.py --input-dir input/DCIM --output-dir output --accel cuda

# Individual stages
python convert_media.py --input-dir input/DCIM --output-dir output --accel cuda
python merge_videos.py --output-dir output --splits-per-day 2
python validate_output.py --output-dir output            # add --deep to decode every file

# Retry only files that failed a previous run
python convert_media.py --retry-failed --max-retries 5

# Preview without running FFmpeg
python process_all.py --dry-run
```

Key options: `--accel {cpu,cuda,qsv,vaapi,amf,auto}`, `--workers N`, `--copy`
(stream-copy without re-encoding), `--splits-per-day N`, `--no-validate`, `--deep-validate`.

## Output

```
output/
  YYYY/MM/DD/<timestamp>_<duration>.mp4    # one file per recorded event
  merged/YYYY/MM/DD/merged_*.mp4           # merged daily clips
  merged/YYYY/MM/DD/merged_contents.json    # which events each merged clip holds
  conversion_summary.txt                   # per-run reports
  merged/merge_summary.txt
```

Every event file has an audio track; it's silent if the camera recorded no sound. Event
folders in which no file is in the cameras' record format are converted one segment at a time
instead, into `YYYY/MM/DD/<timestamp>_<duration>/` folders. An event file isn't redone if more
segments are added to its folder later; delete it to convert the event again.

Events recorded before the camera's clock was set show up on the card in `1970/` or `2106/`
folders. They're converted into the same folders, but their real time is unknown, so they
aren't merged into days; the reports warn about them.

> **Upgrading from an earlier version?** Earlier versions converted `.media` files at the wrong
> speed (about 25% too slow) and without sound
> ([details](docs/decisions/0001-media-format.md)). Convert your cards again, ideally into a new
> output folder. If you reuse an old one, the old per-segment files are left in place but are no
> longer included in merges.

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
