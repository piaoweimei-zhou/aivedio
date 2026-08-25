"""
无限画布 API 桥接层
将 canvas.js 期望的 /api/* 端点映射到现有后端服务
"""

import json
import logging
import os
import uuid
import time
import datetime
import asyncio
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from pydantic import BaseModel

from services.canvas_service import get_canvas_service
from services.gen_task_manager import get_gen_task_manager
from services.task_status import to_frontend_status, TaskStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["infinite-canvas"])

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_PROJECT_DIR = os.path.dirname(_BASE_DIR)  # 项目根目录
_UPLOAD_DIR = os.path.join(_BASE_DIR, "data", "uploads")
_WF_DIR = os.path.join(_PROJECT_DIR, "workflows")


def is_video_url(url: str) -> bool:
    """判断 URL 是否指向视频文件"""
    if not url:
        return False
    low = url.lower().split("?")[0]
    return any(low.endswith(ext) for ext in [".mp4", ".webm", ".mov", ".avi", ".mkv"])


# ⚠️ 不要再在本 router（prefix=/api）定义 GET /comfyui/image：
# main.py 已注册权威的 GET /api/comfyui/image 处理器（优先本地持久化目录
# GENERATED_DIR → 上传目录 → ComfyUI output → 代理 ComfyUI /view 并缓存），
# 可保证 ComfyUI 离线时仍从本地读取已生成资源。本 router 的 include 顺序先于
# main.py 的 @app.get，同名路由会遮蔽主实现，反致 GENERATED_DIR 本地读取失效。
# 如需扩展搜读目录，请改 main.py 的 serve_comfyui_image 的 search_dirs。

# 生成任务已统一由 GenTaskManager 管理（services/gen_task_manager.py）
# 生成任务（gen_*）由 GenTaskManager 自带清理逻辑管理，
# 此循环仅清理 _rh_tasks 缓存（RunningHub 远程任务状态缓存）。
async def _generate_tasks_cleanup_loop():
    """后台定时清理循环：每 60 秒清理一次过期 RH 任务缓存"""
    while True:
        try:
            await asyncio.sleep(60)
            _cleanup_expired_rh_tasks()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[InfiniteCanvas] RH 任务缓存清理循环异常: {e}")


def _ensure_cleanup_task():
    """确保后台清理任务已启动（惰性启动，避免事件循环未就绪时报错）"""
    global _cleanup_task_handle
    if _cleanup_task_handle is None or _cleanup_task_handle.done():
        try:
            _cleanup_task_handle = asyncio.create_task(_generate_tasks_cleanup_loop())
        except RuntimeError:
            # 事件循环未就绪，跳过（后续请求会重试）
            pass


class CreateCanvasBody(BaseModel):
    name: str = "未命名画布"
    title: Optional[str] = None
    project_id: Optional[str] = ""


class MetaBody(BaseModel):
    name: Optional[str] = None
    title: Optional[str] = None


# ============================================================
# 辅助
# ============================================================

def _to_canvas_dict(layout) -> dict:
    return {
        "id": layout.canvas_id,
        "canvas_id": layout.canvas_id,
        "title": layout.name,
        "name": layout.name,
        "icon": "🧩",
        "nodes": [n.to_dict() for n in layout.nodes],
        "edges": [e.to_dict() for e in layout.edges],
        # canvas.js 期望 connections 格式: [{id, from, to, label}]
        "connections": [
            {
                "id": e.edge_id,
                "from": e.source_id,
                "to": e.target_id,
                "label": e.label,
            }
            for e in layout.edges
        ],
        "viewport": layout.viewport.to_dict() if layout.viewport else {"x": 0, "y": 0, "scale": 1},
        "created_at": layout.created_at,
        "updated_at": layout.updated_at,
        "logs": getattr(layout, "logs", []) or [],
    }


# ============================================================
# 基础 API
# ============================================================

@router.get("/config")
async def get_config():
    """获取系统配置"""
    comfyui_status = "disconnected"
    try:
        from services.comfyui_service import get_comfyui_service
        svc = get_comfyui_service()
        comfyui_status = "connected" if await svc.is_alive() else "disconnected"
    except Exception:
        pass

    return {
        "version": "1.0.0",
        "features": ["image_generation", "canvas", "comfyui"],
        "canvas": {"max_nodes": 9999, "max_file_size_mb": 50},
        "comfyui": {"status": comfyui_status},
    }


