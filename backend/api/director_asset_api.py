"""
导演工作台 — 资产注册表 API

与旧版 asset_api.py（模板/批次管理）不同，
此 API 对应导演工作台的 AssetService（资产网络）。
"""

import logging
import os
import uuid
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from services.asset_service import get_asset_service, ASSET_TYPES, STAGE_TYPES, CONTENT_TYPES

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/assets", tags=["导演工作台-资产"])


# ==================== Request Models ====================

class CreateAssetRequest(BaseModel):
    asset_type: str
    content_type: str = ""
    name: str
    urls: List[str] = []
    metadata: Dict[str, Any] = {}
    parent_id: Optional[str] = None
    project_id: Optional[str] = None


class UpdateAssetRequest(BaseModel):
    name: Optional[str] = None
    urls: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


# ==================== Type Endpoints ====================

@router.get("/types")
async def list_asset_types():
    """列出所有资产类型（兼容旧接口）"""
    return {"types": ASSET_TYPES}


@router.get("/stage-types")
async def list_stage_types():
    """列出生产阶段类型"""
    return {"types": STAGE_TYPES}


@router.get("/content-types")
async def list_content_types():
    """列出内容类型"""
    return {"types": CONTENT_TYPES}


# ==================== Upload Endpoint ====================

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "uploads")
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov"}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB 上传大小限制


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传文件，返回可访问的 URL"""
    # 校验扩展名
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # 生成唯一文件名
    file_id = uuid.uuid4().hex[:12]
    save_name = f"{file_id}{ext}"
    save_path = os.path.join(UPLOAD_DIR, save_name)

    # 写入文件（带大小限制，防止 DoS）
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超出限制（最大 {MAX_UPLOAD_SIZE // 1024 // 1024}MB）",
        )

    with open(save_path, "wb") as f:
        f.write(content)

    # 返回静态可访问 URL
    url = f"/static/director/uploads/{save_name}"
    logger.info(f"[AssetAPI] 文件上传成功: {save_name} ({len(content)} bytes)")
    return {"success": True, "url": url, "filename": file.filename}


@router.get("/proxy-image")
async def proxy_image(url: str = Query(..., description="要代理的图片URL")):
    """代理图片请求，解决前端 Canvas 跨域问题

    前端 PoseEditor 使用 Canvas 加载 ComfyUI 输出的图片，
    由于跨域限制，Canvas 会被 taint，导致 toDataURL() 报错。
    此代理端点在服务端获取图片并返回，绕过 CORS 限制。

    安全限制：仅允许代理 http/https 协议的图片 URL，
    禁止访问内网地址（localhost、私有IP、云元数据）以防止 SSRF。
    """
    import httpx
    from urllib.parse import urlparse
    import ipaddress
    import socket

    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 参数")

    # SSRF 防护：只允许 http/https 协议
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持 http/https 协议")

    # SSRF 防护：禁止访问内网地址
    hostname = parsed.hostname
    if hostname:
        # 允许 localhost 和 127.0.0.1（ComfyUI 通常运行在本机）
        # 但禁止其他私有 IP 段和云元数据地址
        blocked_hosts = {"0.0.0.0", "::1"}
        if hostname.lower() in blocked_hosts:
            raise HTTPException(status_code=403, detail="禁止访问该地址")

        # 禁止私有 IP 段和云元数据地址（但允许 127.0.0.1）
        try:
            resolved_ip = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private and not ip.is_loopback:
                raise HTTPException(status_code=403, detail="禁止访问内网地址")
            if ip.is_link_local:
                raise HTTPException(status_code=403, detail="禁止访问链路本地地址")
            # 禁止云元数据地址（169.254.x.x）
            if resolved_ip.startswith("169.254."):
                raise HTTPException(status_code=403, detail="禁止访问云元数据地址")
        except socket.gaierror:
            pass  # DNS 解析失败，让 httpx 处理

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "image/png")
        # 只允许图片类型响应
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"非图片类型: {content_type}")

        return Response(
            content=resp.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Access-Control-Allow-Origin": "*",
            },
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"代理图片获取失败: {e}")


# ==================== CRUD Endpoints ====================

@router.post("")
async def create_asset(request: CreateAssetRequest):
    """创建资产"""
    svc = get_asset_service()
    asset = await svc.create(
        asset_type=request.asset_type,
        name=request.name,
        urls=request.urls,
        metadata=request.metadata,
        parent_id=request.parent_id,
        content_type=request.content_type,
        project_id=request.project_id,
    )
    return {"success": True, "asset": _asset_dict(asset)}


@router.get("")
async def list_assets(
    asset_type: Optional[str] = Query(None),
    content_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    parent_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
):
    """列出资产

    project_id 过滤：
    - 具体项目 ID：返回该项目资产
    - "__none__"：返回无项目归属的资产
    - 不传：返回所有资产
    """
    svc = get_asset_service()
    assets = svc.list_assets(
        asset_type=asset_type,
        content_type=content_type,
        category=category,
        parent_id=parent_id,
        project_id=project_id,
    )
    return {"assets": [_asset_dict(a) for a in assets], "count": len(assets)}


@router.get("/{asset_id}")
async def get_asset(asset_id: str):
    """获取资产详情"""
    svc = get_asset_service()
    asset = svc.get(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"asset": _asset_dict(asset)}


@router.put("/{asset_id}")
async def update_asset(asset_id: str, request: UpdateAssetRequest):
    """更新资产"""
    svc = get_asset_service()
    asset = await svc.update(
        asset_id=asset_id,
        name=request.name,
        urls=request.urls,
        metadata=request.metadata,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"success": True, "asset": _asset_dict(asset)}


@router.delete("/{asset_id}")
async def delete_asset(asset_id: str):
    """删除资产"""
    svc = get_asset_service()
    ok = await svc.delete(asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="资产不存在")
    return {"success": True}


# ==================== Lineage Endpoints ====================

@router.get("/{asset_id}/lineage")
async def get_asset_lineage(asset_id: str):
    """获取资产生产链"""
    svc = get_asset_service()
    chain = svc.lineage(asset_id)
    return {"lineage": [_asset_dict(a) for a in chain]}


@router.get("/{asset_id}/children")
async def get_asset_children(asset_id: str):
    """获取资产衍生资产"""
    svc = get_asset_service()
    children = svc.children(asset_id)
    return {"children": [_asset_dict(a) for a in children]}


# ==================== Stats Endpoint ====================

@router.get("/stats/overview")
async def get_stats():
    """资产统计概览"""
    svc = get_asset_service()
    return {"stats": svc.stats()}


@router.post("/cleanup-orphaned")
async def cleanup_orphaned_assets(
    dry_run: bool = Query(True, description="仅统计不删除"),
    generated_dir: str = Query("", description="持久化图片目录"),
    comfyui_output_dir: str = Query("", description="ComfyUI output 目录"),
):
    """清理图片文件已丢失的孤岛资产"""
    svc = get_asset_service()
    report = await svc.cleanup_orphaned(
        generated_dir=generated_dir or "",
        comfyui_output_dir=comfyui_output_dir or "",
        dry_run=dry_run,
    )
    return {"report": report, "dry_run": dry_run}


# ==================== Template Manifest Endpoint ====================

@router.post("/templates/manifest/{template_id}")
async def update_template_manifest(template_id: str, updates: Dict[str, Any]):
    """更新模板 manifest 中指定 template_id 的条目

    前端 PoseEditor 修正 Pose 后调用此接口更新 manifest 中的文件引用。
    如果 updates 包含 pose_corrected=True，则自动将修正图复制到 templates/ 目录。

    安全限制：只允许更新以下字段：
    - files: 文件引用（合并更新）
    - pose_corrected: Pose 修正标记
    - pose_simplified: 简化 Pose 标记
    """
    from services.template_utils import validate_template_id, update_manifest_entry, TEMPLATE_DIR

    if not validate_template_id(template_id):
        raise HTTPException(status_code=400, detail=f"template_id 格式不合法: {template_id}")

    # 输入验证：只允许更新白名单字段，防止客户端注入 status 等关键字段
    allowed_keys = {"files", "pose_corrected", "pose_simplified"}
    invalid_keys = set(updates.keys()) - allowed_keys
    if invalid_keys:
        raise HTTPException(
            status_code=400,
            detail=f"不允许更新的字段: {invalid_keys}（允许: {allowed_keys}）",
        )

    # 验证 files 字段值不含路径遍历字符
    if "files" in updates and isinstance(updates["files"], dict):
        for key, val in updates["files"].items():
            if not isinstance(val, str):
                raise HTTPException(status_code=400, detail=f"files.{key} 值必须为字符串")
            for ch in ["..", "/", "\\"]:
                if ch in val:
                    raise HTTPException(
                        status_code=400,
                        detail=f"files.{key} 包含非法字符: {ch}",
                    )

    # 如果是 Pose 修正，需要将上传的修正图复制到 templates/ 目录
    # 前端传入 files.pose_simplified 为期望的文件名，同时需要找到上传的源文件
    if updates.get("pose_corrected") and "files" in updates:
        pose_simplified_name = updates["files"].get("pose_simplified", "")
        if pose_simplified_name:
            # 从资产表中查找最近上传的修正 Pose 资产
            svc = get_asset_service()
            for a in reversed(svc.list_assets()):
                if (a.asset_type == "pose"
                    and a.metadata
                    and a.metadata.get("template_id") == template_id
                    and a.metadata.get("extraction_type") == "template_pose_corrected"):
                    # 找到修正图资产，将其文件复制到 templates/ 目录
                    src_url = next((u for u in (a.urls or []) if u), "")
                    if src_url:
                        _copy_uploaded_to_templates(src_url, pose_simplified_name)
                    break

    success = await update_manifest_entry(template_id, updates)
    if not success:
        raise HTTPException(status_code=500, detail="manifest 更新失败")

    return {"success": True, "template_id": template_id}


# ==================== Helper ====================

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


def _copy_uploaded_to_templates(src_url: str, target_name: str):
    """将上传目录中的文件复制到 templates/ 目录

    src_url 格式: /static/director/uploads/{uuid}.png
    target_name: 如 T01_双人正面对话_pose_corrected.png
    """
    import shutil
    from services.template_utils import TEMPLATE_DIR

    if not src_url or not target_name:
        return

    # 从 URL 提取文件名：/static/director/uploads/abc123.png → abc123.png
    url_filename = src_url.rsplit("/", 1)[-1] if "/" in src_url else ""
    if not url_filename:
        logger.warning(f"[AssetAPI] 无法从 URL 提取文件名: {src_url}")
        return

    src_path = os.path.join(UPLOAD_DIR, url_filename)
    if not os.path.exists(src_path):
        logger.warning(f"[AssetAPI] 上传文件不存在: {src_path}")
        return

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    dst_path = TEMPLATE_DIR / target_name

    try:
        shutil.copy2(src_path, str(dst_path))
        logger.info(f"[AssetAPI] 修正图已复制到模板目录: {target_name}")
    except OSError as e:
        logger.error(f"[AssetAPI] 复制修正图失败: {e}")
