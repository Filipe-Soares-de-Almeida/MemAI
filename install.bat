@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\Scripts\activate.bat (
    echo Failed to create .venv -- is Python 3.12+ on PATH?
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo Failed to find node -- is Node 20.19+ on PATH?
    exit /b 1
)

.venv\Scripts\python.exe tools\install-guard.py
if errorlevel 1 exit /b 1

call .venv\Scripts\activate.bat
rem The lock pins every package CI tested, by hash. pip refuses an editable
rem project under --require-hashes, so the checkout goes in on its own after it.
python -m pip install --require-hashes -r requirements-dev.txt
if errorlevel 1 (
    echo.
    echo Install failed.
    exit /b 1
)
pip install --no-deps -e .
if errorlevel 1 (
    echo.
    echo Install failed.
    exit /b 1
)

echo.
echo Building the admin dashboard...
call npm ci
if errorlevel 1 (
    echo.
    echo npm ci failed.
    exit /b 1
)
call npm run build
if errorlevel 1 (
    echo.
    echo Dashboard build failed.
    exit /b 1
)

echo.
echo Done. Run run-admin.bat to start the admin dashboard.
