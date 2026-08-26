"""内容维度配置 API（ⓞ 内容战略层）"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from app.models import DimensionConfig
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/dimensions", tags=["流量侧-维度配置"])


@router.get("", response_model=List[DimensionConfig])
async def list_dimensions() -> List[DimensionConfig]:
    return [DimensionConfig(**r) for r in get_collection("dimensions").list()]


@router.post("", response_model=DimensionConfig)
async def create_dimension(cfg: DimensionConfig) -> DimensionConfig:
    return get_collection("dimensions").insert(cfg)


@router.get("/{dim_id}", response_model=DimensionConfig)
async def get_dimension(dim_id: str) -> DimensionConfig:
    rec = get_collection("dimensions").get(dim_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"dimension not found: {dim_id}")
    return DimensionConfig(**rec)


@router.put("/{dim_id}", response_model=DimensionConfig)
async def update_dimension(dim_id: str, patch: DimensionConfig) -> DimensionConfig:
    col = get_collection("dimensions")
    cur = col.get(dim_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"dimension not found: {dim_id}")
    data = patch.model_dump(exclude_unset=True)
    data["id"] = dim_id
    updated = col.update(dim_id, data)
    return DimensionConfig(**updated)


@router.delete("/{dim_id}")
async def delete_dimension(dim_id: str) -> dict:
    if not get_collection("dimensions").delete(dim_id):
        raise HTTPException(status_code=404, detail=f"dimension not found: {dim_id}")
    return {"success": True}
