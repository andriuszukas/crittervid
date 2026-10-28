import streamlit as st
import subprocess
import os
import sys
from tkinter import Tk, filedialog

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from crittervid.log import read_first_line  # noqa: E402

# The folder holding the crittervid package (src/ in a checkout), so the pipeline
# started below finds this copy of it even when it isn't installed
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pipeline_command():
    """The command that runs the pipeline: this app itself when frozen, so no
    separate Python is needed (#4), or this Python with the crittervid package."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, 'run']
    return [sys.executable, '-m', 'crittervid', 'run']

st.set_page_config(page_title="CritterVid", page_icon="🦜", layout="wide")


def _hostname(host_header):
    """Host header without its port: 'localhost:8501' -> 'localhost', '[::1]:8501' -> '::1'."""
    if host_header.startswith('['):
        return host_header[1:].split(']', 1)[0]
    return host_header.split(':', 1)[0]


# Only serve requests addressed to this computer. The server listens on localhost
# only, but a web page could still reach it through DNS rebinding (its own
# hostname pointed at 127.0.0.1), so refuse any other Host before anything runs.
# Browsers always send a Host header; it's absent only when the app runs without a
# web server (Streamlit's test harness), which a web page can't reach.
_host = st.context.headers.get("Host")
if _host is not None and _hostname(_host).lower() not in ("localhost", "127.0.0.1", "::1"):
    st.error("CritterVid only accepts connections from this computer. "
             "Open it at http://localhost:8501 (or the address the launcher printed).")
    st.stop()

st.title("🦜 CritterVid")
st.markdown("*Automated processing pipeline for wildlife & trail-camera footage*")

# Help section
with st.expander("📖 Help & Instructions"):
    st.markdown("""
    ### What does this pipeline do?
    This tool processes wildlife camera footage in three steps:
    1. **Convert** - Converts `.media` files to `.mp4` format
    2. **Merge** - Combines videos by date into daily clips
    3. **Validate** - Checks that every output has video and audio of the right length, and that merged days contain all their events

    ### Quick Start
    1. Select your **Input directory** containing `.media` files (typically from `DCIM` folder on camera)
    2. Select your **Output directory** where `.mp4` files will be saved
    3. Choose hardware acceleration (choosing your GPU explicitly is recommended)
    4. Click **Run** and wait for processing to complete

    ### Hardware Acceleration
    - **Auto** - Tries to detect your GPU, but currently can't detect NVIDIA cards, so choose your GPU explicitly
    - **NVIDIA** - Best for NVIDIA graphics cards
    - **Intel** - For Intel integrated graphics
    - **AMD** - For AMD graphics cards
    - **CPU** - Software encoding (slowest but works everywhere)

    ### Settings Explained
    - **Number of videos to process in parallel** - Higher = faster but uses more RAM (4 is a good default)
    - **How many mp4 files to generate per day** - Divides each day into N equal time windows, one file per window with footage (1 = one file per day)
    - **Merge everything again** - Merged files whose events haven't changed are normally skipped; tick this after upgrading to a version that merges differently, or if validation reports a problem with merged files
    - **Validate output** - Recommended; takes seconds and catches missing audio, wrong lengths and incomplete merges

    ### Requirements
    - FFmpeg must be installed (may need installing unless the build bundled it)
    - Sufficient disk space (output files are similar size to input)
    - GPU drivers installed for hardware acceleration

    ### Troubleshooting
    - **Slow processing?** Choose your GPU explicitly under Hardware Acceleration instead of CPU or Auto, or reduce parallel workers
    - **Out of memory?** Reduce number of parallel workers
    - **FFmpeg not found?** Install FFmpeg and add it to your PATH. In the packaged app you can also copy `ffmpeg.exe` into the `_internal` folder next to `CritterVid.exe`
    """)

st.header("Input/Output Folders")
st.caption(f"Working directory: {os.getcwd()}")

# Initialize session state defaults
if "input_dir_value" not in st.session_state:
    st.session_state["input_dir_value"] = "input/DCIM"
if "output_dir_value" not in st.session_state:
    st.session_state["output_dir_value"] = "output"

# Input directory with browse button
col1, col2 = st.columns([5, 1])
with col1:
    input_dir = st.text_input("Input directory", value=st.session_state["input_dir_value"], help="Relative or absolute path to input folder containing .media files")
with col2:
    st.markdown("<div style='margin-top: 25px;'></div>", unsafe_allow_html=True)
    if st.button("Browse", key="browse_input", use_container_width=True):
        root = Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        folder = filedialog.askdirectory(initialdir=os.getcwd(), title="Select Input Directory")
        root.destroy()
        if folder:
            st.session_state["input_dir_value"] = folder
            st.rerun()

# Update session state from manual input
if input_dir:
    st.session_state["input_dir_value"] = input_dir

# Output directory with browse button
col3, col4 = st.columns([5, 1])
with col3:
    output_dir = st.text_input("Output directory", value=st.session_state["output_dir_value"], help="Relative or absolute path for converted .mp4 files")
with col4:
    st.markdown("<div style='margin-top: 25px;'></div>", unsafe_allow_html=True)
    if st.button("Browse", key="browse_output", use_container_width=True):
        root = Tk()
        root.withdraw()
        root.wm_attributes('-topmost', 1)
        folder = filedialog.askdirectory(initialdir=os.getcwd(), title="Select Output Directory")
        root.destroy()
        if folder:
            st.session_state["output_dir_value"] = folder
            st.rerun()

# Update session state from manual input
if output_dir:
    st.session_state["output_dir_value"] = output_dir

st.header("Pipeline Parameters")
accel_display = st.selectbox("Hardware Acceleration", ["Auto", "CPU", "NVIDIA", "Intel", "AMD"], help="Select hardware acceleration method. NVidia GPUs are the best.", index=0)
workers = st.number_input("Number of videos to process in parallel", min_value=1, max_value=32, value=4, help="Number of videos to process in parallel.")
splits_per_day = st.number_input("How many mp4 files to generate per day?", min_value=1, max_value=24, value=1, help="Divides each day into this many equal time windows (2: 00:00-12:00 and 12:00-24:00), with one merged file for each window that has footage. Changing it and running again replaces each day's earlier merged files (files holding footage the new ones lack are kept, with a warning).")
force_merge = st.checkbox("Merge everything again", help="Merged files whose events haven't changed since they were made are normally skipped. Tick this to make them all again, e.g. after upgrading to a version that merges differently.", value=False)
validate = st.checkbox("Validate output after processing", help="Check that the outputs have video and audio of the right length and that merged days are complete. Takes seconds.", value=True)

st.header("Run Pipeline")
if st.button("Run"):
    # Map user-friendly names to command-line arguments
    accel_map = {"Auto": "auto", "CPU": "cpu", "NVIDIA": "cuda", "Intel": "qsv", "AMD": "amf"}
    accel = accel_map.get(accel_display, "auto")

    cmd = pipeline_command()
    if input_dir:
        cmd += ["--input-dir", input_dir]
    if output_dir:
        cmd += ["--output-dir", output_dir]
    if accel:
        cmd += ["--accel", accel]
    if workers:
        cmd += ["--workers", str(workers)]
    if splits_per_day:
        cmd += ["--splits-per-day", str(splits_per_day)]
    if force_merge:
        cmd.append("--force-merge")
    if not validate:
        cmd.append("--no-validate")

    st.write(f"Running: {' '.join(cmd)}")

    # Create placeholders for progress and output
    progress_placeholder = st.empty()
    output_placeholder = st.empty()
    output_text = ""
    line_count = 0
    run = None   # (run ID, log path) from the pipeline's first line

    # Run process with live output streaming. These settings are inherited by every
    # pipeline stage: PYTHONUNBUFFERED so output arrives promptly and in order, and
    # PYTHONIOENCODING so stdout and stderr (including tracebacks raised before a
    # script reconfigures its streams) are UTF-8, matching how they're decoded below
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8:replace")
    if not getattr(sys, 'frozen', False):
        env['PYTHONPATH'] = os.pathsep.join([PACKAGE_PARENT] + [p for p in env.get('PYTHONPATH', '').split(os.pathsep) if p])
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        # The pipeline scripts write UTF-8 regardless of the system code page
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )

    # Stream output line by line, update UI every 5 lines
    for line in process.stdout:
        output_text += line
        line_count += 1
        if run is None and line_count == 1:
            run = read_first_line(line)

        # Update UI every 5 lines or on important messages
        if line_count % 5 == 0 or "===" in line or "ERROR" in line or "completed" in line:
            progress_placeholder.info(f"📊 Processing... ({line_count} lines)")
            # Show last 100 lines to keep recent output visible
            lines = output_text.split('\n')
            display_text = '\n'.join(lines[-100:]) if len(lines) > 100 else output_text
            output_placeholder.code(display_text, language=None)

    # Final update to show all output
    output_placeholder.code(output_text, language=None)
    process.wait()

    # Clear progress indicator and show final status
    progress_placeholder.empty()
    if process.returncode == 0:
        st.success("✅ Pipeline completed successfully!")
    else:
        st.error(f"❌ Pipeline failed with exit code {process.returncode}")
    if run:
        run_id, log_path = run
        st.caption(f"Run {run_id}" + (f" · log: {log_path}" if log_path else " · no log file")
                   + " (quote the run ID when reporting a problem)")
