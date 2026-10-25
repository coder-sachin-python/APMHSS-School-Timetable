@echo off
setlocal
cd /d "%~dp0"
title APMHSS - Import Existing Saved Database
py -3 "%~dp0IMPORT_EXISTING_DATA.py"
if errorlevel 1 (
  echo Import failed. Please read the message above.
  pause
)
