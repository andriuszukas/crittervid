import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS = ['ffmpeg_helper.py', 'media_format.py', 'convert_media.py', 'merge_videos.py', 'validate_output.py', 'process_all.py',
           'app.py']

# Matches the camera layout: DCIM/YYYY/MM/DD/<unixtimestamp>_<groupid>/
GROUP_DIR = Path('input/DCIM/2026/10/01/1759300000_0001')


class Workspace:
    """A temp copy of the pipeline scripts, run as subprocesses like the GUI does."""

    def __init__(self, root, env):
        self.root = root
        self.env = env

    def add_media(self, name, content):
        path = self.root / GROUP_DIR / name
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
        """Command lines the fake ffmpeg received, when FAKE_FFMPEG_LOG is set."""
        log = Path(self.env['FAKE_FFMPEG_LOG'])
        return [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]

    def run(self, script, *args):
        return subprocess.run(
            [sys.executable, script, *args],
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
    env = dict(os.environ)
    # Run the scripts as they'd run on a plain machine: these variables would hide
    # buffering and encoding bugs the tests are meant to catch
    for var in ('PYTHONUNBUFFERED', 'PYTHONUTF8', 'PYTHONIOENCODING'):
        env.pop(var, None)
    if path_prefix:
        env['PATH'] = str(path_prefix) + os.pathsep + env['PATH']
    return Workspace(app, env)


@pytest.fixture
def fake_ffmpeg_dir(tmp_path):
    """A directory holding an `ffmpeg` command that runs tests/fake_ffmpeg.py."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    fake = TESTS_DIR / 'fake_ffmpeg.py'
    if os.name == 'nt':
        (bin_dir / 'ffmpeg.bat').write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
    else:
        launcher = bin_dir / 'ffmpeg'
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n')
        launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    return bin_dir


@pytest.fixture
def ws(tmp_path, fake_ffmpeg_dir):
    """Workspace that uses the fake ffmpeg."""
    return make_workspace(tmp_path, fake_ffmpeg_dir)


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
