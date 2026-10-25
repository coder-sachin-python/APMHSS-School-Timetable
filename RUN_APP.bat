@echo off
setlocal
cd /d "%~dp0"
title APMHSS - School Timetable
where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
) else (
    set "PY=python"
)
%PY% --version
if errorlevel 1 (
    echo Python 3.10 or later is required.
    pause
    exit /b 1
)
%PY% -c "import PIL, docx, reportlab, openpyxl" >nul 2>&1
if errorlevel 1 (
    echo Installing missing packages...
    %PY% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Package installation failed.
        pause
        exit /b 1
    )
)
%PY% "CLASS_TIME_TABLE_GENERATOR.py"
if errorlevel 1 (
    echo Application exited with an error. Send a screenshot of the error.
    pause
)
