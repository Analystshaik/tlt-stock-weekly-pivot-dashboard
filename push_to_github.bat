@echo off
setlocal
cd /d "%~dp0"

set "REPO=tlt-stock-weekly-pivot-dashboard"
set "REMOTE=https://github.com/Analystshaik/%REPO%.git"

where git >nul 2>nul || (
  echo ERROR: Git is not installed or not on PATH.
  exit /b 1
)
where git-lfs >nul 2>nul || (
  echo ERROR: Git LFS is not installed or not on PATH.
  echo Install Git LFS, then run this file again.
  exit /b 1
)
if not exist "data\streamlit_market_data.sqlite" (
  echo ERROR: data\streamlit_market_data.sqlite is missing.
  echo Run prepare_cloud_database.bat first.
  exit /b 1
)

git lfs install
if not exist .git git init
git branch -M main

git lfs track "data/*.sqlite"
git add .gitattributes
git add .
git commit -m "Deploy TLT stock weekly pivot dashboard to Streamlit Cloud"

for /f "delims=" %%R in ('git remote 2^>nul') do if /I "%%R"=="origin" git remote remove origin
git remote add origin "%REMOTE%"

echo.
echo Pushing to %REMOTE% ...
git push -u origin main
if errorlevel 1 (
  echo.
  echo PUSH FAILED. If the GitHub repository already has commits, use the commands in CLOUD_DEPLOYMENT.md.
  exit /b 1
)

echo.
echo SUCCESS.
echo GitHub: https://github.com/Analystshaik/%REPO%
echo Streamlit entrypoint: tlt_weekly_pivot_dashboard.py
endlocal
