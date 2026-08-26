"""爆款拆解库 API（① 选题层）：半自动（工具采集）+ 手动补充"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import Hit
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/hits", tags=["流量侧-爆款拆解库"])


@router.post("", response_model=Hit)
async def create_hit(hit: Hit) -> Hit:
    """添加爆款拆解记录（source=auto 由工具拆解上报；manual 手动补充）。"""
    return get_collection("hits").insert(hit)


@router.get("", response_model=List[Hit])
async def list_hits(
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> List[Hit]:
    col = get_collection("hits")
    records = col.list()
    if source:
        records = [r for r in records if r.get("source") == source]
    records.sort(key=lambda r: r.get("created_at", 0.0), reverse=True)
    return [Hit(**r) for r in records[:limit]]


@router.get("/{hit_id}", response_model=Hit)
async def get_hit(hit_id: str) -> Hit:
    rec = get_collection("hits").get(hit_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"hit not found: {hit_id}")
    return Hit(**rec)


@router.delete("/{hit_id}")
async def delete_hit(hit_id: str) -> dict:
    if not get_collection("hits").delete(hit_id):
        raise HTTPException(status_code=404, detail=f"hit not found: {hit_id}")
    return {"success": True}
