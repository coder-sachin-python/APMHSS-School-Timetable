@echo off
setlocal
cd /d "%~dp0"
title APMHSS - Single EXE Builder
color 0B
echo ==============================================
echo APMHSS - Build single EXE with saved school data
echo ==============================================
echo.
if not exist "%~dp0BUILD_SINGLE_EXE.py" (
  echo ERROR: BUILD_SINGLE_EXE.py missing from this folder.
  echo Please EXTRACT ALL files from ZIP before running.
  pause
  exit /b 1
)
py -3 "%~dp0BUILD_SINGLE_EXE.py"
if errorlevel 1 (
  echo.
  echo BUILD FAILED. Read the message above.
  pause
  exit /b 1
)
echo.
echo SUCCESS. Check SINGLE_EXE_READY folder.
pause
