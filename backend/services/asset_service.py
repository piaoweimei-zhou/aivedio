"""
资产注册表服务 (AssetService)
导演工作台 Layer 1：统一管理所有类型的创作资产

核心职责：
- 资产 CRUD（创建/读取/更新/删除）
- 类型系统（开放字符串，可随时扩展）
- 版本化（非破坏性迭代）
- WebSocket 广播（资产变更通知）
"""

import json
import logging
import os
import time
import uuid
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 数据模型
# ============================================================

@dataclass
class AssetRef:
    """资产引用 — 资产注册表中的最小单元"""
    asset_id: str
    asset_type: str              # 生产阶段类型: concept/edit/storyboard/video/pose/lineart/depth/multi_view/pano
    name: str
    content_type: str = ""       # 内容类型: character/scene/prop/"" (空=无内容分类)
    urls: List[str] = field(default_factory=list)   # 图片/视频 URL 列表
    metadata: Dict[str, Any] = field(default_factory=dict)  # 附加元数据
    parent_id: Optional[str] = None     # 来源资产 ID（用于追踪生产链）
    project_id: Optional[str] = None    # 所属项目 ID（None=全局/未分类）
    version: int = 1                    # 版本号（非破坏性迭代）
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.asset_id:
            self.asset_id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at


@dataclass
class AssetProduceResult:
    """资产生产结果 — Stage 插件的输出"""
    asset: AssetRef
    success: bool = True
    error: Optional[str] = None
    elapsed_ms: int = 0
    prompt_id: str = ""  # ComfyUI prompt_id（用于反查生成历史）


# ============================================================
# 资产类型体系
# ============================================================

# 当前支持的资产类型（开放集合，可随时扩展）
# 生产阶段类型
STAGE_TYPES = {
    "concept":      {"label": "概念图",     "category": "image"},
    "edit":         {"label": "精修图",     "category": "image"},
    "multi_view":   {"label": "三视图",     "category": "image"},
    "pano":         {"label": "360全景",    "category": "image"},
    "lineart":      {"label": "线稿",       "category": "image"},
    "depth":        {"label": "深度图",     "category": "image"},
    "depth_clean":  {"label": "清场深度图", "category": "image"},
    "storyboard":   {"label": "分镜帧",     "category": "image"},
    "storyboard_multi": {"label": "多人分镜", "category": "image"},
    "storyboard_layered": {"label": "分层渲染", "category": "image"},
    "storyboard_batch": {"label": "批量分镜", "category": "image"},
    "template_production": {"label": "模板制作", "category": "image"},
    "csv":             {"label": "CSV脚本", "category": "data"},
    "script":          {"label": "AI剧本", "category": "data"},
    "pose":         {"label": "姿态",       "category": "image"},
    "mask":         {"label": "蒙版",       "category": "image"},
    "video":        {"label": "视频",       "category": "video"},
}

# 内容类型（描绘对象维度）
CONTENT_TYPES = {
    "character":    {"label": "角色"},
    "scene":        {"label": "场景"},
    "prop":         {"label": "道具"},
}

# 兼容旧代码：ASSET_TYPES = STAGE_TYPES + CONTENT_TYPES
ASSET_TYPES = {**STAGE_TYPES, **CONTENT_TYPES}


# ============================================================
# AssetService
# ============================================================