@router.get("/workflows")
async def list_workflows():
    """列出可用工作流（从 workflows/ 目录加载，匹配原版 canvas.js 格式）"""
    _BUILTIN = {"Z-Image.json", "Z-Image-Enhance.json", "2511.json", "klein-enhance.json", "Flux2-Klein.json", "upscale.json"}
    items = []
    if os.path.isdir(_WF_DIR):
        for fname in sorted(os.listdir(_WF_DIR)):
            if not fname.endswith(".json") or fname.endswith(".config.json"):
                continue
            if fname in _BUILTIN:
                continue
            rel = fname.replace("\\", "/")
            cfg = {}
            cfg_path = os.path.join(_WF_DIR, fname.replace(".json", ".config.json"))
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f) or {}
                except Exception:
                    pass
            items.append({
                "name": rel,
                "title": cfg.get("title") or fname.replace(".json", ""),
                "builtin": False,
                "field_count": len(cfg.get("fields") or []),
            })
    items.sort(key=lambda item: (0 if item["name"].startswith("custom/") else 1, item["title"]))
    return JSONResponse(
        content={"workflows": items},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/workflows/{name}")
async def get_workflow(name: str):
    """获取工作流详情（匹配原版 canvas.js 格式）"""
    # 安全检查：防止路径遍历攻击（name 中不得包含路径分隔符或父目录引用）
    if not name or "\\" in name or "/" in name or ".." in name:
        raise HTTPException(status_code=400, detail="非法的工作流名称")
    wf_path = os.path.join(_WF_DIR, name)
    # 二次校验：解析后的绝对路径必须在 _WF_DIR 内
    try:
        if not os.path.abspath(wf_path).startswith(os.path.abspath(_WF_DIR) + os.sep):
            raise HTTPException(status_code=400, detail="非法的工作流路径")
    except HTTPException:
        raise
    if not os.path.exists(wf_path):
        wf_path2 = os.path.join(_WF_DIR, f"{name}.json")
        if os.path.exists(wf_path2):
            wf_path = wf_path2
        else:
            raise HTTPException(status_code=404, detail=f"工作流不存在: {name}")
    with open(wf_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 清理 LoadImage 节点中硬编码的不存在图片文件名，避免新环境引用失效文件
    try:
        from services.comfyui_service import COMFYUI_DIR
        _input_dir = os.path.join(COMFYUI_DIR, "input") if COMFYUI_DIR else ""
        for _nid, _ndata in raw.items():
            if isinstance(_ndata, dict) and _ndata.get("class_type") == "LoadImage":
                _inputs = _ndata.get("inputs", {})
                _img = _inputs.get("image", "")
                if isinstance(_img, str) and _img:
                    _exists = os.path.isfile(os.path.join(_input_dir, _img)) if _input_dir else False
                    _exists = _exists or os.path.isfile(os.path.join(_UPLOAD_DIR, _img))
                    if not _exists:
                        _inputs["image"] = ""  # 文件不存在，清空让用户上传时填充
    except Exception as _e:
        logger.warning(f"[InfiniteCanvas] 清理 LoadImage 硬编码图片失败: {_e}")

    # 尝试读取已有的 .config.json，不存在则自动生成
    cfg_path = wf_path.replace(".json", ".config.json")
    cfg = None  # 预初始化，避免 json.load 返回空字典时引用未定义变量
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if loaded:
                    cfg = loaded
        except Exception:
            pass
    if cfg is None:
        # 自动生成 config.fields：扫描工作流节点，全部作为 setting 字段
        fields = []
        _TEXT_ENCODERS = {
            "CLIPTextEncode", "TextEncodeQwenImageEditPlus",
            "TextEncode", "QwenTextEncode",
            "TextEncodeQwenImageEditPlusAdvance_lrzjason",
        }
        _TEXT_INPUT_TYPES = {
            "PrimitiveStringMultiline", "PrimitiveString",
            "Text Multiline",
        }
        # 统计各类节点数量，用于分组和命名
        img_idx = 0
        prompt_idx = 0
        for node_id, node in raw.items():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type", "")
            meta = node.get("_meta", {})
            title = meta.get("title", ct) or ct
            inputs = node.get("inputs", {})

            if ct in _TEXT_ENCODERS:
                # 文本编码器：提取 prompt/text 输入
                raw_text = inputs.get("text") or inputs.get("prompt", "")
                if isinstance(raw_text, str):
                    text_val = raw_text.strip()
                    prompt_idx += 1
                    field = {
                        "id": f"prompt_{node_id}",
                        "node": node_id,
                        "input": "prompt" if "prompt" in inputs else "text",
                        "type": "textarea",
                        "name": f"提示词{prompt_idx} → {title}",
                        "group": "prompts",
                    }
                    if text_val:
                        field["default"] = text_val[:200]
                    fields.append(field)
            elif ct in _TEXT_INPUT_TYPES:
                # 文本输入节点：提取 value/text 输入
                raw_text = inputs.get("value") or inputs.get("text", "")
                if isinstance(raw_text, str):
                    text_val = raw_text.strip()
                    prompt_idx += 1
                    input_key = "value" if "value" in inputs else "text"
                    field = {
                        "id": f"prompt_{node_id}",
                        "node": node_id,
                        "input": input_key,
                        "type": "textarea",
                        "name": f"提示词{prompt_idx} → {title}",
                        "group": "prompts",
                    }
                    if text_val:
                        field["default"] = text_val[:200]
                    fields.append(field)
            elif ct == "LoadImage":
                img_idx += 1
                # 检查默认文件是否实际存在，不存在则留空让用户上传
                default_img = inputs.get("image", "")
                if default_img:
                    from services.comfyui_service import COMFYUI_DIR
                    comfy_input = os.path.join(COMFYUI_DIR, "input", default_img) if COMFYUI_DIR else ""
                    if not comfy_input or not os.path.isfile(comfy_input):
                        default_img = ""  # 文件不存在，不设为默认值
                fields.append({
                    "id": f"image_{node_id}",
                    "node": node_id,
                    "input": "image",
                    "type": "image",
                    "name": f"图片{img_idx} → {title}",
                    "default": default_img,
                    "group": "images",
                    "compact": True,
                })
            elif ct == "Comfly_api_set":
                # API配置节点：提取 apikey 输入
                fields.append({
                    "id": f"apikey_{node_id}",
                    "node": node_id,
                    "input": "apikey",
                    "type": "text",
                    "name": f"API Key → {title}",
                    "group": "settings",
                })
        # 自动生成 groups
        group_ids = []
        for f in fields:
            gid = f.get("group", "")
            if gid and gid not in group_ids:
                group_ids.append(gid)
        groups = []
        if "images" in group_ids:
            groups.append({"id": "images", "name": "参考图片", "collapsed": False, "layout": "grid"})
        if "prompts" in group_ids:
            groups.append({"id": "prompts", "name": "分镜提示词", "collapsed": False, "layout": "list"})
        if "settings" in group_ids:
            groups.append({"id": "settings", "name": "设置", "collapsed": False, "layout": "list"})
        cfg = {"title": name.replace(".json", ""), "fields": fields, "groups": groups}

    return JSONResponse(
        content={"name": name, "workflow": raw, "config": cfg, "builtin": False},
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


# ============================================================
# 资产库（基于 AssetService）
# ============================================================

@router.get("/asset-library")
async def get_asset_library():
    """列出资产库（canvas.js 期望 library.libraries 格式）"""
    from services.asset_service import get_asset_service
    svc = get_asset_service()
    # 构建 canvas.js 期望的 library 格式
    items = []
    for a in svc._assets.values():
        for url in (a.urls or []):
            items.append({"id": a.asset_id, "name": a.name, "url": url, "type": a.asset_type, "content_type": a.content_type})
    library = {
        "libraries": [{"id": "default", "name": "默认资产库", "categories": [{"id": "all", "name": "全部", "items": items}]}],
        "categories": [{"id": "all", "name": "全部"}],
        "active_library_id": "default",
    }
    return {"library": library}


@router.get("/local-assets")
async def get_local_assets():
    """列出本地资产（canvas.js 期望 items/tree 格式）"""
    files = []
    if os.path.isdir(_UPLOAD_DIR):
        for fname in os.listdir(_UPLOAD_DIR):
            fpath = os.path.join(_UPLOAD_DIR, fname)
            if os.path.isfile(fpath):
                ext = os.path.splitext(fname)[1].lower()
                kind = "image"
                if ext in (".mp4", ".webm", ".mov"):
                    kind = "video"
                elif ext in (".mp3", ".wav", ".m4a"):
                    kind = "audio"
                files.append({"url": f"/static/director/uploads/{fname}", "name": fname, "kind": kind, "size": os.path.getsize(fpath)})
    return {"items": files, "tree": None}


@router.get("/canvas-assets")
async def list_canvas_assets():
    """列出所有资产（同 /asset-library）"""
    from services.asset_service import get_asset_service
    svc = get_asset_service()
    assets = []
    for a in svc._assets.values():
        assets.append({
            "id": a.asset_id,
            "name": a.name,
            "type": a.asset_type,
            "content_type": a.content_type,
            "urls": a.urls or [],
            "metadata": a.metadata,
        })
    return {"assets": assets}


# ============================================================
# Smart Canvas API
# ============================================================

@router.get("/prompt-libraries")
async def list_prompt_libraries():
    """列出提示词库（canvas.js 期望 library.libraries 格式）"""
    libraries = []
    for plib in _prompt_libraries.values():
        items = [v for v in _prompt_items.values() if v.get("library_id") == plib["id"]]
        libraries.append({**plib, "items": items})
    return {"library": {"libraries": libraries}}


@router.get("/smart-canvas/prompt-templates")
async def list_smart_prompt_templates():
    """列出智能画布提示词模板"""
    return {"templates": []}


# ============================================================
# 画布 CRUD
# ============================================================

_canvas_trash: dict = {}

@router.get("/canvases/trash")
async def list_trash():
    return {"success": True, "canvases": list(_canvas_trash.values())}


@router.post("/canvases")
async def create_canvas(body: CreateCanvasBody):
    svc = get_canvas_service()
    canvas_name = body.title or body.name
    layout = await svc.create(name=canvas_name)
    return {"success": True, "canvas": _to_canvas_dict(layout)}


@router.get("/canvases")
async def list_canvases():
    svc = get_canvas_service()
    return {"success": True, "canvases": svc.list_canvases()}


@router.get("/canvases/{canvas_id}")
async def get_canvas(canvas_id: str):
    svc = get_canvas_service()
    layout = svc.get(canvas_id)
    if not layout:
        raise HTTPException(status_code=404, detail="画布不存在")
    return {"success": True, "canvas": _to_canvas_dict(layout)}


@router.put("/canvases/{canvas_id}")
async def update_canvas(canvas_id: str, request: Request):
    svc = get_canvas_service()
    layout = svc.get(canvas_id)
    if not layout:
        raise HTTPException(status_code=404, detail="画布不存在")
    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"无效的JSON: {e}")

    data = {}
    # 名称：canvas.js 用 title，也可兼容 name
    if "title" in body:
        data["name"] = body["title"]
    if "name" in body:
        data["name"] = body["name"]

    # 节点：canvas.js 节点有大量顶层字段（url, generatedOutputs, comfyParams, output 等），
    # 需要将非标准字段合并到 metadata 中保存，以便恢复时完整还原
    _CANVAS_NODE_KNOWN_KEYS = {"id", "asset_id", "type", "node_type", "x", "y", "w", "width", "h", "height", "label", "title", "name", "metadata", "runSettings"}
    if "nodes" in body and isinstance(body["nodes"], list):
        mapped_nodes = []
        for n in body["nodes"]:
            # 收集非标准字段，合并到 metadata
            meta = dict(n.get("metadata", n.get("runSettings", {})) or {})
            for k, v in n.items():
                if k not in _CANVAS_NODE_KNOWN_KEYS and v is not None:
                    meta[k] = v
            mapped_nodes.append({
                "node_id": n.get("id", ""),
                "asset_id": n.get("asset_id", ""),
                "node_type": n.get("type", n.get("node_type", "image")),
                "x": float(n.get("x") or 0),
                "y": float(n.get("y") or 0),
                "width": float(n.get("w") or n.get("width") or 240),
                "height": float(n.get("h") or n.get("height") or 180),
                "label": n.get("label", n.get("title", n.get("name", ""))),
                "metadata": meta,
            })
        data["nodes"] = mapped_nodes

    # 连线：canvas.js 用 connections，也可兼容 edges
    conn_key = "connections" if "connections" in body else "edges"
    if conn_key in body and isinstance(body[conn_key], list):
        mapped_edges = []
        for i, e in enumerate(body[conn_key]):
            mapped_edges.append({
                "edge_id": e.get("edge_id", e.get("id", f"e_{i}")),
                "source_id": e.get("source_id", e.get("source", "")),
                "target_id": e.get("target_id", e.get("target", "")),
                "source_port": e.get("source_port", e.get("sourceHandle", "output")),
                "target_port": e.get("target_port", e.get("targetHandle", "input")),
                "label": e.get("label", ""),
            })
        data["edges"] = mapped_edges

    # 视口
    if "viewport" in body:
        vp = body["viewport"]
        data["viewport"] = {"x": vp.get("x", 0), "y": vp.get("y", 0), "zoom": vp.get("zoom", vp.get("scale", 1))}

    # 日志：canvas.js 保存 logs 字段，持久化到磁盘
    if "logs" in body and isinstance(body["logs"], list):
        data["logs"] = body["logs"]

    # 乐观锁：传递 base_updated_at 给服务层
    if "base_updated_at" in body:
        data["base_updated_at"] = body["base_updated_at"]

    result = await svc.update_layout(canvas_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="画布不存在")
    # 乐观锁冲突
    if isinstance(result, dict) and result.get("_conflict"):
        canvas = result.get("canvas")
        canvas_dict = _to_canvas_dict(canvas) if canvas else {}
        raise HTTPException(
            status_code=409,
            detail={
                "message": "画布已被其他客户端修改，请刷新后重试",
                "updated_at": result.get("server_updated_at"),
                "canvas": canvas_dict,
            },
        )
    return {"success": True, "canvas": _to_canvas_dict(result)}


@router.delete("/canvases/{canvas_id}")
async def delete_canvas(canvas_id: str):
    """删除画布（软删除：移入回收站，可通过 /restore 恢复）"""
    svc = get_canvas_service()
    layout = svc.get(canvas_id)
    if not layout:
        # 画布不存在，检查是否已在回收站
        if canvas_id in _canvas_trash:
            return {"success": True, "already_in_trash": True}
        raise HTTPException(status_code=404, detail="画布不存在")
    # 先快照到回收站，再调用 service 永久删除（避免数据残留导致恢复后重复）
    _canvas_trash[canvas_id] = {
        "id": canvas_id,
        "name": layout.name,
        "canvas": _to_canvas_dict(layout),
        "deleted_at": time.time(),
    }
    if not await svc.delete(canvas_id):
        # 并发删除导致失败，回滚 trash 快照
        _canvas_trash.pop(canvas_id, None)
        raise HTTPException(status_code=404, detail="画布不存在")
    return {"success": True, "trashed": True}


@router.post("/canvases/{canvas_id}/meta")
async def update_canvas_meta(canvas_id: str, body: MetaBody):
    svc = get_canvas_service()
    data = {}
    if body.title is not None:
        data["name"] = body.title
    if body.name is not None:
        data["name"] = body.name
    result = await svc.update_layout(canvas_id, data)
    if not result:
        raise HTTPException(status_code=404, detail="画布不存在")
    if isinstance(result, dict) and result.get("_conflict"):
        canvas = result.get("canvas")
        canvas_dict = _to_canvas_dict(canvas) if canvas else {}
        raise HTTPException(
            status_code=409,
            detail={
                "message": "画布已被其他客户端修改，请刷新后重试",
                "updated_at": result.get("server_updated_at"),
                "canvas": canvas_dict,
            },
        )
    return {"success": True, "canvas": _to_canvas_dict(result)}


@router.get("/canvases/{canvas_id}/meta")
async def get_canvas_meta(canvas_id: str):
    svc = get_canvas_service()
    layout = svc.get(canvas_id)
    if not layout:
        raise HTTPException(status_code=404, detail="画布不存在")
    canvas_dict = _to_canvas_dict(layout)
    return {"success": True, "canvas": canvas_dict, "updated_at": layout.updated_at}


@router.post("/canvases/{canvas_id}/touch")
async def touch_canvas(canvas_id: str):
    svc = get_canvas_service()
    # 使用加锁的 touch 方法，避免并发写入冲突
    if not await svc.touch(canvas_id):
        raise HTTPException(status_code=404, detail="画布不存在")
    layout = svc.get(canvas_id)
    return {"success": True, "canvas": _to_canvas_dict(layout)}


@router.post("/canvases/{canvas_id}/restore")
async def restore_canvas(canvas_id: str):
    """从回收站恢复画布"""
    trash_item = _canvas_trash.pop(canvas_id, None)
    if not trash_item:
        raise HTTPException(status_code=404, detail="回收站中无此画布")
    svc = get_canvas_service()
    canvas_data = trash_item.get("canvas") or {}
    # 通过 service 重新创建画布（使用原 ID）
    try:
        layout = await svc.create(
            name=trash_item.get("name") or "恢复的画布",
            canvas_id=canvas_id,
            nodes=canvas_data.get("nodes", []),
            edges=canvas_data.get("edges", []),
            viewport=canvas_data.get("viewport", {}),
        )
        return {"success": True, "canvas": _to_canvas_dict(layout)}
    except Exception as e:
        # 恢复失败，把快照放回回收站以便重试
        _canvas_trash[canvas_id] = trash_item
        raise HTTPException(status_code=500, detail=f"恢复失败: {e}")


@router.delete("/canvases/{canvas_id}/purge")
async def purge_canvas(canvas_id: str):
    """从回收站永久清除画布（不可恢复）"""
    _canvas_trash.pop(canvas_id, None)
    return {"success": True}


# ============================================================
# 文件上传
# ============================================================

def _copy_to_comfyui_input(fname: str, content: bytes) -> dict:
    """将上传的文件同步到 ComfyUI input 目录

    Returns:
        dict: 同步状态，包含 synced/skipped/error 字段，供调用方反馈给前端
    """
    try:
        from services.comfyui_service import COMFYUI_DIR
        if not COMFYUI_DIR:
            return {"synced": False, "skipped": True, "reason": "ComfyUI 未配置"}
        input_dir = os.path.join(COMFYUI_DIR, "input")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, fname), "wb") as f:
            f.write(content)
        return {"synced": True, "skipped": False, "path": os.path.join(input_dir, fname)}
    except Exception as e:
        logger.warning(f"[Upload] 同步到 ComfyUI input 失败: {fname} | {e}")
        return {"synced": False, "skipped": False, "error": str(e)}


