"""
导演工作台 — 提示词中心 API

集中管理所有阶段的提示词，支持变量替换、分类标签、搜索。

端点：
- GET    /api/director/prompts                列出提示词（多维度过滤+搜索）
- GET    /api/director/prompts/{id}           获取提示词详情
- POST   /api/director/prompts                创建提示词
- PUT    /api/director/prompts/{id}           更新提示词
- DELETE /api/director/prompts/{id}           删除提示词
- POST   /api/director/prompts/{id}/resolve   解析提示词（变量替换）
- POST   /api/director/prompts/resolve        直接解析内容（无需 ID）
- GET    /api/director/prompts/categories     获取分类列表
- GET    /api/director/prompts/tags           获取标签列表
- GET    /api/director/prompts/stats          获取统计信息
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.prompt_service import get_prompt_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/prompts", tags=["导演工作台-提示词中心"])


# ==================== Request Models ====================

class PromptVariableRequest(BaseModel):
    name: str
    default: str = ""
    description: str = ""
    required: bool = False


class CreatePromptRequest(BaseModel):
    name: str
    content: str
    category: str = "custom"
    stage_id: str = ""
    variables: List[Dict[str, Any]] = []
    tags: List[str] = []
    project_id: str = ""
    description: str = ""
    quality_score: float = 0.0
    metadata: Dict[str, Any] = {}


class UpdatePromptRequest(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    stage_id: Optional[str] = None
    variables: Optional[List[Dict[str, Any]]] = None
    tags: Optional[List[str]] = None
    project_id: Optional[str] = None
    description: Optional[str] = None
    quality_score: Optional[float] = None


class ResolveRequest(BaseModel):
    variables: Dict[str, str] = {}
    content: str = ""  # 直接解析模式


# ==================== Endpoints ====================

@router.get("")
async def list_prompts(
    project_id: str = Query(""),
    stage_id: str = Query(""),
    category: str = Query(""),
    tag: str = Query(""),
    keyword: str = Query(""),
):
    """列出提示词（支持多维度过滤+搜索）"""
    svc = get_prompt_service()
    prompts = svc.list_prompts(
        project_id=project_id,
        stage_id=stage_id,
        category=category,
        tag=tag,
        keyword=keyword,
    )
    return {
        "success": True,
        "prompts": [p.to_dict() for p in prompts],
        "total": len(prompts),
    }


@router.get("/categories")
async def get_categories():
    """获取分类列表"""
    svc = get_prompt_service()
    return {"success": True, "categories": svc.get_categories()}


@router.get("/tags")
async def get_tags():
    """获取标签列表"""
    svc = get_prompt_service()
    return {"success": True, "tags": svc.get_tags()}


@router.get("/stats")
async def get_stats(project_id: str = Query("")):
    """获取统计信息"""
    svc = get_prompt_service()
    return {"success": True, "stats": svc.get_stats(project_id=project_id)}


@router.get("/{prompt_id}")
async def get_prompt(prompt_id: str):
    """获取提示词详情"""
    svc = get_prompt_service()
    entry = svc.get(prompt_id)
    if not entry:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"success": True, "prompt": entry.to_dict()}


@router.post("")
async def create_prompt(request: CreatePromptRequest):
    """创建提示词"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="提示词名称不能为空")
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="提示词内容不能为空")

    svc = get_prompt_service()
    entry = svc.create(
        name=request.name.strip(),
        content=request.content,
        category=request.category,
        stage_id=request.stage_id,
        variables=request.variables,
        tags=request.tags,
        project_id=request.project_id,
        description=request.description,
        quality_score=request.quality_score,
        metadata=request.metadata,
    )
    logger.info(f"[PromptAPI] 创建提示词 | id={entry.prompt_id} | name={entry.name}")
    return {"success": True, "prompt": entry.to_dict()}


@router.put("/{prompt_id}")
async def update_prompt(prompt_id: str, request: UpdatePromptRequest):
    """更新提示词"""
    svc = get_prompt_service()
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    entry = svc.update(prompt_id, updates)
    if not entry:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"success": True, "prompt": entry.to_dict()}


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: str):
    """删除提示词"""
    svc = get_prompt_service()
    ok = svc.delete(prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"success": True, "message": "提示词已删除"}


@router.post("/{prompt_id}/resolve")
async def resolve_prompt(prompt_id: str, request: ResolveRequest):
    """解析提示词 — 替换变量占位符，返回最终 prompt 字符串"""
    svc = get_prompt_service()
    result = svc.resolve(prompt_id, request.variables)
    if not result:
        raise HTTPException(status_code=404, detail="提示词不存在")
    resolved, entry = result
    return {
        "success": True,
        "resolved": resolved,
        "prompt": entry.to_dict(),
    }


@router.post("/resolve")
async def resolve_content(request: ResolveRequest):
    """直接解析提示词内容（无需 prompt_id）"""
    svc = get_prompt_service()
    if not request.content:
        raise HTTPException(status_code=400, detail="content 不能为空")
    resolved = svc.resolve_content(request.content, request.variables)
    return {
        "success": True,
        "resolved": resolved,
        "original": request.content,
    }


# ==================== 阶段 C：项目默认提示词 ====================

class SetDefaultRequest(BaseModel):
    project_id: str
    stage_id: str = ""


@router.post("/{prompt_id}/set-default")
async def set_default_prompt(prompt_id: str, request: SetDefaultRequest):
    """设置项目+阶段的默认提示词"""
    svc = get_prompt_service()
    ok = svc.set_default(prompt_id, request.project_id, request.stage_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"success": True, "message": "已设为默认提示词"}


@router.post("/{prompt_id}/unset-default")
async def unset_default_prompt(prompt_id: str):
    """取消默认提示词"""
    svc = get_prompt_service()
    ok = svc.unset_default(prompt_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提示词不存在")
    return {"success": True, "message": "已取消默认"}


@router.get("/defaults/{project_id}")
async def get_default_prompt(project_id: str, stage_id: str = Query("")):
    """获取项目+阶段的默认提示词"""
    svc = get_prompt_service()
    entry = svc.get_default(project_id, stage_id)
    return {
        "success": True,
        "prompt": entry.to_dict() if entry else None,
    }


# ==================== 阶段 C：版本历史 ====================

@router.get("/{prompt_id}/history")
async def get_prompt_history(prompt_id: str):
    """获取提示词的版本历史"""
    svc = get_prompt_service()
    versions = svc.get_history(prompt_id)
    return {
        "success": True,
        "versions": versions,
        "total": len(versions),
    }


class RollbackRequest(BaseModel):
    version: int


@router.post("/{prompt_id}/rollback")
async def rollback_prompt(prompt_id: str, request: RollbackRequest):
    """回滚到指定历史版本"""
    svc = get_prompt_service()
    entry = svc.rollback(prompt_id, request.version)
    if not entry:
        raise HTTPException(status_code=404, detail="提示词或版本不存在")
    return {
        "success": True,
        "prompt": entry.to_dict(),
        "message": f"已回滚到 v{request.version}",
    }
