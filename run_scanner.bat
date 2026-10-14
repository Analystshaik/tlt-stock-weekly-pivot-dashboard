@echo off
setlocal
cd /d "%~dp0"
py -3.10 tlt_weekly_pivot_scanner.py
if errorlevel 1 goto :error
echo.
echo Weekly pivot scan completed.
pause
exit /b 0

:error
echo.
echo Scanner failed. Review the message above.
pause
exit /b 1
