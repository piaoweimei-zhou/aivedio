"""包装层 API（③ 包装层，B4）：标题/钩子/封面生成 + 模板管理"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from app.models import Dimension, Monetizer, PackagingTemplate
from app.packaging import generate_packaging, list_templates
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/packaging", tags=["流量侧-包装层"])


@router.post("/generate")
async def generate(
    title: str,
    dimension: Dimension,
    monetizer: Monetizer,
    max_titles: int = 5,
) -> Dict[str, object]:
    """按 维度 × 变现 生成标题候选 + 钩子 + 封面风格。"""
    return generate_packaging(title, dimension, monetizer, max_titles)


# ---------- 模板管理 ----------

@router.get("/templates", response_model=List[PackagingTemplate])
async def list_templates_api() -> List[PackagingTemplate]:
    return list_templates()


@router.post("/templates", response_model=PackagingTemplate)
async def create_template(tpl: PackagingTemplate) -> PackagingTemplate:
    return get_collection("packaging").insert(tpl)


@router.put("/templates/{tpl_id}", response_model=PackagingTemplate)
async def update_template(tpl_id: str, patch: PackagingTemplate) -> PackagingTemplate:
    col = get_collection("packaging")
    cur = col.get(tpl_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"template not found: {tpl_id}")
    data = patch.model_dump(exclude_unset=True)
    data["id"] = tpl_id
    updated = col.update(tpl_id, data)
    return PackagingTemplate(**updated)


@router.delete("/templates/{tpl_id}")
async def delete_template(tpl_id: str) -> dict:
    if not get_collection("packaging").delete(tpl_id):
        raise HTTPException(status_code=404, detail=f"template not found: {tpl_id}")
    return {"success": True}
