@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\HullParam\venv"
set "LOG=%~dp0install_log.txt"
echo ============================================================
echo  HullParam install
echo  virtual env : %VENV%
echo  log file    : %LOG%
echo ============================================================
echo HullParam install log > "%LOG%"
echo folder: %~dp0 >> "%LOG%"

set "PY="
for %%V in (3.13 3.12 3.11 3.14 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert sys.version_info[:2] >= (3,10) and sys.maxsize > 2**32" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo [ERROR] 64-bit Python 3.10 - 3.14 was not found.
  echo   1. Install Python from https://www.python.org/downloads/windows/
  echo      ^(check "Add python.exe to PATH" in the installer^)
  echo   2. Run install.bat again.
  echo [ERROR] python not found >> "%LOG%"
  py -0p >> "%LOG%" 2>&1
  where python >> "%LOG%" 2>&1
  pause
  exit /b 1
)
echo Python: %PY%
%PY% -c "import sys; print(sys.version)"
%PY% -c "import sys; print(sys.version)" >> "%LOG%" 2>&1

if exist "%VENV%\Scripts\python.exe" (
  echo Existing virtual env found - reusing it.
) else (
  echo Creating virtual env ...
  %PY% -m venv "%VENV%" >> "%LOG%" 2>&1
  if errorlevel 1 (
    echo [ERROR] could not create the virtual env. See install_log.txt
    pause
    exit /b 1
  )
)
echo Installing packages (1-3 min, about 200 MB) ...
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] package install failed. Last lines of install_log.txt:
  powershell -NoProfile -Command "Get-Content -Tail 15 -LiteralPath $env:LOG"
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" -c "import OCP, shapely, yaml, numpy; print('check OK')" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] packages installed but import failed. See install_log.txt
  pause
  exit /b 1
)
echo.
echo ============================================================
echo  Install finished. Double-click run.bat to start HullParam.
echo ============================================================
echo install OK >> "%LOG%"
pause
