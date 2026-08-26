"""账号矩阵配置 API（维度 × 变现 × 人设 × 节奏）"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from app.models import AccountConfig
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/accounts", tags=["流量侧-账号矩阵"])


@router.get("", response_model=List[AccountConfig])
async def list_accounts() -> List[AccountConfig]:
    return [AccountConfig(**r) for r in get_collection("accounts").list()]


@router.post("", response_model=AccountConfig)
async def create_account(cfg: AccountConfig) -> AccountConfig:
    return get_collection("accounts").insert(cfg)


@router.get("/{acc_id}", response_model=AccountConfig)
async def get_account(acc_id: str) -> AccountConfig:
    rec = get_collection("accounts").get(acc_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"account not found: {acc_id}")
    return AccountConfig(**rec)


@router.put("/{acc_id}", response_model=AccountConfig)
async def update_account(acc_id: str, patch: AccountConfig) -> AccountConfig:
    col = get_collection("accounts")
    cur = col.get(acc_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"account not found: {acc_id}")
    data = patch.model_dump(exclude_unset=True)
    data["id"] = acc_id
    updated = col.update(acc_id, data)
    return AccountConfig(**updated)


@router.delete("/{acc_id}")
async def delete_account(acc_id: str) -> dict:
    if not get_collection("accounts").delete(acc_id):
        raise HTTPException(status_code=404, detail=f"account not found: {acc_id}")
    return {"success": True}
