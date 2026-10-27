@echo off
echo Stopping CritterVid...

REM Packaged app: stop CritterVid.exe and everything it started (/T), such as
REM the pipeline and FFmpeg
taskkill /F /T /IM CritterVid.exe >nul 2>&1

REM Development mode: find the GUI by its command line, whatever port it chose,
REM instead of killing whatever listens on 8501: "launcher.py" (run_app.bat),
REM "crittervid gui" (with or without -m) or "streamlit run app.py". The script
REM must be the one being run (optionally with a folder), not just mentioned later
REM on the command line. \x22 is a double quote, written as a regex escape so it
REM doesn't end the quoted -Command string.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and ($_.CommandLine -match 'streamlit(\.exe)?\W+run\s+(\x22([^\x22]*[\\/])?app\.py\x22|([^\x22\s]*[\\/])?app\.py(\s|$))' -or $_.CommandLine -match 'python(\.exe)?\x22?\s+(\x22([^\x22]*[\\/])?launcher\.py\x22|([^\x22\s]*[\\/])?launcher\.py(\s|$))' -or $_.CommandLine -match 'crittervid(\.exe)?\x22?\s+gui(\s|$)') } | ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>&1 | Out-Null }"

echo Done!
timeout /t 2
