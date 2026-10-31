# CritterVid - Deployment Guide

This guide has three parts:

- [Using the packaged app](#using-the-packaged-app) — for researchers who were given `CritterVid.zip`
- [Running from source](#running-from-source) — for anyone with a copy of this repository
- [Building the packaged app](#building-the-packaged-app) — for whoever prepares `CritterVid.zip`

## Using the packaged app

The packaged app runs on **Windows 10 or newer**.

### What you need

1. **Python 3.10 or newer**, added to `PATH`. The app still uses your own Python to run the processing steps; removing this requirement is tracked in [#4](https://github.com/andriuszukas/crittervid/issues/4).
   - Download it from https://www.python.org/downloads/.
   - In the installer, tick **Add python.exe to PATH**. It is not ticked by default.
   - To check, open a new Command Prompt and run `python --version`.
   - No extra Python packages are needed.
2. **FFmpeg**, unless the person who built the app bundled it (ask them). If it isn't bundled, either:
   - install FFmpeg and add it to `PATH` (download from https://ffmpeg.org/download.html), or
   - copy `ffmpeg.exe` and `ffprobe.exe` (both come with FFmpeg) into the `CritterVid\_internal\` folder.

### Starting and stopping

1. Unzip `CritterVid.zip` and double-click `CritterVid.exe` in the `CritterVid` folder.
2. A console window opens and prints an address, usually http://localhost:8501. Open it in your browser.
3. Keep the console window open while you work. Closing it stops CritterVid.

The app only accepts connections from your own computer, and needs no internet connection.

## Running from source

### Prerequisites

1. **Python 3.10 to 3.13.** Python 3.14 isn't supported yet, because the pinned PyInstaller in `requirements-dev.txt` doesn't install on it.
   ```bash
   python --version
   ```
2. **Python packages**, on any system:
   ```bash
   pip install -r requirements.txt
   ```
   This installs the `crittervid` command (as an editable package, so changes in the folder take effect), and Streamlit (the GUI). The pipeline itself needs only `tzdata`, the time zone data (Windows has none of its own).

   To build the app or run the tests, install `requirements-dev.txt` instead. It adds PyInstaller and pytest:
   ```bash
   pip install -r requirements-dev.txt
   ```
3. **FFmpeg**, on `PATH` or as `ffmpeg.exe` (Windows) / `ffmpeg` (macOS, Linux) in the project folder. Download from https://ffmpeg.org/download.html.

### Starting the GUI

- **Any platform:** run `crittervid gui` (or just `crittervid`), then open the address it prints, usually http://localhost:8501.
- **Windows:** you can also double-click `run_app.bat`.

To stop the GUI, press Ctrl+C in its terminal, or run `stop_app.bat` on Windows.

### Command line

```bash
# Full pipeline: convert -> merge -> validate
crittervid run --input-dir input/DCIM --output-dir output --accel cuda

# Individual steps
crittervid convert --input-dir input/DCIM --output-dir output --accel cuda
crittervid merge --output-dir output --splits-per-day 2
crittervid validate --output-dir output            # add --deep to decode every file

# Retry only the files that failed in the previous run
crittervid convert --retry-failed --max-retries 5

# Preview without running FFmpeg
crittervid run --dry-run
```

`crittervid` is installed by `pip install -r requirements.txt`; `python -m crittervid` does the
same. `crittervid --help` lists the commands, and `crittervid <command> --help` their options. The
older script names (`python process_all.py`, `python convert_media.py`, …) still work for now,
and run from a checkout even without installing.

## Building the packaged app

Builds are Windows-only for now; builds for macOS and Linux are planned ([#46](https://github.com/andriuszukas/crittervid/issues/46)).

1. Set up Python 3.10 to 3.13 and install `requirements-dev.txt`, as in [Running from source](#running-from-source).
2. **Optional:** put `ffmpeg.exe` and `ffprobe.exe` in the project folder to bundle them, so users don't have to install FFmpeg. Without them, the build still works and prints a note that FFmpeg wasn't bundled. ffprobe is used to check outputs and to join files exactly; without it, validation falls back to slow full decoding.
3. Run `build.bat`.
4. The app is in `dist\CritterVid\`, with `CritterVid.exe` as the main program. It's about 190–210 MB, plus the size of `ffmpeg.exe` if bundled.
5. Zip the whole `dist\CritterVid\` folder and share the zip:
   ```bash
   tar -a -c -f CritterVid.zip -C dist CritterVid
   ```

Tell users whether FFmpeg is bundled, and that they need Python on `PATH` (see [What you need](#what-you-need)).

## System requirements

- **Minimum:** 4 GB RAM, and enough free disk space for the converted videos (similar in size to the input).
- **Recommended:** 8 GB+ RAM, an SSD, and an NVIDIA GPU for hardware-accelerated conversion.

## GPU acceleration

`.media` files in the cameras' usual record format are converted **without re-encoding the video**, so the GPU setting makes no difference for them. It applies to other `.media` files, which FFmpeg has to re-encode.

In the GUI, choose under **Hardware Acceleration**; on the command line, use `--accel`:

| GUI | Command line | Hardware |
|---|---|---|
| NVIDIA | `--accel cuda` | NVIDIA GPUs (NVENC) |
| Intel | `--accel qsv` | Intel Quick Sync Video |
| AMD | `--accel amf` | AMD GPUs (AMF) |
| CPU | `--accel cpu` | Software encoding; works everywhere, slowest |
| Auto | `--accel auto` | Tries to detect the GPU (see below) |

**Auto is unreliable for now.** On Python 3.12 and newer it never picks NVIDIA ([#2](https://github.com/andriuszukas/crittervid/issues/2)). For the packaged app, that means the Python version you installed. If you have an NVIDIA card, choose **NVIDIA** explicitly.

## Troubleshooting

### "FFmpeg not found", or every conversion fails
- Install FFmpeg and add it to `PATH`, or
- for the packaged app, copy `ffmpeg.exe` and `ffprobe.exe` into `CritterVid\_internal\`, or
- when running from source, put it in the project folder.

### Checking the setup
`crittervid doctor` (with your `--input-dir` and `--output-dir`) checks that FFmpeg and ffprobe are found, tries each hardware encoder on a tiny test clip, shows which one `--accel auto` picks and whether it works, checks the output folder and free disk space, and shows the camera's time zone saved for the output folder. Each line starts with OK, WARNING or ERROR.

### Footage lands on the wrong day, or "events fall on a different day than on the card"
Set the camera's time zone: "Camera's time zone" in the GUI, or `--timezone` (such as `--timezone Europe/Vilnius`). Without it, days follow this computer's time zone. If the camera's clock was wrong, set the clock offset too. Both are saved in `deployment.json` in the output folder; see the README's "Camera time zone and other details".

### "events weren't merged: they were recorded before the camera's clock was set"
The camera's clock wasn't set when it recorded them, so their days are unknown. The merge lists each series of such events; give each one the real start of its first event, in the camera's local time: under "Recorded before the camera's clock was set" in the GUI, or with `--series-start NAME=TIME`. See the README's "Events recorded before the camera's clock was set".

### "deployment.json can't be read", or "unknown time zone"
The output folder's `deployment.json` is broken, or names a time zone that doesn't exist. Fix it (it's a small JSON file) or delete it and give the details again. Time zone names are IANA names such as `Europe/London`, spelt exactly.

### Finding out what went wrong
Each run's first line names its run log (`Run <id> started; log: <path>`); the GUI shows the run ID and the log's path when the run ends. The run log has the details the console leaves out, such as FFmpeg's full error output and tracebacks. The app log, `%LOCALAPPDATA%\CritterVid\Logs\crittervid.log` on Windows, also has runs that stopped before writing any output. See the README's "Logs" section.

### The packaged app reports "Pipeline failed with exit code 9009", or says Python can't be found
Versions before 0.1.0 needed Python on `PATH` to run the pipeline from the packaged app. Use a current build, which runs it itself.

### "Module not found" errors
- When running from source, install the packages as described in [Running from source](#running-from-source).
- For a build, rebuild with `build.bat`.

### Slow processing
- Leave Hardware Acceleration on Auto, or choose your GPU, rather than CPU. `crittervid doctor` shows which encoders work.
- Reduce the number of videos processed in parallel.
- On the command line, `--copy` copies the video stream without re-encoding, which is much faster when the input is already H.264. It isn't available in the GUI.

## Notes

- The packaged app is Windows-only. On macOS and Linux, run from source.
- The first start can take a few seconds while Streamlit loads.
- No internet connection is needed, and the GUI only accepts connections from your own computer.
