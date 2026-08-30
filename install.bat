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

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e ".[dev]"
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
