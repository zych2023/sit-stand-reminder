@echo off
rem ===================================================================
rem  Sit/Stand Reminder - debug launcher (keeps a console window open)
rem  Use this when you want to SEE the errors / logs.
rem ===================================================================
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "PY="

rem 1) optional override: put the full path of python.exe in python_path.txt
if exist "%~dp0python_path.txt" set /p PY=<"%~dp0python_path.txt"

rem 2) a virtual environment created next to this script (python -m venv .venv)
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

rem 3) the launcher used by the official python.org installer
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\python.exe"

rem 4) whatever python.exe happens to be on PATH
if not defined PY for %%I in (python.exe) do set "PY=%%~$PATH:I"

if not defined PY (
  echo [ERROR] python.exe was not found.
  echo Please install Python 3, or put its full path into python_path.txt
  pause
  exit /b 1
)

echo Using interpreter: %PY%
echo ------------------------------------------------------------
"%PY%" "%~dp0sitstand.py"
echo ------------------------------------------------------------
echo The program has exited. Check app.log for details.
pause
