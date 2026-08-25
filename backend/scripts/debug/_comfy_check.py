import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.comfyui.config import COMFYUI_DIR, COMFYUI_OUTPUT_DIR
print("COMFYUI_DIR:", repr(COMFYUI_DIR))
print("COMFYUI_OUTPUT_DIR:", repr(COMFYUI_OUTPUT_DIR))
print("output exists:", os.path.isdir(COMFYUI_OUTPUT_DIR))
