# 0002: One package with one entry point

- **Status:** accepted (2026-10-26), with the recommended answers to the questions at the end
- **Issues:** #24 (restructure), which unblocks #25 (step interface), #26 (logging), #29 (`doctor`) and fixes #4 (the app needs a system Python)

## Context

CritterVid is five scripts in the project root: `convert_media.py`, `merge_videos.py`, `validate_output.py`, `process_all.py` and `app.py`. Two helpers sit beside them: `media_format.py` and `ffmpeg_helper.py`. `launcher.py` starts the Streamlit GUI and is the PyInstaller entry point.

- **Scripts start each other as separate programs.** `process_all.py` runs each stage with `python <script>`. The GUI runs `process_all.py` the same way. When frozen by PyInstaller, it calls a `python` that end users don't have (#4).
- **Settings are module globals** (`OUTPUT_ROOT`, `MERGED_ROOT`, `SUMMARY_REPORT`, …), reassigned in each `main()`. The FFmpeg path is looked up when a module is imported. Validation imports the merge's name pattern, and calls its day grouping and window split after setting its globals.
- **Everything depends on the flat layout:**
  - The PyInstaller spec lists each script as a data file.
  - The tests copy the scripts into a temporary folder and run them there, with the fake FFmpeg first on `PATH`.
  - FFmpeg is looked up next to the scripts (`_internal` in the built app), then on `PATH`.
- **No package or `pyproject.toml`.** Installing means `pip install -r requirements.txt`, then running a script by path.

Steps #25 (pluggable steps), #26 (logging with run IDs), #29 (`crittervid doctor`) and the PySide6 GUI (#41) all need a real package with explicit settings first.

## Decision

### Layout

```
pyproject.toml              name "crittervid", requires-python >=3.10
src/crittervid/
  __init__.py               __version__
  __main__.py               python -m crittervid; the built app's entry point
  cli.py                    argparse subcommands
  config.py                 PipelineConfig (frozen dataclass)
  names.py                  file-name patterns, day grouping, window split (shared by merge and validation)
  media_format.py           unchanged, moved
  ffmpeg.py                 was ffmpeg_helper.py
  convert.py                was convert_media.py
  merge.py                  was merge_videos.py
  validate.py               was validate_output.py
  pipeline.py               was process_all.py: runs the stages in order
  gui/launch.py             was launcher.py: port search, starts Streamlit
  gui/streamlit_app.py      was app.py (until PySide6, #41)
tools/inspect_media.py      stays a tool, imports crittervid.media_format
tests/                      unchanged location
```

Files move with `git mv`, so history follows them.

### One entry point

```
crittervid run       [options]    convert, merge, validate (was process_all.py)
crittervid convert   [options]
crittervid merge     [options]
crittervid validate  [options]
crittervid gui                    also what running with no arguments does
```

- **Ways to start it:**
  - `python -m crittervid …` from an install or an editable install (`pip install -e .`).
  - After installing, a `crittervid` command does the same.
  - The frozen app is the same program: `CritterVid.exe run …`.
- **The options stay as they are today.** `--input-dir`, `--splits-per-day`, `--force-merge` and the others keep their names. Relative defaults (`input/DCIM`, `output`) still mean relative to the current folder.
- **`crittervid run` calls the stages in-process,** one after another. FFmpeg is still a separate program.
- **`__main__` reads the subcommand before importing anything heavy.** `run` and the other stages never import Streamlit. Only `gui` imports it, inside `gui/launch.py`.
- **`gui/launch.py` takes over `launcher.py`:** the free-port search and the Streamlit flags. It finds `streamlit_app.py` next to itself (`Path(__file__)`), which works from a checkout, an install or the bundle.

### How the GUI runs the pipeline

The GUI starts **itself** as a child process: `sys.executable -m crittervid run …` from source, or `CritterVid.exe run …` when frozen (`sys.executable` is then the app). It does not run the stages inside its own process.

- **This fixes #4:** no outside `python` is needed.
- **The GUI stays responsive and survives a crash in a stage.**
- **The child's output still reaches the GUI.** It keeps today's `PYTHONIOENCODING` and `PYTHONUNBUFFERED` settings, and it's started with `CREATE_NO_WINDOW` on Windows, so it doesn't open a console of its own.
- **The built app stays a console build,** as now. A windowed build would leave the child without standard output.
- **Stopping reuses #9's process-group Ctrl+Break handling** unchanged. This is what the Stop button (#42) will use.

### Settings

A frozen `PipelineConfig` dataclass replaces the module globals. It holds the input and output folders, acceleration, workers, splits per day, copy mode, retry options, force merge and validation options.

- The CLI builds one from the arguments, and each stage takes it as a parameter.
- Paths derived from it, such as the merged folder and the report files, are properties, so they can't drift apart.
- One shared cancel token replaces the converter's module-level `STOP` event, so the merge and validation can be cancelled the same way.

### Finding FFmpeg

Looked up when first needed, not when a module is imported. ffmpeg and ffprobe are each found in this order, and the first match wins:

