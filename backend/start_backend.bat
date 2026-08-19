@echo off
setlocal
cd /d "%~dp0"

rem ===== Director backend launcher (fixed local ComfyUI config) =====
set "COMFYUI_DIR=D:\1\2\ComfyUI_windows_portable\ComfyUI"
set "COMFYUI_PYTHON=D:\1\2\ComfyUI_windows_portable\python_embeded\python.exe"
set "COMFYUI_BASE_URL=http://127.0.0.1:8188"
set "PYTHONUTF8=1"

netstat -ano | findstr "LISTENING" | findstr ":8188" >nul 2>&1
if errorlevel 1 echo [start] ComfyUI 8188 not running, will auto-start on first task.

echo [start] Starting backend at http://127.0.0.1:8000
".venv-test\Scripts\python.exe" -m uvicorn main:app --app-dir "%CD%" --port 8000 --host 127.0.0.1
endlocal