@router.post("/ai/upload")
async def upload_ai_reference(files: List[UploadFile] = File(...)):
    """上传参考图/视频/音频文件到持久化目录"""
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".flv"}
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
    uploaded = []
    for file in files:
        content = await file.read()
        if not content:
            continue
        ext = os.path.splitext(file.filename or "")[1].lower()
        ct = (file.content_type or "").lower()
        if ext in video_exts or ct.startswith("video/"):
            kind = "video"
        elif ext in audio_exts or ct.startswith("audio/"):
            kind = "audio"
        else:
            kind = "image"
        fname = f"ai_ref_{uuid.uuid4().hex[:12]}{ext or '.png'}"
        with open(os.path.join(_UPLOAD_DIR, fname), "wb") as f:
            f.write(content)
        # 同步到 ComfyUI input（如果是图片），返回同步状态供前端提示
        comfyui_sync = {"synced": False, "skipped": True, "reason": "非图片"}
        if kind == "image":
            comfyui_sync = _copy_to_comfyui_input(fname, content)
        uploaded.append({
            "url": f"/static/director/uploads/{fname}",
            "name": file.filename or fname,
            "kind": kind,
            "mime": ct,
            "comfyui_sync": comfyui_sync,
        })
    return {"files": uploaded}


@router.post("/upload")
async def upload_image(files: List[UploadFile] = File(...)):
    """上传到 ComfyUI input 目录"""
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    uploaded = []
    for file in files:
        content = await file.read()
        if not content:
            continue
        fname = f"comfy_{uuid.uuid4().hex[:8]}_{file.filename or 'img'}"
        with open(os.path.join(_UPLOAD_DIR, fname), "wb") as f:
            f.write(content)
        # 同时写入 ComfyUI input（如果可用）
        try:
            from services.comfyui_service import COMFYUI_DIR, get_comfyui_service
            if COMFYUI_DIR:
                input_dir = os.path.join(COMFYUI_DIR, "input")
                os.makedirs(input_dir, exist_ok=True)
                with open(os.path.join(input_dir, fname), "wb") as f2:
                    f2.write(content)
        except Exception:
            pass
        uploaded.append({"comfy_name": fname})
    return {"files": uploaded}


@router.post("/comfyui/upload/image")
async def upload_comfyui_image(file: UploadFile = File(...)):
    """canvas.js 上传图片到 ComfyUI input 目录"""
    try:
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="空文件")
        ext = os.path.splitext(file.filename or "img.png")[1] or ".png"
        fname = f"comfy_{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex[:4]}{ext}"
        with open(os.path.join(_UPLOAD_DIR, fname), "wb") as f:
            f.write(content)
        # 同时写入 ComfyUI input（如果可用）
        try:
            from services.comfyui_service import COMFYUI_DIR
            if COMFYUI_DIR:
                input_dir = os.path.join(COMFYUI_DIR, "input")
                os.makedirs(input_dir, exist_ok=True)
                with open(os.path.join(input_dir, fname), "wb") as f2:
                    f2.write(content)
        except Exception as e:
            logger.warning(f"[Upload] ComfyUI input 写入失败（非致命）: {e}")
        return {"name": fname, "filename": fname}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Upload] 上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ai/upload-base64")
async def upload_ai_base64(request: Request):
    """Base64 上传"""
    body = await request.json()
    data = body.get("data", "")
    name = body.get("name", "image.png")
    content_type = body.get("content_type", "image/png")
    if not data:
        return {"files": []}
    import base64
    if "," in data:
        data = data.split(",")[1]
    try:
        content = base64.b64decode(data)
    except Exception:
        return {"files": []}
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(name)[1].lower() or ".png"
    fname = f"b64_{uuid.uuid4().hex[:12]}{ext}"
    with open(os.path.join(_UPLOAD_DIR, fname), "wb") as f:
        f.write(content)
    _copy_to_comfyui_input(fname, content)
    return {"files": [{"url": f"/static/director/uploads/{fname}", "name": name, "kind": "image", "mime": content_type}]}


# ============================================================
# 媒体预览
# ============================================================

@router.get("/media-preview")
async def media_preview(url: str = "", w: int = 0):
    """媒体预览缩略图

    安全限制：仅允许重定向到本机相对路径或已知白名单域名，
    防止开放重定向被钓鱼攻击利用。
    """
    if not url:
        raise HTTPException(status_code=400, detail="url 参数必填")

    # 仅允许本机相对路径（/开头但非 //）
    if url.startswith("/") and not url.startswith("//"):
        return RedirectResponse(url=url)

    # 远程 URL 需在白名单内
    from urllib.parse import urlparse
    parsed = urlparse(url)
    allowed_hosts = {
        "localhost", "127.0.0.1", "0.0.0.0",
        "www.runninghub.cn", "runninghub.cn",
        "modelscope.cn", "www.modelscope.cn",
    }
    # 也允许 ComfyUI 自身主机名（通过环境变量配置）
    comfy_host = os.getenv("COMFYUI_HOST", "")
    if comfy_host:
        allowed_hosts.add(comfy_host)
    if parsed.hostname in allowed_hosts:
        return RedirectResponse(url=url)

    # 其他远程 URL：不重定向，返回 400 防止钓鱼
    raise HTTPException(status_code=400, detail="不允许的 URL 来源")


# ============================================================
# 核心生成：ComfyUI 工作流执行（异步模式）
# ============================================================

@router.post("/generate")
async def generate(request: Request):
    """核心生成接口：创建异步任务，立即返回 task_id"""
    body = await request.json()
    raw_wf = body.get("workflow_json", "")
    params = body.get("params", {})
    prompt_text = body.get("prompt_text", body.get("prompt", ""))
    gen_type = body.get("type", body.get("gen_type", ""))
    refs = body.get("refs", [])
    optional_empty_nodes = body.get("optional_empty_nodes", [])

    logger.info(f"[InfiniteCanvas] generate | type={gen_type} | prompt_len={len(prompt_text)} | params_keys={list(params.keys())}")

    from services.comfyui_service import get_comfyui_service, COMFYUI_DIR
    import shutil  # 函数内统一导入，避免下方多处重复 import

    comfyui_svc = get_comfyui_service()

    # 检查 ComfyUI 是否可用
    try:
        comfyui_ok = await comfyui_svc._check_alive()
        if not comfyui_ok:
            return {"images": [], "error": "ComfyUI 未连接，请在设置中配置并启动 ComfyUI"}
    except Exception:
        return {"images": [], "error": "ComfyUI 未连接，请在设置中配置并启动 ComfyUI"}

    # 解析工作流数据
    wf_data = None
    if isinstance(raw_wf, dict):
        wf_data = raw_wf.get("workflow") or raw_wf
    elif isinstance(raw_wf, str) and raw_wf.strip():
        wf_path = os.path.join(_WF_DIR, raw_wf)
        if not os.path.exists(wf_path):
            wf_path2 = os.path.join(_WF_DIR, f"{raw_wf}.json")
            if os.path.exists(wf_path2):
                wf_path = wf_path2
            else:
                return {"images": [], "error": f"工作流文件不存在: {raw_wf}"}
        with open(wf_path, "r", encoding="utf-8") as f:
            wf_data = json.load(f)

    if wf_data:
        # 注入参数
        logger.info(f"[InfiniteCanvas] 注入参数: {json.dumps(params, ensure_ascii=False)}")
        # ⭐ 修复 Deep Issue 4：node_id 白名单校验，防止前端传入错误节点 ID 破坏工作流
        # 仅允许覆写 inputs 字段，禁止触碰 class_type/_meta/connections 等结构字段
        SAFE_INPUT_KEYS = {"inputs"}
        for node_id_str, node_params in params.items():
            node_id = str(node_id_str)
            if node_id not in wf_data:
                logger.warning(f"[InfiniteCanvas] 跳过未知节点 ID: {node_id}（不在工作流模板中）")
                continue
            if not isinstance(node_params, dict):
                logger.warning(f"[InfiniteCanvas] 节点 {node_id} 参数非 dict，跳过")
                continue
            for k, v in node_params.items():
                # 只允许注入 inputs，阻止篡改工作流结构
                if k not in SAFE_INPUT_KEYS and k != "inputs":
                    logger.warning(f"[InfiniteCanvas] 节点 {node_id} 拒绝非 inputs 字段写入: {k}")
                    continue
                if k == "inputs" and isinstance(v, dict):
                    for k2, v2 in v.items():
                        wf_data[node_id]["inputs"][k2] = v2
                elif isinstance(v, dict):
                    # 兼容旧格式：{node_id: {input_key: val}}
                    for k2, v2 in v.items():
                        wf_data[node_id]["inputs"][k2] = v2
                else:
                    wf_data[node_id]["inputs"][k] = v
            # 确保图片文件存在于 ComfyUI input 目录
            ct = wf_data[node_id].get("class_type", "")
            if ct == "LoadImage" and COMFYUI_DIR:
                img_name = wf_data[node_id].get("inputs", {}).get("image", "")
                if img_name:
                    input_dir = os.path.join(COMFYUI_DIR, "input")
                    img_path = os.path.join(input_dir, img_name)
                    if not os.path.isfile(img_path):
                        src = os.path.join(_UPLOAD_DIR, img_name)
                        if os.path.isfile(src):
                            os.makedirs(input_dir, exist_ok=True)
                            shutil.copy2(src, img_path)
                            logger.info(f"[InfiniteCanvas] 复制图片 {img_name} → ComfyUI input")
                        else:
                            asset_upload_dir = os.path.join(_BASE_DIR, "data", "uploads")
                            src2 = os.path.join(asset_upload_dir, img_name)
                            if os.path.isfile(src2):
                                os.makedirs(input_dir, exist_ok=True)
                                shutil.copy2(src2, img_path)
                                logger.info(f"[InfiniteCanvas] 复制资产库图片 {img_name} → ComfyUI input")
                            else:
                                logger.warning(f"[InfiniteCanvas] 图片不存在: {img_name} (上传目录+资产库+ComfyUI input均未找到)")

        # 检查 LoadImage 节点图片
        missing_images = []
        input_dir = os.path.join(COMFYUI_DIR, "input") if COMFYUI_DIR else ""
        output_dir = os.path.join(COMFYUI_DIR, "output") if COMFYUI_DIR else ""
        for nid, ndata in wf_data.items():
            if not isinstance(ndata, dict) or ndata.get("class_type") != "LoadImage":
                continue
            if nid in optional_empty_nodes:
                logger.info(f"[InfiniteCanvas] LoadImage node={nid} 可选字段为空，使用占位图")
                placeholder_name = "_optional_placeholder.png"
                placeholder_path = os.path.join(input_dir, placeholder_name) if input_dir else ""
                if input_dir and not os.path.isfile(placeholder_path):
                    os.makedirs(input_dir, exist_ok=True)
                    try:
                        from PIL import Image as PILImage
                        img = PILImage.new("RGBA", (64, 64), (0, 0, 0, 0))
                        img.save(placeholder_path)
                    except Exception:
                        import struct, zlib
                        raw = b'\x00\x00\x00\x00' * 64
                        compressed = zlib.compress(raw)
                        def png_chunk(ctype, data):
                            c = ctype + data
                            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
                        with open(placeholder_path, 'wb') as pf:
                            pf.write(b'\x89PNG\r\n\x1a\n')
                            pf.write(png_chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 6, 0, 0, 0)))
                            pf.write(png_chunk(b'IDAT', compressed))
                            pf.write(png_chunk(b'IEND', b''))
                if input_dir:
                    ndata.setdefault("inputs", {})["image"] = placeholder_name
                continue
            img_name = ndata.get("inputs", {}).get("image", "")
            if not img_name:
                continue
            title = ndata.get("_meta", {}).get("title", nid)
            img_in_input = os.path.isfile(os.path.join(input_dir, img_name)) if input_dir else False
            img_in_upload = os.path.isfile(os.path.join(_UPLOAD_DIR, img_name))
            img_in_output = os.path.isfile(os.path.join(output_dir, img_name)) if output_dir else False
            asset_upload_dir = os.path.join(_BASE_DIR, "data", "uploads")
            img_in_asset_upload = os.path.isfile(os.path.join(asset_upload_dir, img_name))
            if img_in_input:
                logger.info(f"[InfiniteCanvas] LoadImage node={nid} image={img_name} ✓ (input)")
            elif img_in_upload:
                if input_dir:
                    os.makedirs(input_dir, exist_ok=True)
                    shutil.copy2(os.path.join(_UPLOAD_DIR, img_name), os.path.join(input_dir, img_name))
                    logger.info(f"[InfiniteCanvas] LoadImage node={nid} image={img_name} 复制 upload→input ✓")
            elif img_in_asset_upload:
                if input_dir:
                    os.makedirs(input_dir, exist_ok=True)
                    shutil.copy2(os.path.join(asset_upload_dir, img_name), os.path.join(input_dir, img_name))
                    logger.info(f"[InfiniteCanvas] LoadImage node={nid} image={img_name} 复制 asset_upload→input ✓")
            elif img_in_output:
                if input_dir:
                    os.makedirs(input_dir, exist_ok=True)
                    shutil.copy2(os.path.join(output_dir, img_name), os.path.join(input_dir, img_name))
                    logger.info(f"[InfiniteCanvas] LoadImage node={nid} image={img_name} 复制 output→input ✓")
            else:
                missing_images.append(f"{title}(节点{nid}): {img_name}")
                logger.warning(f"[InfiniteCanvas] LoadImage node={nid} image={img_name} ✗ 不存在")

        if missing_images:
            err_msg = "以下图片文件不存在，请上传或选择：\n" + "\n".join(missing_images)
            logger.error(f"[InfiniteCanvas] {err_msg}")
            return {"images": [], "error": err_msg}

    # 创建异步任务（统一通过 GenTaskManager 管理）
    gen_mgr = get_gen_task_manager()
    task = await gen_mgr.create_task(
        f"canvas_generate_{gen_type}" if gen_type else "canvas_generate",
        _execute_generate_task,
        comfyui_svc, wf_data, prompt_text, gen_type,
    )
    await gen_mgr.submit_task(task.task_id)

    return {"task_id": task.task_id, "status": "pending"}


