"""热点 API（P1a）：同步/查询/手动导入。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.hotspots import HotspotItem, items_to_topics, sync
from app.models import Dimension, Monetizer
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/hotspots", tags=["流量侧-热点"])


@router.post("/sync")
async def sync_hotspots(
    limit: int = Query(default=50, ge=1, le=200),
    source: Optional[str] = Query(default=None, description="只同步指定源"),
) -> Dict[str, Any]:
    """手动触发热点同步：拉取外部源 → 去重 → 写入选题库(source=hot) → 打分。"""
    source_names = [source] if source else None
    return sync(limit=limit, source_names=source_names)


@router.get("")
async def list_hot_topics(limit: int = Query(default=50, le=200)) -> List[Dict[str, Any]]:
    """最近入库的热点选题（按创建时间倒序）。"""
    col = get_collection("topics")
    rows = [t for t in col.list() if t.get("source") == "hot"]
    rows.sort(key=lambda r: r.get("created_at", 0), reverse=True)
    return rows[:limit]


@router.post("/import")
async def import_hotspots(
    items: List[Dict[str, Any]],
    dimension: Optional[Dimension] = None,
    monetizer: Optional[Monetizer] = None,
) -> Dict[str, Any]:
    """手动导入热点（离线兜底）：[{title, heat, url}] → 选题库。"""
    if not items:
        raise HTTPException(status_code=400, detail="items 不能为空")
    from app.api.topics import build_topic

    parsed = [
        HotspotItem(
            title=(i.get("title") or "").strip(),
            heat=float(i.get("heat") or 0),
            url=i.get("url") or "",
            source="manual",
        )
        for i in items
        if (i.get("title") or "").strip()
    ]
    if not parsed:
        raise HTTPException(status_code=400, detail="没有合法 title 的条目")

    existing = {t.get("title", "") for t in get_collection("topics").list()}
    col = get_collection("topics")
    new_count = 0
    for it in parsed:
        if it.title in existing:
            continue
        topic = items_to_topics([it], dimension=dimension, monetizer=monetizer)[0]
        topic = build_topic(topic)
        col.insert(topic)
        existing.add(it.title)
        new_count += 1
    return {"imported": new_count, "total": len(parsed)}
