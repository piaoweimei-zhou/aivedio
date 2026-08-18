"""
导演工作台 — 供应商 API

提供供应商发现和能力查询接口
"""

import logging
from typing import Optional, Dict
from fastapi import APIRouter, Query

from services.provider_service import get_provider_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/director/providers", tags=["导演工作台-供应商"])


@router.get("")
async def list_providers(capability: Optional[str] = Query(None)):
    """列出供应商（可按能力筛选：image/video/text）"""
    svc = get_provider_service()
    return {"providers": svc.available_providers(capability=capability or "")}


@router.get("/{provider_id}")
async def get_provider(provider_id: str):
    """获取供应商详情"""
    svc = get_provider_service()
    provider = svc.get_provider(provider_id)
    if not provider:
        return {"error": f"供应商 {provider_id} 不存在"}
    return provider.info()


@router.get("/health/all")
async def health_check():
    """所有 Provider 健康检查

    返回每个 provider 的可用性状态，用于批量任务启动前预检。
    """
    svc = get_provider_service()
    return {"providers": svc.health_check()}


# ============================================================
# Provider 配置向导
# ============================================================

# Provider 配置元数据（前端向导用）
PROVIDER_CONFIG_META = [
    {
        "provider_id": "comfyui",
        "name": "ComfyUI（本地）",
        "description": "本地部署的 ComfyUI，支持图片生成、精修、放大",
        "capabilities": ["image", "refine", "upscale"],
        "required_envs": [
            {"key": "COMFYUI_BASE_URL", "label": "ComfyUI 服务地址", "default": "http://127.0.0.1:8188", "required": True},
            {"key": "COMFYUI_API_KEY", "label": "API Key（可选，如启用了鉴权）", "default": "", "required": False},
        ],
        "docs_url": "https://github.com/comfyanonymous/ComfyUI",
    },
    {
        "provider_id": "openai_compat",
        "name": "OpenAI 兼容（图片+文本）",
        "description": "OpenAI 兼容协议的图片生成与文本生成服务。文本能力用于 AI 剧本生成（支持 DeepSeek）",
        "capabilities": ["image", "text"],
        "required_envs": [
            {"key": "OPENAI_API_KEY", "label": "API Key", "default": "", "required": True},
            {"key": "OPENAI_BASE_URL", "label": "Base URL（DeepSeek 填 https://api.deepseek.com）", "default": "https://api.deepseek.com", "required": True},
            {"key": "OPENAI_TEXT_MODEL", "label": "文本模型（AI剧本用，DeepSeek 填 deepseek-chat / deepseek-reasoner）", "default": "deepseek-chat", "required": False},
        ],
        "docs_url": "https://platform.openai.com/docs/api-reference",
    },
    {
        "provider_id": "runninghub",
        "name": "RunningHub（云端）",
        "description": "RunningHub 云端 ComfyUI，支持图片和视频生成",
        "capabilities": ["image", "video"],
        "required_envs": [
            {"key": "RUNNINGHUB_API_KEY", "label": "API Key", "default": "", "required": True},
            {"key": "RUNNINGHUB_WALLET_API_KEY", "label": "钱包 API Key（可选）", "default": "", "required": False},
        ],
        "docs_url": "https://www.runninghub.cn",
    },
    {
        "provider_id": "jimeng",
        "name": "即梦（Jimeng CLI）",
        "description": "字节即梦 CLI 工具，支持图片和视频生成",
        "capabilities": ["image", "video"],
        "required_envs": [
            {"key": "JIMENG_CLI_PATH", "label": "jimeng CLI 路径", "default": "jimeng", "required": True},
            {"key": "JIMENG_POLL_SECONDS", "label": "轮询间隔（秒）", "default": "30", "required": False},
        ],
        "docs_url": "",
    },
    {
        "provider_id": "volcengine",
        "name": "火山引擎（方舟）",
        "description": "火山引擎方舟平台，支持图片/视频/文本生成（豆包、Seedream、Seedance）",
        "capabilities": ["image", "video", "text"],
        "required_envs": [
            {"key": "ARK_API_KEY", "label": "ARK API Key", "default": "", "required": True},
            {"key": "VOLCENGINE_BASE_URL", "label": "Base URL", "default": "https://ark.cn-beijing.volces.com", "required": False},
            {"key": "VOLCENGINE_IMAGE_MODEL", "label": "图像模型（Endpoint ID）", "default": "doubao-seedream-4-5", "required": False},
            {"key": "VOLCENGINE_VIDEO_MODEL", "label": "视频模型（Endpoint ID）", "default": "doubao-seedance-1-5-pro", "required": False},
            {"key": "VOLCENGINE_TEXT_MODEL", "label": "文本模型（Endpoint ID）", "default": "doubao-seed-2-0-pro", "required": False},
        ],
        "docs_url": "https://www.volcengine.com/product/ark",
    },
    {
        "provider_id": "gemini",
        "name": "Gemini",
        "description": "Google Gemini 图片生成",
        "capabilities": ["image"],
        "required_envs": [
            {"key": "GEMINI_API_KEY", "label": "API Key", "default": "", "required": True},
            {"key": "GEMINI_BASE_URL", "label": "Base URL", "default": "https://generativelanguage.googleapis.com", "required": False},
        ],
        "docs_url": "https://ai.google.dev",
    },
    {
        "provider_id": "modelscope",
        "name": "ModelScope",
        "description": "阿里 ModelScope，支持三视图生成",
        "capabilities": ["image"],
        "required_envs": [
            {"key": "MODELSCOPE_API_KEY", "label": "API Key", "default": "", "required": True},
            {"key": "MODELSCOPE_BASE_URL", "label": "Base URL", "default": "https://api-inference.modelscope.cn", "required": False},
        ],
        "docs_url": "https://www.modelscope.cn",
    },
]


