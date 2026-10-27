"""Old name of `crittervid gui`, kept until the package restructure is complete
(docs/decisions/0002). Runs from a checkout without installing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'src'))
from crittervid.cli import main  # noqa: E402

sys.exit(main(['gui']))
