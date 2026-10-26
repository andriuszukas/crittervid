"""
Finding FFmpeg and ffprobe, and passing paths to them
"""
import os
import sys
import shutil


def app_dir():
    """Where a bundled or hand-placed ffmpeg is looked for: the PyInstaller
    bundle's folder (_internal) when frozen, otherwise the project folder, the
    one holding src/ (or this package's folder, if it isn't under src/)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    here = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(here)
    return os.path.dirname(parent) if os.path.basename(parent) == 'src' else here

def media_arg(path):
    """A file path as FFmpeg and ffprobe should get it: absolute, so a colon in a
    relative path (e.g. cam:2/...) isn't taken for a protocol such as cam:."""
    return os.path.abspath(path)


# Paths can be in any encoding on Linux; Python keeps bytes that aren't UTF-8 as
# surrogates, and this error handler writes them back as the same bytes
PATH_TEXT = {'encoding': 'utf-8', 'errors': 'surrogateescape'}


def get_ffmpeg_path():
    """
    Find ffmpeg executable in the following order:
    1. The app's folder (see app_dir)
    2. System PATH
    3. Common installation locations
    """
    script_dir = app_dir()

    # Check in script directory first
    local_ffmpeg = os.path.join(script_dir, 'ffmpeg')
    if os.path.isfile(local_ffmpeg):
        return local_ffmpeg

    # Check with .exe extension (Windows)
    local_ffmpeg_exe = os.path.join(script_dir, 'ffmpeg.exe')
    if os.path.isfile(local_ffmpeg_exe):
        return local_ffmpeg_exe

    # Check in system PATH
    ffmpeg_in_path = shutil.which('ffmpeg')
    if ffmpeg_in_path:
        return ffmpeg_in_path

    # Check common Mac locations
    if sys.platform == 'darwin':
        mac_paths = [
            '/usr/local/bin/ffmpeg',
            '/opt/homebrew/bin/ffmpeg',
            '/usr/bin/ffmpeg',
        ]
        for path in mac_paths:
            if os.path.isfile(path):
                return path

    # Fallback to 'ffmpeg' and hope it's in PATH
    return 'ffmpeg'

def get_ffprobe_path():
    """
    Find ffprobe, which comes with FFmpeg: next to the ffmpeg in use first, then
    the same places as ffmpeg. Returns None if it can't be found.
    """
    ffmpeg = get_ffmpeg_path()
    folder = os.path.dirname(shutil.which(ffmpeg) or ffmpeg)
    for name in ('ffprobe', 'ffprobe.exe'):
        candidate = os.path.join(folder, name)
        if folder and os.path.isfile(candidate):
            return candidate
    script_dir = app_dir()
    for name in ('ffprobe', 'ffprobe.exe'):
        candidate = os.path.join(script_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which('ffprobe')
