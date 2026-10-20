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
- **Merge** converted clips into one or more files per day (`--splits-per-day`).
- **Validate** every output by re-decoding it to confirm it plays.
- **Robust** — skips already-converted files, retries failures, handles Ctrl+C gracefully, and writes summary reports.
- **GUI or CLI** — a Streamlit web UI, or run each stage directly from the terminal.

## Requirements

- Python 3.10 to 3.13 (3.14 isn't supported yet: the pinned PyInstaller doesn't install on it)
- [FFmpeg](https://ffmpeg.org/download.html) — either on your `PATH`, or place `ffmpeg.exe` (Windows) / `ffmpeg` (macOS, Linux) in the project folder.
  > **Note:** FFmpeg is intentionally **not** committed to this repo (it exceeds GitHub's file-size limit). Download it separately.
- Python dependencies, on Windows:
  ```bash
  pip install -r requirements.txt
  ```
  On macOS and Linux, `requirements.txt` doesn't install yet ([#6](https://github.com/andriuszukas/crittervid/issues/6)); run `pip install streamlit==1.50.0` for the GUI. The command-line scripts need no packages.

## Input layout

The pipeline expects the camera's folder structure under the input directory:

```
input/DCIM/YYYY/MM/DD/<unixtimestamp>_<groupid>/*.media
```

Timestamps and group IDs are parsed from this layout and preserved in the output filenames.

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
python validate_output.py --output-dir output --accel cuda

# Retry only files that failed a previous run
python convert_media.py --retry-failed --max-retries 5

# Preview without running FFmpeg
python process_all.py --dry-run
```

Key options: `--accel {cpu,cuda,qsv,vaapi,amf,auto}`, `--workers N`, `--copy`
(stream-copy without re-encoding), `--splits-per-day N`, `--no-validate`.

## Output

```
output/
  YYYY/MM/DD/<timestamp>_<duration>.mp4    # one file per recorded event
  merged/YYYY/MM/DD/merged_*.mp4           # merged daily clips
  conversion_summary.txt                   # per-run reports
  merged/merge_summary.txt
```

Every event file has an audio track; it's silent if the camera recorded no sound. Event
folders in which no file is in the cameras' record format are converted one segment at a time
instead, into `YYYY/MM/DD/<timestamp>_<duration>/` folders. An event file isn't redone if more
segments are added to its folder later; delete it to convert the event again.

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
