@echo off
setlocal
cd /d "%~dp0"

rem Windows keeps an .exe locked while a process is running it, so pip cannot
rem rewrite .venv\Scripts\memai-mcp.exe while a host still has a server on it.
rem A host does not restart a server it lost, but it starts a fresh one for the
rem next session.
rem
rem The branch reads taskkill's exit code and not what it printed: the text is
rem localised, and `find` resolves to another program entirely on a PATH that
rem carries a Unix toolchain.

taskkill /f /im memai-mcp.exe 2>nul
if errorlevel 1 (
    echo memai mcp: not running
    exit /b 1
)

echo.
echo memai mcp: stopped. Finish the update before opening a new session.
exit /b 0