async def _execute_generate_task(comfyui_svc, wf_data, prompt_text: str, gen_type: str = ""):
    """后台执行生成任务（通过 GenTaskManager 调度，返回结果字典）

    返回值会被 GenTaskManager 写入 task.result。
    异常会被 GenTaskManager 捕获并写入 task.error。
    """
    prompt_id = ""
    try:
        if wf_data:
            prompt_id = await comfyui_svc._queue_prompt_with_retry(wf_data)
            filenames = await comfyui_svc._wait_for_completion(prompt_id, task_type="generate")
            all_filenames = filenames or []
            logger.info(f"[InfiniteCanvas] 生成完成 | prompt_id={prompt_id[:8]} | filenames={all_filenames}")
            _generated_dir = os.path.join(_BASE_DIR, "data", "generated")
            for fn in all_filenames:
                from services.comfyui_service import COMFYUI_DIR
                fpath = os.path.join(COMFYUI_DIR or "", "output", fn) if COMFYUI_DIR else ""
                if not fpath or not os.path.isfile(fpath):
                    fpath2 = os.path.join(_generated_dir, fn)
                    if os.path.isfile(fpath2):
                        pass
                    else:
                        logger.warning(f"[InfiniteCanvas] 输出文件不存在: {fn}")
        else:
            result = await comfyui_svc.generate(
                prompt=prompt_text or "A beautiful scene",
                size="1024x1024",
                model="",
                reference_images=[],
                content_type="",
                asset_tag="canvas_gen",
            )
            all_filenames = result.filenames or []
    except Exception as e:
        # 异常增强：ComfyUI 校验失败时检测缺失图片，给出更友好的错误信息
        err_msg = str(e)
        if ("Custom validation failed" in err_msg or "validation" in err_msg.lower()) and wf_data:
            from services.comfyui_service import COMFYUI_DIR
            missing = []
            for nid, ndata in wf_data.items():
                if isinstance(ndata, dict) and ndata.get("class_type") == "LoadImage":
                    img = ndata.get("inputs", {}).get("image", "")
                    title = ndata.get("_meta", {}).get("title", nid)
                    if img:
                        input_dir = os.path.join(COMFYUI_DIR, "input") if COMFYUI_DIR else ""
                        upload_dir = _UPLOAD_DIR
                        exists = (os.path.isfile(os.path.join(input_dir, img)) if input_dir else False) or os.path.isfile(os.path.join(upload_dir, img))
                        if not exists:
                            missing.append(f"{title}({nid}): {img}")
            if missing:
                raise RuntimeError(f"以下 {len(missing)} 个图片文件不存在，请点击节点上的上传按钮上传：\n" + "\n".join(missing)) from e
            raise RuntimeError("ComfyUI 图片处理异常（文件可能正在同步，请稍后重试）") from e
        raise

    image_urls = [f"/api/comfyui/image?filename={fn}" for fn in all_filenames]
    logger.info(f"[InfiniteCanvas] 返回图片: {image_urls}")

    # 生成结果回写资产库，防止切换页面/重启后结果消失
    asset_id = await _register_generated_asset(
        image_urls,
        asset_type="storyboard",
        name=f"画布生成",
        prompt=prompt_text,
        gen_type=gen_type,
    )

    return {
        "images": image_urls,
        "success": True,
        "asset_id": asset_id,
        "prompt_id": prompt_id,
    }


async def _register_generated_asset(
    image_urls: list,
    *,
    asset_type: str = "storyboard",
    name: str = "画布生成",
    prompt: str = "",
    gen_type: str = "",
    source_asset_ids: Optional[list] = None,
) -> Optional[str]:
    """将生成结果回写到 AssetService，返回 asset_id

    D1 修复：无限画布生成的图片必须进入资产库，否则：
    - 切换页面/重启后结果消失（仅存内存任务字典）
    - VideoPage 无法选择画布结果作为输入
    - 资产库永远看不到画布产出
    """
    if not image_urls:
        return None
    try:
        from services.asset_service import get_asset_service
        asset_svc = get_asset_service()
        metadata = {
            "source": "infinite_canvas",
            "gen_type": gen_type or "comfyui",
            "prompt": prompt,
        }
        if source_asset_ids:
            metadata["source_asset_ids"] = source_asset_ids
        asset = await asset_svc.create(
            asset_type=asset_type,
            name=name,
            urls=image_urls,
            metadata=metadata,
            parent_id=source_asset_ids[0] if source_asset_ids else None,
        )
        logger.info(f"[InfiniteCanvas] 生成结果回写资产库 | asset_id={asset.asset_id} | urls={len(image_urls)} | type={asset_type}")
        return asset.asset_id
    except Exception as e:
        logger.error(f"[InfiniteCanvas] 生成结果回写资产库失败: {e}", exc_info=True)
        return None


@router.get("/generate/{task_id}")
async def get_generate_status(task_id: str):
    """查询生成任务状态（从 GenTaskManager 查询）"""
    _ensure_cleanup_task()
    gen_mgr = get_gen_task_manager()
    task = gen_mgr.get_task(task_id)
    if not task:
        return {"status": "failed", "error": f"任务不存在: {task_id}"}

    # 状态映射：后端 completed → 前端 succeeded
    status = to_frontend_status(task.status)

    result = {"status": status, "task_id": task_id}
    if status == TaskStatus.SUCCEEDED and task.result:
        result.update(task.result)
    elif status == TaskStatus.FAILED:
        result["error"] = task.error
        result["images"] = []
    return result


# ============================================================
# 在线图片生成（通过 ProviderService）
# ============================================================

@router.post("/online-image")
async def online_image(request: Request):
    """在线图片生成：通过已配置的供应商"""
    body = await request.json()
    provider_id = body.get("provider_id", "")
    model = body.get("model", "")
    prompt = body.get("prompt", "")
    refs = body.get("reference_images", [])
    size = body.get("size", body.get("resolution", "1024x1024"))

    logger.info(f"[InfiniteCanvas] online-image | provider={provider_id} | model={model}")

    if not provider_id:
        return {"images": [], "error": "未指定供应商，请在左下角设置中添加 API"}

    try:
        from services.provider_service import get_provider_service
        svc = get_provider_service()

        # 将参考图列表转为 ProviderService 期望的格式：[{url: ..., type: ...}, ...]
        ref_items = []
        for r in refs:
            if isinstance(r, dict):
                ref_items.append({"url": r.get("url", r.get("image_url", "")), "type": r.get("type", "image")})
            elif isinstance(r, str):
                ref_items.append({"url": r, "type": "image"})

        result = await svc.generate_image(
            provider_id=provider_id,
            prompt=prompt,
            size=size,
            model=model,
            reference_images=ref_items,
        )
        return {"images": result.images, "success": True, "elapsed_ms": result.elapsed_ms}
    except Exception as e:
        logger.error(f"[InfiniteCanvas] online-image 失败: {e}")
        return {"images": [], "error": str(e)}


# ============================================================
# 视频生成（通过 ProviderService）
# ============================================================

@router.post("/canvas-video")
async def canvas_video(request: Request):
    """视频生成"""
    body = await request.json()
    provider_id = body.get("provider_id", "")
    prompt = body.get("prompt", "")
    model = body.get("model", "")
    duration = body.get("duration", 5)
    aspect_ratio = body.get("aspect_ratio", "16:9")
    refs = body.get("reference_images", [])
    # ⭐ 修复 Deep Issue 1：补齐视频质量参数（此前全丢，Provider 拿到默认值）
    width = body.get("width")
    height = body.get("height")
    frame_count = body.get("frame_count")
    seed = body.get("seed")
    fps = body.get("fps")
    resolution = body.get("resolution", "")

    logger.info(f"[InfiniteCanvas] canvas-video | provider={provider_id} | duration={duration}s | {width}x{height} | fps={fps}")

    if not provider_id:
        return {"videos": [], "error": "未指定供应商"}

    try:
        from services.provider_service import get_provider_service
        svc = get_provider_service()
        result = await svc.generate_video(
            provider_id=provider_id,
            prompt=prompt,
            model=model,
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            width=width,
            height=height,
            frame_count=frame_count,
            seed=seed,
            fps=fps,
            images=[r.get("url", r) if isinstance(r, dict) else r for r in refs],
        )
        videos = []
        if result.video_url:
            videos.append({"url": result.video_url, "duration": duration})
        # D5 修复：视频生成结果回写资产库，供后续剪辑/导出阶段使用
        video_urls = [v["url"] for v in videos if v.get("url")]
        asset_id = await _register_generated_asset(
            video_urls,
            asset_type="video",
            name=f"画布视频 {model[:20]}",
            prompt=prompt,
            gen_type=f"video:{provider_id}",
        )
        return {"videos": videos, "success": True, "asset_id": asset_id}
    except Exception as e:
        logger.error(f"[InfiniteCanvas] canvas-video 失败: {e}")
        return {"videos": [], "error": str(e)}


