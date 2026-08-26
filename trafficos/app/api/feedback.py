"""迭代回灌 API（P2a 权重 + P2b 配比）：一键回灌 + 权重/配比查看。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from app.feedback import (apply_ratio_adjustment, get_active_weights,
                          run_feedback)
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/feedback", tags=["流量侧-迭代飞轮"])


@router.post("/apply")
async def apply_feedback(
    days: int = Query(default=7, ge=1, le=90),
    force: bool = Query(default=False, description="ROI 为 0 或无数据时也强制重打分"),
) -> Dict[str, Any]:
    """一键迭代回灌：周 ROI → 自动调权重 → 重打选题分（全量可审计）。"""
    return run_feedback(days=days, force=force)


@router.get("/weights")
async def current_weights() -> Dict[str, Any]:
    """当前生效权重 + 历史审计。"""
    return {
        "active_weights": get_active_weights(),
        "history": get_collection("weight_history").list(),
    }


@router.post("/ratio")
async def ratio_adjustment(
    days: int = Query(default=7, ge=1, le=90),
    dry_run: bool = Query(default=True, description="默认只出建议；false 才写回配比"),
) -> Dict[str, Any]:
    """P2b 配比动态调整：维度 ROI → 配比增减建议（dry_run）或写回 dimensions.ratio。"""
    return apply_ratio_adjustment(days=days, dry_run=dry_run)
