@echo off
rem ============================================================================================
rem  QSD - find strategy ideas automatically
rem
rem  Double-click this file. It asks what to look for, then works on its own:
rem    - searches the web, papers, GitHub and YouTube
rem    - reads what it finds and extracts the rules
rem    - fact-checks every value against the source, and drops anything it cannot verify
rem    - keeps going until it has enough ideas, or the AI budget runs out
rem    - saves the ideas as notes, grouped into folders
rem
rem  Safe to close the window: progress is stored, and `qsd harvest --resume <id>` continues it.
rem
rem  This is the RUN button. `auto_idea_finder.bat` is the separate "show me the dashboard" button.
rem ============================================================================================
title QSD - find strategy ideas
cd /d "%~dp0"

if not exist ".venv\Scripts\qsd.exe" (
    echo.
    echo   Could not find .venv\Scripts\qsd.exe in %CD%
    echo   Install first:  python -m venv .venv
    echo                   .venv\Scripts\pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

rem ---- keys: read from the user environment, never from this file ---------------------------------
rem The helper prints `set "NAME=value"` lines; only names are echoed, never the values. The PowerShell path
rem is used unquoted because it holds no spaces - quoting it inside a `for /f` command makes cmd treat it as a
rem filename (measured: forms A and B fail with "filename, directory name, or volume label syntax is incorrect").
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
for /f "usebackq delims=" %%S in (`%PS% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\read_keys.ps1"`) do %%S

if not defined DEEPSEEK_API_KEY (
    echo.
    echo   DEEPSEEK_API_KEY is not set, so nothing can be read or extracted.
    echo   Set it once, in any Command Prompt:
    echo       setx DEEPSEEK_API_KEY "your-key-here"
    echo   then open this file again.
    echo.
    pause
    exit /b 1
)

rem ---- ask what to look for ----------------------------------------------------------------------
echo.
echo   ================================================================
echo     QSD - automatic strategy idea finder
echo   ================================================================
echo.
echo   What kind of strategy ideas should I look for?
echo   Example: intraday breakout strategies in forex
echo.
set "REQUEST="
set /p "REQUEST=  Search for: "
if "%REQUEST%"=="" (
    echo.
    echo   Nothing entered, stopping.
    pause
    exit /b 1
)

echo.
set "TARGET=10"
set "TARGET_IN="
set /p "TARGET_IN=  How many good ideas (default 10): "
if not "%TARGET_IN%"=="" set "TARGET=%TARGET_IN%"

if defined FIRECRAWL_API_KEY (set "CHANNELS=papers + GitHub + YouTube + web") else (set "CHANNELS=papers + GitHub + YouTube")

echo.
echo   Request  : %REQUEST%
echo   Target   : %TARGET% idea(s) worth backtesting
echo   Channels : %CHANNELS%
echo   Daily AI budget stops it if it runs out, and it waits for the reset rather than overspending.
echo.
echo   ----------------------------------------------------------------
echo   It now works on its own. To watch progress, open a second
echo   window and run auto_idea_finder.bat for the dashboard.
echo   ----------------------------------------------------------------
echo.

rem ---- run ---------------------------------------------------------------------------------------
set "MAXR=%QSD_MAX_ROUNDS%"
if "%MAXR%"=="" set "MAXR=500"
set "SLEEP=%QSD_SLEEP%"
if "%SLEEP%"=="" set "SLEEP=0"
set "MAXSLEEP=%QSD_MAX_SLEEP%"
if "%MAXSLEEP%"=="" set "MAXSLEEP=86400"
set "DOCS=%QSD_DOCS%"
if "%DOCS%"=="" set "DOCS=8"

".venv\Scripts\qsd.exe" harvest "%REQUEST%" --target %TARGET% --max-rounds %MAXR% --docs %DOCS% --sleep %SLEEP% --max-sleep %MAXSLEEP%
set "RC=%ERRORLEVEL%"

rem ---- file the results away --------------------------------------------------------------------
echo.
echo   Saving the ideas as notes, grouped into folders...
".venv\Scripts\qsd.exe" notes --dir "%~dp0strategy_ideas" --group-by family
".venv\Scripts\qsd.exe" handoff --export

echo.
echo   ----------------------------------------------------------------
if "%RC%"=="0" (
    echo   Finished. Ideas are grouped in:  %~dp0strategy_ideas\
) else (
    echo   Ended early ^(exit %RC%^). Everything found so far is in:
    echo       %~dp0strategy_ideas\
)
echo.
echo   Backtest queue :  %~dp0data\backtest_queue\pending\
echo   Bridge bundle  :  %~dp0data\handoff_bridge\
echo   Dashboard      :  http://127.0.0.1:8877/   (run auto_idea_finder.bat)
echo   ----------------------------------------------------------------
echo.
pause
