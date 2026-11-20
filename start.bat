@echo off
chcp 65001 >nul
echo ===================================================
echo   TanieLoty - Uruchamianie serwera i aplikacji PWA
echo ===================================================
.\.venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
pause
