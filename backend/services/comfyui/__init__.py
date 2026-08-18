"""
ComfyUI 子模块包

将 ComfyUIService 的职责拆分为独立模块：
- client.py: 纯 HTTP 调用封装（提交工作流、等待完成、查询进度）
- process_manager.py: 进程启停 + 健康检查 + 空闲自停
- file_handler.py: 图片/视频下载与存储
"""

from .client import ComfyUIClient
from .process_manager import ComfyUIProcessManager
from .file_handler import ComfyUIFileHandler

__all__ = [
    "ComfyUIClient",
    "ComfyUIProcessManager",
    "ComfyUIFileHandler",
]
