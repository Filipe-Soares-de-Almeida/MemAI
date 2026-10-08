@echo off
setlocal
cd /d "%~dp0"

rem Windows locks a running .exe, so pip cannot rewrite memai-mcp.exe while a host runs it.
rem Reads taskkill's exit code, not its output: the text is localised.

taskkill /f /im memai-mcp.exe 2>nul
if errorlevel 1 (
    echo memai mcp: not running
    exit /b 1
)

echo.
echo memai mcp: stopped. Finish the update before opening a new session.
exit /b 0
