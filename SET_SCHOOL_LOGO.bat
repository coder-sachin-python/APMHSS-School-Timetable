@echo off
setlocal
cd /d "%~dp0"
title APMHSS - Choose School Logo
where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
) else (
    set "PY=python"
)
%PY% -m pip install --disable-pip-version-check pillow
if errorlevel 1 (
    echo Could not install Pillow.
    pause
    exit /b 1
)
%PY% "SET_SCHOOL_LOGO.py"
if errorlevel 1 pause
