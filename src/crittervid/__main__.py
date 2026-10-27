"""python -m crittervid: the crittervid command. Also the app bundle's entry point,
so it imports the package by name rather than relative to itself."""
import sys

from crittervid.cli import main

sys.exit(main())
