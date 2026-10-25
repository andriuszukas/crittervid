# 0002: One package with one entry point

- **Status:** proposed
- **Issues:** #24 (restructure), which unblocks #25 (step interface), #26 (logging), #29 (`doctor`) and fixes #4 (the app needs a system Python)

## Context

CritterVid is five scripts in the project root: `convert_media.py`, `merge_videos.py`, `validate_output.py`, `process_all.py` and `app.py`. Two helpers sit beside them: `media_format.py` and `ffmpeg_helper.py`.

- **Scripts start each other as separate programs.** `process_all.py` runs each stage with `python <script>`. The GUI runs `process_all.py` the same way. When frozen by PyInstaller, it calls a `python` that end users don't have (#4).
- **Settings are module globals** (`OUTPUT_ROOT`, `MERGED_ROOT`, `SUMMARY_REPORT`, …), reassigned in each `main()`. Code that imports a stage, such as validation reusing the merge's day grouping, has to set those globals first.
- **Everything depends on the flat layout:**
  - The PyInstaller spec lists each script as a data file.
  - The tests copy the scripts into a temporary folder and run them there.
  - FFmpeg is looked up next to the scripts, and the README tells users to put `ffmpeg.exe` in the project folder.
- **No package or `pyproject.toml`.** Installing means `pip install -r requirements.txt`, then running a script by path.

Steps #25 (pluggable steps), #26 (logging with run IDs), #29 (`crittervid doctor`) and the PySide6 GUI (#41) all need a real package with explicit settings first.

## Decision

### Layout

```
pyproject.toml              name "crittervid", requires-python >=3.10
src/crittervid/
  __init__.py               version
  __main__.py               python -m crittervid
  cli.py                    argparse subcommands
  config.py                 PipelineConfig (frozen dataclass)
  media_format.py           unchanged, moved
  ffmpeg.py                 was ffmpeg_helper.py
  convert.py                was convert_media.py
  merge.py                  was merge_videos.py
  validate.py               was validate_output.py
  pipeline.py               was process_all.py: runs the stages in order
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
  - `python -m crittervid …` always works from a checkout.
  - After `pip install .`, a `crittervid` command does the same.
  - The frozen app is the same program: `CritterVid.exe run …`.
- **The options stay as they are today.** `--input-dir`, `--splits-per-day`, `--force-merge` and the others keep their names, so the README examples change only in how the program is started.
- **`crittervid run` calls the stages in-process,** one after another. FFmpeg is still a separate program.

### How the GUI runs the pipeline

The GUI starts **itself** as a child process: `sys.executable -m crittervid run …` from source, or `CritterVid.exe run …` when frozen. It does not run the stages inside its own process.

- **This fixes #4:** no outside `python` is needed.
- **The GUI stays responsive and survives a crash in a stage.**
- **Stopping reuses #9's process-group Ctrl+Break handling** unchanged. This is what the Stop button (#42) will use.

### Settings

A frozen `PipelineConfig` dataclass replaces the module globals. It holds the input and output folders, acceleration, workers, splits per day, copy mode, retry options, force merge and validation options.

- The CLI builds one from the arguments, and each stage takes it as a parameter.
- Paths derived from it, such as the merged folder and the report files, are properties, so they can't drift apart.
- One shared cancel token replaces the converter's module-level `STOP` event, so the merge and validation can be cancelled the same way.

### Finding FFmpeg

In order, the first match wins:

1. `CRITTERVID_FFMPEG` / `CRITTERVID_FFPROBE`, if set (useful for tests and unusual installs).
2. The folder of the frozen executable.
3. The project root: the folder that holds `pyproject.toml`, when running from a checkout. This keeps the README's "put `ffmpeg.exe` in the project folder" working.
4. `PATH`.

### Dependencies

`pyproject.toml` becomes the single list:

| Group | Contents |
|---|---|
| core | nothing; the pipeline needs only the standard library |
| `gui` extra | Streamlit (PySide6 later) |
| `gpu` extra | GPUtil, plus WMI on Windows only |
| `dev` extra | pytest, PyInstaller |

`requirements.txt` and `requirements-dev.txt` stay as one-line wrappers (`-e .[gui,gpu]` and `-e .[gui,gpu,dev]`), so the README commands keep working.

### Compatibility while it lands

- **The old scripts stay** in the project root for one release, as three-line shims, e.g. `convert_media.py` calls `crittervid.cli.main(['convert', *sys.argv[1:]])`.
- **`run_app.bat`, existing habits and the GUI's "Running: …" line** keep working.
- **The shims print a one-line notice** naming the new command. They're removed in the release after.

### Tests

- The tests run `python -m crittervid …` with `PYTHONPATH=src` against the checkout, instead of copying scripts.
- The fake FFmpeg is found through `CRITTERVID_FFMPEG`, not by putting the script folder first on `PATH`.
- A small test checks that each shim still forwards its arguments.
- CI installs the package with `pip install -e .[dev,gui]`.

## Plan: small PRs, each green on CI

1. **Package skeleton.**
   - Add `pyproject.toml` and `src/crittervid/`.
   - `git mv` `media_format.py` and `ffmpeg_helper.py` into the package, with shims left behind.
   - CI installs the package.
   - No behaviour change.
2. **Move the stages.**
   - `git mv` the converter, merge, validation and orchestrator into the package, with shims.
   - Each stage gets `main(argv)`.
   - Still module globals; still no behaviour change.
3. **One entry point (fixes #4).**
   - `cli.py` with the subcommands; `crittervid run` calls the stages in-process.
   - The GUI starts `<itself> run`.
   - The PyInstaller spec builds from `crittervid.__main__`, and the frozen app answers `run`, `gui` and the other commands.
4. **`PipelineConfig` and the shared cancel token** replace the globals.
5. **Tests on `python -m crittervid`.**
   - FFmpeg lookup as above.
   - README, DEPLOYMENT and `.github/copilot-instructions.md` show the new commands.

Each PR gets its own review, as usual. After these: #26 (logging), #25 (step interface) and #29 (`doctor`), each on top of `PipelineConfig`.

## Risks

- **PyInstaller and self-invocation.** The frozen GUI must start `CritterVid.exe run` without opening a new window or triggering Windows Defender prompts. PR 3 checks a real build on Windows, and on Linux and macOS once #46 builds them.
- **In-process stages share one process.** A bug that leaks state between stages (the reason for the globals' reassignments today) shows up in `run`, not in single-stage tests. PR 4 removes the globals. Until then `run` resets them, as `main()` does today.
- **Ctrl+C in-process.** #9's handling is written for a stage that is its own process. With stages in-process, one handler covers the run. PR 3 keeps #9's tests and adds one for `crittervid run`.
- **Size of the move.** PRs 1 and 2 are mostly renames. Keeping behaviour identical, and the suite untouched apart from imports, makes them easy to review.

## Questions for you

1. **Shims for one release, then remove them?** Recommended. Users who run `python process_all.py` keep working, with a notice.
2. **GUI runs the pipeline as a child process of itself?** Recommended. The alternative, running stages in a GUI thread, is harder to stop cleanly, and a crash takes the GUI down.
3. **Publish to PyPI later?** This layout allows it, but nothing here depends on it. The name `crittervid` is free there today.
