"""crittervid gui: starts the Streamlit app on a free port of this computer."""
import errno
import os
import socket
import sys

from streamlit.web import cli as stcli

# Errors that mean an IPv6 address can't be used on this machine at all, as
# opposed to the port being taken
_NO_IPV6 = {getattr(errno, name) for name in
            ('EADDRNOTAVAIL', 'EAFNOSUPPORT', 'WSAEADDRNOTAVAIL', 'WSAEAFNOSUPPORT')
            if hasattr(errno, name)}


def port_is_free(port):
    """True if no program listens on the port on any local IPv4 or IPv6 address.

    Checking 127.0.0.1 alone isn't enough: on Windows that bind succeeds even
    when another program listens on all addresses, and Streamlit also binds ::1.
    """
    for family, address in ((socket.AF_INET, '0.0.0.0'), (socket.AF_INET, '127.0.0.1'),
                            (socket.AF_INET6, '::'), (socket.AF_INET6, '::1')):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.bind((address, port))
        except OSError as e:
            if family == socket.AF_INET6 and e.errno in _NO_IPV6:
                continue
            return False
    return True


def find_free_port(start_port=8501, max_tries=10):
    for port in range(start_port, start_port + max_tries):
        if port_is_free(port):
            return port
    return start_port  # Fallback to default


def main(argv=None):
    # The app ships next to this module: in a checkout, an install, or the app
    # bundle (where it's a data file in crittervid/gui/)
    app_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'streamlit_app.py')

    # Find an available port starting from 8501
    port = find_free_port()

    # Launch streamlit with explicit options. These flags are what configure the
    # packaged app: Streamlit only reads .streamlit/config.toml from the current
    # folder or the user's home, not from the bundle.
    sys.argv = [
        'streamlit',
        'run',
        app_path,
        '--global.developmentMode=false',
        f'--server.port={port}',
        # Listen on this computer only; the GUI must not be reachable from the network
        '--server.address=localhost',
        '--server.headless=true',
        '--browser.serverAddress=localhost',
        '--browser.gatherUsageStats=false',
    ]
    return stcli.main()
