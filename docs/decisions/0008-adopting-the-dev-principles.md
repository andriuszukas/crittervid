# 0008: Adopting the dev principles in the existing code

- **Status:** proposed (2026-11-04)
- **Principles:** the project's dev principles, "Growing without weaving". This record says where the code already follows them, where it doesn't, and how it gets there in small steps, each a PR of its own.

## Where the code stands

- **Already true, but not checked:**
  - **Direction:** modules depend in one direction, including imports inside functions. The pure modules (`media_format`, `names`, `index`, `config`) use only the standard library. `log`, `ffmpeg` and `deployment` build on them. The steps (`convert`, `merge`, `validate`, `doctor`) build on both. The composition (`steps`, `pipeline`, `cli`) and the GUI sit on top.
  - **Steps** don't import each other.
  - **Optional parts:** importing the command line loads neither Streamlit nor Tkinter.
  - **The checks today:** only `index.py`'s rule (decision record 0007) is enforced by a test.
- **Loaded by name, which an import check can't see:**
  - `cli.COMMANDS` and `steps.STEPS` load their modules by name, and that's how `cli` selects the GUI.
  - `crittervid.spec` repeats the same list for the packaged app.
  - Nothing checks that these names exist or that the lists agree.
- **Files outside the package:**
  - **Root scripts:** `convert_media.py`, `app.py` and the others are kept for old commands (decision record 0002).
  - **Developer tools:** `tools/inspect_media.py` reaches `media_format` through one of those root scripts.
  - **Assembly points:** `run_app.bat`, `launcher.py` and `app.py` are deployment entry points of their own.
- **External inputs not pinned by content hash:**
  - Python packages are pinned by version only, and installing them needs the network.
  - On CI:
    - the GitHub Actions are pinned by tag, not commit;
    - Python by minor version only;
    - FFmpeg comes from a Windows download with no hash check, a Chocolatey fallback, apt on Linux and brew on macOS.
  - In the build:
    - `build.bat` installs PyInstaller from the network when it's missing;
    - `crittervid.spec` uses UPX if one happens to be on PATH, so the app's size depends on the machine.
  - There's no local mirror, and no build proves its inputs are complete.
- **Shared documents without one list:**
  - The parts already exchange documents, each with its own record and version:
    - `deployment.json` (0005, 0006);
    - the event index (0007);
    - the run log (0003);
    - the list of failed files;
    - the command-line options, which are how the GUI drives the pipeline.
  - `merged_contents.json` is the merge's private record.
  - Nothing lists which documents are public, and so must stay compatible, and which are private.
- **One role, two implementations, without a shared suite:** the fake FFmpeg and ffprobe in `tests/` stand in for the real ones. Nothing checks that they behave the same where the pipeline relies on it.
- **Decisions mixed with effects in `merge.py`:** what to merge, what's up to date, what's stale and which indexes are current is decided inside the code that runs FFmpeg and writes files. `index.py` shows the target shape.
- **Not measured:** the packaged app's size.

## Decision

### Shared documents, listed

- **One list:** `docs/documents.md` lists each document the parts exchange, with its owner, its schema or record, and whether it's public or private.
- **Public documents:**
  - Their changes are additive.
  - A version or schema number rises when a meaning changes.
  - Older documents stay readable, with the same meaning.
  - These rules are already in each record; the list makes them findable.
- **Private documents** (the contents record) can change with their owner.

### 1. The layers, as data, and tests that enforce them

- **The layers are written down as data:** a table in `tests/architecture.py` gives each file its layer. Each rule states its purpose in the test.

| Layer | Files | May import |
|---|---|---|
| core | `media_format`, `names`, `index`, `config` | the standard library and core; no `subprocess` |
| services | `log`, `ffmpeg`, `deployment` | core, services |
| steps | `convert`, `merge`, `validate`, `doctor` | core, services; never another step |
| composition | `steps`, `pipeline`, `cli`, `__main__` | everything above. It may load the GUI only by name, on demand, never by `import` |
| GUI | `gui.*` | anything; the only layer that may import Streamlit or Tkinter |
| root scripts | the root `*.py` and `.bat` files (old commands, 0002) | only the `crittervid` package, through its public modules |
| tools | `tools/*.py` | core and services, through `crittervid.*` (`inspect_media.py` moves off the root script) |

- **What the tests check:**
  - **Imports:** they read each file's imports from its source, including imports inside functions, and reject any the table doesn't allow. A file the table doesn't list fails, so every file has an owner.
  - **Modules loaded by name:**
    - the names in `cli.COMMANDS` and `steps.STEPS` are resolved and checked against the table, in the same way;
    - the spec's `hiddenimports` must contain all of them.
  - **Loading on demand:**
    - in a fresh interpreter, importing `crittervid.cli` loads no step module, no GUI module, and neither Streamlit nor Tkinter;
    - running one command loads only its own step.
  - **The plain install:** a CI job installs `crittervid` without extras and runs `crittervid --help`, `crittervid doctor`, and a small convert and merge. So the command-line app works without the GUI, and Streamlit isn't installed.
  - **Old entry points:** the old entry points (`app.py`, `launcher.py`) keep their existing start-up tests, until #43 retires them.

### 2. A shared behaviour suite for the FFmpeg role