# ============================================================
# LLM 对话（通过供应商）
# ============================================================

@router.post("/canvas-llm")
async def canvas_llm(request: Request):
    """LLM 对话（通过 OpenAI 兼容 API）"""
    body = await request.json()
    prompt = body.get("message", body.get("prompt", ""))
    provider_id = body.get("provider", body.get("provider_id", ""))
    model = body.get("model", "gpt-4o-mini")
    system_prompt = body.get("system_prompt", "You are a helpful assistant.")
    messages = body.get("messages", [])

    logger.info(f"[InfiniteCanvas] canvas-llm | provider={provider_id} | prompt={prompt[:50]}...")

    # 尝试直接调用 OpenAI 兼容 API
    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    if not api_key:
        return {"choices": [{"message": {"content": "请设置 OPENAI_API_KEY 环境变量以使用 LLM 功能"}}]}

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        msgs = [{"role": "system", "content": system_prompt}]
        if messages:
            msgs.extend(messages)
        else:
            msgs.append({"role": "user", "content": prompt})
        resp = await client.chat.completions.create(model=model, messages=msgs)
        content = resp.choices[0].message.content if resp.choices else ""
        return {"choices": [{"message": {"content": content}}]}
    except Exception as e:
        logger.warning(f"[InfiniteCanvas] canvas-llm 失败: {e}")
        return {"choices": [{"message": {"content": f"[LLM 调用失败: {e}]"}}]}


# ============================================================
# 图片任务管理
# ============================================================
# 图片生成任务（已统一由 GenTaskManager 管理）
# ============================================================

# RunningHub 任务状态缓存（RH 任务在远程执行，本地仅缓存查询结果）
_rh_tasks: dict = {}
_RH_TASK_TTL = 86400    # 24 小时（RH 任务查询周期较长）
_RH_TASK_MAX = 500      # 最大缓存条目数，防止内存泄漏

@router.post("/canvas-image-tasks")
async def create_image_task(request: Request):
    body = await request.json()
    # 统一通过 GenTaskManager 管理图片生成任务（替代旧的 _image_tasks 字典）
    from services.gen_task_manager import get_gen_task_manager
    gen_mgr = get_gen_task_manager()
    task = await gen_mgr.create_task(
        "canvas_image_generate",
        _execute_image_task_via_gen,
        body,
    )
    await gen_mgr.submit_task(task.task_id)
    return {"task_id": task.task_id, "status": "pending"}


async def _execute_image_task_via_gen(body: dict):
    """后台执行图片生成任务（通过 GenTaskManager 调度）

    返回值会被 GenTaskManager 写入 task.result。
    异常会被 GenTaskManager 捕获并写入 task.error。
    """
    provider_id = body.get("provider_id", "")
    model = body.get("model", "")
    prompt = body.get("prompt", "")
    refs = body.get("reference_images", [])
    size = body.get("size", body.get("resolution", "1024x1024"))
    quality = body.get("quality", "")

    if not provider_id:
        raise ValueError("未指定供应商")

    from services.provider_service import get_provider_service
    svc = get_provider_service()

    ref_items = []
    for r in refs:
        if isinstance(r, dict):
            ref_items.append({"url": r.get("url", r.get("image_url", "")), "type": r.get("type", "image")})
        elif isinstance(r, str):
            ref_items.append({"url": r, "type": "image"})

    kwargs = {}
    if quality:
        kwargs["quality"] = quality

    result = await svc.generate_image(
        provider_id=provider_id,
        prompt=prompt,
        size=size,
        model=model,
        reference_images=ref_items,
        **kwargs,
    )
    image_urls = result.images or []

    # 生成结果回写资产库
    source_asset_ids = [r.get("asset_id") for r in refs if isinstance(r, dict) and r.get("asset_id")]
    asset_id = await _register_generated_asset(
        image_urls,
        asset_type="storyboard",
        name=f"画布生成",
        prompt=prompt,
        gen_type=provider_id,
        source_asset_ids=source_asset_ids or None,
    )

    return {
        "images": image_urls,
        "elapsed_ms": result.elapsed_ms,
        "asset_id": asset_id,
    }


def _cleanup_expired_rh_tasks():
    """清理过期的 RunningHub 任务缓存，防止内存泄漏

    注意：仅清理 RH 任务缓存（外部任务状态缓存），图片生成任务已由 GenTaskManager 统一清理。
    """
    now = time.time()
    # RH 任务缓存
    expired_rh = [
        k for k, v in _rh_tasks.items()
        if v.get("status") in ("SUCCESS", "FAILED")
        and now - v.get("updated_at", v.get("created_at", 0)) > _RH_TASK_TTL
    ]
    for k in expired_rh:
        _rh_tasks.pop(k, None)
    # 超出最大缓存条目数时，淘汰最旧的条目
    if len(_rh_tasks) > _RH_TASK_MAX:
        sorted_keys = sorted(_rh_tasks.keys(), key=lambda k: _rh_tasks[k].get("created_at", 0))
        for k in sorted_keys[:len(_rh_tasks) - _RH_TASK_MAX]:
            _rh_tasks.pop(k, None)
    if expired_rh:
        logger.info(f"[InfiniteCanvas] 清理过期 RH 任务缓存 | rh={len(expired_rh)}")


@router.get("/canvas-image-tasks/{task_id}")
async def get_image_task(task_id: str):
    """查询图片生成任务（GET 方式，通过 GenTaskManager 查询）"""
    from services.gen_task_manager import get_gen_task_manager
    from services.task_status import to_frontend_status
    gen_mgr = get_gen_task_manager()
    task = gen_mgr.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = task.result or {}
    return {
        "task_id": task_id,
        "status": to_frontend_status(task.status),
        "result": result,
        "images": result.get("images", []),
        "error": task.error,
    }


@router.post("/image-task-query")
async def query_image_task(request: Request):
    """查询图片生成任务（POST 方式，通过 GenTaskManager 查询）"""
    body = await request.json()
    task_id = body.get("task_id", "")
    from services.gen_task_manager import get_gen_task_manager
    from services.task_status import to_frontend_status
    gen_mgr = get_gen_task_manager()
    task = gen_mgr.get_task(task_id)
    if not task:
        return {"status": "failed", "images": []}
    result = task.result or {}
    return {
        "status": to_frontend_status(task.status),
        "images": result.get("images", []),
        "error": task.error,
    }


# ============================================================
# 资产库管理（磁盘持久化，避免服务重启后丢失）
# ============================================================

# 持久化目录
_LIB_PERSIST_DIR = os.path.join(_BASE_DIR, "data", "libraries")
_LIB_PERSIST_FILE = os.path.join(_LIB_PERSIST_DIR, "library_state.json")

_asset_libraries: dict = {}
_asset_categories: dict = {}
_asset_items: dict = {}
_prompt_libraries: dict = {}
_prompt_items: dict = {}
_prompt_categories: dict = {}


def _save_library_state():
    """将资产库/提示词库状态持久化到磁盘（原子写入）"""
    try:
        os.makedirs(_LIB_PERSIST_DIR, exist_ok=True)
        state = {
            "asset_libraries": _asset_libraries,
            "asset_categories": _asset_categories,
            "asset_items": _asset_items,
            "prompt_libraries": _prompt_libraries,
            "prompt_items": _prompt_items,
            "prompt_categories": _prompt_categories,
            "saved_at": time.time(),
        }
        tmp_path = _LIB_PERSIST_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, _LIB_PERSIST_FILE)
    except Exception as e:
        logger.warning(f"[InfiniteCanvas] 资产库状态持久化失败: {e}")