class AssetService:
    """资产注册表服务"""

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = storage_dir or os.path.join(
            os.path.dirname(__file__), "..", "assets"
        )
        self._assets: Dict[str, AssetRef] = {}
        self._ws_callbacks: List = []  # WebSocket 广播回调
        self._lock = asyncio.Lock()    # 并发写入锁
        self._load()

    # ── CRUD ──────────────────────────────────────────────

    async def create(
        self,
        asset_type: str,
        name: str,
        urls: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
        parent_id: Optional[str] = None,
        content_type: str = "",
        project_id: Optional[str] = None,
    ) -> AssetRef:
        """创建资产

        project_id 自动继承：若未显式传入且 parent_id 有值，则从父资产继承 project_id。
        这确保 Stage 产物（通过 parent_id 关联输入资产）自动归属同一项目。
        """
        # 项目归属继承：未显式传入时，从父资产继承
        if project_id is None and parent_id:
            parent = self._assets.get(parent_id)
            if parent and parent.project_id:
                project_id = parent.project_id

        async with self._lock:
            asset = AssetRef(
                asset_id=uuid.uuid4().hex[:12],
                asset_type=asset_type,
                content_type=content_type,
                name=name,
                urls=urls or [],
                metadata=metadata or {},
                parent_id=parent_id,
                project_id=project_id,
            )
            self._assets[asset.asset_id] = asset
            await asyncio.get_event_loop().run_in_executor(None, self._save)
        self._broadcast("asset:created", asset)
        logger.info(f"[AssetService] 创建资产 | type={asset_type} content_type={content_type} name={name} id={asset.asset_id} project={project_id or '-'}")
        return asset

    def get(self, asset_id: str) -> Optional[AssetRef]:
        """获取单个资产"""
        return self._assets.get(asset_id)

    def list_assets(
        self,
        asset_type: Optional[str] = None,
        category: Optional[str] = None,
        parent_id: Optional[str] = None,
        content_type: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> List[AssetRef]:
        """列出资产（支持过滤）

        project_id 过滤规则：
        - 传具体 ID：返回该项目下的资产
        - 传 "__none__"：返回无项目归属的资产（全局/未分类）
        - 不传（None）：返回所有资产（不过滤项目）
        """
        results = list(self._assets.values())
        if asset_type:
            results = [a for a in results if a.asset_type == asset_type]
        if content_type:
            results = [a for a in results if a.content_type == content_type]
        if category:
            results = [a for a in results if ASSET_TYPES.get(a.asset_type, {}).get("category") == category]
        if parent_id:
            results = [a for a in results if a.parent_id == parent_id]
        if project_id is not None:
            if project_id == "__none__":
                results = [a for a in results if not a.project_id]
            else:
                results = [a for a in results if a.project_id == project_id]
        return sorted(results, key=lambda a: a.updated_at, reverse=True)

    async def update(
        self,
        asset_id: str,
        name: Optional[str] = None,
        urls: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[AssetRef]:
        """更新资产（非破坏性迭代：版本号+1）"""
        async with self._lock:
            asset = self._assets.get(asset_id)
            if not asset:
                return None
            if name is not None:
                asset.name = name
            if urls is not None:
                asset.urls = urls
            if metadata is not None:
                asset.metadata.update(metadata)
            asset.version += 1
            asset.updated_at = time.time()
            await asyncio.get_event_loop().run_in_executor(None, self._save)
        self._broadcast("asset:updated", asset)
        return asset

    async def delete(self, asset_id: str) -> bool:
        """删除资产，并通知 CanvasService 清理引用该资产的孤立节点"""
        async with self._lock:
            asset = self._assets.pop(asset_id, None)
            if not asset:
                return False
            await asyncio.get_event_loop().run_in_executor(None, self._save)
        # 通知 CanvasService 清理引用该资产的节点
        try:
            from services.canvas_service import get_canvas_service
            canvas_svc = get_canvas_service()
            await canvas_svc.remove_nodes_by_asset(asset_id)
        except Exception as e:
            logger.warning(f"[AssetService] 通知 CanvasService 清理孤立节点失败: {e}")
        self._broadcast("asset:deleted", asset)
        return True

    # ── 生产-消费 ─────────────────────────────────────────

    def consume(self, asset_id: str) -> Optional[AssetRef]:
        """消费资产（获取引用，不删除）"""
        return self.get(asset_id)

    def consume_multi(self, asset_ids: List[str]) -> List[AssetRef]:
        """消费多个资产"""
        return [a for aid in asset_ids if (a := self.get(aid)) is not None]

    async def produce(
        self,
        asset_type: str,
        name: str,
        urls: List[str],
        parent_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
        content_type: str = "",
        project_id: Optional[str] = None,
    ) -> AssetRef:
        """生产新资产（Stage 插件的输出入口）"""
        return await self.create(
            asset_type=asset_type,
            name=name,
            urls=urls,
            metadata=metadata or {},
            parent_id=parent_id,
            content_type=content_type,
            project_id=project_id,
        )

    # ── 血缘追踪 ──────────────────────────────────────────

    def lineage(self, asset_id: str) -> List[AssetRef]:
        """获取资产的生产链（从源头到当前）"""
        chain = []
        current = self.get(asset_id)
        while current:
            chain.append(current)
            if current.parent_id:
                current = self.get(current.parent_id)
            else:
                break
        return list(reversed(chain))

    def children(self, asset_id: str) -> List[AssetRef]:
        """获取资产的所有衍生资产"""
        return [a for a in self._assets.values() if a.parent_id == asset_id]

    # ── WebSocket 广播 ────────────────────────────────────

    def on_change(self, callback):
        """注册资产变更回调"""
        self._ws_callbacks.append(callback)

    def _broadcast(self, event: str, asset: AssetRef):
        """广播资产变更"""
        for cb in self._ws_callbacks:
            try:
                cb(event, asset)
            except Exception as e:
                logger.warning(f"[AssetService] 广播回调异常: {e}")

    # ── 持久化 ────────────────────────────────────────────

    def _load(self):
        """从文件加载资产注册表"""
        db_path = os.path.join(self.storage_dir, "asset_registry.json")
        if not os.path.exists(db_path):
            os.makedirs(self.storage_dir, exist_ok=True)
            self._save()
            return
        try:
            with open(db_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("assets", []):
                asset = AssetRef(**item)
                self._assets[asset.asset_id] = asset
            logger.info(f"[AssetService] 加载 {len(self._assets)} 个资产")
        except Exception as e:
            logger.error(f"[AssetService] 加载资产注册表失败: {e}")
            self._assets = {}

    def _save(self):
        """保存资产注册表到文件（原子写入：先写临时文件 → os.rename 替换）"""
        os.makedirs(self.storage_dir, exist_ok=True)
        db_path = os.path.join(self.storage_dir, "asset_registry.json")
        tmp_path = db_path + ".tmp"
        data = {
            "assets": [
                {
                    "asset_id": a.asset_id,
                    "asset_type": a.asset_type,
                    "content_type": a.content_type,
                    "name": a.name,
                    "urls": a.urls,
                    "metadata": a.metadata,
                    "parent_id": a.parent_id,
                    "project_id": a.project_id,
                    "version": a.version,
                    "created_at": a.created_at,
                    "updated_at": a.updated_at,
                }
                for a in self._assets.values()
            ]
        }
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, db_path)
        except Exception as e:
            # 清理临时文件
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            logger.error(f"[AssetService] 保存资产注册表失败: {e}")

    # ── 统计 ──────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """资产统计信息"""
        type_counts = {}
        for a in self._assets.values():
            type_counts[a.asset_type] = type_counts.get(a.asset_type, 0) + 1
        return {
            "total": len(self._assets),
            "by_type": type_counts,
        }

    # ── 清理孤岛资产 ──────────────────────────────────────

    async def cleanup_orphaned(
        self,
        generated_dir: str = "",
        comfyui_output_dir: str = "",
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """清理图片文件已丢失的孤岛资产

        Args:
            generated_dir: 持久化图片目录（如果指定，也检查此目录）
            comfyui_output_dir: ComfyUI output 目录（如果指定，也检查此目录）
            dry_run: 仅统计不删除

        Returns:
            清理报告 {"checked": int, "removed": int, "kept": int, "orphaned_ids": List[str]}
        """
        search_dirs = []
        if generated_dir and os.path.isdir(generated_dir):
            search_dirs.append(generated_dir)
        if comfyui_output_dir and os.path.isdir(comfyui_output_dir):
            search_dirs.append(comfyui_output_dir)

        orphaned = []
        kept = 0
        for a in list(self._assets.values()):
            if not a.urls:
                # urls 为空 → 无图片关联，标记为孤岛
                orphaned.append(a.asset_id)
                continue
            found = False
            for url in a.urls:
                if not url:
                    continue
                # 从 URL 提取文件名
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                fname = params.get("filename", [None])[0] or url.rsplit("/", 1)[-1].split("?")[0]
                if not fname:
                    continue
                # 检查文件是否存在于任何搜索目录
                for d in search_dirs:
                    fpath = os.path.join(d, fname)
                    if os.path.isfile(fpath):
                        found = True
                        break
                if found:
                    break
            if found:
                kept += 1
            else:
                orphaned.append(a.asset_id)

        report = {
            "checked": len(self._assets),
            "removed": 0,
            "kept": kept,
            "orphaned_ids": orphaned,
            "dry_run": dry_run,
        }

        if not dry_run:
            for aid in orphaned:
                await self.delete(aid)
            report["removed"] = len(orphaned)

        return report


# ============================================================
# 单例
# ============================================================

_instance: Optional[AssetService] = None

def get_asset_service() -> AssetService:
    global _instance
    if _instance is None:
        _instance = AssetService()
    return _instance


def reset_asset_service():
    """重置单例，用于单元测试隔离"""
    global _instance
    _instance = None
