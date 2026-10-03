@echo off
cd /d "%~dp0"
".tools\python\python.exe" "scripts\start-services.py"
if errorlevel 1 (
  echo Start failed. See .runtime logs.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8765"
echo Dashboard: http://127.0.0.1:8765
echo AGH settings: http://127.0.0.1:4177
timeout /t 3 >nul
