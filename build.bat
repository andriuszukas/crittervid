@echo off
echo ========================================
echo CritterVid - Build Script
echo ========================================
echo.

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
echo IMPORTANT: Make sure ffmpeg.exe is in your PATH or in the same folder!
echo.
pause