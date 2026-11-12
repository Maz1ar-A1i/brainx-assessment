@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
if errorlevel 1 (
  echo CaptchaLab AI failed to start. Review the error above.
  pause
  exit /b 1
)
