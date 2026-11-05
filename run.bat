@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\HullParam\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo HullParam is not installed yet. Run install.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
rem usage: run.bat [project_folder]   (you can drag a project folder onto run.bat)
"%VENV%\Scripts\python.exe" "%~dp0hullparam.py" app %*
pause
