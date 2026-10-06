import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS = ['ffmpeg_helper.py', 'convert_media.py', 'merge_videos.py', 'validate_output.py', 'process_all.py']

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

    def run(self, script, *args):
        return subprocess.run(
            [sys.executable, script, *args],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )


def make_workspace(tmp_path, path_prefix=None):
    app = tmp_path / 'app'
    app.mkdir()
    # Copy the scripts so an ffmpeg.exe sitting in the repo folder can't take
    # precedence over the ffmpeg chosen for the test
    for name in SCRIPTS:
        shutil.copy(REPO_ROOT / name, app)
    env = dict(os.environ)
    env.pop('PYTHONUNBUFFERED', None)
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


@pytest.fixture
def real_ws(tmp_path):
    """Workspace that uses the real ffmpeg from PATH; skips if it isn't installed."""
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg not found on PATH')
    return make_workspace(tmp_path)
