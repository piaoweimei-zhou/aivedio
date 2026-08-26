"""数据回传 API（⑤ 数据层，B7）：内容表现指标采集"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import MetricRecord
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/metrics", tags=["流量侧-数据回传"])


@router.post("", response_model=MetricRecord)
async def report_metric(rec: MetricRecord) -> MetricRecord:
    """回传单条内容表现（流量 + 变现转化）。"""
    return get_collection("metrics").insert(rec)


@router.post("/batch", response_model=Dict[str, int])
async def report_metric_batch(recs: List[MetricRecord]) -> Dict[str, int]:
    """批量回传（发布/数据采集端定时同步）。"""
    col = get_collection("metrics")
    for r in recs:
        col.insert(r)
    return {"inserted": len(recs)}


@router.get("", response_model=List[MetricRecord])
async def list_metrics(
    content_id: Optional[str] = Query(default=None),
    account_id: Optional[str] = Query(default=None),
    dimension: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=1000),
) -> List[MetricRecord]:
    col = get_collection("metrics")
    records = col.list()
    if content_id:
        records = [r for r in records if r.get("content_id") == content_id]
    if account_id:
        records = [r for r in records if r.get("account_id") == account_id]
    if dimension:
        records = [r for r in records if r.get("dimension") == dimension]
    records.sort(key=lambda r: r.get("collected_at", 0.0), reverse=True)
    return [MetricRecord(**r) for r in records[:limit]]


@router.get("/{met_id}", response_model=MetricRecord)
async def get_metric(met_id: str) -> MetricRecord:
    rec = get_collection("metrics").get(met_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"metric not found: {met_id}")
    return MetricRecord(**rec)


@router.delete("/{met_id}")
async def delete_metric(met_id: str) -> dict:
    if not get_collection("metrics").delete(met_id):
        raise HTTPException(status_code=404, detail=f"metric not found: {met_id}")
    return {"success": True}
