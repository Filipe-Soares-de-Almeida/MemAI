@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo .venv not found. Run install.bat first.
    exit /b 1
)

call .venv\Scripts\activate.bat

set MEMAI_ADMIN_PORT=8888
python -m memai.admin %*
