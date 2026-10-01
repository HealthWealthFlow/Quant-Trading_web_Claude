@echo off
rem Double-click to open the QSD dashboard (read-only) in your browser.
rem Keep this window open while you use the dashboard; close it (or press Ctrl+C) to stop.
title QSD dashboard
cd /d "%~dp0"
set "URL=http://127.0.0.1:8765/"

rem Already running? Just open the browser.
netstat -ano | findstr /r /c:":8765 .*LISTENING" >nul
if not errorlevel 1 (
    echo Dashboard is already running. Opening %URL%
    start "" "%URL%"
    timeout /t 3 >nul
    exit /b 0
)

if not exist ".venv\Scripts\qsd.exe" (
    echo Could not find .venv\Scripts\qsd.exe in %CD%
    echo Install first: python -m venv .venv  then  .venv\Scripts\pip install -e ".[dev]"
    pause
    exit /b 1
)

rem Open the browser a few seconds after the server starts.
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start "" %URL%"

echo Starting dashboard at %URL%
echo Keep this window open while you use the dashboard. Close it to stop.
echo.
".venv\Scripts\qsd.exe" web
echo.
echo The dashboard stopped. If you see an error above, send a screenshot of it.
pause
