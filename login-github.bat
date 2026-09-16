@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "GH=C:\Program Files\GitHub CLI\gh.exe"
if not exist "%GH%" (
  echo [ERROR] GitHub CLI not found. Install it first: winget install GitHub.cli
  pause
  exit /b 1
)

echo.
echo === GitHub login (one time only) ===
echo.
echo Press Enter for every question. Defaults are correct:
echo   GitHub.com   -^>   HTTPS   -^>   Yes   -^>   Login with a web browser
echo.
echo Then:
echo   1. Copy the one-time code shown on screen (like ABCD-1234)
echo   2. Press Enter, the browser opens (or go to https://github.com/login/device)
echo   3. Paste the code and click Authorize
echo.
echo Wait until you see: "Congratulations, you're all set!"
echo.
pause

"%GH%" auth login --hostname github.com --git-protocol https --web

echo.
echo === login status ===
"%GH%" auth status
echo.
pause
endlocal
