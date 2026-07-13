"""
Helper module to locate ffmpeg executable
"""
import os
import sys
import shutil

def get_ffmpeg_path():
    """
    Find ffmpeg executable in the following order:
    1. Same directory as script
    2. System PATH
    3. Common installation locations
    """
    # Get script directory
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle
        script_dir = sys._MEIPASS
    else:
        # Running in normal Python
        script_dir = os.path.dirname(os.path.abspath(__file__))

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