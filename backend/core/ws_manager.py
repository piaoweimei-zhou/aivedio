"""
统一 WebSocket 连接管理器
取代 main.py / agent_api.py / debug_ws.py / training_api.py 中的 4 个独立管理器

架构：
- 通道订阅模式 (channel-based pub/sub)
- 支持单播/多播/广播/频道广播
- 自动探测死连接
- 连接统计
"""

import json
import logging
import time
from typing import Dict, Set, Optional, Callable, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSChannel:
    """WebSocket 频道"""
    
    def __init__(self, name: str):
        self.name = name
        self.connections: Dict[str, WebSocket] = {}  # conn_id -> ws
        self.groups: Dict[str, Set[str]] = {}  # group_key -> {conn_ids}
    
    @property
    def count(self) -> int:
        return len(self.connections)
    
    def add(self, conn_id: str, ws: WebSocket, group: str = ""):
        self.connections[conn_id] = ws
        if group:
            if group not in self.groups:
                self.groups[group] = set()
            self.groups[group].add(conn_id)
    
    def remove(self, conn_id: str):
        self.connections.pop(conn_id, None)
        for group in self.groups.values():
            group.discard(conn_id)


class UnifiedWSManager:
    """
    统一 WebSocket 连接管理器
    
    使用示例:
        manager = UnifiedWSManager()
        
        # 注册连接
        await manager.accept(ws, "client_xxx", channel="chat")
        
        # 单播
        await manager.send("client_xxx", {"type": "hello"})
        
        # 频道广播
        await manager.broadcast_channel("chat", {"type": "notification"})
        
        # 全部广播
        await manager.broadcast_all({"type": "system"})
    """
    
    def __init__(self):
        # 预定义频道
        self.channels: Dict[str, WSChannel] = {
            "chat": WSChannel("chat"),           # 通用聊天
            "agent": WSChannel("agent"),          # Agent 执行
            "multi_agent": WSChannel("multi_agent"),  # 多Agent协作
            "debug": WSChannel("debug"),          # 调试器
            "training": WSChannel("training"),    # 模型训练
            "terminal": WSChannel("terminal"),    # 终端
            "run": WSChannel("run"),              # 运行输出
            "rag": WSChannel("rag"),              # RAG 索引/搜索
            "broadcast": WSChannel("broadcast"),  # 系统广播
            "pipeline": WSChannel("pipeline"),    # 管线进度推送
        }
        
        # 连接元数据
        self.metadata: Dict[str, dict] = {}
        
        # 统计
        self.stats = {
            "total_connections": 0,
            "total_disconnections": 0,
            "messages_sent": 0,
            "start_time": time.time(),
        }
    
    async def accept(self, ws: WebSocket, conn_id: str, channel: str = "chat", group: str = "", meta: dict = None):
        """接受并注册 WebSocket 连接"""
        await ws.accept()
        
        ch = self.channels.get(channel)
        if not ch:
            ch = WSChannel(channel)
            self.channels[channel] = ch
        
        # ⭐ 清理同 group 中的旧连接（防止前端重连时旧连接泄漏）
        if group and group in ch.groups:
            old_conn_ids = list(ch.groups[group])
            for old_id in old_conn_ids:
                if old_id != conn_id:
                    old_ws = ch.connections.get(old_id)
                    if old_ws:
                        try:
                            await old_ws.close()
                        except Exception:
                            pass
                    self.disconnect(old_id)
        
        ch.add(conn_id, ws, group)
        self.metadata[conn_id] = {
            "channel": channel,
            "group": group,
            "connected_at": time.time(),
            **(meta or {}),
        }
        self.stats["total_connections"] += 1
        
        _group_count = len(ch.groups.get(group, set())) if group and group in ch.groups else 0
        logger.debug(f"[WS] {conn_id} → channel={channel} group={group} (active: {self.active_count}, group_conns: {_group_count})")
    
    def disconnect(self, conn_id: str):
        """断开连接"""
        meta = self.metadata.pop(conn_id, {})
        channel = meta.get("channel", "")
        group = meta.get("group", "")
        if channel in self.channels:
            self.channels[channel].remove(conn_id)
        self.stats["total_disconnections"] += 1
        _ch = self.channels.get(channel)
        _group_count = len(_ch.groups.get(group, set())) if group and _ch and group in _ch.groups else 0
        logger.debug(f"[WS] {conn_id} ✗ channel={channel} group={group} (active: {self.active_count}, group_conns: {_group_count})")
    
    async def send(self, conn_id: str, message: dict):
        """单播：向指定连接发送消息"""
        # 在所有频道中查找
        for ch in self.channels.values():
            if conn_id in ch.connections:
                try:
                    ws = ch.connections[conn_id]
                    await ws.send_text(json.dumps(message))
                    self.stats["messages_sent"] += 1
                    return
                except Exception as e:
                    logger.warning(f"[WS] send to {conn_id} failed: {e}")
                    self.disconnect(conn_id)
                    return
        logger.debug(f"[WS] conn {conn_id} not found")
    
    async def send_group(self, channel: str, group: str, message: dict):
        """组播：向频道中某分组的所有连接发送消息"""
        ch = self.channels.get(channel)
        if not ch:
            return
        group_conns = ch.groups.get(group, set())
        dead = set()
        for conn_id in group_conns:
            ws = ch.connections.get(conn_id)
            if ws:
                try:
                    await ws.send_text(json.dumps(message))
                    self.stats["messages_sent"] += 1
                except Exception as e:
                    dead.add(conn_id)
                    logger.debug(f"[WS] 组播发送失败 channel={channel} group={group} conn={conn_id}: {e}")
            else:
                dead.add(conn_id)
        for conn_id in dead:
            self.disconnect(conn_id)
    
    async def broadcast_channel(self, channel: str, message: dict):
        """频道广播：向某频道所有连接发送消息"""
        ch = self.channels.get(channel)
        if not ch:
            return
        dead = []
        for conn_id, ws in list(ch.connections.items()):
            try:
                await ws.send_text(json.dumps(message))
                self.stats["messages_sent"] += 1
            except Exception as e:
                dead.append(conn_id)
                logger.debug(f"[WS] 频道广播发送失败 channel={channel} conn={conn_id}: {e}")
        for conn_id in dead:
            self.disconnect(conn_id)
    
    async def broadcast_all(self, message: dict):
        """全广播：向所有频道所有连接发送消息"""
        for channel in self.channels:
            await self.broadcast_channel(channel, message)
    
    @property
    def active_count(self) -> int:
        return len(self.metadata)
    
    def get_channel_count(self, channel: str) -> int:
        ch = self.channels.get(channel)
        return ch.count if ch else 0
    
    async def cleanup_stale_connections(self, max_age_seconds: int = 600):
        """清理超时的死连接（发送 ping 检测，失败则断开）"""
        now = time.time()
        stale = []
        for conn_id, meta in list(self.metadata.items()):
            age = now - meta.get("connected_at", now)
            if age > max_age_seconds:
                ch_name = meta.get("channel", "")
                ch = self.channels.get(ch_name)
                if ch and conn_id in ch.connections:
                    try:
                        await ch.connections[conn_id].send_text(json.dumps({"type": "ping"}))
                    except Exception:
                        stale.append(conn_id)
        for conn_id in stale:
            logger.warning(f"[WS] 清理超时死连接: {conn_id}")
            self.disconnect(conn_id)
        if stale:
            logger.info(f"[WS] 清理完成: 移除 {len(stale)} 个死连接 (active: {self.active_count})")
    
    def get_stats(self) -> dict:
        """获取连接统计"""
        elapsed = time.time() - self.stats["start_time"]
        return {
            "active_connections": self.active_count,
            "total_connections": self.stats["total_connections"],
            "total_disconnections": self.stats["total_disconnections"],
            "messages_sent": self.stats["messages_sent"],
            "uptime_seconds": int(elapsed),
            "channels": {
                name: ch.count
                for name, ch in self.channels.items()
                if ch.count > 0
            },
        }
    
    def exists(self, conn_id: str) -> bool:
        """检查连接是否存在"""
        return conn_id in self.metadata

    # ============ 后向兼容方法 ============
    
    async def connect(self, websocket: WebSocket, client_id: str):
        """兼容原 ConnectionManager.connect()"""
        await self.accept(websocket, client_id, channel="chat")
    
    async def connect_multi_agent(self, websocket: WebSocket, execution_id: str):
        """兼容原 ConnectionManager.connect_multi_agent()"""
        await self.accept(websocket, execution_id, channel="multi_agent", group=execution_id)
    
    def disconnect_multi_agent(self, execution_id: str):
        """兼容原 ConnectionManager.disconnect_multi_agent()"""
        self.disconnect(execution_id)
    
    async def send_personal_message(self, message: dict, client_id: str):
        """兼容原 ConnectionManager.send_personal_message()"""
        await self.send(client_id, message)
    
    async def send_to_multi_agent(self, message: dict, execution_id: str):
        """兼容原 ConnectionManager.send_to_multi_agent()"""
        await self.send_group("multi_agent", execution_id, message)
    
    async def broadcast(self, message: dict):
        """兼容原 ConnectionManager.broadcast()"""
        await self.broadcast_all(message)


# 全局单例
_manager: Optional[UnifiedWSManager] = None


def get_ws_manager() -> UnifiedWSManager:
    global _manager
    if _manager is None:
        _manager = UnifiedWSManager()
    return _manager
