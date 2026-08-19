@echo off
echo ========================================================
echo   Albert RAG Admin - Studio Markdown & Albert API Server
echo ========================================================
cd /d "%~dp0\backend"
.\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
