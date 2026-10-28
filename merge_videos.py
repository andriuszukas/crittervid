"""Old name of crittervid.merge, kept so existing commands and imports keep
working until the package restructure is complete (docs/decisions/0002)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

if __name__ == '__main__':
    import runpy
    print("Note: 'merge_videos.py' is now 'crittervid merge' (or 'python -m crittervid merge'); "
          "this name will be removed in a later version.", file=sys.stderr)
    runpy.run_module('crittervid.merge', run_name='__main__', alter_sys=True)
else:
    import crittervid.merge as _module
    sys.modules[__name__] = _module
