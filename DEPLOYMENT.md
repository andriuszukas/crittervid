# CritterVid - Deployment Guide

## Building Standalone Executable

### Prerequisites for Building (Developers Only)
- Python 3.7 or higher installed on your **development machine**
- PyInstaller (automatically installed by build.bat)
- FFmpeg installed and in PATH (for testing)

**Note:** End users do NOT need Python installed - it's bundled in the .exe!

### Build Steps

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the build script:**
   ```bash
   build.bat
   ```

3. **Find your executable:**
   - Location: `dist\CritterVid\`
   - Main executable: `CritterVid.exe`
   - Size: ~188 MB (includes Python 3.13 runtime + all dependencies)

### Distribution

To distribute the application to end users:

1. **Copy the entire folder:**
   ```
   dist\CritterVid\
   ```

2. **Include FFmpeg (users need this):**
   - Either: Add `ffmpeg.exe` to the `CritterVid` folder
   - Or: Instruct users to install FFmpeg separately and add to PATH

3. **Zip and share:**
   ```bash
   # Zip the folder
   tar -a -c -f CritterVid.zip CritterVid
   ```

4. **Users need:**
   - ✅ The zip file (no Python installation required!)
   - ✅ FFmpeg (separate download)
   - ✅ Windows 10 or higher

### Running the Application

**Option 1: Standalone executable (for end users)**
```bash
cd dist\CritterVid
CritterVid.exe
```
End users do NOT need Python installed - everything is bundled in the .exe!

**Option 2: Direct Python (for development only)**
```bash
python app.py
# or
run_app.bat
```

The Streamlit UI will open in your default browser.

## Running Without Building (Manual Setup)

If you want to run the application directly without building the standalone .exe, you need:

### Prerequisites

**1. Python 3.7 or higher**
```bash
python --version  # Check if installed
```
Download from: https://www.python.org/downloads/

**2. Install Python Dependencies**
```bash
cd crittervid
pip install -r requirements.txt
```

This installs:
- streamlit (Web UI framework)
- GPUtil (GPU detection)
- WMI (Windows Management Interface)
- pyinstaller (only needed for building .exe)

**3. FFmpeg (Required)**
- Download: https://ffmpeg.org/download.html
- Either add to system PATH, or place `ffmpeg.exe` in the project folder

### Running the Application

**Option 1: Quick Launch (recommended)**
```bash
run_app.bat
```

**Option 2: Streamlit Command**
```bash
streamlit run app.py
```

**Option 3: Run Pipeline Scripts Directly**
```bash
# Full pipeline
python process_all.py --input-dir input/DCIM --output-dir output

# Individual steps
python convert_media.py --input-dir input/DCIM --output-dir output --accel cuda
python merge_videos.py --output-dir output --splits-per-day 2
python validate_output.py --output-dir output --accel cuda
```

**Note:** The standalone .exe is only needed for distribution to users who don't have Python installed.

## System Requirements

### Minimum
- Windows 10 or higher
- 4GB RAM
- FFmpeg installed

### Recommended
- Windows 10/11
- 8GB+ RAM
- NVIDIA GPU with CUDA support (for hardware acceleration)
- SSD storage

## GPU Acceleration

The application supports hardware-accelerated video processing:
- **NVIDIA**: CUDA (`--accel cuda`)
- **Intel**: Quick Sync Video (`--accel qsv`)
- **AMD**: AMF (`--accel amf`)
- **Auto-detect**: Automatically detects available GPU

## Troubleshooting

### "FFmpeg not found"
- Install FFmpeg: https://ffmpeg.org/download.html
- Add FFmpeg to system PATH
- Or place `ffmpeg.exe` in the application folder

### "Module not found" errors
- Rebuild with: `build.bat`
- Check all dependencies installed: `pip install -r requirements.txt`

### Slow processing
- Enable GPU acceleration in the UI
- Reduce number of workers
- Use `--copy` mode if video codec is compatible

## Command-Line Usage

All scripts can be run directly:

```bash
# Convert only
python convert_media.py --input-dir input/DCIM --output-dir output --accel cuda

# Merge only
python merge_videos.py --output-dir output --splits-per-day 2

# Validate only
python validate_output.py --output-dir output --accel cuda

# Full pipeline
python process_all.py --input-dir input/DCIM --output-dir output --accel cuda

# Retry failed conversions
python convert_media.py --retry-failed --max-retries 5
```

## Notes

- The standalone executable is **Windows-only**
- For Linux/Mac, use Python directly
- First run may be slower (Streamlit initialization)
- The application requires internet connection for Streamlit components