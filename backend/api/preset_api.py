"""
导演工作台 — 预设 API

保存/管理任务参数预设，支持项目级默认预设。

端点：
- GET    /api/director/presets                列出预设（支持 project_id / stage_id 过滤）
- GET    /api/director/presets/{id}           获取预设详情
- POST   /api/director/presets                创建预设
- PUT    /api/director/presets/{id}           更新预设
- DELETE /api/director/presets/{id}           删除预设
- POST   /api/director/presets/{id}/apply     应用预设（返回参数快照）
- POST   /api/director/presets/{id}/set-default  设置为项目默认预设
- GET    /api/director/presets/default        获取项目默认预设
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.preset_service import get_preset_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/presets", tags=["导演工作台-预设"])


# ==================== Request Models ====================

class CreatePresetRequest(BaseModel):
    name: str
    stage_id: str
    project_id: str = ""
    provider_id: str = ""
    params: Dict[str, Any] = {}
    reference_asset_ids: List[str] = []
    description: str = ""
    is_default: bool = False
    metadata: Dict[str, Any] = {}


class UpdatePresetRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    stage_id: Optional[str] = None
    provider_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    reference_asset_ids: Optional[List[str]] = None
    project_id: Optional[str] = None


class SetDefaultRequest(BaseModel):
    project_id: str


# ==================== Endpoints ====================

@router.get("")
async def list_presets(
    project_id: str = Query(""),
    stage_id: str = Query(""),
):
    """列出预设"""
    svc = get_preset_service()
    presets = svc.list_presets(project_id=project_id, stage_id=stage_id)
    return {
        "success": True,
        "presets": [p.to_dict() for p in presets],
        "total": len(presets),
    }


@router.get("/default")
async def get_default_preset(
    project_id: str = Query(""),
    stage_id: str = Query(""),
):
    """获取项目默认预设"""
    svc = get_preset_service()
    preset = svc.get_default(project_id=project_id, stage_id=stage_id)
    return {
        "success": True,
        "preset": preset.to_dict() if preset else None,
    }


@router.get("/{preset_id}")
async def get_preset(preset_id: str):
    """获取预设详情"""
    svc = get_preset_service()
    preset = svc.get(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"success": True, "preset": preset.to_dict()}


@router.post("")
async def create_preset(request: CreatePresetRequest):
    """创建预设"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="预设名称不能为空")
    if not request.stage_id:
        raise HTTPException(status_code=400, detail="stage_id 不能为空")

    svc = get_preset_service()
    preset = svc.create(
        name=request.name.strip(),
        stage_id=request.stage_id,
        project_id=request.project_id,
        provider_id=request.provider_id,
        params=request.params,
        reference_asset_ids=request.reference_asset_ids,
        description=request.description,
        is_default=request.is_default,
        metadata=request.metadata,
    )
    logger.info(f"[PresetAPI] 创建预设 | id={preset.preset_id} | name={preset.name}")
    return {"success": True, "preset": preset.to_dict()}


@router.put("/{preset_id}")
async def update_preset(preset_id: str, request: UpdatePresetRequest):
    """更新预设"""
    svc = get_preset_service()
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    preset = svc.update(preset_id, updates)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"success": True, "preset": preset.to_dict()}


@router.delete("/{preset_id}")
async def delete_preset(preset_id: str):
    """删除预设"""
    svc = get_preset_service()
    ok = svc.delete(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"success": True, "message": "预设已删除"}


@router.post("/{preset_id}/apply")
async def apply_preset(preset_id: str):
    """应用预设 — 返回参数快照供前端填充表单"""
    svc = get_preset_service()
    snapshot = svc.apply(preset_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"success": True, "snapshot": snapshot}


@router.post("/{preset_id}/set-default")
async def set_default_preset(preset_id: str, request: SetDefaultRequest):
    """设置为项目默认预设"""
    svc = get_preset_service()
    ok = svc.set_default(preset_id, request.project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"success": True, "message": "已设为项目默认预设"}
