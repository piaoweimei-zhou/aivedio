"""
导演工作台 — 工作流模板 API

提供工作流模板管理和从模板创建批量任务的接口。

端点：
- GET    /api/director/workflow-templates           列出模板
- GET    /api/director/workflow-templates/{id}      获取模板详情
- POST   /api/director/workflow-templates           创建自定义模板
- PUT    /api/director/workflow-templates/{id}      更新自定义模板
- DELETE /api/director/workflow-templates/{id}      删除自定义模板
- POST   /api/director/workflow-templates/{id}/create-batch  从模板创建批量任务
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.workflow_template_service import get_workflow_template_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/workflow-templates", tags=["导演工作台-工作流模板"])


# ==================== Request Models ====================

class WorkflowStepTemplateRequest(BaseModel):
    stage_id: str
    name: str = ""
    input_mode: str = "auto"            # auto / fixed / user_select
    input_from_steps: List[str] = []
    input_asset_ids: List[str] = []
    provider_id: str = ""
    params: Dict[str, Any] = {}
    max_retries: int = 0
    description: str = ""


class CreateTemplateRequest(BaseModel):
    name: str
    description: str = ""
    steps: List[WorkflowStepTemplateRequest]
    required_inputs: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[WorkflowStepTemplateRequest]] = None
    required_inputs: Optional[List[Dict[str, Any]]] = None


class CreateBatchFromTemplateRequest(BaseModel):
    name: str
    project_id: str = ""
    input_assets: Dict[str, List[str]] = {}     # {"character": ["asset_id1"], "scene": ["asset_id2"]}
    step_params: Dict[str, Dict[str, Any]] = {}  # {"step_1": {"prompt": "..."}}
    stop_on_failure: bool = True
    auto_start: bool = False                     # 创建后自动启动


# ==================== Endpoints ====================

@router.get("")
async def list_templates(category: str = Query("")):
    """列出工作流模板"""
    svc = get_workflow_template_service()
    templates = svc.list_templates(category=category)
    return {
        "success": True,
        "templates": [t.to_dict() for t in templates],
        "total": len(templates),
    }


@router.get("/{template_id}")
async def get_template(template_id: str):
    """获取模板详情"""
    svc = get_workflow_template_service()
    tpl = svc.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"success": True, "template": tpl.to_dict()}


@router.post("")
async def create_template(request: CreateTemplateRequest):
    """创建自定义模板"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    if not request.steps:
        raise HTTPException(status_code=400, detail="模板至少需要一个步骤")

    svc = get_workflow_template_service()
    tpl = svc.create_custom(
        name=request.name.strip(),
        description=request.description,
        steps=[s.model_dump() for s in request.steps],
        required_inputs=request.required_inputs,
        metadata=request.metadata,
    )
    logger.info(f"[WorkflowAPI] 创建模板 | id={tpl.template_id} | name={tpl.name}")
    return {"success": True, "template": tpl.to_dict()}


@router.put("/{template_id}")
async def update_template(template_id: str, request: UpdateTemplateRequest):
    """更新自定义模板"""
    svc = get_workflow_template_service()
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if "steps" in updates and updates["steps"]:
        updates["steps"] = [s.model_dump() if hasattr(s, 'model_dump') else s for s in updates["steps"]]
    tpl = svc.update(template_id, updates)
    if not tpl:
        raise HTTPException(status_code=400, detail="更新失败（模板不存在或为预置模板）")
    return {"success": True, "template": tpl.to_dict()}


@router.delete("/{template_id}")
async def delete_template(template_id: str):
    """删除自定义模板"""
    svc = get_workflow_template_service()
    ok = svc.delete(template_id)
    if not ok:
        raise HTTPException(status_code=400, detail="删除失败（模板不存在或为预置模板）")
    return {"success": True, "message": "模板已删除"}


@router.post("/{template_id}/create-batch")
async def create_batch_from_template(template_id: str, request: CreateBatchFromTemplateRequest):
    """从模板创建批量任务"""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="批量任务名称不能为空")

    svc = get_workflow_template_service()
    batch = svc.create_batch_from_template(
        template_id=template_id,
        name=request.name.strip(),
        project_id=request.project_id,
        input_assets=request.input_assets,
        step_params=request.step_params,
        stop_on_failure=request.stop_on_failure,
    )
    if not batch:
        raise HTTPException(status_code=404, detail="模板不存在")

    logger.info(
        f"[WorkflowAPI] 从模板创建批量任务 | "
        f"template={template_id} batch={batch.batch_id}"
    )

    # 自动启动
    if request.auto_start:
        from services.batch_task_service import get_batch_task_service
        await get_batch_task_service().start(batch.batch_id)
        logger.info(f"[WorkflowAPI] 自动启动批量任务 | batch={batch.batch_id}")

    return {"success": True, "batch": batch.to_dict()}
