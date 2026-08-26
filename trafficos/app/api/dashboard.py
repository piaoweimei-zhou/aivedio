"""数据看板 API（⑤ 数据层，B7）：总览 / 维度 / 账号 / 内容 / ROI 周报

看板三层下钻：内容 → 账号 → 维度；ROI = 收益 / 内容投入。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.analytics import aggregate, group_by
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/dashboard", tags=["流量侧-数据看板"])


def _records(dimension: Optional[str] = None, account_id: Optional[str] = None) -> List[dict]:
    col = get_collection("metrics")
    records = col.list()
    if dimension:
        records = [r for r in records if r.get("dimension") == dimension]
    if account_id:
        records = [r for r in records if r.get("account_id") == account_id]
    return records


@router.get("/overview")
async def overview() -> Dict[str, Any]:
    """总览：全量聚合 + 各维度占比。"""
    records = _records()
    agg = aggregate(records)
    by_dim = group_by(records, "dimension")
    total_revenue = agg["revenue"]
    dims = []
    for d in by_dim:
        dims.append({
            "dimension": d["group"],
            "revenue": d["revenue"],
            "views": d["views"],
            "contents": d["contents"],
            "conversion_total": d["conversion_total"],
            "revenue_share": round(d["revenue"] / total_revenue, 4) if total_revenue else 0.0,
        })
    return {"overall": agg, "dimensions": dims, "generated_at": _ts()}


@router.get("/by-dimension")
async def by_dimension(dimension: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    """按维度聚合（含 ROI），按 revenue 降序。"""
    return group_by(_records(dimension=dimension), "dimension")


@router.get("/by-account")
async def by_account(dimension: Optional[str] = Query(default=None)) -> List[Dict[str, Any]]:
    """按账号聚合（含 ROI），按 revenue 降序。"""
    return group_by(_records(dimension=dimension), "account_id")


@router.get("/by-content")
async def by_content(
    dimension: Optional[str] = Query(default=None),
    account_id: Optional[str] = Query(default=None),
) -> List[Dict[str, Any]]:
    """单条内容表现（最细粒度归因）。"""
    records = _records(dimension=dimension, account_id=account_id)
    items = []
    for r in records:
        agg = aggregate([r])
        items.append({
            "content_id": r.get("content_id"),
            "account_id": r.get("account_id"),
            "dimension": r.get("dimension"),
            "monetizer": r.get("monetizer"),
            "views": agg["views"],
            "conversion_total": agg["conversion_total"],
            "revenue": agg["revenue"],
            "roi": agg["roi"],
            "conversions": agg["conversions"],
        })
    items.sort(key=lambda x: x["revenue"], reverse=True)
    return items


@router.get("/roi-report")
async def roi_report() -> Dict[str, Any]:
    """ROI 归因周报：按维度 + 按账号，给出 收益/投入/ROI。"""
    records = _records()
    return {
        "summary": aggregate(records),
        "by_dimension": group_by(records, "dimension"),
        "by_account": group_by(records, "account_id"),
        "generated_at": _ts(),
        "note": "ROI = revenue / (cost_per_content × 内容条数)；cost_per_content 可经 env 配置",
    }


def _ts() -> float:
    import time
    return time.time()
