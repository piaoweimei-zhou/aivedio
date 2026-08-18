"""
ComfyUI 服务
通过 HTTP API 调用本地 ComfyUI 节点生成图像
支持自动启动 + 连接重试
支持 Z-Image 瑶光版（文生图）和 Qwen Image Edit（图生图）

实现已拆分到以下子模块（Mixin 组合）：
- comfyui_helpers: 共享工具与常量
- comfyui_lifecycle: 进程/内存/健康管理
- comfyui_generation: 图像生成与队列
- comfyui_video: 视频生成
- comfyui_tts: TTS 音频
- comfyui_storyboard: 分镜生成
"""

import asyncio
import logging
import os
import subprocess
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass

import aiohttp

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIConfig, ComfyUIGenResult
from services.comfyui_lifecycle import ComfyUILifecycleMixin
from services.comfyui_generation import ComfyUIGenerationMixin
from services.comfyui_video import ComfyUIVideoMixin
from services.comfyui_tts import ComfyUITTSMixin
from services.comfyui_storyboard import ComfyUIStoryboardMixin

logger = logging.getLogger(__name__)


class ComfyUIService(
    ComfyUILifecycleMixin,
    ComfyUIGenerationMixin,
    ComfyUIVideoMixin,
    ComfyUITTSMixin,
    ComfyUIStoryboardMixin,
):
    """ComfyUI 服务（含自动启动、空闲自停、健康检查、显存协调）"""

    def __init__(self, config: Optional[ComfyUIConfig] = None):
        self.config = config or ComfyUIConfig()
        self._process: Optional[subprocess.Popen] = None
        self._comfyui_log_f = None  # ⭐ Fix 10: ComfyUI 日志文件句柄
        self._image_cache: OrderedDict = OrderedDict()  # 支持 LRU 淘汰
        self._last_used: float = 0
        self._idle_shutdown_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None  # 健康检查后台任务
        # 并发控制：最多 2 个并发生成
        self._semaphore = asyncio.Semaphore(2)
        # 按模型类型分别计数（SDXL/Z-Image vs Qwen），不同模型可共存无需互相重启
        self._model_generation_count: Dict[str, int] = {"sd": 0, "qwen": 0}
        # ⭐ 活跃生成标志：防止空闲定时器在生成进行中误杀 ComfyUI
        self._active_generation: bool = False
        self._max_generations_before_restart: Dict[str, int] = {"sd": 3, "qwen": 3}
        # 防重入标志：防止多个协程并发执行 stop + restart
        self._restart_in_progress: bool = False
        # ComfyUI 重启事件回调（用于 WS 广播）
        self._restart_callbacks: List[Callable[[str, int], Awaitable[None]]] = []
        # 预估重启等待时间（秒）
        self._estimated_restart_secs: int = 15
        # ⭐ 共享 aiohttp session（复用连接，减少内存碎片）
        self._http_session: Optional[aiohttp.ClientSession] = None
        # 输出文件 → 子目录映射（SaveAudio 等输出到子目录的文件）
        self._output_subfolders: Dict[str, str] = {}

        # ── 子模块实例（P2 拆分：委托职责到独立模块）──────────
        from services.comfyui.client import ComfyUIClient
        from services.comfyui.process_manager import ComfyUIProcessManager
        from services.comfyui.file_handler import ComfyUIFileHandler

        self._client = ComfyUIClient(
            base_url=self.config.base_url,
            output_dir=self.config.output_dir,
        )
        self._process_mgr = ComfyUIProcessManager(
            comfyui_dir=COMFYUI_DIR,
            base_url=self.config.base_url,
            check_alive_fn=self._check_alive,
            on_restart=None,
        )
        self._file_handler = ComfyUIFileHandler(
            comfyui_dir=COMFYUI_DIR or "",
            output_dir=self.config.output_dir,
            base_url=self.config.base_url,
            http_session_fn=self._get_http_session,
        )


# ============================================================
# 全局单例
# ============================================================

_comfyui_service: Optional[ComfyUIService] = None


def get_comfyui_service() -> ComfyUIService:
    global _comfyui_service
    if _comfyui_service is None:
        _comfyui_service = ComfyUIService()
    return _comfyui_service
