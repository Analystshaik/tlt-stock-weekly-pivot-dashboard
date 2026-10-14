@echo off
setlocal
cd /d "%~dp0"
py -3.10 tlt_weekly_pivot_backtest.py
if errorlevel 1 goto :error
echo.
echo Pivot backtest completed.
pause
exit /b 0

:error
echo.
echo Backtest failed. Run run_scanner.bat first and review the message above.
pause
exit /b 1