@router.get("/config/meta")
async def get_config_meta():
    """获取所有 Provider 的配置元数据（前端向导用）"""
    return {"providers": PROVIDER_CONFIG_META}


# 前端「环境变量配置」卡片可管理的键
CONFIG_KEYS = [
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_TEXT_MODEL",
    "GEMINI_API_KEY", "GEMINI_BASE_URL",
    "ARK_API_KEY", "VOLCENGINE_BASE_URL",
    "VOLCENGINE_IMAGE_MODEL", "VOLCENGINE_VIDEO_MODEL", "VOLCENGINE_TEXT_MODEL",
    "RUNNINGHUB_API_KEY", "RUNNINGHUB_WALLET_API_KEY",
    "MODELSCOPE_API_KEY", "MODELSCOPE_BASE_URL",
    "JIMENG_CLI_PATH", "JIMENG_POLL_SECONDS",
    "COMFYUI_BASE_URL", "COMFYUI_API_KEY",
    "FFMPEG_PATH",
]


@router.get("/config")
async def get_provider_config():
    """读取当前 Provider 配置（服务端 .env / 环境变量）

    密钥不再存前端 localStorage，统一由后端管理。
    """
    import os

    values = {}
    for k in CONFIG_KEYS:
        v = os.environ.get(k, "").strip()
        if v:
            values[k] = v
    return {"config": values}


@router.post("/config/save")
async def save_provider_config(configs: Dict[str, str]):
    """保存 Provider 配置到 .env 文件

    Args:
        configs: {"OPENAI_API_KEY": "sk-xxx", "COMFYUI_BASE_URL": "http://..."}
    """
    import os
    from pathlib import Path

    env_file = Path(__file__).parent.parent / ".env"
    # 读取现有 .env
    existing = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()

    # 合并新配置
    existing.update(configs)

    # 写回 .env
    lines = ["# Provider 配置（由配置向导生成）", ""]
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    env_file.write_text("\n".join(lines), encoding="utf-8")

    # 更新当前进程的环境变量（立即生效）
    for k, v in configs.items():
        os.environ[k] = v

    logger.info(f"[ProviderConfig] 保存 {len(configs)} 项配置到 .env")
    return {"success": True, "message": f"已保存 {len(configs)} 项配置", "env_file": str(env_file)}


@router.post("/config/test")
async def test_provider_config(provider_id: str, configs: Dict[str, str]):
    """测试 Provider 配置（不保存，临时生效）

    Args:
        provider_id: 要测试的 provider ID
        configs: 临时配置
    """
    import os
    from services.provider_service import get_provider_service

    # 临时设置环境变量
    old_values = {}
    for k, v in configs.items():
        old_values[k] = os.environ.get(k)
        os.environ[k] = v

    try:
        svc = get_provider_service()
        provider = svc.get_provider(provider_id)
        if not provider:
            return {"success": False, "message": f"Provider {provider_id} 不存在"}

        available = provider.is_available()
        return {
            "success": available,
            "message": "配置有效，Provider 可用" if available else "配置无效或 Provider 不可用",
            "provider_id": provider_id,
            "available": available,
        }
    finally:
        # 恢复环境变量
        for k, old in old_values.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
