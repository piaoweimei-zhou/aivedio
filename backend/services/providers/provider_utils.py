"""
供应商共享工具函数

从 Infinite-Canvas main.py 提取的通用辅助函数，
供各供应商 Provider 复用。
"""

import base64
import io
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from services.paths import GENERATED_DIR, OUTPUT_DIR, UPLOADS_DIR

logger = logging.getLogger(__name__)

# ============================================================
# API Key 管理
# ============================================================

def provider_key_env(provider_id: str) -> str:
    """供应商 API Key 的环境变量名"""
    mapping = {
        "comfyui": "COMFYUI_API_KEY",
        "modelscope": "MODELSCOPE_API_KEY",
        "runninghub": "RUNNINGHUB_API_KEY",
        "volcengine": "ARK_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "openai_compat": "OPENAI_API_KEY",
    }
    if provider_id in mapping:
        return mapping[provider_id]
    return f"API_PROVIDER_{re.sub(r'[^A-Za-z0-9]', '_', provider_id).upper()}_KEY"


def get_api_key(provider_id: str) -> str:
    """从环境变量读取供应商 API Key"""
    return os.getenv(provider_key_env(provider_id), "").strip()


def bearer_auth(api_key: str) -> str:
    """构造 Bearer 认证头值"""
    token = api_key.removeprefix("Bearer ").strip()
    return f"Bearer {token}" if token else ""


# ============================================================
# 尺寸解析
# ============================================================

def parse_size(size: str) -> Tuple[int, int]:
    """解析尺寸字符串 '1024x1024' → (1024, 1024)"""
    match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", str(size or ""))
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


# ============================================================
# 图片保存
# ============================================================

# OUTPUT_DIR 由 services.paths 提供（T7 收敛）


def output_path_for(filename: str, category: str = "output") -> str:
    """获取输出文件的绝对路径"""
    directory = os.path.join(OUTPUT_DIR, category)
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def output_url_for(filename: str, category: str = "output") -> str:
    """获取输出文件的 URL 路径"""
    return f"/output/{category}/{filename}"


async def save_image_to_output(
    image_data: Dict[str, Any],
    prefix: str = "online_",
    category: str = "output",
) -> str:
    """
    保存图片到输出目录

    Args:
        image_data: {"type": "b64"/"url", "value": str, "mime_type": str}
    Returns:
        本地 URL 路径
    """
    filename = f"{prefix}{uuid.uuid4().hex[:10]}.png"
    path = output_path_for(filename, category)

    if image_data.get("type") == "b64":
        mime = str(image_data.get("mime_type", "")).lower()
        if "jpeg" in mime or "jpg" in mime:
            filename = filename[:-4] + ".jpg"
            path = output_path_for(filename, category)
        elif "webp" in mime:
            filename = filename[:-4] + ".webp"
            path = output_path_for(filename, category)
        with open(path, "wb") as f:
            f.write(base64.b64decode(image_data["value"]))
        return output_url_for(filename, category)

    value = image_data.get("value", "")
    # 已经是本地路径
    if value.startswith("/output/") or value.startswith("/assets/"):
        return value

    # 下载远程图片
    try:
        timeout = httpx.Timeout(connect=20.0, read=300.0, write=60.0, pool=20.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(value)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            if "jpeg" in content_type or "jpg" in content_type:
                filename = filename[:-4] + ".jpg"
                path = output_path_for(filename, category)
            elif "webp" in content_type:
                filename = filename[:-4] + ".webp"
                path = output_path_for(filename, category)
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        logger.warning(f"保存远程图片失败: {e}")
        return value


async def save_video_to_output(
    url: str,
    prefix: str = "video_",
    category: str = "output",
) -> str:
    """保存远程视频到输出目录"""
    if not url:
        return ""
    if url.startswith("/output/") or url.startswith("/assets/"):
        return url

    filename = f"{prefix}{uuid.uuid4().hex[:10]}.mp4"
    path = output_path_for(filename, category)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=20.0)) as client:
            response = await client.get(url)
            response.raise_for_status()
            with open(path, "wb") as f:
                f.write(response.content)
            return output_url_for(filename, category)
    except Exception as e:
        logger.warning(f"保存远程视频失败: {e}")
        return url


