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
   - copy `ffmpeg.exe` into the `CritterVid\_internal\` folder.

### Starting and stopping

1. Unzip `CritterVid.zip` and double-click `CritterVid.exe` in the `CritterVid` folder.
2. A console window opens and prints an address, usually http://localhost:8501. Open it in your browser.
3. Keep the console window open while you work. Closing it stops CritterVid.

The app only accepts connections from your own computer, and needs no internet connection.

## Running from source

### Prerequisites

1. **Python 3.10 to 3.13.** Python 3.14 isn't supported yet, because the pinned PyInstaller in `requirements.txt` doesn't install on it.
   ```bash
   python --version
   ```
2. **Python packages:**
   - On **Windows**:
     ```bash
     pip install -r requirements.txt
     ```
     This installs Streamlit (the GUI), GPUtil and WMI (used by Auto GPU detection), PyInstaller (for building) and pytest (for tests).
   - On **macOS and Linux**, `requirements.txt` doesn't install yet because WMI is Windows-only ([#6](https://github.com/andriuszukas/crittervid/issues/6)). Install Streamlit for the GUI instead:
     ```bash
     pip install streamlit==1.50.0
     ```
     The command-line scripts need no packages at all.
3. **FFmpeg**, on `PATH` or as `ffmpeg.exe` (Windows) / `ffmpeg` (macOS, Linux) in the project folder. Download from https://ffmpeg.org/download.html.

### Starting the GUI

- **Windows:** double-click `run_app.bat`, then open the address it prints, usually http://localhost:8501.
- **Any platform:** from the project folder, run
  ```bash
  streamlit run app.py
  ```
  This opens your browser automatically. The first time, Streamlit asks for an email address in the terminal; press Enter to skip.

To stop the GUI, press Ctrl+C in its terminal, or run `stop_app.bat` on Windows.

### Command line

```bash
# Full pipeline: convert -> merge -> validate
python process_all.py --input-dir input/DCIM --output-dir output --accel cuda

# Individual steps
python convert_media.py --input-dir input/DCIM --output-dir output --accel cuda
python merge_videos.py --output-dir output --splits-per-day 2
python validate_output.py --output-dir output --accel cuda

# Retry only the files that failed in the previous run
python convert_media.py --retry-failed --max-retries 5
```

## Building the packaged app

Builds are Windows-only for now; builds for macOS and Linux are planned ([#46](https://github.com/andriuszukas/crittervid/issues/46)).

1. Set up Python 3.10 to 3.13 and install `requirements.txt`, as in [Running from source](#running-from-source).
2. **Optional:** put `ffmpeg.exe` in the project folder to bundle it, so users don't have to install FFmpeg. Without it, the build still works and prints a note that FFmpeg wasn't bundled.
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
- for the packaged app, copy `ffmpeg.exe` into `CritterVid\_internal\`, or
- when running from source, put it in the project folder.

### The packaged app reports "Pipeline failed with exit code 9009", or says Python can't be found
Python isn't on `PATH`. Reinstall Python with **Add python.exe to PATH** ticked, then open a new Command Prompt and check `python --version`.

### "Module not found" errors
- When running from source, install the packages as described in [Running from source](#running-from-source).
- For a build, rebuild with `build.bat`.

### Slow processing
- Choose your GPU under Hardware Acceleration instead of CPU or Auto.
- Reduce the number of videos processed in parallel.
- On the command line, `--copy` copies the video stream without re-encoding, which is much faster when the input is already H.264. It isn't available in the GUI.

## Notes

- The packaged app is Windows-only. On macOS and Linux, run from source.
- The first start can take a few seconds while Streamlit loads.
- No internet connection is needed, and the GUI only accepts connections from your own computer.
