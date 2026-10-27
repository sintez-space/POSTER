@echo off
cd /d "%~dp0"
set PY=Z:\ai\bridge\venv\Scripts\python.exe
if not exist "%PY%" set PY=python
set PYTHONIOENCODING=utf-8
for /f "tokens=5" %%a in ('netstat -ano ^| findstr "127.0.0.1:8766" ^| findstr LISTENING') do (
  echo закрываю старую панель PID %%a
  taskkill /PID %%a /F >nul 2>&1
)
timeout /t 1 /nobreak >nul
"%PY%" panel.py