- **The suite:** `tests/ffmpeg_contract.py` holds the behaviours the pipeline relies on from FFmpeg and ffprobe:
  - copying a stream;
  - a concat list with `inpoint`, `outpoint` and `duration`;
  - chapters from a metadata file;
  - `creation_time`;
  - ffprobe's stream start and length;
  - a failing input's exit code.
- **Both implementations pass it:** it runs against the fake tools always, and against the real ones when they're installed. A fake that drifts from FFmpeg then fails a test, not a user.
- **The GUI next:** the second role with two implementations will be the GUI, when the PySide6 one (#41) joins the Streamlit one. Both must turn the same fields into the same command line.

### 3. Every external input pinned by its content hash

**3a. The cheap pins first**
- **GitHub Actions** are pinned by commit SHA, with the tag in a comment.
- **CI's Python** is an exact patch version per job.
- **FFmpeg on CI:**
  - Windows uses the pinned zip, checked by hash, and the Chocolatey fallback goes.
  - Linux uses a pinned static build, checked by hash.
  - macOS too, if a pinned arm64 build is available (question 3). Otherwise brew stays, recorded here as an exception with its reason.
- **The build:** `build.bat` no longer installs PyInstaller from the network; it says how to install from the lock. The spec sets `upx=False`.
- **The list of pins:** `tools/pins.json` holds every non-Python pin: URL, SHA-256, and what it's for.

**3b. Python packages: locks, a mirror and offline installs**
- **Lock files:**
  - `requirements.lock` (the app) and `requirements-dev.lock` (building and testing) pin every package by version and SHA-256, including indirect ones and `pip`, `setuptools` and `wheel`. That's for the supported systems and Pythons only: Windows, macOS and Linux; 3.10 to 3.13.
  - They're made with a pinned `uv` (question 2). Making them needs the network, and it's a deliberate act in its own commit.
- **The project itself:**
  - It isn't hashed, as it's the code being built.
  - It's installed after the lock with `pip install --no-deps --no-build-isolation -e .`, so pip needs nothing from the network.
- **tzdata:** `pyproject.toml` keeps its floor for users (0005); the lock pins the exact version used for development, CI and builds.
- **The mirror:**
  - `python tools/mirror.py fill <folder>` runs `pip download --require-hashes` for one system and Python (about 200 MB, rather than 2 GB for all of them) and writes the folder's own list of hashes.
  - `python tools/mirror.py verify <folder>` checks every file against the lock without the network.
  - A mirror can be copied between machines, because each file is checked against the repository, not trusted for where it came from. It lives outside the repository.
- **Offline commands:** installing, testing and building run from a mirror alone:
  1. `pip install --no-index --find-links <mirror> --require-hashes -r requirements-dev.lock`;
  2. then the project, as above.
  - `build.bat`, the README and DEPLOYMENT give these commands.
- **CI:**
  - CI runs the same commands, so it stays optional.
  - One Linux job then runs the install, the tests and a build in `docker run --network none` with the mirror mounted, which proves the pins are complete.
  - On the Windows and macOS runners the `--no-index` install proves it for their wheels. The shipped Windows `.exe` is built from the same lock.

### 4. Decisions apart from effects in the merge

- **A pure planner:** a new core module, `merge_plan.py`, decides what a merge run does.
  - It takes plain data: the converted files, each day's contents record and offsets, the merged and index files present, and the settings.
  - It returns the actions in order: merge this part from these files, skip this one, write this index, remove these stale files, and report these warnings.
  - It touches no files and runs no FFmpeg.
- **`merge.py` becomes the shell:** it gathers the facts, performs the actions, and reports the results.
- **Behaviour stays the same,** guarded by the existing tests. The planner gets its own tests on plain data.

### 5. The packaged app's size, checked where it's built

- **A budget:** a build check (`tools/check_app.py`, run after `build.bat` and on CI's Windows build, not part of the ordinary test run) measures the app's size against a budget. It also checks that the app holds what its spec selects: the pinned FFmpeg when it's bundled, and the GUI.
- **Setting it:** the budget is recorded here when this step lands: the measured size of an offline build, plus a margin. Raising it later needs a recorded reason.
- **The plain install** gets no size budget, as it's a few kilobytes. It's checked for having no GUI (step 1).

## Order

1. Step 1, the layer rules: it locks in what's right before anything moves. The documents list goes with it.
2. Step 3a, the cheap pins.
3. Step 2, the FFmpeg behaviour suite.
4. Step 3b, the locks, the mirror and offline installs.
5. Step 4, the merge planner.
6. Step 5, the app's size, measured from an offline build.

Each step updates this record's status line, so the stated design and the enforced one agree.

## Questions for you

1. **This order, with the layer rules first and the cheap pins second?** Recommended: they protect the current shape and close the easiest gaps before anything moves. The alternative is the merge planner first, since it's the visible design change.
2. **`uv` for the lock files?** Recommended: one universal lock covers the supported systems, with hashes. uv is pinned by hash and needed only to make the locks. The alternative is pip-tools, with one lock per system, because it resolves only for the system it runs on.
3. **FFmpeg on macOS CI: a pinned static build if a trustworthy arm64 one exists, otherwise brew as a recorded exception?** Recommended: Windows ships FFmpeg and Linux proves the offline build, so macOS matters least. The alternative is no FFmpeg on macOS CI, running only the fake-FFmpeg tests there.
