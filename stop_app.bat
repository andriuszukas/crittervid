@echo off
echo Stopping CritterVid...
taskkill /F /IM CritterVid.exe 2>nul
taskkill /F /FI "WINDOWTITLE eq streamlit*" 2>nul
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8501" ^| find "LISTENING"') do taskkill /F /PID %%a 2>nul
echo Done!
timeout /t 2