def _load_library_state():
    """从磁盘加载资产库/提示词库状态"""
    global _asset_libraries, _asset_categories, _asset_items
    global _prompt_libraries, _prompt_items, _prompt_categories
    if not os.path.exists(_LIB_PERSIST_FILE):
        return
    try:
        with open(_LIB_PERSIST_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
        _asset_libraries = state.get("asset_libraries", {}) or {}
        _asset_categories = state.get("asset_categories", {}) or {}
        _asset_items = state.get("asset_items", {}) or {}
        _prompt_libraries = state.get("prompt_libraries", {}) or {}
        _prompt_items = state.get("prompt_items", {}) or {}
        _prompt_categories = state.get("prompt_categories", {}) or {}
        logger.info(
            f"[InfiniteCanvas] 加载资产库状态 | "
            f"asset_libs={len(_asset_libraries)} asset_items={len(_asset_items)} "
            f"prompt_libs={len(_prompt_libraries)} prompt_items={len(_prompt_items)}"
        )
    except Exception as e:
        logger.warning(f"[InfiniteCanvas] 加载资产库状态失败: {e}")


# 模块加载时恢复状态
_load_library_state()

# 资产库 CRUD
@router.post("/asset-library/libraries")
async def create_asset_library(request: Request):
    body = await request.json()
    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    _asset_libraries[lib_id] = {"id": lib_id, "name": body.get("name", "未命名")}
    _save_library_state()
    return {"success": True, "library": _asset_libraries[lib_id]}

@router.post("/asset-library/categories")
async def create_asset_category(request: Request):
    body = await request.json()
    cat_id = f"cat_{uuid.uuid4().hex[:8]}"
    _asset_categories[cat_id] = {"id": cat_id, "name": body.get("name", ""), "library_id": body.get("library_id", "")}
    _save_library_state()
    return {"success": True, "category": _asset_categories[cat_id]}

@router.post("/asset-library/items")
async def create_asset_item(request: Request):
    body = await request.json()
    item_id = f"item_{uuid.uuid4().hex[:8]}"
    _asset_items[item_id] = {"id": item_id, "library_id": body.get("library_id", ""), "category_id": body.get("category_id", ""), "url": body.get("url", ""), "name": body.get("name", "")}
    _save_library_state()
    # D6 修复：同步到 AssetService，让 React 端（资产库/VideoPage）也能使用
    try:
        from services.asset_service import get_asset_service
        asset_svc = get_asset_service()
        url = body.get("url", "")
        await asset_svc.create(
            asset_type="storyboard",
            name=body.get("name", "画布资产"),
            urls=[url] if url else [],
            metadata={
                "source": "canvas_asset_library",
                "library_id": body.get("library_id", ""),
                "category_id": body.get("category_id", ""),
                "item_id": item_id,
            },
        )
    except Exception as e:
        logger.warning(f"[InfiniteCanvas] 资产同步到 AssetService 失败: {e}")
    return {"success": True, "item": _asset_items[item_id]}

@router.post("/asset-library/items/batch")
async def batch_create_asset_items(request: Request):
    body = await request.json()
    items = body.get("items", [])
    created = []
    for item in items:
        item_id = f"item_{uuid.uuid4().hex[:8]}"
        _asset_items[item_id] = {"id": item_id, "library_id": body.get("library_id", ""), "category_id": body.get("category_id", ""), "url": item.get("url", ""), "name": item.get("name", "")}
        created.append(_asset_items[item_id])
    _save_library_state()
    # D6 修复：批量同步到 AssetService
    try:
        from services.asset_service import get_asset_service
        asset_svc = get_asset_service()
        for item in created:
            url = item.get("url", "")
            if url:
                await asset_svc.create(
                    asset_type="storyboard",
                    name=item.get("name", "画布资产"),
                    urls=[url],
                    metadata={
                        "source": "canvas_asset_library",
                        "library_id": body.get("library_id", ""),
                        "category_id": body.get("category_id", ""),
                        "item_id": item["id"],
                    },
                )
    except Exception as e:
        logger.warning(f"[InfiniteCanvas] 批量资产同步到 AssetService 失败: {e}")
    return {"success": True, "items": created}

@router.post("/asset-library/items/delete")
async def batch_delete_asset_items(request: Request):
    body = await request.json()
    for iid in body.get("ids", []):
        _asset_items.pop(iid, None)
    _save_library_state()
    return {"success": True}

@router.patch("/asset-library/items/{item_id}")
async def update_asset_item(item_id: str, request: Request):
    body = await request.json()
    if item_id in _asset_items:
        if "name" in body:
            _asset_items[item_id]["name"] = body["name"]
        _save_library_state()
        return {"success": True, "item": _asset_items[item_id]}
    return {"success": False}

@router.delete("/asset-library/items/{item_id}")
async def delete_asset_item(item_id: str):
    _asset_items.pop(item_id, None)
    _save_library_state()
    return {"success": True}

@router.post("/asset-library/workflows/upload")
async def upload_asset_workflow(request: Request):
    """上传工作流文件到资产库"""
    form = await request.form()
    library_id = form.get("library_id", "")
    category_id = form.get("category_id", "")
    files = form.getlist("files")

    created = []
    for file in files:
        if not file or not file.filename:
            continue
        item_id = f"aitem_{uuid.uuid4().hex[:8]}"
        # 保存文件到上传目录
        content = await file.read()
        safe_name = file.filename.replace("..", "").replace("/", "").replace("\\", "")
        save_path = os.path.join(_UPLOAD_DIR, safe_name)
        os.makedirs(_UPLOAD_DIR, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(content)
        url = f"/api/ai/upload/{safe_name}"
        _asset_items[item_id] = {
            "id": item_id,
            "library_id": library_id,
            "category_id": category_id,
            "name": safe_name,
            "asset_type": "workflow",
            "urls": [url],
            "created_at": time.time(),
        }
        created.append(_asset_items[item_id])

    _save_library_state()
    # 返回 library 数据供前端刷新
    libraries = []
    for alib in _asset_libraries.values():
        items = [v for v in _asset_items.values() if v.get("library_id") == alib["id"]]
        libraries.append({**alib, "items": items})
    return {"success": True, "workflow": {"id": created[0]["id"] if created else "", "name": created[0]["name"] if created else ""}, "library": {"libraries": libraries}}

# 提示词库 CRUD
@router.post("/prompt-libraries")
async def create_prompt_library(request: Request):
    body = await request.json()
    lib_id = f"plib_{uuid.uuid4().hex[:8]}"
    _prompt_libraries[lib_id] = {"id": lib_id, "name": body.get("name", "未命名")}
    _save_library_state()
    return {"success": True, "library": _prompt_libraries[lib_id]}

@router.post("/prompt-libraries/categories")
async def create_prompt_category(request: Request):
    body = await request.json()
    cat_id = f"pcat_{uuid.uuid4().hex[:8]}"
    _prompt_categories[cat_id] = {"id": cat_id, "name": body.get("name", ""), "library_id": body.get("library_id", "")}
    _save_library_state()
    return {"success": True, "category": _prompt_categories[cat_id]}

@router.post("/prompt-libraries/items")
async def create_prompt_item(request: Request):
    body = await request.json()
    item_id = f"pitem_{uuid.uuid4().hex[:8]}"
    _prompt_items[item_id] = {
        "id": item_id, "library_id": body.get("library_id", ""),
        "name": body.get("name", ""), "category": body.get("category", ""),
        "positive": body.get("positive", ""), "negative": body.get("negative", ""),
        "scene": body.get("scene", ""),
    }
    _save_library_state()
    # 返回 library 数据供前端刷新列表
    libraries = []
    for plib in _prompt_libraries.values():
        items = [v for v in _prompt_items.values() if v.get("library_id") == plib["id"]]
        libraries.append({**plib, "items": items})
    return {"success": True, "item": _prompt_items[item_id], "library": {"libraries": libraries}}

@router.patch("/prompt-libraries/items/{item_id}")
async def update_prompt_item(item_id: str, request: Request):
    body = await request.json()
    if item_id in _prompt_items:
        for k in ("name", "category", "positive", "negative", "scene", "library_id"):
            if k in body:
                _prompt_items[item_id][k] = body[k]
        _save_library_state()
        return {"success": True, "item": _prompt_items[item_id]}
    return {"success": False}

@router.delete("/prompt-libraries/items/{item_id}")
async def delete_prompt_item(item_id: str):
    _prompt_items.pop(item_id, None)
    _save_library_state()
    return {"success": True}


# ============================================================
# 其他工具端点
# ============================================================

@router.post("/ai/import-local-image")
async def import_local_image(request: Request):
    """导入本地图片"""
    body = await request.json()
    paths = body.get("paths", [])
    files = []
    for p in paths:
        if os.path.isfile(p):
            fname = os.path.basename(p)
            import shutil
            dst = os.path.join(_UPLOAD_DIR, f"import_{uuid.uuid4().hex[:8]}_{fname}")
            os.makedirs(_UPLOAD_DIR, exist_ok=True)
            try:
                shutil.copy2(p, dst)
                files.append({"url": f"/static/director/uploads/{os.path.basename(dst)}", "name": fname, "kind": "image"})
            except Exception:
                pass
    return {"files": files}


@router.post("/cloud-video/upload")
async def cloud_video_upload(request: Request):
    """上传本地媒体文件到云端，返回公开可访问的 URL"""
    body = await request.json()
    url = body.get("url", "")
    service = body.get("service", "auto")

    if not url:
        return {"url": "", "error": "缺少 url"}

    # 如果已经是远程 URL，直接返回
    if url.startswith(("http://", "https://")):
        return {"url": url, "expires": "permanent"}

    try:
        from services.providers.provider_utils import output_file_from_url
        local_path = output_file_from_url(url)
        if not local_path or not os.path.exists(local_path):
            return {"url": "", "error": f"文件不存在: {url}"}

        # 尝试上传到配置的云存储
        import httpx
        # 检查是否有 RunningHub 可用（作为中转上传）
        from services.providers.runninghub_provider import RunningHubProvider
        rh = RunningHubProvider()
        if rh.is_available():
            headers = rh._headers()
            base_url = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")
            upload_url = f"{base_url}/task/openapi/upload"
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)) as client:
                with open(local_path, "rb") as f:
                    resp = await client.post(
                        upload_url,
                        headers={"Authorization": headers["Authorization"]},
                        files={"file": (os.path.basename(local_path), f)},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    cloud_url = data.get("data", {}).get("url", "")
                    if cloud_url:
                        return {"url": cloud_url, "expires": "3 days"}

        # 回退：返回本地 API URL（仅本机可访问）
        if url.startswith("/"):
            return {"url": url, "expires": "local"}
        return {"url": f"/api/comfyui/image?filename={os.path.basename(local_path)}", "expires": "local"}

    except Exception as e:
        logger.error(f"[InfiniteCanvas] cloud-video/upload 失败: {e}")
        # 回退到本地 URL
        return {"url": url, "expires": "local"}


@router.post("/runninghub/upload-asset")
async def runninghub_upload_asset(request: Request):
    """上传资产到 RunningHub，返回 fileName"""
    body = await request.json()
    url = body.get("url", "")
    use_wallet = body.get("useWallet", False)

    if not url:
        return {"success": False, "error": "缺少 url"}

    try:
        from services.providers.runninghub_provider import RunningHubProvider
        provider = RunningHubProvider()
        if not provider.is_available():
            return {"success": False, "error": "RunningHub API Key 未配置"}

        headers = provider._headers(use_wallet=use_wallet)
        base_url = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")

        # 如果是本地路径，先读取文件再上传
        import httpx
        from services.providers.provider_utils import output_file_from_url
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)) as client:
            upload_url = f"{base_url}/task/openapi/upload"

            if url.startswith("/output/") or url.startswith("/assets/"):
                # 本地文件路径
                local_path = output_file_from_url(url)
                if local_path and os.path.exists(local_path):
                    with open(local_path, "rb") as f:
                        resp = await client.post(
                            upload_url,
                            headers={"Authorization": headers["Authorization"]},
                            files={"file": (os.path.basename(local_path), f)},
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        file_name = data.get("data", {}).get("fileName", os.path.basename(local_path))
                        return {"success": True, "data": {"fileName": file_name, "url": data.get("data", {}).get("url", "")}}
                return {"success": False, "error": f"文件不存在: {url}"}
            elif url.startswith(("http://", "https://")):
                # 远程 URL：下载后上传
                import tempfile
                try:
                    dl_resp = await client.get(url, follow_redirects=True)
                    dl_resp.raise_for_status()
                    fname = url.split("/")[-1].split("?")[0] or "image.png"
                    with tempfile.NamedTemporaryFile(suffix=f"_{fname}", delete=False) as tmp:
                        tmp.write(dl_resp.content)
                        tmp_path = tmp.name
                    try:
                        with open(tmp_path, "rb") as f:
                            resp = await client.post(
                                upload_url,
                                headers={"Authorization": headers["Authorization"]},
                                files={"file": (fname, f)},
                            )
                            resp.raise_for_status()
                            data = resp.json()
                            file_name = data.get("data", {}).get("fileName", fname)
                            return {"success": True, "data": {"fileName": file_name, "url": data.get("data", {}).get("url", "")}}
                    finally:
                        os.unlink(tmp_path)
                except Exception as e:
                    return {"success": False, "error": f"下载远程文件失败: {e}"}
            else:
                # 可能已经是 RunningHub 文件名
                return {"success": True, "data": {"fileName": url}}

    except Exception as e:
        logger.error(f"[InfiniteCanvas] RunningHub upload-asset 失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/local-assets/upload")
async def upload_local_asset(request: Request):
    return {"success": True}


# ============================================================
# 画布资产检查与下载
# ============================================================

@router.post("/canvas-assets/check")
async def check_canvas_assets(request: Request):
    """检查资产 URL 是否存在"""
    body = await request.json()
    urls = body.get("urls", [])
    exists = {}
    for url in urls:
        if not url:
            exists[url] = False
            continue
        # 尝试从 URL 提取文件路径
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        fname = params.get("filename", [None])[0]
        if not fname:
            fname = url.rsplit("/", 1)[-1].split("?")[0]
        if not fname:
            exists[url] = False
            continue
        # 检查文件是否存在
        found = False
        for d in [os.path.join(_BASE_DIR, "data", "generated"), _UPLOAD_DIR]:
            fpath = os.path.join(d, fname)
            if os.path.isfile(fpath):
                found = True
                break
        # 检查 ComfyUI output 目录
        if not found:
            try:
                from services.comfyui_service import COMFYUI_DIR
                if COMFYUI_DIR:
                    fpath = os.path.join(COMFYUI_DIR, "output", fname)
                    if os.path.isfile(fpath):
                        found = True
            except Exception:
                pass
        exists[url] = found
    return {"exists": exists}


@router.post("/canvas-assets/download")
async def download_canvas_assets(request: Request):
    """下载资产为 ZIP 包"""
    body = await request.json()
    urls = body.get("urls", [])
    items = body.get("items", [])
    filename = body.get("filename", "assets.zip")

    from fastapi.responses import StreamingResponse
    from services.providers.provider_utils import output_file_from_url
    import io
    import zipfile

    def _resolve_local_path(url: str) -> Optional[str]:
        """将 URL 解析为本地文件路径，找不到返回 None"""
        if not url or not isinstance(url, str):
            return None
        # 本地 /output/、/assets/、/static/ 路径
        if url.startswith(("/output/", "/assets/", "/static/")):
            p = output_file_from_url(url)
            if p and os.path.isfile(p):
                return p
            return None
        # 远程 URL 不下载（避免在 ZIP 接口里产生长耗时操作）
        return None

    def _safe_entry_name(name: str, idx: int, fallback_ext: str = ".png") -> str:
        """生成安全的 ZIP 条目名，防止路径遍历"""
        base = os.path.basename(name or "") or f"file_{idx}"
        # 去掉任何目录成分
        base = base.replace("\\", "/").split("/")[-1]
        if not base:
            base = f"file_{idx}{fallback_ext}"
        if "." not in base:
            base += fallback_ext
        return f"assets/{idx:03d}_{base}"

    zip_buffer = io.BytesIO()
    missing: list = []
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        idx = 0
        # 处理 items（带名称）
        for item in items:
            if not isinstance(item, dict):
                continue
            item_url = item.get("url", "")
            item_name = item.get("name", "file")
            local_path = _resolve_local_path(item_url)
            if local_path:
                with open(local_path, "rb") as f:
                    zf.writestr(_safe_entry_name(item_name, idx), f.read())
            else:
                missing.append(item_url)
            idx += 1
        # 处理 urls（纯 URL 列表）
        for url in urls:
            if not url:
                continue
            local_path = _resolve_local_path(url)
            if local_path:
                with open(local_path, "rb") as f:
                    zf.writestr(_safe_entry_name(os.path.basename(url), idx), f.read())
            else:
                missing.append(url)
            idx += 1

    zip_buffer.seek(0)
    # 在响应头中返回缺失数量，便于前端提示
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if missing:
        headers["X-Missing-Count"] = str(len(missing))
    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers=headers,
    )


