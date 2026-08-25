"""
画布布局持久化服务 (CanvasService)

管理分镜画布的节点布局：
- 节点位置、尺寸
- 连线关系
- 视口状态（缩放、平移）
- 按项目/场景保存和恢复
"""

from services.paths import CANVAS_DIR

import json
import logging
import os
import time
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 画布数据存储路径
CANVAS_DATA_DIR = CANVAS_DIR  # T7: 收敛

# CanvasNode 允许的字段名
_NODE_FIELDS = {
    "node_id",
    "asset_id",
    "node_type",
    "x",
    "y",
    "width",
    "height",
    "label",
    "metadata",
}  # noqa: E501
_EDGE_FIELDS = {"edge_id", "source_id", "target_id", "source_port", "target_port", "label"}
_VIEWPORT_FIELDS = {"x", "y", "zoom", "scale"}


@dataclass
class CanvasNode:
    """画布节点"""

    node_id: str
    asset_id: str = ""
    node_type: str = "image"  # image / video / text / group
    x: float = 0.0
    y: float = 0.0
    width: float = 240.0
    height: float = 180.0
    label: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = asdict(self)
        # canvas.js 期望的字段名（保留 node_id/node_type 以便磁盘持久化）
        result["id"] = self.node_id
        result["type"] = self.node_type
        result["w"] = self.width
        result["h"] = self.height
        result["title"] = self.label
        result["name"] = self.label
        # 从 metadata 提取图片 URL（兼容 urls / asset_urls / url / image_url）
        urls = self.metadata.get("urls", []) if self.metadata else []
        if not urls:
            urls = self.metadata.get("asset_urls", []) if self.metadata else []
        result["url"] = (
            urls[0] if urls else (self.metadata.get("url") or self.metadata.get("image_url") or "")
        )  # noqa: E501
        # 将 metadata 中的额外字段展开到顶层（如 comfyParams 等）
        if self.metadata:
            for k, v in self.metadata.items():
                if k not in _NODE_FIELDS and k not in ("urls", "url"):
                    result[k] = v
        return result


@dataclass
class CanvasEdge:
    """画布连线"""

    edge_id: str
    source_id: str
    target_id: str
    source_port: str = "output"
    target_port: str = "input"
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CanvasViewport:
    """画布视口"""

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0

    def to_dict(self) -> dict:
        result = asdict(self)
        # canvas.js 用 scale，后端用 zoom
        result["scale"] = self.zoom
        return result


@dataclass
class CanvasLayout:
    """画布布局"""

    canvas_id: str
    name: str = "未命名画布"
    nodes: List[CanvasNode] = field(default_factory=list)
    edges: List[CanvasEdge] = field(default_factory=list)
    viewport: CanvasViewport = field(default_factory=CanvasViewport)
    created_at: float = 0.0
    updated_at: float = 0.0
    logs: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "canvas_id": self.canvas_id,
            "name": self.name,
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "viewport": self.viewport.to_dict(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "logs": self.logs,
        }


