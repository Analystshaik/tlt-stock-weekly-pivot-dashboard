@echo off
setlocal
cd /d "%~dp0"
py -3.10 -m streamlit run tlt_weekly_pivot_dashboard.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo Dashboard failed. Review the message above.
pause
exit /b 1
