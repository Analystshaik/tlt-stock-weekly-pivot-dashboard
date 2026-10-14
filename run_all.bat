@echo off
setlocal
cd /d "%~dp0"
echo [1/3] Scanning TLT weekly reversal pivots...
py -3.10 tlt_weekly_pivot_scanner.py
if errorlevel 1 goto :error
echo.
echo [2/3] Backtesting index reactions...
py -3.10 tlt_weekly_pivot_backtest.py
if errorlevel 1 goto :error
echo.
echo [3/3] Starting Streamlit dashboard...
py -3.10 -m streamlit run tlt_weekly_pivot_dashboard.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Process stopped because a step failed. Review the message above.
pause
exit /b 1
