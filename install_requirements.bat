@echo off
setlocal
cd /d "%~dp0"
py -3.10 -m pip install --upgrade pip
if errorlevel 1 goto :error
py -3.10 -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo.
echo Installation complete.
pause
exit /b 0

:error
echo.
echo Installation failed. Review the message above.
pause
exit /b 1
