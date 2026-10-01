@echo off
rem Double-click to start a research run: describe what to look for, then everything runs by itself
rem (search -> read papers -> extract -> fact-check -> score -> follow-up search -> backtest queue),
rem with progress here and on the live monitor in your browser.
title QSD research
cd /d "%~dp0"

if not exist ".venv\Scripts\qsd.exe" (
    echo Could not find .venv\Scripts\qsd.exe in %CD%
    echo Install first: python -m venv .venv  then  .venv\Scripts\pip install -e ".[dev]"
    pause
    exit /b 1
)

".venv\Scripts\qsd.exe" research
echo.
echo Research window finished. If you see an error above, send a screenshot of it.
pause
