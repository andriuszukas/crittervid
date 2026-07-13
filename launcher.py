"""
Launcher script for CritterVid
This launches the Streamlit app properly
"""
import sys
import os
from streamlit.web import cli as stcli

if __name__ == '__main__':
    # Get the directory where the executable is located
    if getattr(sys, 'frozen', False):
        # Running in PyInstaller bundle
        app_dir = sys._MEIPASS
    else:
        # Running in normal Python environment
        app_dir = os.path.dirname(os.path.abspath(__file__))

    # Path to app.py
    app_path = os.path.join(app_dir, 'app.py')

    # Set Streamlit config directory
    config_dir = os.path.join(app_dir, '.streamlit')
    os.environ['STREAMLIT_CONFIG_PATH'] = config_dir

    # Find an available port starting from 8501
    import socket
    def find_free_port(start_port=8501, max_tries=10):
        for port in range(start_port, start_port + max_tries):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(('', port))
                    return port
            except OSError:
                continue
        return start_port  # Fallback to default

    port = find_free_port()

    # Launch streamlit with explicit options
    sys.argv = [
        'streamlit',
        'run',
        app_path,
        '--global.developmentMode=false',
        f'--server.port={port}',
        '--server.headless=true',
        '--server.enableCORS=false',
        '--server.enableXsrfProtection=false',
        '--browser.serverAddress=localhost',
        '--browser.gatherUsageStats=false',
    ]
    sys.exit(stcli.main())