# ============================================================
# 提示词库批量删除
# ============================================================

@router.post("/prompt-libraries/items/delete")
async def batch_delete_prompt_items(request: Request):
    """批量删除提示词条目"""
    body = await request.json()
    ids = body.get("ids", [])
    for iid in ids:
        _prompt_items.pop(iid, None)
    _save_library_state()
    # 返回 library 数据供前端刷新
    libraries = []
    for plib in _prompt_libraries.values():
        items = [v for v in _prompt_items.values() if v.get("library_id") == plib["id"]]
        libraries.append({**plib, "items": items})
    return {"success": True, "library": {"libraries": libraries}}


# ============================================================
# ModelScope/在线模型生成
# ============================================================

@router.post("/angle/generate")
async def angle_generate(request: Request):
    """Qwen Edit 图片编辑（通过 ModelScope）"""
    body = await request.json()
    prompt = body.get("prompt", "")
    image = body.get("image", "")
    logger.info(f"[InfiniteCanvas] angle/generate | prompt={prompt[:50]}...")

    try:
        from services.providers.modelscope_provider import ModelScopeProvider
        provider = ModelScopeProvider()
        if provider.is_available():
            result = await provider.generate_image(
                prompt=prompt,
                reference_images=[{"url": image, "type": "image"}] if image else [],
            )
            return {"images": result.images}
        return {"images": [], "error": "ModelScope 未配置"}
    except Exception as e:
        logger.error(f"[InfiniteCanvas] angle/generate 失败: {e}")
        return {"images": [], "error": str(e)}


@router.post("/ms/generate")
async def ms_generate(request: Request):
    """ModelScope 图片生成"""
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "")
    images = body.get("images", [])
    logger.info(f"[InfiniteCanvas] ms/generate | model={model} | prompt={prompt[:50]}...")

    try:
        from services.providers.modelscope_provider import ModelScopeProvider
        provider = ModelScopeProvider()
        if provider.is_available():
            result = await provider.generate_image(
                prompt=prompt,
                model=model,
                reference_images=[{"url": img, "type": "image"} for img in images] if images else [],
            )
            image_urls = result.images or []
            # D1 修复：ModelScope 生成结果回写资产库
            asset_id = await _register_generated_asset(
                image_urls,
                asset_type="storyboard",
                name=f"MS生成 {model[:20]}",
                prompt=prompt,
                gen_type=f"modelscope:{model}",
            )
            return {"images": image_urls, "asset_id": asset_id}
        return {"images": [], "error": "ModelScope 未配置"}
    except Exception as e:
        logger.error(f"[InfiniteCanvas] ms/generate 失败: {e}")
        return {"images": [], "error": str(e)}


# ============================================================
# 画布工作流导入导出
# ============================================================

@router.post("/canvas-workflows/export")
async def export_canvas_workflow(request: Request):
    """导出画布工作流为 JSON"""
    body = await request.json()
    nodes = body.get("nodes", [])
    connections = body.get("connections", [])
    filename = body.get("filename", "workflow.json")

    from fastapi.responses import StreamingResponse
    import io
    content = json.dumps({"nodes": nodes, "connections": connections}, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/canvas-workflows/export-to-library")
async def export_workflow_to_library(request: Request):
    """导出工作流到资产库"""
    body = await request.json()
    nodes_data = body.get("nodes", [])
    connections_data = body.get("connections", [])
    name = body.get("name", body.get("filename", "workflow"))
    library_id = body.get("library_id", "")
    category_id = body.get("category_id", "")

    # 创建资产条目
    item_id = f"aitem_{uuid.uuid4().hex[:8]}"
    workflow_json = json.dumps({"nodes": nodes_data, "connections": connections_data}, ensure_ascii=False, indent=2)

    # 保存工作流文件
    safe_name = name.replace("..", "").replace("/", "").replace("\\", "")
    if not safe_name.endswith(".json"):
        safe_name += ".json"
    save_path = os.path.join(_UPLOAD_DIR, safe_name)
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        f.write(workflow_json)

    url = f"/api/ai/upload/{safe_name}"
    _asset_items[item_id] = {
        "id": item_id,
        "library_id": library_id,
        "category_id": category_id,
        "name": name,
        "asset_type": "workflow",
        "urls": [url],
        "created_at": time.time(),
    }
    _save_library_state()

    # 返回 library 数据供前端刷新
    libraries = []
    for alib in _asset_libraries.values():
        items = [v for v in _asset_items.values() if v.get("library_id") == alib["id"]]
        libraries.append({**alib, "items": items})

    return {
        "success": True,
        "workflow": {"id": item_id, "name": name},
        "item": _asset_items[item_id],
        "library": {"libraries": libraries},
    }


@router.post("/canvas-workflows/import")
async def import_canvas_workflow(file: UploadFile = File(...)):
    """导入工作流（支持 JSON 和 ZIP 文件）"""
    import io
    import zipfile
    try:
        content = await file.read()
        fname = (file.filename or "").lower()

        # ZIP 文件：尝试提取内部 JSON
        if fname.endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    json_files = [n for n in zf.namelist() if n.endswith(".json")]
                    if json_files:
                        content = zf.read(json_files[0])
                    else:
                        return {"success": True, "nodes": [], "connections": []}
            except Exception:
                return {"success": True, "nodes": [], "connections": []}

        # 解析 JSON
        data = json.loads(content)
        logger.info(f"[CanvasWorkflow] 导入工作流 | file={fname} | keys={list(data.keys())}")

        # 格式 1: 直接 nodes/connections
        nodes = data.get("nodes", []) if isinstance(data.get("nodes"), list) else []
        connections = data.get("connections", []) if isinstance(data.get("connections"), list) else []

        # 格式 2: {workflow: {nodes, connections}}
        if not nodes and isinstance(data.get("workflow"), dict):
            wf = data["workflow"]
            nodes = wf.get("nodes", []) if isinstance(wf.get("nodes"), list) else []
            connections = wf.get("connections", []) if isinstance(wf.get("connections"), list) else []

        # 格式 3: {format: 'infinite-canvas-workflow', nodes, connections} 已兼容（直接读取 data.nodes）

        logger.info(f"[CanvasWorkflow] 解析结果 | nodes={len(nodes)} connections={len(connections)}")
        return {"success": True, "nodes": nodes, "connections": connections}
    except Exception as e:
        logger.warning(f"[CanvasWorkflow] 导入工作流解析失败: {e}")
        return {"success": True, "nodes": [], "connections": []}


# ============================================================
# RunningHub 工作流提交
# ============================================================

@router.post("/runninghub/submit")
@router.post("/runninghub/workflow-submit")
async def runninghub_submit(request: Request):
    """提交 RunningHub 应用/工作流任务"""
    body = await request.json()
    mode = "workflow" if "/workflow-submit" in str(request.url) else "app"
    node_info_list = body.get("nodeInfoList", [])
    use_wallet = body.get("useWallet", False)

    try:
        from services.providers.runninghub_provider import RunningHubProvider
        provider = RunningHubProvider()
        if not provider.is_available():
            return {"success": False, "error": "RunningHub API Key 未配置"}

        headers = provider._headers(use_wallet=use_wallet)
        base_url = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")

        if mode == "workflow":
            workflow_id = body.get("workflowId", "").strip()
            if not workflow_id:
                return {"success": False, "error": "缺少 workflowId"}
            workflow_json = body.get("workflow")
            payload = {"workflowId": workflow_id, "nodeInfoList": node_info_list}
            if workflow_json:
                payload["workflow"] = workflow_json
            endpoint_url = f"{base_url}/task/openapi/workflow/run"
        else:
            webapp_id = body.get("webappId", "").strip()
            instance_type = body.get("instanceType", "")
            if not webapp_id:
                return {"success": False, "error": "缺少 webappId"}
            payload = {"webappId": webapp_id, "nodeInfoList": node_info_list}
            if instance_type:
                payload["instanceType"] = instance_type
            endpoint_url = f"{base_url}/task/openapi/app/run"

        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)) as client:
            resp = await client.post(endpoint_url, headers=headers, json=payload)
            resp.raise_for_status()
            result = resp.json()

        task_id = result.get("taskId") or result.get("data", {}).get("taskId", "")
        if not task_id:
            return {"success": False, "error": f"RunningHub 未返回 taskId: {result}"}

        # 存储任务信息供查询
        _rh_tasks[task_id] = {
            "status": "PENDING",
            "mode": mode,
            "use_wallet": use_wallet,
            "created_at": time.time(),
        }

        return {"success": True, "data": {"taskId": task_id, "status": "pending"}}

    except Exception as e:
        logger.error(f"[InfiniteCanvas] RunningHub submit 失败: {e}")
        return {"success": False, "error": str(e)}


