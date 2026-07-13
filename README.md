# crittervid

A pipeline for processing wildlife / trail-camera footage. It converts proprietary
`.media` segments to `.mp4`, merges them into longer per-day clips, and validates that
the results are playable. Runs as a command-line tool or a simple browser-based GUI,
and can be bundled into a standalone Windows executable.

## Features

- **Convert** `.media` files to `.mp4` with FFmpeg, in parallel.
- **Hardware acceleration** — NVIDIA (CUDA), Intel (QSV), AMD (AMF), or CPU, with auto-detection.
- **Merge** converted clips into one or more files per day (`--splits-per-day`).
- **Validate** every output by re-decoding it to confirm it plays.
- **Robust** — skips already-converted files, retries failures, handles Ctrl+C gracefully, and writes summary reports.
- **GUI or CLI** — a Streamlit web UI, or run each stage directly from the terminal.

## Requirements

- Python 3.7+
- [FFmpeg](https://ffmpeg.org/download.html) — either on your `PATH`, or place `ffmpeg.exe` in the project folder.
  > **Note:** `ffmpeg.exe` is intentionally **not** committed to this repo (it exceeds GitHub's file-size limit). Download it separately.
- Python dependencies:
  ```bash
  pip install -r requirements.txt
  ```

## Input layout

The pipeline expects the camera's folder structure under the input directory:

```
input/DCIM/YYYY/MM/DD/<unixtimestamp>_<groupid>/*.media
```

Timestamps and group IDs are parsed from this layout and preserved in the output filenames.

## Usage

### GUI (recommended)

```bash
streamlit run app.py
# or, on Windows:
run_app.bat
```

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
  YYYY/MM/DD/<timestamp>_<groupid>/*.mp4   # converted segments
  merged/YYYY/MM/DD/merged_*.mp4           # merged daily clips
  conversion_summary.txt                   # per-run reports
  merged/merge_summary.txt
```

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
