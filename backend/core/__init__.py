"""core: 核心基础设施。

当前仅保留 ws_manager（通道订阅式 WebSocket 管理器）。
其余异常/限流/熔断/auth 等模块在本仓库中无实现，勿在此导入；
实时推送请引用 live 的 services/ws_service.py。
"""

from .ws_manager import WSChannel, UnifiedWSManager, get_ws_manager

__all__ = ["WSChannel", "UnifiedWSManager", "get_ws_manager"]
