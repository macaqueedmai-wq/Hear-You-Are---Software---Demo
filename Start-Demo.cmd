@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 請先執行：python scripts\setup.py
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\run.py --open
if errorlevel 1 pause