@router.get("/runninghub/query")
async def runninghub_query(taskId: str = ""):
    """查询 RunningHub 任务状态"""
    if not taskId:
        return {"success": False, "error": "缺少 taskId"}

    try:
        from services.providers.runninghub_provider import RunningHubProvider
        provider = RunningHubProvider()
        if not provider.is_available():
            return {"success": False, "error": "RunningHub API Key 未配置"}

        task_info = _rh_tasks.get(taskId, {})
        use_wallet = task_info.get("use_wallet", False)
        headers = provider._headers(use_wallet=use_wallet)
        base_url = os.getenv("RUNNINGHUB_BASE_URL", "https://www.runninghub.cn")

        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0, write=60.0, pool=20.0)) as client:
            query_url = f"{base_url}/task/openapi/query"
            resp = await client.post(query_url, headers=headers, json={"taskId": taskId})
            resp.raise_for_status()
            result = resp.json()

        status = str(result.get("status", "")).upper()
        # 映射状态码：0=排队 1=运行 2=完成 3=成功 4=失败
        status_map = {"0": "PENDING", "1": "RUNNING", "2": "COMPLETED", "3": "SUCCESS", "4": "FAILED"}
        mapped_status = status_map.get(status, status)
        # 统一转为前端小写状态（succeeded/failed/pending/running）
        from services.task_status import to_frontend_status
        frontend_status = to_frontend_status(mapped_status)

        if mapped_status == "SUCCESS":
            # 提取输出 URL
            outputs = result.get("data", {}).get("outputs", [])
            urls = []
            for out in outputs:
                if isinstance(out, dict):
                    for key in ("url", "image_url", "video_url"):
                        url = out.get(key, "")
                        if url and str(url).startswith(("http://", "https://")):
                            # 下载到本地
                            try:
                                from services.providers.provider_utils import save_image_to_output, save_video_to_output
                                is_video = any(ext in str(url).lower() for ext in [".mp4", ".webm", ".mov", ".avi"])
                                if is_video:
                                    local_url = await save_video_to_output(url, prefix="rh_")
                                else:
                                    local_url = await save_image_to_output({"type": "url", "value": url}, prefix="rh_")
                                urls.append(local_url)
                            except Exception as e:
                                logger.warning(f"RunningHub 输出下载失败: {e}")
                                urls.append(url)
            _rh_tasks[taskId] = {"status": "SUCCESS", "urls": urls, "updated_at": time.time()}
            # D1 修复：RunningHub 生成结果也回写资产库
            rh_asset_id = await _register_generated_asset(
                urls,
                asset_type="video" if any(is_video_url(u) for u in urls) else "storyboard",
                name=f"RH生成 {taskId[-6:]}",
                gen_type="runninghub",
            )
            _rh_tasks[taskId]["asset_id"] = rh_asset_id
            return {"success": True, "data": {"status": frontend_status, "urls": urls, "asset_id": rh_asset_id}}

        if mapped_status == "FAILED":
            fail_reason = result.get("failReason") or result.get("data", {}).get("failReason", "任务失败")
            _rh_tasks[taskId] = {"status": "FAILED", "failReason": fail_reason, "updated_at": time.time()}
            return {"success": True, "data": {"status": frontend_status, "failReason": fail_reason}}

        _rh_tasks[taskId] = {"status": mapped_status, "updated_at": time.time()}
        return {"success": True, "data": {"status": frontend_status}}

    except Exception as e:
        logger.error(f"[InfiniteCanvas] RunningHub query 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 资产库 PATCH/DELETE 端点补充
# ============================================================

@router.patch("/asset-library/libraries/{library_id}")
async def update_asset_library(library_id: str, request: Request):
    body = await request.json()
    if library_id in _asset_libraries:
        if "name" in body:
            _asset_libraries[library_id]["name"] = body["name"]
        _save_library_state()
        return {"success": True, "library": _asset_libraries[library_id]}
    return {"success": False}


@router.delete("/asset-library/libraries/{library_id}")
async def delete_asset_library(library_id: str):
    _asset_libraries.pop(library_id, None)
    _save_library_state()
    return {"success": True}


@router.patch("/asset-library/categories/{category_id}")
async def update_asset_category(category_id: str, request: Request):
    body = await request.json()
    if category_id in _asset_categories:
        if "name" in body:
            _asset_categories[category_id]["name"] = body["name"]
        _save_library_state()
        return {"success": True, "category": _asset_categories[category_id]}
    return {"success": False}


@router.delete("/asset-library/categories/{category_id}")
async def delete_asset_category(category_id: str):
    _asset_categories.pop(category_id, None)
    _save_library_state()
    return {"success": True}


# ============================================================
# 提示词库 PATCH/DELETE 端点补充
# ============================================================

@router.patch("/prompt-libraries/categories/{category_id}")
async def update_prompt_category(category_id: str, request: Request):
    body = await request.json()
    if category_id in _prompt_categories:
        if "name" in body:
            _prompt_categories[category_id]["name"] = body["name"]
        _save_library_state()
        return {"success": True, "category": _prompt_categories[category_id]}
    return {"success": False}


@router.delete("/prompt-libraries/categories/{category_id}")
async def delete_prompt_category(category_id: str):
    _prompt_categories.pop(category_id, None)
    _save_library_state()
    return {"success": True}


# ============================================================
# QC 质检报告查询 / 强制发布留痕 端点
# ============================================================

def _read_qc_report(asset_id: str) -> Optional[Dict[str, Any]]:
    """从资产 metadata 或落盘 JSON 读取 QC 报告。

    支持两种查询键：
      - qc_report 资产自身的 asset_id
      - 被质检的视频资产 asset_id（报告文件名含该 id）
    """
    from services.asset_service import get_asset_service
    svc = get_asset_service()
    # 1) 直接命中 qc_report 资产
    asset = svc._assets.get(asset_id)
    if asset is not None:
        meta = getattr(asset, "metadata", None) or {}
        if "qc" in meta:
            return meta.get("qc")
    # 2) 按视频资产 id 在落盘目录找 qc_report_{asset_id}.json
    import os
    qc_dir = os.path.join("data", "generated", "qc")
    cand = os.path.join(qc_dir, f"qc_report_{asset_id}.json")
    if os.path.exists(cand):
        with open(cand, "r", encoding="utf-8") as f:
            report = json.load(f)
        # 合并独立 gate 文件：强制发布留痕写在 qc_gate_{id}.json（不被 qc_report 覆盖冲掉）
        gate_path = os.path.join(qc_dir, f"qc_gate_{asset_id}.json")
        if os.path.exists(gate_path):
            try:
                with open(gate_path, "r", encoding="utf-8") as gf:
                    report["gate"] = json.load(gf)
            except Exception:
                pass
        return report
    # 3) 在资产库里反查 name 匹配的 qc_report
    for a in svc._assets.values():
        if getattr(a, "asset_type", "") == "qc_report" and getattr(a, "name", "").endswith(f"qc_report_{asset_id}.json"):
            meta = getattr(a, "metadata", None) or {}
            if "qc" in meta:
                return meta.get("qc")
    return None


@router.get("/qc/report/{asset_id}")
async def get_qc_report(asset_id: str):
    """查询某资产（视频或 qc 报告）的质量质检报告。"""
    report = _read_qc_report(asset_id)
    if report is None:
        return {"success": False, "error": "未找到该资产的质检报告", "asset_id": asset_id}
    return {"success": True, "asset_id": asset_id, "report": report}


@router.post("/qc/force-publish")
async def qc_force_publish(request: Request):
    """质检未达标时强制发布，留痕到门禁结果。

    请求体：{"asset_id": "<视频资产 id>", "operator": "导演名(可选)", "reason": "强制发布原因"}
    返回更新后的 gate 结果。
    """
    import os
    body = await request.json()
    asset_id = body.get("asset_id", "")
    if not asset_id:
        return {"success": False, "error": "缺少 asset_id"}
    operator = body.get("operator", "未知操作者")
    reason = body.get("reason", "")

    report = _read_qc_report(asset_id)
    if report is None:
        return {"success": False, "error": "未找到该资产的质检报告", "asset_id": asset_id}

    gate = report.get("gate", {})
    gate["forced_publish"] = True
    gate["forced_by"] = operator
    gate["forced_reason"] = reason
    gate["forced_at"] = datetime.now().isoformat()
    gate["note"] = f"已强制发布（操作者:{operator}）。原因:{reason}"

    # 回写落盘 gate 文件
    qc_dir = os.path.join("data", "generated", "qc")
    gate_path = os.path.join(qc_dir, f"qc_gate_{asset_id}.json")
    try:
        with open(gate_path, "w", encoding="utf-8") as f:
            json.dump(gate, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # 同步回写 qc_report_{id}.json 内的 gate 副本（否则下次从文件读时强制标记丢失）
    report_path = os.path.join(qc_dir, f"qc_report_{asset_id}.json")
    try:
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                rep = json.load(f)
            rep["gate"] = gate
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(rep, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    # 回写报告元数据（若资产仍在内存）
    from services.asset_service import get_asset_service
    svc = get_asset_service()
    asset = svc._assets.get(asset_id)
    if asset is not None:
        meta = getattr(asset, "metadata", {}) or {}
        if "qc" in meta:
            meta["qc"]["gate"] = gate
    return {"success": True, "asset_id": asset_id, "gate": gate}


@router.get("/qc/history/{asset_id}")
async def get_qc_history(asset_id: str):
    """查询某视频资产的历次质检记录（#8 趋势对比）。

    返回：按时间升序的历史条目列表（总分/维度/拦截状态/快照文件名），
    以及基于首末两次的对比结论（趋势、维度变化、是否从拦截→通过等）。
    """
    import os
    qc_dir = os.path.join("data", "generated", "qc")
    history_path = os.path.join(qc_dir, f"qc_history_{asset_id}.json")
    if not os.path.exists(history_path):
        return {"success": False, "error": "该资产暂无质检历史", "asset_id": asset_id}
    try:
        with open(history_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception as e:
        return {"success": False, "error": f"历史文件解析失败: {e}", "asset_id": asset_id}
    if not isinstance(history, list) or not history:
        return {"success": False, "error": "该资产暂无质检历史", "asset_id": asset_id}

    comparison = _build_qc_comparison(history)
    return {
        "success": True,
        "asset_id": asset_id,
        "count": len(history),
        "history": history,
        "comparison": comparison,
    }


def _build_qc_comparison(history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """基于首末两次 QC 生成对比结论。"""
    first, last = history[0], history[-1]
    dims = ["composition", "lip_sync", "rhythm", "compliance", "consistency"]

    def _dims_of(entry: Dict[str, Any]) -> Dict[str, Any]:
        d = entry.get("dimensions") or {}
        if isinstance(d, dict):
            return d
        # 容忍历史快照里 dimensions 被存成 list 的异常格式
        if isinstance(d, list) and d and isinstance(d[-1], dict):
            return d[-1]
        return {}

    diff = {}
    fd, ld = _dims_of(first), _dims_of(last)
    for d in dims:
        fv, lv = fd.get(d), ld.get(d)
        if isinstance(fv, (int, float)) and isinstance(lv, (int, float)):
            diff[d] = round(lv - fv, 1)
    return {
        "first_ts": first.get("ts"),
        "last_ts": last.get("ts"),
        "score_delta": round(float(last.get("total_score", 0)) - float(first.get("total_score", 0)), 1),
        "dimension_delta": diff,
        "blocked_from": bool(first.get("blocked")),
        "blocked_to": bool(last.get("blocked")),
        "passed_from": bool(first.get("passed")),
        "passed_to": bool(last.get("passed")),
        "improved": (not first.get("passed")) and bool(last.get("passed")),
        "regressed": bool(first.get("passed")) and (not last.get("passed")),
    }
