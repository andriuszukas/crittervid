"""Old name of crittervid.convert, kept so existing commands and imports keep
working until the package restructure is complete (docs/decisions/0002)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))

if __name__ == '__main__':
    import runpy
    runpy.run_module('crittervid.convert', run_name='__main__', alter_sys=True)
else:
    import crittervid.convert as _module
    sys.modules[__name__] = _module
