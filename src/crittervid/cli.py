"""The crittervid command: one entry point for every step of the pipeline.

    crittervid run       convert, merge and validate (each in this process)
    crittervid convert   .media files to .mp4
    crittervid merge     converted files into days
    crittervid validate  check the outputs
    crittervid gui       the graphical interface (also what no command opens)
    crittervid doctor    check FFmpeg, the encoders, the output folder and disk space

Each command has its own options; `crittervid <command> --help` lists them.
"""
import importlib
import logging
import signal
import sys

from . import __version__, log
from .steps import STEPS

# Command name -> module whose main(argv) runs it: a whole run, each step, the GUI
COMMANDS = {
    'run': 'crittervid.pipeline',
    **{step.name: step.module for step in STEPS},
    'gui': 'crittervid.gui.launch',
    'doctor': 'crittervid.doctor',
}


def usage():
    return (__doc__.strip() + '\n')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:   # e.g. the app opened by double-clicking it
        argv = ['gui']
    if argv[0] in ('-h', '--help'):
        print(usage())
        return 0
    if argv[0] in ('-V', '--version'):
        print(f'crittervid {__version__}')
        return 0
    command, args = argv[0], argv[1:]
    if command not in COMMANDS:
        print(f"crittervid: unknown command '{command}'\n\n{usage()}", file=sys.stderr)
        return 2
    # On Windows, Ctrl+Break (how a program stops a console child) stops a run
    # like Ctrl+C
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    module = importlib.import_module(COMMANDS[command])
    try:
        return module.main(args)
    except KeyboardInterrupt:
        if log.current() is None:   # stopped before a run started (e.g. the GUI's server)
            print('=== Stopped by user (Ctrl+C) ===')
        else:
            log.say(log.get(command), '=== Stopped by user (Ctrl+C) ===', level=logging.WARNING)
        return 1
    finally:
        log.stop_run()   # write out what's left, also after Ctrl+C
