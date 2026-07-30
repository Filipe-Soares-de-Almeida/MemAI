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

rem No MEMAI_ADMIN_PORT here: 8888 is the default now. This script setting
rem it was the only reason the dashboard and the documented default ever
rem disagreed, and setting it again would put the same split back -- an
rem MCP server started from a desktop config never sees a variable this
rem process exports, so it would go looking on the wrong port.
python -m memai.admin %*
