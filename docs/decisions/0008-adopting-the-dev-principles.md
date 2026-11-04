# 0008: Adopting the dev principles in the existing code

- **Status:** proposed (2026-11-04)
- **Principles:** the project's dev principles, "Growing without weaving". This record says where the code already follows them, where it doesn't, and how it gets there in small steps, each a PR of its own.

## Where the code stands

- **Already true, but not checked:**
  - **Direction:** modules depend in one direction. The pure modules (`media_format`, `names`, `index`, `config`) use only the standard library. `log`, `ffmpeg` and `deployment` build on them. The steps (`convert`, `merge`, `validate`, `doctor`) build on both. The composition (`steps`, `pipeline`, `cli`) and the GUI sit on top.
  - **Steps:** they don't import each other.
  - **Optional parts:** importing the command line or any step loads neither Streamlit nor Tkinter.
  - Only `index.py`'s rule (decision record 0007) is enforced by a test. Nothing would stop the rest from eroding.
- **Not true:**
  - **Pinning and the network:** dependencies are pinned by version, not by content hash. Installing them needs the network.
  - **FFmpeg on CI:** Windows CI downloads FFmpeg with no hash check, or falls back to Chocolatey.
  - **Offline builds:** there's no local mirror, and no build proves its inputs are complete.
- **Partly true: `merge.py`.** Its decisions sit inside the code that runs FFmpeg and writes files: what to merge, what's up to date, what's stale, and which indexes are current. `index.py` shows the target shape. Day grouping already lives apart, in `names.py`.
- **Not measured:**
  - **The packaged app's size:** there's no budget for it.
  - **The plain install:** nothing checks that `pip install crittervid`, without the `gui` extra, works on its own.

## Decision

### 1. The layers, as data, and a test that enforces them

- **The layers are written down as data:** a small table in `tests/architecture.py` maps each module to its layer. Each rule in the test suite states its purpose.

| Layer | Modules | May import |
|---|---|---|
| core | `media_format`, `names`, `index`, `config` | the standard library and core; no `subprocess` |
| services | `log`, `ffmpeg`, `deployment` | core, services |
| steps | `convert`, `merge`, `validate`, `doctor` | core, services; never another step |
| composition | `steps`, `pipeline`, `cli`, `__main__` | everything above, except the GUI |
| GUI | `gui.*` | anything; the only layer that may import Streamlit or Tkinter |

- **What the tests check:**
  - **Allowed imports:** they read each module's imports from its source and reject any the table doesn't allow, including a relative import of a private name from another layer.
  - **A new module** fails the test until it's given a layer, so every module has an owner.
  - **No optional features on import:** in a fresh interpreter, importing `crittervid.cli` and each step leaves Streamlit, Tkinter and the GUI out of `sys.modules`.
  - **The plain install:** a CI job installs `crittervid` without extras and runs `crittervid --help`, `crittervid doctor` and a small convert and merge, so the command-line app works without the GUI.
- **Later roles** with more than one implementation get a shared contract suite. The first is the GUI, when the PySide6 one (#41) joins the Streamlit one: both must turn the same fields into the same run.

### 2. Every external input pinned by its content hash, and builds without the network

- **Lock files:**
  - `requirements.lock` and `requirements-dev.lock` pin every package, including indirect ones, by version and SHA-256, for every supported system. They're made with `uv pip compile --universal --generate-hashes` from `pyproject.toml`.
  - Every install uses `--require-hashes`, so any file whose hash doesn't match is refused.
  - Making the lock files needs the network, and it's a deliberate act, done in its own commit.
- **Other inputs:** `tools/pins.json` lists the rest, by URL and SHA-256: FFmpeg for Windows (the build CI uses and the app can bundle), and the tools that make the lock files.
- **A local mirror:**
  - `python tools/mirror.py fill <folder>` downloads exactly the pinned files and checks each against its hash.
  - `python tools/mirror.py verify <folder>` checks a mirror without the network.
  - A mirror can be copied between machines, because every file is checked against the repository, not trusted for where it came from. It lives outside the repository by default.
- **Offline commands:** installing, testing and building run from the mirror alone, with `pip install --no-index --find-links <mirror> --require-hashes -r requirements-dev.lock`. `build.bat` and the docs say how.
- **On CI:**
  - CI runs the same commands, so CI stays optional.
  - Every job fills and verifies a mirror and installs from it.
  - One Linux job then installs, tests and builds with the network switched off, in a network namespace, to prove the pins are complete.
  - FFmpeg on Windows comes from the pinned zip, checked by hash. The unpinned Chocolatey fallback goes.

### 3. Decisions apart from effects in the merge

- **A pure planner:** a new core module, `merge_plan.py`, decides what a merge run does.
  - It takes plain data: the converted files, each day's contents record and offsets, the merged and index files present, and the settings.
  - It returns the actions in order: merge this part from these files, skip this one, write this index, remove these stale files, and report these warnings.
  - It touches no files and runs no FFmpeg.
- **`merge.py` becomes the shell:** it gathers those facts, performs the actions, and reports the results.
- **Behaviour stays the same,** guarded by the existing tests. The planner gets its own tests on plain data, which run in milliseconds.

### 4. Size budgets for each deployment

- **The packaged app:** a build test measures its size. The budget is recorded in this record when step 4 lands: the measured size, plus a margin. Raising it later needs a recorded reason.
- **The plain install** has a budget too, measured from its installed files.
- **Each deployment is checked where it's assembled:** the app for what it's meant to bundle, and the plain install for having no GUI.

## Order

1. **The layer rules** (step 1): cheap, and it locks in what's already right before anything moves.
2. **Pinning, the mirror and offline builds** (step 2): it touches CI and the build, not the code.
3. **The merge planner** (step 3): a refactor, and safer with the rules in place.
4. **The size budgets** (step 4): they need the offline build to measure the same thing each time.

Each step updates this record's status line, so the stated design and the enforced one agree.

## Questions for you

1. **This order, with the layer rules first?** Recommended: it protects the current shape before anything changes. The alternative is the merge planner first, since it's the visible design change.
2. **`uv` for the lock files?** Recommended: one lock covers Windows, macOS and Linux (`--universal`), with hashes. uv is itself pinned by hash and is needed only to make the locks, not to install or run. The alternative is pip-tools, with one lock file per system, because it resolves for the system it runs on.
3. **Prove offline builds on one Linux CI job, with the network switched off?** Recommended: GitHub's Windows and macOS runners can't easily cut the network. There the `--no-index` install from the mirror proves the same pins are complete. The alternative is the `--no-index` proof everywhere, without a network-off job.
