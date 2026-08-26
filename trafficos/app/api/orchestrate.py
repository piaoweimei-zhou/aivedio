# -*- coding: utf-8 -*-
"""热点自动编排 API：选题 → 脚本 → director contract 生产。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Query

from app.orchestrator import orchestrate_produce

router = APIRouter(prefix="/api/traffic/orchestrate", tags=["流量侧-编排"])


@router.post("/produce")
async def produce(
    dimension: str = Query("soft_ad", description="维度：pure_content/knowledge/soft_ad"),
    monetizer: str = Query("tool", description="变现：tool/adshare/netdisk/..."),
    platform: str = Query("douyin", description="目标平台：douyin/kuaishou/bilibili/xiaohongshu"),
    duration_s: float = Query(5.0, ge=4.0, le=60.0, description="单段时长（秒）"),
    account_id: str = Query("tool_1"),
) -> Dict[str, Any]:
    """一键：从热点选题 → 生成脚本 → 提交 director 生产。"""
    return orchestrate_produce(
        dimension=dimension,
        monetizer=monetizer,
        platform=platform,
        duration_s=duration_s,
        account_id=account_id,
    )
