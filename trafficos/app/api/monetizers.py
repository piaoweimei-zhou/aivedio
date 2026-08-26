"""变现方式配置 API（7 轨兜底变现）"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from app.models import MonetizerConfig
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/monetizers", tags=["流量侧-变现配置"])


@router.get("", response_model=List[MonetizerConfig])
async def list_monetizers() -> List[MonetizerConfig]:
    return [MonetizerConfig(**r) for r in get_collection("monetizers").list()]


@router.post("", response_model=MonetizerConfig)
async def create_monetizer(cfg: MonetizerConfig) -> MonetizerConfig:
    return get_collection("monetizers").insert(cfg)


@router.get("/{mon_id}", response_model=MonetizerConfig)
async def get_monetizer(mon_id: str) -> MonetizerConfig:
    rec = get_collection("monetizers").get(mon_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"monetizer not found: {mon_id}")
    return MonetizerConfig(**rec)


@router.put("/{mon_id}", response_model=MonetizerConfig)
async def update_monetizer(mon_id: str, patch: MonetizerConfig) -> MonetizerConfig:
    col = get_collection("monetizers")
    cur = col.get(mon_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"monetizer not found: {mon_id}")
    data = patch.model_dump(exclude_unset=True)
    data["id"] = mon_id
    updated = col.update(mon_id, data)
    return MonetizerConfig(**updated)


@router.delete("/{mon_id}")
async def delete_monetizer(mon_id: str) -> dict:
    if not get_collection("monetizers").delete(mon_id):
        raise HTTPException(status_code=404, detail=f"monetizer not found: {mon_id}")
    return {"success": True}
