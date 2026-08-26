"""发布层 API（④ 发布层，B6 半自动）：发布包生成 + PublishJob 管理"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import Dimension, Monetizer, PublishJob
from app.publishing import build_publish_package
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/publish", tags=["流量侧-发布层"])


@router.post("/package")
async def generate_package(
    title: str,
    video_path: str,
    dimension: Optional[Dimension] = None,
    monetizer: Optional[Monetizer] = None,
    cover_style: str = "",
    account_id: str = "",
    topic_id: str = "",
    content_id: str = "",
    platform: str = "douyin",
) -> Dict[str, Any]:
    """半自动发布：生成发布包（视频+封面+标题+文案+清单）。

    支持平台（P1c）：douyin/kuaishou/bilibili/xiaohongshu，
    话题与发布注意按平台差异化；manifest 含平台归因。
    发布包目录下的 manifest.json 即全自动发布的机器输入。
    """
    try:
        return build_publish_package(
            title=title,
            video_path=video_path,
            dimension=dimension,
            monetizer=monetizer,
            cover_style=cover_style,
            account_id=account_id,
            topic_id=topic_id,
            content_id=content_id,
            platform=platform,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------- PublishJob（半自动：手动发布后标记 published）----------

@router.post("/jobs", response_model=PublishJob)
async def create_job(job: PublishJob) -> PublishJob:
    """创建发布任务（半自动：status=pending，等待手动发布）。"""
    return get_collection("publish_jobs").insert(job)


@router.get("/jobs", response_model=List[PublishJob])
async def list_jobs(
    status: Optional[str] = Query(default=None),
    account_id: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> List[PublishJob]:
    col = get_collection("publish_jobs")
    records = col.list()
    if status:
        records = [r for r in records if r.get("status") == status]
    if account_id:
        records = [r for r in records if r.get("account_id") == account_id]
    if platform:
        records = [r for r in records if r.get("platform") == platform]
    records.sort(key=lambda r: r.get("created_at", 0.0), reverse=True)
    return [PublishJob(**r) for r in records[:limit]]


@router.put("/jobs/{job_id}/mark-published", response_model=PublishJob)
async def mark_published(job_id: str, note: str = "") -> PublishJob:
    """手动发布完成后标记 published（半自动路径的闭环动作）。"""
    col = get_collection("publish_jobs")
    cur = col.get(job_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    data = dict(cur)
    data["status"] = "published"
    data["result"] = {"published_at": time.time(), "note": note, "mode": "semi_auto"}
    return PublishJob(**col.update(job_id, data))


@router.put("/jobs/{job_id}/status", response_model=PublishJob)
async def update_job_status(job_id: str, status: str) -> PublishJob:
    """通用状态流转（pending/publishing/published/failed）。"""
    col = get_collection("publish_jobs")
    cur = col.get(job_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"job not found: {job_id}")
    data = dict(cur)
    data["status"] = status
    return PublishJob(**col.update(job_id, data))
