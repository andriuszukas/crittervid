"""
Finding FFmpeg and ffprobe, and passing paths to them
"""
import functools
import os
import sys
import shutil
import subprocess
import threading


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


@functools.lru_cache(maxsize=None)
def get_ffmpeg_path():
    """
    Find ffmpeg executable in the following order (once, when first needed):
    1. CRITTERVID_FFMPEG, if set
    2. The app's folder (see app_dir)
    3. System PATH
    4. Common installation locations
    """
    if os.environ.get('CRITTERVID_FFMPEG'):
        return os.environ['CRITTERVID_FFMPEG']
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

@functools.lru_cache(maxsize=None)
def get_ffprobe_path():
    """
    Find ffprobe, which comes with FFmpeg: CRITTERVID_FFPROBE if set, next to the
    ffmpeg in use, then the same places as ffmpeg. Returns None if it can't be
    found.
    """
    if os.environ.get('CRITTERVID_FFPROBE'):
        return os.environ['CRITTERVID_FFPROBE']
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


def tool_version(path):
    """The version FFmpeg or ffprobe reports (e.g. '9.0.2-full_build-www.gyan.dev'),
    or None if it can't be run."""
    try:
        result = subprocess.run([path, '-version'], capture_output=True, text=True, timeout=20,
                                encoding='utf-8', errors='replace')
    except (OSError, subprocess.SubprocessError):
        return None
    words = result.stdout.split()
    return words[2] if len(words) > 2 and words[1] == 'version' else None


# The encoder each --accel choice uses
ENCODERS = {'cpu': 'libx264', 'cuda': 'h264_nvenc', 'qsv': 'h264_qsv', 'amf': 'h264_amf', 'vaapi': 'h264_vaapi'}
VAAPI_DEVICE = '/dev/dri/renderD128'


def encoder_works(accel):
    """Whether this machine's FFmpeg can encode with an --accel choice's encoder:
    (True, '') or (False, why). Tries a tiny generated clip, so it says whether
    the GPU and its driver work, not only whether FFmpeg knows the encoder."""
    from . import log
    clip = ['-f', 'lavfi', '-i', 'color=c=black:s=256x256:r=15:d=0.4']
    if accel == 'vaapi':
        cmd = ['-vaapi_device', VAAPI_DEVICE, *clip, '-vf', 'format=nv12,hwupload']
    else:
        cmd = clip
    cmd = [get_ffmpeg_path(), '-v', 'error', '-y', *cmd, '-frames:v', '5', '-c:v', ENCODERS[accel], '-f', 'null', '-']
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, 'the test encode took too long'
    except OSError as e:
        return False, str(e)
    if result.returncode == 0:
        return True, ''
    return False, encoder_failure(ENCODERS[accel], result.stderr)


def encoder_failure(encoder, stderr):
    """Why an encoder couldn't be used: its own first error line, such as
    "Cannot load nvcuda.dll" (no NVIDIA driver) or "Error creating a MFX
    session", which FFmpeg's later lines ("Could not open encoder before EOF")
    don't say; otherwise the line logging picks."""
    from . import log
    own = f'[{encoder} @'
    for line in stderr.decode('utf-8', errors='replace').splitlines():
        line = line.strip()
        if line.startswith(own) and ']' in line:
            reason = line.split(']', 1)[1].strip()
            if reason:
                return reason
    return log.ffmpeg_failure(stderr)[0]


# FFmpeg options for each --accel choice: (before -i, after -i). VAAPI decodes on
# the CPU and uploads the frames to the GPU, which works with any input
ACCEL_OPTIONS = {
    'cpu': ([], []),
    'cuda': (['-hwaccel', 'cuda'], ['-c:v', 'h264_nvenc']),
    'qsv': (['-hwaccel', 'qsv'], ['-c:v', 'h264_qsv']),
    'vaapi': (['-vaapi_device', VAAPI_DEVICE], ['-vf', 'format=nv12,hwupload', '-c:v', 'h264_vaapi']),
    'amf': (['-hwaccel', 'dxva2'], ['-c:v', 'h264_amf']),
}
# What --accel auto tries, in order: the discrete GPUs' encoders (NVIDIA, AMD)
# before Intel's integrated one, then VAAPI (Linux); the CPU if none works
ACCEL_ORDER = ('cuda', 'amf', 'qsv', 'vaapi')

_detect_lock = threading.Lock()
_detected = None


def detect_accel():
    """--accel auto: the first encoder in ACCEL_ORDER that encodes a test clip on
    this machine (see encoder_works), or 'cpu'. Worked out once per run, when
    first needed."""
    global _detected
    with _detect_lock:
        if _detected is None:
            _detected = next((accel for accel in ACCEL_ORDER
                              if (accel != 'vaapi' or sys.platform.startswith('linux')) and encoder_works(accel)[0]),
                             'cpu')
        return _detected
