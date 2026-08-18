"""
导演工作台 — 项目管理 API

提供项目 CRUD 和项目维度的资产聚合查询。
项目是资产和任务的归属维度，用于支持批量化和自动化。

端点：
- POST   /api/director/projects          创建项目
- GET    /api/director/projects          列出项目
- GET    /api/director/projects/{id}     获取项目详情
- PUT    /api/director/projects/{id}     更新项目
- DELETE /api/director/projects/{id}     删除项目
- GET    /api/director/projects/{id}/assets   获取项目下所有资产
- GET    /api/director/projects/{id}/stats    获取项目统计
- POST   /api/director/projects/{id}/assets/{asset_id}  将资产加入项目
- DELETE /api/director/projects/{id}/assets/{asset_id}  将资产移出项目
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.project_service import get_project_service
from services.asset_service import get_asset_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/projects", tags=["导演工作台-项目"])


# ==================== Request Models ====================

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    metadata: Dict[str, Any] = {}


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ==================== Helper ====================

def _project_dict(project) -> Dict[str, Any]:
    return project.to_dict()


def _asset_dict(asset) -> Dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "asset_type": asset.asset_type,
        "content_type": asset.content_type,
        "name": asset.name,
        "urls": asset.urls,
        "metadata": asset.metadata,
        "parent_id": asset.parent_id,
        "project_id": getattr(asset, "project_id", None),
        "version": asset.version,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


# ==================== CRUD Endpoints ====================

@router.post("")
async def create_project(request: CreateProjectRequest):
    """创建项目"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    svc = get_project_service()
    project = svc.create(
        name=request.name.strip(),
        description=request.description,
        metadata=request.metadata,
    )
    logger.info(f"[ProjectAPI] 创建项目 | id={project.project_id} | name={project.name}")
    return {"success": True, "project": _project_dict(project)}


@router.get("")
async def list_projects(status: Optional[str] = Query(None)):
    """列出项目"""
    svc = get_project_service()
    projects = svc.list_projects(status=status)
    return {"projects": [_project_dict(p) for p in projects], "count": len(projects)}


@router.get("/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    svc = get_project_service()
    project = svc.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    return {"project": _project_dict(project)}


@router.put("/{project_id}")
async def update_project(project_id: str, request: UpdateProjectRequest):
    """更新项目"""
    svc = get_project_service()
    project = svc.update(
        project_id=project_id,
        name=request.name,
        description=request.description,
        status=request.status,
        metadata=request.metadata,
    )
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    return {"success": True, "project": _project_dict(project)}


@router.delete("/{project_id}")
async def delete_project(project_id: str):
    """删除项目（关联资产的 project_id 不会被清除，变为悬空）"""
    svc = get_project_service()
    ok = svc.delete(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    return {"success": True}


# ==================== 资产关联 Endpoints ====================

@router.get("/{project_id}/assets")
async def list_project_assets(
    project_id: str,
    asset_type: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
):
    """获取项目下所有资产"""
    svc = get_project_service()
    if not svc.get(project_id):
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")

    asset_svc = get_asset_service()
    assets = asset_svc.list_assets(
        asset_type=asset_type,
        content_type=content_type,
        category=category,
        project_id=project_id,
    )
    return {"assets": [_asset_dict(a) for a in assets], "count": len(assets)}


@router.get("/{project_id}/stats")
async def get_project_stats(project_id: str):
    """获取项目统计"""
    svc = get_project_service()
    if not svc.get(project_id):
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    stats = svc.get_stats(project_id)
    return {"success": True, "stats": stats}


@router.post("/{project_id}/assets/{asset_id}")
async def add_asset_to_project(project_id: str, asset_id: str):
    """将资产加入项目（设置 asset.project_id）"""
    svc = get_project_service()
    if not svc.get(project_id):
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")

    asset_svc = get_asset_service()
    asset = asset_svc.get(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail=f"资产不存在: {asset_id}")

    # 直接修改内存中的资产并持久化
    asset.project_id = project_id
    asset.updated_at = __import__("time").time()
    # 触发持久化（复用 update 的锁逻辑）
    await asset_svc.update(asset_id, metadata={"_project_touched": True})
    asset.metadata.pop("_project_touched", None)

    logger.info(f"[ProjectAPI] 资产加入项目 | asset={asset_id} | project={project_id}")
    return {"success": True, "asset": _asset_dict(asset)}


@router.delete("/{project_id}/assets/{asset_id}")
async def remove_asset_from_project(project_id: str, asset_id: str):
    """将资产移出项目（清除 asset.project_id）"""
    svc = get_project_service()
    if not svc.get(project_id):
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")

    asset_svc = get_asset_service()
    asset = asset_svc.get(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail=f"资产不存在: {asset_id}")

    asset.project_id = None
    asset.updated_at = __import__("time").time()
    await asset_svc.update(asset_id, metadata={"_project_touched": True})
    asset.metadata.pop("_project_touched", None)

    logger.info(f"[ProjectAPI] 资产移出项目 | asset={asset_id} | project={project_id}")
    return {"success": True, "asset": _asset_dict(asset)}
