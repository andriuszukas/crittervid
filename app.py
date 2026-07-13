import streamlit as st
import subprocess
import os
import sys
from tkinter import Tk, filedialog

# Get the correct script directory (works both in dev and frozen exe)
if getattr(sys, 'frozen', False):
    # Running in PyInstaller bundle
    SCRIPT_DIR = sys._MEIPASS
else:
    # Running in normal Python
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="CritterVid", page_icon="🦜", layout="wide")

st.title("🦜 CritterVid")
st.markdown("*Automated processing pipeline for wildlife & trail-camera footage*")

# Help section
with st.expander("📖 Help & Instructions"):
    st.markdown("""
    ### What does this pipeline do?
    This tool processes wildlife camera footage in three steps:
    1. **Convert** - Converts `.media` files to `.mp4` format
    2. **Merge** - Combines videos by date into daily clips
    3. **Validate** - Checks that output files are playable

    ### Quick Start
    1. Select your **Input directory** containing `.media` files (typically from `DCIM` folder on camera)
    2. Select your **Output directory** where `.mp4` files will be saved
    3. Choose hardware acceleration (Auto recommended)
    4. Click **Run** and wait for processing to complete

    ### Hardware Acceleration
    - **Auto** - Automatically detects your GPU (recommended)
    - **NVIDIA** - Best for NVIDIA graphics cards
    - **Intel** - For Intel integrated graphics
    - **AMD** - For AMD graphics cards
    - **CPU** - Software encoding (slowest but works everywhere)

    ### Settings Explained
    - **Number of videos to process in parallel** - Higher = faster but uses more RAM (4 is a good default)
    - **How many mp4 files to generate per day** - Splits each day's footage into N files (1 = one file per day)
    - **Validate output** - Recommended to ensure files are playable

    ### Requirements
    - FFmpeg must be installed (included with standalone executable)
    - Sufficient disk space (output files are similar size to input)
    - GPU drivers installed for hardware acceleration

    ### Troubleshooting
    - **Slow processing?** Enable GPU acceleration or reduce parallel workers
    - **Out of memory?** Reduce number of parallel workers
    - **FFmpeg not found?** Ensure FFmpeg is in your PATH or in the application folder
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
splits_per_day = st.number_input("How many mp4 files to generate per day?", min_value=1, max_value=24, value=1, help="Number of videosplits to create per day.")
validate = st.checkbox("Validate output after processing", help="Validate if the output mp4 files are playable after processing.", value=True)

st.header("Run Pipeline")
if st.button("Run"):
    # Map user-friendly names to command-line arguments
    accel_map = {"Auto": "auto", "CPU": "cpu", "NVIDIA": "cuda", "Intel": "qsv", "AMD": "amf"}
    accel = accel_map.get(accel_display, "auto")

    # Use the correct path to process_all.py
    process_script = os.path.join(SCRIPT_DIR, "process_all.py")

    # In frozen exe, we need to call python directly on the script
    # In dev, sys.executable is the python interpreter
    if getattr(sys, 'frozen', False):
        # Running as frozen exe - call python on the script
        cmd = ["python", process_script]
    else:
        # Running in development - use sys.executable
        cmd = [sys.executable, process_script]
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
    if not validate:
        cmd.append("--no-validate")

    st.write(f"Running: {' '.join(cmd)}")

    # Create placeholders for progress and output
    progress_placeholder = st.empty()
    output_placeholder = st.empty()
    output_text = ""
    line_count = 0

    # Run process with live output streaming
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    # Stream output line by line, update UI every 5 lines
    for line in process.stdout:
        output_text += line
        line_count += 1

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
