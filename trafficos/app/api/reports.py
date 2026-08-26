"""ROI 周报 API（P1d）：周聚合报告 + markdown/JSON 输出。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from app.report import to_markdown, weekly_report
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/reports", tags=["流量侧-ROI周报"])


@router.get("/weekly")
async def get_weekly_report(
    days: int = Query(default=7, ge=1, le=90),
    format: str = Query(default="json", description="json/markdown"),
    top_n: int = Query(default=5, ge=1, le=20),
) -> Dict[str, Any]:
    """近 N 天 ROI 周报（总览+按账号/维度/变现+Top内容+建议）。"""
    records = get_collection("metrics").list()
    report = weekly_report(records, days=days, top_n=top_n)
    if format == "markdown":
        return {"format": "markdown", "markdown": to_markdown(report), "report": report}
    return {"format": "json", "report": report}