# ============================================================
# 参考图处理
# ============================================================

def reference_to_data_url(ref: Dict[str, Any], max_size: int = 1536) -> str:
    """
    将参考图转为 data URL (base64)

    Args:
        ref: {"url": str, ...}
        max_size: 最长边像素限制
    """
    url = ref.get("url", "")
    # 已经是 data URL
    if url.startswith("data:"):
        return url
    # 远程 URL 直接返回
    if url.startswith("http://") or url.startswith("https://"):
        return url
    # 本地路径
    local_path = output_file_from_url(url)
    if not local_path or not os.path.exists(local_path):
        return url

    try:
        from PIL import Image
        with Image.open(local_path) as img:
            img.load()
            w, h = img.size
            if max_size and max(w, h) > max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            fmt = "PNG" if img.mode == "RGBA" else "JPEG"
            img.save(buf, format=fmt, quality=88 if fmt == "JPEG" else None)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            mime = "image/png" if fmt == "PNG" else "image/jpeg"
            return f"data:{mime};base64,{encoded}"
    except Exception as e:
        logger.warning(f"参考图转换失败: {e}")

    # fallback: 直接读文件
    try:
        with open(local_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        ext = os.path.splitext(local_path)[1].lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/png")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return url


def output_file_from_url(url: str) -> Optional[str]:
    """从 URL 路径获取本地文件绝对路径

    支持 URL 格式：
        /output/{category}/{filename}        → OUTPUT_DIR/{category}/{filename}
        /api/comfyui/image?filename=xxx      → ComfyUI output 目录/xxx（需运行时解析）
        /static/director/uploads/xxx         → 上传目录/xxx
        /data/generated/xxx                  → GENERATED_DIR/xxx
        /data/uploads/xxx                    → 上传目录/xxx
    """
    text = str(url or "").strip()
    if not text:
        return None
    # 去掉查询参数
    clean = text.split("?", 1)[0].split("#", 1)[0]

    # 1. /output/{category}/{filename}
    if clean.startswith("/output/"):
        rel = clean[len("/output/"):]
        return os.path.join(OUTPUT_DIR, rel)
    if clean.startswith("/assets/output/"):
        rel = clean[len("/assets/output/"):]
        return os.path.join(OUTPUT_DIR, "output", rel)

    # 2. /api/comfyui/image?filename=xxx — 解析 filename + subfolder
    #    到持久化目录 GENERATED_DIR（含 subfolder 子目录）与 ComfyUI output 目录查找
    if clean == "/api/comfyui/image":
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(str(url))
        params = parse_qs(parsed.query)
        filename = (params.get("filename", [""])[0] or "").strip()
        subfolder = (params.get("subfolder", [""])[0] or "").strip("/")
        if not filename:
            return None
        fname = os.path.basename(filename)
        _gen_dir = GENERATED_DIR
        # 1) 持久化目录（含 subfolder 结构）
        if subfolder:
            cand = os.path.join(_gen_dir, subfolder, fname)
            if os.path.isfile(cand):
                return cand
        # 2) 持久化目录扁平
        cand = os.path.join(_gen_dir, fname)
        if os.path.isfile(cand):
            return cand
        # 3) 递归搜索持久化目录
        if os.path.isdir(_gen_dir):
            for _root, _dirs, _files in os.walk(_gen_dir):
                if fname in _files:
                    return os.path.join(_root, fname)
        # 4) ComfyUI output 目录（含 subfolder）
        try:
            from services.comfyui.config import COMFYUI_OUTPUT_DIR
        except Exception:
            COMFYUI_OUTPUT_DIR = ""
        if COMFYUI_OUTPUT_DIR:
            if subfolder:
                cand = os.path.join(COMFYUI_OUTPUT_DIR, subfolder, fname)
                if os.path.isfile(cand):
                    return cand
            cand = os.path.join(COMFYUI_OUTPUT_DIR, fname)
            if os.path.isfile(cand):
                return cand
        return None

    # 3. /static/director/uploads/xxx
    if clean.startswith("/static/director/uploads/"):
        rel = clean[len("/static/director/uploads/"):]
        _upload_dir = UPLOADS_DIR
        return os.path.join(_upload_dir, rel)

    # 4. /data/generated/xxx
    if clean.startswith("/data/generated/"):
        rel = clean[len("/data/generated/"):]
        _gen_dir = GENERATED_DIR
        return os.path.join(_gen_dir, rel)

    # 5. /data/uploads/xxx
    if clean.startswith("/data/uploads/"):
        rel = clean[len("/data/uploads/"):]
        _upload_dir = UPLOADS_DIR
        return os.path.join(_upload_dir, rel)
    return None


# ============================================================
# 响应解析
# ============================================================

# OpenAI 响应中常见的图片输出键名
IMAGE_OUTPUT_KEY_HINTS = [
    "url", "image_url", "imageUrl", "output_url", "outputUrl",
    "image", "images", "output", "result", "data",
]
IMAGE_BASE64_KEY_HINTS = ["b64_json", "b64", "base64", "image_data"]
IMAGE_CONTAINER_KEY_HINTS = ["data", "output", "results", "images", "items"]


def extract_image_from_response(data: Any) -> Dict[str, Any]:
    """
    从供应商响应中提取图片

    Returns:
        {"type": "url"/"b64", "value": str, "mime_type": str}
    """
    # Gemini 格式
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            content = (candidate.get("content") or {}) if isinstance(candidate, dict) else {}
            parts = content.get("parts", []) if isinstance(content, dict) else []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if isinstance(inline, dict) and inline.get("data"):
                    return {
                        "type": "b64",
                        "value": inline["data"],
                        "mime_type": inline.get("mimeType") or inline.get("mime_type") or "image/png",
                    }

    # OpenAI 标准格式
    if isinstance(data, dict):
        # /v1/images/generations 标准格式
        d = data.get("data")
        if isinstance(d, list) and d:
            item = d[0]
            if isinstance(item, dict):
                if item.get("url"):
                    return {"type": "url", "value": item["url"]}
                if item.get("b64_json"):
                    return {"type": "b64", "value": item["b64_json"], "mime_type": "image/png"}

        # 递归搜索
        result = _extract_image_recursive(data, 0)
        if result:
            return result

    return {"type": "url", "value": ""}


def _extract_image_recursive(value: Any, depth: int) -> Optional[Dict[str, Any]]:
    """递归搜索响应中的图片"""
    if depth > 8 or value is None:
        return None
    if isinstance(value, str):
        if value.startswith(("http://", "https://", "/output/", "/assets/")):
            return {"type": "url", "value": value}
        return None
    if isinstance(value, list):
        for item in value:
            found = _extract_image_recursive(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(value, dict):
        return None

    for key in IMAGE_BASE64_KEY_HINTS:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return {"type": "b64", "value": item.strip(), "mime_type": value.get("mime_type") or "image/png"}
    for key in IMAGE_OUTPUT_KEY_HINTS:
        item = value.get(key)
        if isinstance(item, str) and item.strip() and (item.startswith(("http://", "https://", "/output/", "/assets/"))):
            return {"type": "url", "value": item}
        found = _extract_image_recursive(item, depth + 1)
        if found:
            return found
    for key in IMAGE_CONTAINER_KEY_HINTS:
        found = _extract_image_recursive(value.get(key), depth + 1)
        if found:
            return found
    return None
