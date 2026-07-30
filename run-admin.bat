@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo .venv not found. Run install.bat first.
    exit /b 1
)

call .venv\Scripts\activate.bat

rem The webfonts ship with the repo, so there is nothing to fetch here and
rem nothing that touches the network on the way to a dashboard. Refreshing
rem them is tools\fetch-fonts.py, run by hand -- see its docstring.

set MEMAI_ADMIN_PORT=8888
python -m memai.admin %*
