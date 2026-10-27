@echo off
cd /d "%~dp0"
set PY=Z:\ai\bridge\venv\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install -q -r calendar\requirements.txt
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8767" ^| findstr LISTENING') do (
  echo закрываю старый календарь PID %%a
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul
"%PY%" calendar\run.py