1. `CRITTERVID_FFMPEG` / `CRITTERVID_FFPROBE`, if set. Tests use these, as can unusual installs.
2. When frozen: the bundle's folder (`sys._MEIPASS`, which is `_internal` in PyInstaller's one-folder build). That's where `build.bat` and DEPLOYMENT tell users to put them, so the shipped layout is unchanged.
3. From a checkout: the project root, meaning the folder that holds `pyproject.toml`. This keeps the README's "put `ffmpeg.exe` in the project folder" working.
4. `PATH`.
5. The common macOS install locations checked today.

### Dependencies and version

`pyproject.toml` becomes the single list:

| Group | Contents |
|---|---|
| core | nothing; the pipeline needs only the standard library |
| `gui` extra | Streamlit (PySide6 later) |
| `gpu` extra | GPUtil, plus WMI on Windows only |
| `dev` extra | pytest, PyInstaller |

(Later: the `gpu` extra was removed in #105, when Auto started trying the encoders themselves instead of detecting the GPU. The requirements files became `-e .[gui]` and `-e .[gui,dev]`.)

- **Requirements files:** `requirements.txt` and `requirements-dev.txt` become one-line wrappers (`-e .[gui,gpu]` and `-e .[gui,gpu,dev]`). The path is relative to the folder you run pip from, so the README tells you to run them from the project folder, as now.
- **Version:** read with `importlib.metadata`, falling back to `__init__.__version__`. That covers the built app, which has no package metadata.
- **Python versions:** nothing used needs more than Python 3.10. `tomllib` isn't needed.

### Compatibility while it lands

- **The old scripts stay** in the project root for one release, as short wrappers. Each puts `src` on Python's import path itself, so it works from a fresh checkout without installing. For example, `convert_media.py` calls `crittervid.cli.main(['convert', *sys.argv[1:]])`, and `app.py` runs the Streamlit app.
- **`run_app.bat`, existing habits and the GUI's "Running: …" line** keep working.
- **The wrappers print a one-line notice** naming the new command. They're removed in the release after.

### Tests

- The tests run `python -m crittervid …` from the workspace, with an absolute `PYTHONPATH` pointing at `src`. Relative defaults keep working.
- The fake FFmpeg and ffprobe are passed through `CRITTERVID_FFMPEG` / `CRITTERVID_FFPROBE`. Otherwise a real `ffmpeg.exe` in the project folder would take precedence. Copying the scripts avoided that until now.
- The GUI tests load `src/crittervid/gui/streamlit_app.py`. The inspect tool and its test import `crittervid.media_format`.
- A small test checks that each wrapper still forwards its arguments.
- CI installs the package with `pip install -e .[dev,gui]`.

## Plan: small PRs, each green on CI

1. **Package skeleton.**
   - Add `pyproject.toml`, `src/crittervid/` and the version.
   - `git mv` `media_format.py` and `ffmpeg_helper.py` into the package. Wrappers left behind put `src` on the import path, so the tests are untouched.
   - CI installs the package.
   - No behaviour change.
2. **Move the stages and share their names.**
   - `git mv` the converter, merge, validation and orchestrator into the package, with wrappers.
   - Move the shared name patterns, day grouping and window split into `names.py`.
   - Each stage gets `main(argv)`.
   - Still module globals; still no behaviour change.
3. **One command.**
   - `cli.py` and `__main__` with the subcommands. `crittervid run` calls the stages in-process.
   - FFmpeg is looked up lazily, with the `CRITTERVID_*` overrides.
   - The tests move to `python -m crittervid`, including the end-to-end and real-FFmpeg tests, so they don't only test the wrappers.
4. **GUI and built app (fixes #4).**
   - `gui/launch.py` replaces `launcher.py`, and the GUI starts `<itself> run`.
   - The PyInstaller spec builds from `crittervid/__main__.py` and ships `streamlit_app.py`.
   - Check a real Windows build: `run` works without a system Python, with no extra window, and output reaches the GUI.
5. **`PipelineConfig` and the shared cancel token** replace the globals. README, DEPLOYMENT and `.github/copilot-instructions.md` show the new commands.

Each PR gets its own review, as usual. After these: #26 (logging), #25 (step interface) and #29 (`doctor`), each on top of `PipelineConfig`.

## Risks

- **PyInstaller and self-invocation.** The frozen GUI must start `CritterVid.exe run` without opening a new window or triggering Windows Defender prompts. PR 4 checks a real build on Windows, and on Linux and macOS once #46 builds them.
- **In-process stages share one process.** A bug that leaks state between stages (the reason for the globals' reassignments today) shows up in `run`, not in single-stage tests. PR 5 removes the globals. Until then `run` resets them, as `main()` does today.
- **Ctrl+C in-process.** #9's handling is written for a stage that is its own process. With stages in-process, one handler covers the run. PR 3 keeps #9's tests and adds one for `crittervid run`.
- **Size of the move.** PRs 1 and 2 are mostly renames. Keeping behaviour identical, and the suite untouched apart from imports, makes them easy to review.

## Questions (answered: the recommended options)

1. **Wrappers for one release, then remove them?** Recommended. Users who run `python process_all.py` keep working, with a notice.
2. **GUI runs the pipeline as a child process of itself?** Recommended. The alternative, running stages in a GUI thread, is harder to stop cleanly, and a crash takes the GUI down.
3. **Publish to PyPI later?** This layout allows it, but nothing here depends on it. The name `crittervid` is free there today.
