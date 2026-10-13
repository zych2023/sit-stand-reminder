@echo off
rem ===================================================================
rem  Sit/Stand Reminder - one click launcher (no console window)
rem  Double-click this file to start the app.
rem ===================================================================
setlocal
cd /d "%~dp0"

set "PYW="

rem 1) optional override: put the full path of pythonw.exe in pythonw_path.txt
if exist "%~dp0pythonw_path.txt" set /p PYW=<"%~dp0pythonw_path.txt"

rem 2) a virtual environment created next to this script (python -m venv .venv)
if not defined PYW if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYW=%~dp0.venv\Scripts\pythonw.exe"

rem 3) the launcher used by the official python.org installer
if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\pythonw.exe"

rem 4) whatever pythonw.exe happens to be on PATH
if not defined PYW for %%I in (pythonw.exe) do set "PYW=%%~$PATH:I"

if not defined PYW (
  echo.
  echo  [ERROR] pythonw.exe was not found.
  echo  Please install Python 3, run Debug.bat to see details,
  echo  or create a file named pythonw_path.txt next to this script
  echo  containing the full path of your pythonw.exe.
  echo.
  pause
  exit /b 1
)

start "" "%PYW%" "%~dp0sitstand.py"
exit /b 0
