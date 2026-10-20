@echo off
echo ========================================
echo CritterVid - Build Script
echo ========================================
echo.

REM Run from this script's folder, so relative paths work wherever it's started
cd /d "%~dp0"

REM Check if pyinstaller is installed
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller not found. Installing...
    pip install pyinstaller==6.11.1
    if errorlevel 1 (
        echo Failed to install PyInstaller!
        pause
        exit /b 1
    )
)

echo Cleaning previous build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Building standalone executable...
pyinstaller crittervid.spec

if errorlevel 1 (
    echo.
    echo Build FAILED!
    pause
    exit /b 1
)

echo.
echo ========================================
echo Build completed successfully!
echo ========================================
echo.
echo The standalone application is in: dist\CritterVid\
echo.
echo To run: dist\CritterVid\CritterVid.exe
echo.
if exist ffmpeg.exe (
    echo FFmpeg was bundled from ffmpeg.exe in the project folder.
) else (
    echo NOTE: FFmpeg was not bundled. Users need FFmpeg on their PATH, or
    echo copy ffmpeg.exe and ffprobe.exe into dist\CritterVid\_internal\ before zipping.
)
echo.
pause