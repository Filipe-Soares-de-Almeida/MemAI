@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\activate.bat (
    echo .venv not found. Run install.bat first.
    exit /b 1
)

call .venv\Scripts\activate.bat

rem The webfonts are untracked, so a fresh clone has none and the dashboard
rem renders in whatever the system stack offers. Fetch them once, only when a
rem face is actually missing -- this is the one thing in MemAI that touches
rem the network, and it must not do so on every launch. A failure here is not
rem fatal: admin.css falls back to an installed Roboto and then to the system
rem stack, so the dashboard still starts, just not in the specified typeface.
set FONTS_DIR=src\memai\webui\fonts
set FONTS_MISSING=
for %%F in (roboto-400.woff2 roboto-500.woff2 roboto-700.woff2 ^
            roboto-mono-400.woff2 roboto-mono-500.woff2 roboto-mono-600.woff2) do (
    if not exist "%FONTS_DIR%\%%F" set FONTS_MISSING=1
)
if defined FONTS_MISSING (
    echo Fetching the Roboto faces the dashboard is designed for...
    python tools\fetch-fonts.py
    if errorlevel 1 echo   continuing without them - the dashboard falls back to the system font stack.
    echo.
)

set MEMAI_ADMIN_PORT=8888
python -m memai.admin %*
