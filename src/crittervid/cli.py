"""The crittervid command: one entry point for every step of the pipeline.

    crittervid run       convert, merge and validate (each in this process)
    crittervid convert   .media files to .mp4
    crittervid merge     converted files into days
    crittervid validate  check the outputs
    crittervid gui       the graphical interface (also what no command opens)

Each command has its own options; `crittervid <command> --help` lists them.
"""
import importlib
import signal
import sys

from . import __version__, log

# Command name -> module whose main(argv) runs it
COMMANDS = {
    'run': 'pipeline',
    'convert': 'convert',
    'merge': 'merge',
    'validate': 'validate',
    'gui': 'gui.launch',
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
    module = importlib.import_module(f'{__package__}.{COMMANDS[command]}')
    try:
        return module.main(args)
    except KeyboardInterrupt:
        log.say(log.get(command), '=== Stopped by user (Ctrl+C) ===')
        return 1
    finally:
        log.stop_run()   # write out what's left, also after Ctrl+C
