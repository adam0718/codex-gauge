@echo off
rem =====================================================================
rem  Codex Gauge launcher
rem  Starts the floating window with pythonw (no console window).
rem  IMPORTANT: this file must stay pure ASCII. cmd.exe parses .bat with
rem  the OEM codepage, so non-ASCII text here would corrupt the script.
rem  The project path is resolved at runtime via "%~dp0".
rem =====================================================================
setlocal
cd /d "%~dp0"

if not exist "src\codex_gauge.py" (
    echo [ERROR] src\codex_gauge.py not found.
    pause
    exit /b 1
)

set "LAUNCH="

rem --- 1) Python Launcher, only if it has tkinter ---
py -3 -c "import tkinter" >nul 2>nul
if not errorlevel 1 (
    set "LAUNCH=pyw -3"
    goto :run
)

rem --- 2) Per-user installs under LOCALAPPDATA ---
for /d %%V in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~V\python.exe" (
        "%%~V\python.exe" -c "import tkinter" >nul 2>nul && (
            set "LAUNCH=%%~V\pythonw.exe"
            goto :run
        )
    )
)

rem --- 3) System-wide installs under Program Files ---
for /d %%V in ("%ProgramFiles%\Python3*") do (
    if exist "%%~V\python.exe" (
        "%%~V\python.exe" -c "import tkinter" >nul 2>nul && (
            set "LAUNCH=%%~V\pythonw.exe"
            goto :run
        )
    )
)

echo [ERROR] No Python 3 with tkinter was found.
echo Install Python 3.9+ from python.org, keeping the "tcl/tk" component.
pause
exit /b 1

:run
start "" %LAUNCH% "src\codex_gauge.py"
exit /b 0
