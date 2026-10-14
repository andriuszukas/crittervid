# Copilot Instructions for CritterVid

CritterVid converts segmented `.media` files from wildlife trail cameras into `.mp4`, merges them into longer clips per day, and validates that the results play. It runs as a command-line pipeline or a Streamlit GUI, and can be packaged as a Windows executable with PyInstaller.

## Input layout
- Camera files live under `input/DCIM/YYYY/MM/DD/<unix_timestamp>_<NNNN>/*.media`, with a `.info` file alongside (currently unused).
- The layout matches Tuya's documented IPC storage format: the folder timestamp is the event start (UTC) and `NNNN` is the event length in seconds; each `.media` file name is its offset in seconds into the event. The code currently calls `NNNN` a "group ID" (see issue #30).

## Pipeline
- `media_format.py` — the cameras' `.media` record format (24-byte headers with microsecond timestamps; H.264 video, 8 kHz PCM audio): reading records, aligning audio, and writing the video as MPEG-TS so every frame keeps its timestamp. See `docs/decisions/0001-media-format.md`.
- `convert_media.py` — converts each event folder into one `<timestamp>_<duration>.mp4`: video copied with camera timestamps, audio as AAC, length checked against the folder name. Files not in the record format fall back to per-segment FFmpeg conversion (`--accel`, `--copy`).
- `merge_videos.py` — groups converted files by day and joins them with FFmpeg's concat demuxer (`inpoint 0` per file, audio re-encoded once) into `--splits-per-day` files.
- `validate_output.py` — decodes every output to check it plays.
- `process_all.py` — runs the three stages in order; `app.py` is the GUI that runs `process_all.py`.
- `ffmpeg_helper.py` — finds FFmpeg (project folder, then `PATH`).

## Conventions
- Write FFmpeg output to `<name>.mp4.part` and rename on success, so a failed run never leaves a partial `.mp4`.
- Every script exits non-zero when anything fails; `process_all.py` exits non-zero if any stage did.
- Write all text (stdout, summaries, concat lists) as UTF-8; escape `'` as `'\''` in concat lists.
- Keep code cross-platform (Windows, macOS, Linux): `pathlib`, no Windows-only APIs.

## Tests
- `python -m pytest`. Most tests use a stand-in FFmpeg (`tests/fake_ffmpeg.py`). `tests/test_e2e.py` (a generated SD card through the whole pipeline) and `tests/test_real_ffmpeg.py` need FFmpeg and ffprobe on `PATH`; `tests/test_gui.py` (Streamlit `AppTest`) needs Streamlit. They're skipped when those are missing, unless `CRITTERVID_REQUIRE_DEPS` is set, as on CI.
- CI runs the suite on Windows, macOS and Ubuntu (`.github/workflows/tests.yml`).

## Workflow
- Work is tracked as GitHub issues grouped into roadmap milestones (Phase 0–10). One branch per issue; PRs reference it with `Closes #N`.