class CanvasService:
    """画布布局持久化服务"""

    # 防抖写入间隔（秒）：高频 update_node 时，延迟合并写入磁盘
    DEBOUNCE_INTERVAL = 1.0

    def __init__(self):
        self._canvases: Dict[str, CanvasLayout] = {}
        self._lock = asyncio.Lock()  # 并发写入锁
        self._debounce_timers: Dict[str, asyncio.TimerHandle] = {}  # canvas_id → 防抖定时器
        self._debounce_broadcast_timers: Dict[str, asyncio.TimerHandle] = {}  # 广播防抖定时器
        os.makedirs(CANVAS_DATA_DIR, exist_ok=True)
        self._load_all()

    def _load_all(self):
        """从磁盘加载所有画布"""
        if not os.path.exists(CANVAS_DATA_DIR):
            return
        for fname in os.listdir(CANVAS_DATA_DIR):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(CANVAS_DATA_DIR, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    layout = self._dict_to_layout(data)
                    self._canvases[layout.canvas_id] = layout
                except Exception as e:
                    logger.warning(f"[CanvasService] 加载画布失败: {fname} | {e}")

    def _dict_to_layout(self, data: dict) -> CanvasLayout:
        """字典转 CanvasLayout"""
        # CanvasNode 的合法字段，过滤掉 canvas.js 的额外字段
        _NODE_FIELDS = {
            "node_id",
            "asset_id",
            "node_type",
            "x",
            "y",
            "width",
            "height",
            "label",
            "metadata",
        }  # noqa: E501
        nodes = []
        for n in data.get("nodes", []):
            filtered = {k: v for k, v in n.items() if k in _NODE_FIELDS}
            nodes.append(CanvasNode(**filtered))
        edges = [CanvasEdge(**e) for e in data.get("edges", [])]
        # CanvasViewport 的合法字段，过滤掉 scale
        vp_data = data.get("viewport", {})
        vp_filtered = {k: v for k, v in vp_data.items() if k in ("x", "y", "zoom")}
        viewport = CanvasViewport(**vp_filtered) if vp_filtered else CanvasViewport()
        return CanvasLayout(
            canvas_id=data["canvas_id"],
            name=data.get("name", "未命名画布"),
            nodes=nodes,
            edges=edges,
            viewport=viewport,
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            logs=data.get("logs", []) if isinstance(data.get("logs"), list) else [],
        )

    def _save(self, canvas_id: str):
        """保存画布到磁盘（原子写入：先写临时文件 → os.rename 替换）"""
        layout = self._canvases.get(canvas_id)
        if not layout:
            return
        path = os.path.join(CANVAS_DATA_DIR, f"{canvas_id}.json")
        tmp_path = path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(layout.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception as e:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            logger.error(f"[CanvasService] 保存画布失败: {canvas_id} | {e}")

    async def _broadcast(self, canvas_id: str, action: str, data: dict = None):
        """向 /api/ws/canvas 订阅客户端广播画布变更（fire-and-forget）。

        事件经 core.ws_manager 的 pipeline 频道下发，前端按 canvas_id 过滤。
        """
        try:
            from core.ws_manager import get_ws_manager

            await get_ws_manager().broadcast_channel(
                "pipeline",
                {
                    "type": "canvas_update",
                    "canvas_id": canvas_id,
                    "action": action,
                    "data": data or {},
                },
            )
        except Exception as e:
            logger.warning(f"[CanvasService] WS 广播失败: {e}")

    def _debounced_save(self, canvas_id: str):
        """防抖写入：延迟 DEBOUNCE_INTERVAL 秒后执行磁盘写入，期间再次调用会重置计时器。
        适用于高频 update_node（拖拽节点）场景，合并多次写入为一次。"""
        # 取消已有的定时器
        old_timer = self._debounce_timers.get(canvas_id)
        if old_timer and not old_timer.cancelled():
            old_timer.cancel()
        # 创建新的延迟写入定时器
        loop = asyncio.get_event_loop()
        self._debounce_timers[canvas_id] = loop.call_later(
            self.DEBOUNCE_INTERVAL, lambda: asyncio.ensure_future(self._flush_save(canvas_id))
        )

    # 广播防抖定时器在 __init__ 中初始化为实例变量

    def _debounced_broadcast(self, canvas_id: str, action: str, data: dict = None):
        """防抖广播：延迟 DEBOUNCE_INTERVAL 秒后广播，期间再次调用会重置计时器。"""
        key = f"{canvas_id}:{action}"
        old_timer = self._debounce_broadcast_timers.get(key)
        if old_timer and not old_timer.cancelled():
            old_timer.cancel()
        loop = asyncio.get_event_loop()
        self._debounce_broadcast_timers[key] = loop.call_later(
            self.DEBOUNCE_INTERVAL,
            lambda: asyncio.ensure_future(self._broadcast(canvas_id, action, data)),
        )

    async def _flush_save(self, canvas_id: str):
        """执行防抖后的实际磁盘写入"""
        self._debounce_timers.pop(canvas_id, None)
        await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)

    async def create(
        self,
        name: str = "未命名画布",
        canvas_id: Optional[str] = None,
        nodes: Optional[List[Dict[str, Any]]] = None,
        edges: Optional[List[Dict[str, Any]]] = None,
        viewport: Optional[Dict[str, Any]] = None,
    ) -> CanvasLayout:
        """创建新画布

        Args:
            name: 画布名称
            canvas_id: 指定画布 ID（用于从回收站恢复），不传则自动生成
            nodes/edges/viewport: 初始布局数据（用于恢复场景）
        """
        import uuid

        if canvas_id is None:
            canvas_id = f"canvas_{uuid.uuid4().hex[:8]}"
        now = time.time()
        layout = CanvasLayout(
            canvas_id=canvas_id,
            name=name,
            created_at=now,
            updated_at=now,
        )
        # 填充初始数据（用于从回收站恢复）
        if nodes:
            for n in nodes:
                filtered = {k: v for k, v in n.items() if k in _NODE_FIELDS}
                try:
                    layout.nodes.append(CanvasNode(**filtered))
                except Exception as e:
                    logger.warning(f"[CanvasService] 恢复节点失败: {e}")
        if edges:
            for e in edges:
                filtered = {k: v for k, v in e.items() if k in _EDGE_FIELDS}
                try:
                    layout.edges.append(CanvasEdge(**filtered))
                except Exception as e:
                    logger.warning(f"[CanvasService] 恢复连线失败: {e}")
        if viewport:
            for k in _VIEWPORT_FIELDS:
                if k in viewport:
                    setattr(layout.viewport, k, viewport[k])
        async with self._lock:
            self._canvases[canvas_id] = layout
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "created", {"name": name})
        logger.info(f"[CanvasService] 创建画布 | id={canvas_id} | name={name}")
        return layout

    def get(self, canvas_id: str) -> Optional[CanvasLayout]:
        """获取画布"""
        return self._canvases.get(canvas_id)

    def list_canvases(self) -> List[dict]:
        """列出所有画布"""
        return [
            {
                "id": c.canvas_id,
                "canvas_id": c.canvas_id,
                "title": c.name,
                "name": c.name,
                "node_count": len(c.nodes),
                "edge_count": len(c.edges),
                "created_at": c.created_at,
                "updated_at": c.updated_at,
            }
            for c in sorted(self._canvases.values(), key=lambda x: x.updated_at, reverse=True)
        ]

    async def update_layout(self, canvas_id: str, data: dict) -> Optional[CanvasLayout]:
        """更新画布布局（节点、连线、视口）

        支持乐观锁：若 data 中包含 base_updated_at，则与服务端 layout.updated_at 比较，
        不一致时返回特殊标记 _conflict=True 的字典而非 CanvasLayout。
        """
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return None

            # 乐观锁冲突检测
            base_updated_at = data.get("base_updated_at")
            if base_updated_at is not None:
                base_ts = float(base_updated_at)
                # 容忍 1 秒内的时钟偏差
                if abs(layout.updated_at - base_ts) > 1.0:
                    return {
                        "_conflict": True,
                        "server_updated_at": layout.updated_at,
                        "canvas": layout,
                    }  # noqa: E501

            if "name" in data:
                layout.name = data["name"]
            if "nodes" in data:
                # 将已知字段提取为 CanvasNode，未知字段合并到 metadata 中
                cleaned_nodes = []
                for n in data["nodes"]:
                    known = {k: v for k, v in n.items() if k in _NODE_FIELDS and k != "metadata"}
                    extra = {k: v for k, v in n.items() if k not in _NODE_FIELDS}
                    # 合并 metadata：保留原有 metadata 中的值，额外字段覆盖
                    meta = dict(n.get("metadata", {}) or {})
                    meta.update(extra)
                    known["metadata"] = meta
                    cleaned_nodes.append(CanvasNode(**known))
                layout.nodes = cleaned_nodes
            if "edges" in data:
                layout.edges = [
                    CanvasEdge(**{k: v for k, v in e.items() if k in _EDGE_FIELDS})
                    for e in data["edges"]
                ]  # noqa: E501
            if "viewport" in data:
                layout.viewport = CanvasViewport(
                    **{k: v for k, v in data["viewport"].items() if k in _VIEWPORT_FIELDS}
                )  # noqa: E501
            if "logs" in data and isinstance(data["logs"], list):
                layout.logs = data["logs"]

            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "layout_updated")
        return layout

    async def add_node(self, canvas_id: str, node_data: dict) -> Optional[CanvasNode]:
        """添加节点"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return None
            node = CanvasNode(**node_data)
            layout.nodes.append(node)
            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "node_added", {"node_id": node.node_id})
        return node

    async def update_node(self, canvas_id: str, node_id: str, data: dict) -> Optional[CanvasNode]:
        """更新节点"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return None
            for node in layout.nodes:
                if node.node_id == node_id:
                    for k, v in data.items():
                        if k == "metadata" and isinstance(v, dict):
                            # metadata 深度合并：保留原有字段，新字段覆盖
                            merged = dict(node.metadata or {})
                            merged.update(v)
                            node.metadata = merged
                        elif hasattr(node, k):
                            setattr(node, k, v)
                    layout.updated_at = time.time()
                    # 拖拽等高频操作使用防抖写入+防抖广播，合并多次磁盘 I/O 和 WS 消息
                    self._debounced_save(canvas_id)
                    self._debounced_broadcast(canvas_id, "node_updated", {"node_id": node_id})
                    return node
            return None

    async def remove_node(self, canvas_id: str, node_id: str) -> bool:
        """删除节点"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return False
            layout.nodes = [n for n in layout.nodes if n.node_id != node_id]
            layout.edges = [
                e for e in layout.edges if e.source_id != node_id and e.target_id != node_id
            ]
            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "node_removed", {"node_id": node_id})
        return True

    async def touch(self, canvas_id: str) -> bool:
        """更新画布的 updated_at 时间戳（线程安全，加锁防止并发写入冲突）"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return False
            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        return True

    async def remove_nodes_by_asset(self, asset_id: str) -> int:
        """删除所有画布中引用指定资产的节点（资产删除时调用，防止孤立引用）"""
        removed_total = 0
        async with self._lock:
            for canvas_id, layout in list(self._canvases.items()):
                orphan_ids = [n.node_id for n in layout.nodes if n.asset_id == asset_id]
                if not orphan_ids:
                    continue
                layout.nodes = [n for n in layout.nodes if n.asset_id != asset_id]
                layout.edges = [
                    e
                    for e in layout.edges
                    if e.source_id not in orphan_ids and e.target_id not in orphan_ids
                ]
                layout.updated_at = time.time()
                await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
                removed_total += len(orphan_ids)
                logger.info(
                    f"[CanvasService] 清理孤立节点 | canvas={canvas_id} | "
                    f"asset_id={asset_id} | removed={len(orphan_ids)}"
                )
        if removed_total:
            await self._broadcast(
                "", "orphan_nodes_removed", {"asset_id": asset_id, "count": removed_total}
            )  # noqa: E501
        return removed_total

    async def add_edge(self, canvas_id: str, edge_data: dict) -> Optional[CanvasEdge]:
        """添加连线"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return None
            edge = CanvasEdge(**edge_data)
            layout.edges.append(edge)
            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "edge_added", {"edge_id": edge.edge_id})
        return edge

    async def remove_edge(self, canvas_id: str, edge_id: str) -> bool:
        """删除连线"""
        async with self._lock:
            layout = self._canvases.get(canvas_id)
            if not layout:
                return False
            layout.edges = [e for e in layout.edges if e.edge_id != edge_id]
            layout.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save, canvas_id)
        await self._broadcast(canvas_id, "edge_removed", {"edge_id": edge_id})
        return True

    async def delete(self, canvas_id: str) -> bool:
        """删除画布"""
        async with self._lock:
            if canvas_id not in self._canvases:
                return False
            del self._canvases[canvas_id]
            path = os.path.join(CANVAS_DATA_DIR, f"{canvas_id}.json")
            try:
                os.remove(path)
            except Exception:
                pass
        await self._broadcast(canvas_id, "deleted")
        logger.info(f"[CanvasService] 删除画布 | id={canvas_id}")
        return True


# ============================================================
# 单例
# ============================================================

_instance: Optional[CanvasService] = None


def get_canvas_service() -> CanvasService:
    global _instance
    if _instance is None:
        _instance = CanvasService()
    return _instance


def reset_canvas_service():
    """重置单例，用于单元测试隔离"""
    global _instance
    _instance = None
