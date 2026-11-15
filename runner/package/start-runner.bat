@echo off
setlocal
cd /d "%~dp0"
title TestMax Runner
echo.
echo  TestMax Runner
echo  --------------
echo.

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo  Python 3.11 or newer was not found on this computer.
  echo  Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH"
  echo  in the installer. Then double-click this file again.
  goto :fail
)
where node >nul 2>nul
if errorlevel 1 (
  echo  Node.js was not found. Install Node.js 20 or newer from https://nodejs.org/
  echo  and double-click this file again.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo  npm was not found. Reinstall Node.js from https://nodejs.org/ and try again.
  goto :fail
)

if not exist ".venv\Scripts\python.exe" (
  echo  Preparing a private Python environment...
  python -m venv .venv
  if errorlevel 1 goto :fail_install
)

if not exist ".installed" (
  echo  First start: installing the runner. This needs internet and takes a few minutes.
  echo.
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements-runner.txt
  if errorlevel 1 goto :fail_install
  pushd runner
  call npm install --no-audit --no-fund --loglevel=error
  if errorlevel 1 ( popd & goto :fail_install )
  cd test-runtime
  call npm install --no-audit --no-fund --loglevel=error
  if errorlevel 1 ( popd & goto :fail_install )
  popd
  echo installed> .installed
  echo  Installed.
  echo.
)

".venv\Scripts\python.exe" -m runner setup
echo.
echo  The runner has stopped. You can close this window.
pause
exit /b 0

:fail_install
echo.
echo  The installation did not finish. Check your internet connection and double-click
echo  this file again; it continues where it stopped.
:fail
echo.
pause
exit /b 1
