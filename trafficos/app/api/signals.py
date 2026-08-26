"""需求信号 API（ⓢ 工具传感器）：工具上报（脱敏聚合）+ 查询 + top 关键词"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.models import Signal
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/signals", tags=["流量侧-需求信号"])

# 注：工具端上报时应为脱敏聚合数据（领域/关键词热度），不采集个人信息。
# 生产环境应给本组接口加独立鉴权（TRAFFICOS_SIGNAL_API_KEY）。


@router.post("", response_model=Signal)
async def report_signal(sig: Signal) -> Signal:
    """工具上报需求信号（去水印等工具，脱敏聚合）。"""
    return get_collection("signals").insert(sig)


@router.post("/batch", response_model=Dict[str, int])
async def report_signal_batch(sigs: List[Signal]) -> Dict[str, int]:
    """批量上报（工具端定时聚合一并上报）。"""
    col = get_collection("signals")
    for s in sigs:
        col.insert(s)
    return {"inserted": len(sigs)}


@router.get("", response_model=List[Signal])
async def list_signals(
    field: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=1000),
) -> List[Signal]:
    col = get_collection("signals")
    records = col.list()
    if field:
        records = [r for r in records if r.get("field") == field]
    records.sort(key=lambda r: r.get("heat", 0.0), reverse=True)
    return [Signal(**r) for r in records[:limit]]


@router.get("/top-keywords")
async def top_keywords(
    field: Optional[str] = Query(default=None),
    top: int = Query(default=20, le=100),
) -> Dict[str, Any]:
    """聚合 top 关键词（按关键词去重求和 heat），供选题引擎消费。"""
    col = get_collection("signals")
    records = col.list()
    if field:
        records = [r for r in records if r.get("field") == field]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in records:
        kw = r.get("keyword", "")
        if not kw:
            continue
        entry = agg.setdefault(kw, {
            "keyword": kw, "heat": 0.0, "count": 0, "field": r.get("field", ""),
        })
        entry["heat"] += float(r.get("heat", 0.0))
        entry["count"] += 1
    items = sorted(agg.values(), key=lambda e: e["heat"], reverse=True)[:top]
    return {"top": items, "total_keywords": len(agg)}
