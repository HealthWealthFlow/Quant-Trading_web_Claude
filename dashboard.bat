@echo off
rem Double-click to open the QSD (Quant Strategy Discovery) dashboard, read-only, in your browser.
rem Keep this window open while you use the dashboard; close it (or press Ctrl+C) to stop.
title QSD dashboard
cd /d "%~dp0"

rem Port for this dashboard. Change it here if another program needs 8877.
set "PORT=8877"
set "URL=http://127.0.0.1:%PORT%/"

rem Something already listening on the port?
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul
if errorlevel 1 goto start_server
curl -s -m 5 "%URL%" | findstr /c:"Quant Strategy Discovery" >nul
if errorlevel 1 (
    echo Port %PORT% is used by another program, not by the QSD dashboard.
    echo Edit dashboard.bat in Notepad and change "set PORT=%PORT%" to another number, e.g. 8878.
    pause
    exit /b 1
)
echo QSD dashboard is already running. Opening %URL%
start "" "%URL%"
timeout /t 3 >nul
exit /b 0

:start_server
if not exist ".venv\Scripts\qsd.exe" (
    echo Could not find .venv\Scripts\qsd.exe in %CD%
    echo Install first: python -m venv .venv  then  .venv\Scripts\pip install -e ".[dev]"
    pause
    exit /b 1
)

rem Open the browser a few seconds after the server starts.
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start "" %URL%"

echo Starting QSD dashboard at %URL%
echo Keep this window open while you use the dashboard. Close it to stop.
echo.
".venv\Scripts\qsd.exe" web --port %PORT%
echo.
echo The dashboard stopped. If you see an error above, send a screenshot of it.
pause
