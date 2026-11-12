@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo Setup failed. Review the error above.
  pause
  exit /b 1
)
echo.
echo Setup completed successfully. Run run.bat to start CaptchaLab AI.
pause
