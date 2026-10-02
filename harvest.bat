@echo off
rem Unattended harvesting: keep searching until N strategy ideas are good enough to backtest.
rem
rem   harvest.bat                          asks for the request, target 5, up to 5 rounds
rem   harvest.bat "request here" 10 8      target 10 ideas, up to 8 rounds
rem
rem Each round searches, reads documents, extracts and scores, and searches for replication and
rem contradiction evidence. It stops on its own when the target is reached, when progress stalls,
rem or when the daily AI budget runs out (it then waits for the budget to reset and continues).
rem Safe to close the window at any time: the campaign stores its progress and `--resume ID` continues.
title QSD harvest
cd /d "%~dp0"

if not exist ".venv\Scripts\qsd.exe" (
    echo Could not find .venv\Scripts\qsd.exe in %CD%
    echo Install first: python -m venv .venv  then  .venv\Scripts\pip install -e ".[dev]"
    pause
    exit /b 1
)

set "REQUEST=%~1"
set "TARGET=%~2"
set "ROUNDS=%~3"
if "%TARGET%"=="" set "TARGET=5"
if "%ROUNDS%"=="" set "ROUNDS=5"

if "%REQUEST%"=="" (
    echo What should it look for? For example: intraday breakout strategies in forex
    set /p REQUEST=Request: 
)
if "%REQUEST%"=="" (
    echo No request given, stopping.
    pause
    exit /b 1
)

echo.
echo Request : %REQUEST%
echo Target  : %TARGET% promising idea(s)
echo Rounds  : up to %ROUNDS% (each round reads 8 documents)
echo.
echo Reading a video needs captions enabled. To enable them, add this to config\local.yaml:
echo     discovery:
echo       youtube_transcripts: true
echo.
pause

".venv\Scripts\qsd.exe" harvest "%REQUEST%" --target %TARGET% --max-rounds %ROUNDS% --docs 8
echo.
echo Harvest finished. Results: dashboard or `qsd score`. If you see an error above, send a screenshot.
pause
