"""
WebSocket 实时推送服务

为批量任务提供实时进度推送，替代前端 3s 轮询。

设计：
- BatchTaskService 在步骤状态变更时调用 ws_service.notify()
- WebSocket 端点 /api/ws/batches/{batch_id} 推送实时事件
- 支持订阅多个 batch_id（前端批量任务列表页）
- 客户端断开后自动清理

事件类型：
- batch_started: 批量任务启动
- step_started: 步骤开始执行
- step_completed: 步骤完成
- step_failed: 步骤失败
- step_skipped: 步骤跳过
- batch_completed: 批量任务完成
- batch_failed: 批量任务失败
- progress: 进度更新（completed/total/percent）
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Set

from fastapi import WebSocket, WebSocketDisconnect

from services.task_status import TaskStatus, to_frontend_status

logger = logging.getLogger(__name__)


@dataclass
class WsEvent:
    """WebSocket 推送事件"""

    event: str  # 事件类型
    batch_id: str  # 批量任务 ID
    step_id: str = ""  # 步骤 ID（可选）
    stage_id: str = ""  # 阶段 ID（可选）
    status: str = ""  # 状态
    message: str = ""  # 消息
    progress: Dict[str, Any] = None  # 进度信息
    timestamp: float = 0.0  # 时间戳

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not self.timestamp:
            d["timestamp"] = time.time()
        if self.progress is None:
            del d["progress"]
        return d


class WsConnectionManager:
    """WebSocket 连接管理器

    管理 batch_id → WebSocket 连接集合 的映射。
    一个 batch_id 可被多个客户端订阅（多窗口查看）。
    """

    def __init__(self):
        # batch_id → set[WebSocket]
        self._subscriptions: Dict[str, Set[WebSocket]] = {}
        # WebSocket → set[batch_id]（反向映射，便于断开时清理）
        self._subscribed_batches: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, websocket: WebSocket, batch_id: str):
        """订阅指定 batch_id 的事件"""
        async with self._lock:
            if batch_id not in self._subscriptions:
                self._subscriptions[batch_id] = set()
            self._subscriptions[batch_id].add(websocket)

            if websocket not in self._subscribed_batches:
                self._subscribed_batches[websocket] = set()
            self._subscribed_batches[websocket].add(batch_id)

        logger.info(
            f"[WsManager] 订阅 | batch={batch_id} | "
            f"当前订阅数={len(self._subscriptions[batch_id])}"
        )

    async def unsubscribe(self, websocket: WebSocket, batch_id: str = ""):
        """取消订阅（batch_id 为空则取消所有）"""
        async with self._lock:
            batches_to_remove = (
                self._subscribed_batches.get(websocket, set()) if not batch_id else {batch_id}
            )
            for bid in batches_to_remove:
                conns = self._subscriptions.get(bid)
                if conns:
                    conns.discard(websocket)
                    if not conns:
                        del self._subscriptions[bid]
            if not batch_id:
                self._subscribed_batches.pop(websocket, None)
            else:
                subs = self._subscribed_batches.get(websocket)
                if subs:
                    subs.discard(batch_id)

    async def broadcast(self, batch_id: str, event: WsEvent):
        """向订阅了 batch_id 的所有客户端推送事件"""
        conns = self._subscriptions.get(batch_id, set()).copy()
        if not conns:
            return

        message = json.dumps(event.to_dict(), ensure_ascii=False)
        dead_conns = []

        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning(f"[WsManager] 推送失败 | error={e}")
                dead_conns.append(ws)

        # 清理断开的连接
        if dead_conns:
            async with self._lock:
                for ws in dead_conns:
                    self._subscriptions.get(batch_id, set()).discard(ws)

        logger.info(
            f"[WsManager] 推送 | batch={batch_id} | event={event.event} | " f"clients={len(conns)}"
        )


# 全局单例
_ws_manager = WsConnectionManager()


def get_ws_manager() -> WsConnectionManager:
    return _ws_manager


# ============================================================
# 事件通知接口（供 BatchTaskService 调用）
# ============================================================


async def notify_batch_started(batch_id: str, total_steps: int):
    """通知批量任务启动"""
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="batch_started",
            batch_id=batch_id,
            message=f"批量任务启动，共 {total_steps} 个步骤",
            progress={"total": total_steps, "completed": 0, "percent": 0},
        ),
    )


async def notify_step_started(batch_id: str, step_id: str, stage_id: str):
    """通知步骤开始执行"""
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="step_started",
            batch_id=batch_id,
            step_id=step_id,
            stage_id=stage_id,
            status="running",
            message=f"步骤 {step_id} 开始执行",
        ),
    )


async def notify_step_completed(
    batch_id: str,
    step_id: str,
    stage_id: str,
    output_asset_id: str,
    completed: int,
    total: int,
):
    """通知步骤完成"""
    percent = int(completed * 100 / total) if total > 0 else 0
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="step_completed",
            batch_id=batch_id,
            step_id=step_id,
            stage_id=stage_id,
            status=to_frontend_status(TaskStatus.COMPLETED),
            message=f"步骤 {step_id} 完成",
            progress={
                "completed": completed,
                "total": total,
                "percent": percent,
                "output_asset_id": output_asset_id,
            },
        ),
    )


async def notify_step_failed(
    batch_id: str,
    step_id: str,
    stage_id: str,
    error: str,
    completed: int,
    total: int,
):
    """通知步骤失败"""
    percent = int(completed * 100 / total) if total > 0 else 0
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="step_failed",
            batch_id=batch_id,
            step_id=step_id,
            stage_id=stage_id,
            status="failed",
            message=f"步骤 {step_id} 失败: {error}",
            progress={"completed": completed, "total": total, "percent": percent},
        ),
    )


async def notify_step_skipped(
    batch_id: str,
    step_id: str,
    stage_id: str,
    reason: str,
):
    """通知步骤跳过"""
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="step_skipped",
            batch_id=batch_id,
            step_id=step_id,
            stage_id=stage_id,
            status="skipped",
            message=f"步骤 {step_id} 跳过: {reason}",
        ),
    )


async def notify_batch_completed(batch_id: str, completed: int, total: int, elapsed_ms: int):
    """通知批量任务完成"""
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="batch_completed",
            batch_id=batch_id,
            status=to_frontend_status(TaskStatus.COMPLETED),
            message=f"批量任务完成，{completed}/{total} 步骤成功，耗时 {elapsed_ms}ms",
            progress={
                "completed": completed,
                "total": total,
                "percent": 100,
                "elapsed_ms": elapsed_ms,
            },
        ),
    )


async def notify_batch_failed(
    batch_id: str, failed_step_id: str, error: str, completed: int, total: int
):  # noqa: E501
    """通知批量任务失败"""
    percent = int(completed * 100 / total) if total > 0 else 0
    await _ws_manager.broadcast(
        batch_id,
        WsEvent(
            event="batch_failed",
            batch_id=batch_id,
            status="failed",
            message=f"批量任务失败：步骤 {failed_step_id} 失败，{completed}/{total} 已完成",
            progress={
                "completed": completed,
                "total": total,
                "percent": percent,
                "failed_step": failed_step_id,
            },  # noqa: E501
        ),
    )


# ============================================================
# WebSocket 端点处理
# ============================================================


async def handle_batch_ws(websocket: WebSocket, batch_id: str):
    """WebSocket 端点处理函数

    使用方式（在 main.py 或 api 中注册）：
        @app.websocket("/api/ws/batches/{batch_id}")
        async def batch_ws(websocket: WebSocket, batch_id: str):
            await handle_batch_ws(websocket, batch_id)
    """
    await websocket.accept()
    logger.info(f"[WsManager] WebSocket 连接 | batch={batch_id}")

    await _ws_manager.subscribe(websocket, batch_id)

    # 发送连接成功消息
    await websocket.send_text(
        json.dumps(
            {
                "event": "connected",
                "batch_id": batch_id,
                "message": f"已订阅批量任务 {batch_id} 的实时事件",
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        )
    )

    try:
        # 保持连接，接收客户端心跳
        while True:
            data = await websocket.receive_text()
            # 处理客户端消息（如 ping）
            if data == "ping":
                await websocket.send_text(
                    json.dumps(
                        {
                            "event": "pong",
                            "timestamp": time.time(),
                        },
                        ensure_ascii=False,
                    )
                )
    except WebSocketDisconnect:
        logger.info(f"[WsManager] WebSocket 断开 | batch={batch_id}")
    except Exception as e:
        logger.warning(f"[WsManager] WebSocket 异常 | batch={batch_id} | error={e}")
    finally:
        await _ws_manager.unsubscribe(websocket, batch_id)
