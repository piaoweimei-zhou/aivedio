@echo off
netstat -ano | findstr "LISTENING" | findstr ":8188" >nul 2>&1
if not errorlevel 1 (
    echo ComfyUI already running on 8188.
    exit /b 0
)
echo Starting ComfyUI...
start "ComfyUI" "D:\1\2\ComfyUI_windows_portable\run_nvidia_gpu.bat"