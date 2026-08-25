"""ComfyUI 统一配置模块

集中管理 ComfyUI 目录、Python 解释器、端口等配置，避免在多个模块中
重复读取环境变量和实现检测逻辑。

使用方式：
    from services.comfyui.config import COMFYUI_DIR, COMFYUI_OUTPUT_DIR, COMFYUI_INPUT_DIR
"""

import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ── 项目根目录与工作流目录（全项目单一来源）──────────────────
# backend/services/comfyui/config.py → 上溯 4 级到项目根 d:\director
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[3]
PROJECT_ROOT: str = str(_PROJECT_ROOT)
WORKFLOWS_DIR: str = str(_PROJECT_ROOT / "workflows")


def _detect_comfyui_dir() -> str:
    """自动检测 ComfyUI 安装目录

    优先级：
    1. 环境变量 COMFYUI_DIR（需存在 output 子目录）
    2. 环境变量 COMFYUI_SEARCH_PATHS（分号分隔的自定义检测路径）
    3. 常见安装路径（按顺序检测）
    """
    env_dir = os.environ.get("COMFYUI_DIR", "")
    if env_dir and os.path.isdir(env_dir) and os.path.isdir(os.path.join(env_dir, "output")):
        return env_dir

    # 用户可通过环境变量自定义检测路径列表（分号分隔）
    custom_paths = os.environ.get("COMFYUI_SEARCH_PATHS", "")
    search_paths = []
    if custom_paths:
        search_paths.extend([p.strip() for p in custom_paths.split(";") if p.strip()])
    search_paths.extend(
        [
            os.path.join(os.path.expanduser("~"), ".codebuddy", "comfyui"),
            os.path.join(os.path.expanduser("~"), "ComfyUI"),
        ]
    )
    for path in search_paths:
        if os.path.isdir(path) and os.path.isdir(os.path.join(path, "output")):
            return path

    # 兜底：返回环境变量值（可能为空），避免完全无值
    return env_dir


# ── 配置常量 ──────────────────────────────────────────────────

COMFYUI_DIR: str = _detect_comfyui_dir()
COMFYUI_OUTPUT_DIR: str = os.path.join(COMFYUI_DIR, "output") if COMFYUI_DIR else ""
COMFYUI_INPUT_DIR: str = os.path.join(COMFYUI_DIR, "input") if COMFYUI_DIR else ""

COMFYUI_PYTHON: str = os.environ.get("COMFYUI_PYTHON", "python")
COMFYUI_BASE_URL: str = os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
COMFYUI_SCRIPT: str = "main.py"

# 内存/显存监控阈值（单一来源，process_manager 和 comfyui_service 共用）
MEMORY_HIGH_THRESHOLD: int = int(os.environ.get("COMFYUI_MEMORY_THRESHOLD", "80"))
VRAM_HIGH_THRESHOLD: int = int(os.environ.get("COMFYUI_VRAM_THRESHOLD", "90"))
MEMORY_CHECK_INTERVAL: int = int(os.environ.get("COMFYUI_MEMORY_CHECK_INTERVAL", "30"))

if not COMFYUI_DIR:
    logger.warning("[ComfyUIConfig] 未检测到 ComfyUI 安装目录，请设置 COMFYUI_DIR 环境变量")
