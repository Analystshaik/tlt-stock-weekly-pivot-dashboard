@echo off
setlocal
cd /d "%~dp0"

set "SOURCE=C:\Keystone\ibkr_production_data_engine_v2\data\ibkr_market_data.sqlite"
set "TARGET=data\streamlit_market_data.sqlite"

if not exist "%SOURCE%" (
  echo ERROR: Source database was not found:
  echo   %SOURCE%
  echo.
  echo Edit SOURCE in prepare_cloud_database.bat if your database moved.
  exit /b 1
)

if not exist data mkdir data
copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 exit /b 1

echo Cloud database prepared:
echo   %CD%\%TARGET%
echo.
echo Next run: push_to_github.bat
endlocal
