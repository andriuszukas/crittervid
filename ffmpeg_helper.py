"""Old name of crittervid.ffmpeg, kept so existing scripts and imports keep
working until the package restructure is complete (docs/decisions/0002)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
import crittervid.ffmpeg as _module  # noqa: E402

sys.modules[__name__] = _module
