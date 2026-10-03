@echo off
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Failed to find git -- is Git on PATH?
    exit /b 1
)
if not exist .git (
    echo %~dp0 is not a git checkout. update.bat updates a clone of MemAI.
    exit /b 1
)

if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe tools\install-guard.py
    if errorlevel 1 exit /b 1
)

rem cmd reads a batch file as it runs it, and the pull below can rewrite this
rem one. A parenthesised block is read whole before it starts.
(
    echo Pulling the latest changes...
    git pull --ff-only
    if errorlevel 1 (
        echo.
        echo git pull failed. A fast-forward was not possible: commit, stash or
        echo discard the local changes, then run update.bat again.
        exit /b 1
    )
    echo.
    call install.bat
    if errorlevel 1 exit /b 1
    exit /b 0
)
