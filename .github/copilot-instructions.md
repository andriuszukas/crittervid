# Copilot Instructions for CritterVid

CritterVid converts segmented `.media` files from wildlife trail cameras into `.mp4`, merges them into longer clips per day, and validates that the results play. It runs as a command-line pipeline or a Streamlit GUI, and can be packaged as a Windows executable with PyInstaller.

## Input layout
- Camera files live under `input/DCIM/YYYY/MM/DD/<unix_timestamp>_<duration>/*.media`, with a `.info` file alongside (currently unused).
- The layout matches Tuya's documented IPC storage format: the folder timestamp is the event start (UTC) and `<duration>` is the event length in seconds (`0000` if unknown); each `.media` file name is its offset in seconds into the event.

## Pipeline
- The code is the `src/crittervid/` package (`docs/decisions/0002-package-and-entry-point.md`); the old root scripts are wrappers kept for one release.
- `src/crittervid/media_format.py` — the cameras' `.media` record format (24-byte headers with microsecond timestamps; H.264 video, 8 kHz PCM audio): reading records, aligning audio, and writing the video as MPEG-TS so every frame keeps its timestamp. See `docs/decisions/0001-media-format.md`.
- `src/crittervid/convert.py` (was `convert_media.py`) — converts each event folder into one `<timestamp>_<duration>.mp4`: video copied with camera timestamps (pauses kept), audio placed by its timestamps and encoded as AAC (silent if none), damaged segments skipped or trimmed with warnings, length checked against the folder name. Event folders with no record-format file fall back to per-segment FFmpeg conversion (`--accel`, `--copy`).
- `src/crittervid/merge.py` (was `merge_videos.py`) — groups converted files by day and joins them with FFmpeg's concat demuxer (`inpoint 0` per file, audio re-encoded once) into one file per `--splits-per-day` time window (an event goes in the window it starts in).
- `src/crittervid/validate.py` (was `validate_output.py`) — fast checks with ffprobe: event files have video and audio of matching length, merged days match the sum of their files (grouped like the merge), names are as expected. `--deep` decodes every file once.
- `src/crittervid/config.py` — `PipelineConfig` (a run's settings and the paths derived from them) and the `CancelToken` shared by every step of a run; each step has `run(config)`, and its `main(argv)` only turns options into a config.
- `src/crittervid/log.py` — logging for a run (`docs/decisions/0003-logging.md`): `start_run` gives it an ID and sends the `crittervid` logger to the console (messages as printed before), a JSON-lines run log in `<output>/logs/` and the app log in the OS's log folder. Not used by the steps yet.
- `src/crittervid/cli.py` — the `crittervid` command (`python -m crittervid`): `run`, `convert`, `merge`, `validate`, each handing its options to that step's `main(argv)`.
- `src/crittervid/names.py` — file-name patterns, day grouping and the split into time windows, shared by the merge and validation.
- `src/crittervid/pipeline.py` (was `process_all.py`) — runs the three stages in order, in one process (`crittervid run`).
- `src/crittervid/gui/` — `crittervid gui` (also the default with no command): `launch.py` (was `launcher.py`) starts Streamlit on a free local port with `streamlit_app.py` (was `app.py`), which runs the pipeline by starting `crittervid run` (the app itself when built, so no separate Python is needed).
- `src/crittervid/ffmpeg.py` (was `ffmpeg_helper.py`) — finds FFmpeg (project folder, or `_internal` in the built app, then `PATH`).

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
