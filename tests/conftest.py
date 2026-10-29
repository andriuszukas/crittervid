import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# Tests that import the package directly use the checkout's copy, installed or not
sys.path.insert(0, str(REPO_ROOT / 'src'))
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS = ['ffmpeg_helper.py', 'media_format.py', 'convert_media.py', 'merge_videos.py', 'validate_output.py', 'process_all.py',
           'app.py']

# The old script names of the pipeline steps, and their crittervid commands
COMMANDS = {'convert_media.py': 'convert', 'merge_videos.py': 'merge', 'validate_output.py': 'validate',
            'process_all.py': 'run'}

# Matches the camera layout: DCIM/YYYY/MM/DD/<unixtimestamp>_<duration>/
GROUP_DIR = Path('input/DCIM/2025/10/01/1759300000_0001')
AFTERNOON_DIR = Path('input/DCIM/2025/10/01/1759330000_0001')


class Workspace:
    """A temp copy of the pipeline scripts, run as subprocesses like the GUI does."""

    def __init__(self, root, env):
        self.root = root
        self.env = env
        # The test cameras' time zone, which the steps get as --timezone (decision
        # record 0005); None gives none, as for a camera whose time zone isn't known
        self.camera_timezone = 'UTC'

    def add_media(self, name, content):
        path = self.root / GROUP_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def add_afternoon_media(self, name, content):
        """A segment of a second event that day, at 14:46 UTC (GROUP_DIR's is at 06:26)."""
        path = self.root / AFTERNOON_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def converted(self, name):
        """Where convert_media.py writes the .mp4 for the given .media file name."""
        stem = Path(name).stem
        return self.root / 'output' / GROUP_DIR.relative_to('input/DCIM') / f'1759300000_0001_{stem}.mp4'

    def files(self, pattern):
        return sorted((self.root / 'output').rglob(pattern))

    def ffmpeg_calls(self):
        """Command lines the fake ffmpeg received, when FAKE_FFMPEG_LOG is set
        (it writes one file per call, named after the log path)."""
        log = Path(self.env['FAKE_FFMPEG_LOG'])
        return [json.loads(f.read_text(encoding='utf-8')) for f in sorted(log.parent.glob(log.name + '.*'))]

    def concat_lists(self):
        """Text of the concat lists the fake ffmpeg received (see ffmpeg_calls)."""
        log = Path(self.env['FAKE_FFMPEG_LOG'])
        return [f.read_text(encoding='utf-8') for f in sorted(log.parent.glob(log.name + '-concat.*'))]

    def command(self, script, *args):
        """The command line for a pipeline step, named by its old script: the
        crittervid command for the steps, the script itself otherwise."""
        if script in COMMANDS:
            if self.camera_timezone and '--help' not in args:
                args = ('--timezone', self.camera_timezone, *args)
            return [sys.executable, '-m', 'crittervid', COMMANDS[script], *args]
        return [sys.executable, script, *args]

    def run(self, script, *args):
        return subprocess.run(
            self.command(script, *args),
            cwd=self.root, env=self.env, capture_output=True,
            encoding='utf-8', errors='replace',
        )


def make_workspace(tmp_path, path_prefix=None):
    app = tmp_path / 'app'
    app.mkdir()
    # Copy the scripts so an ffmpeg.exe sitting in the repo folder can't take
    # precedence over the ffmpeg chosen for the test
    for name in SCRIPTS:
        shutil.copy(REPO_ROOT / name, app)
    # The package the scripts import (they put src/ on the import path)
    shutil.copytree(REPO_ROOT / 'src', app / 'src', ignore=shutil.ignore_patterns('__pycache__', '*.egg-info'))
    env = dict(os.environ)
    # Run the scripts as they'd run on a plain machine: these variables would hide
    # buffering and encoding bugs the tests are meant to catch
    for var in ('PYTHONUNBUFFERED', 'PYTHONUTF8', 'PYTHONIOENCODING'):
        env.pop(var, None)
    # The computer's time zone, for tests that don't give the camera's
    env['TZ'] = 'UTC0'
    # `python -m crittervid` finds this copy of the package, not the checkout's
    env['PYTHONPATH'] = os.pathsep.join([str(app / 'src')] + [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p])
    for var in ('CRITTERVID_FFMPEG', 'CRITTERVID_FFPROBE'):
        env.pop(var, None)
    # The app log goes to the test's folder, not the user's
    env['CRITTERVID_LOG_DIR'] = str(tmp_path / 'app-logs')
    if path_prefix:
        env['PATH'] = str(path_prefix) + os.pathsep + env['PATH']
    return Workspace(app, env)


@pytest.fixture
def fake_ffmpeg_dir(tmp_path):
    """A directory holding `ffmpeg` and `ffprobe` commands that run
    tests/fake_ffmpeg.py and tests/fake_ffprobe.py."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    for tool in ('ffmpeg', 'ffprobe'):
        fake = TESTS_DIR / f'fake_{tool}.py'
        if os.name == 'nt':
            (bin_dir / f'{tool}.bat').write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
        else:
            launcher = bin_dir / tool
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
            launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return bin_dir


@pytest.fixture
def ws(tmp_path, fake_ffmpeg_dir):
    """Workspace that uses the fake ffmpeg."""
    return make_workspace(tmp_path, fake_ffmpeg_dir)


@pytest.fixture(autouse=True)
def own_app_log(tmp_path, monkeypatch):
    """Tests that run CritterVid in this process log to their own folder, never
    the user's app log, and leave logging switched off afterwards."""
    monkeypatch.setenv('CRITTERVID_LOG_DIR', str(tmp_path / 'app-logs'))
    yield
    from crittervid import log
    log.stop_run()


def require(available, what):
    """Skip the test if a dependency is missing, or fail when CRITTERVID_REQUIRE_DEPS
    is set (as on CI), so a broken install can't silently skip tests."""
    if available:
        return
    if os.environ.get('CRITTERVID_REQUIRE_DEPS'):
        pytest.fail(f'{what} not available (required by CRITTERVID_REQUIRE_DEPS)')
    pytest.skip(f'{what} not available')


@pytest.fixture
def real_ws(tmp_path):
    """Workspace that uses the real ffmpeg and ffprobe from PATH."""
    require(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe on PATH')
    return make_workspace(tmp_path)
