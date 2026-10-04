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

rem install.bat --webui-zip <path> installs a memai-webui-<version>.zip fetched by hand.
set "WEBUI_ARGS="
if /i "%~1"=="--webui-zip" (
    if "%~2"=="" (
        echo --webui-zip needs the path to memai-webui-^<version^>.zip
        exit /b 1
    )
    set WEBUI_ARGS=--zip "%~f2"
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
echo Installing the admin dashboard...
rem Builds with Node 22.18+ when it can, else installs this version's prebuilt release asset.
python tools\install-webui.py %WEBUI_ARGS%
if errorlevel 2 (
    echo.
    echo Done, without a dashboard: the MCP server works, the dashboard does not.
    exit /b 0
)

echo.
echo Done. Run run-admin.bat to start the admin dashboard.
