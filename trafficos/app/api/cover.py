"""封面合成 API（③ 包装层，B5）：render + 已合成封面静态服务"""
from __future__ import annotations

import os
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.cover import render_cover

router = APIRouter(prefix="/api/traffic/cover", tags=["流量侧-封面合成"])


def _covers_dir() -> str:
    base = os.environ.get("TRAFFICOS_DATA_DIR", "") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data",
    )
    return os.path.join(base, "covers")


@router.post("/render")
async def render(
    title: str,
    cover_style: str = "",
    bg_url: Optional[str] = None,
) -> Dict[str, object]:
    """合成封面：背景（可选 bg_url）+ 标题大字 + 角标。"""
    return render_cover(title, cover_style, bg_url, output_dir=_covers_dir())


@router.get("/files/{filename}")
async def get_cover_file(filename: str) -> FileResponse:
    """访问已合成封面。"""
    # 防路径穿越：只允许纯文件名
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = os.path.join(_covers_dir(), filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"cover not found: {filename}")
    return FileResponse(path, media_type="image/jpeg")
