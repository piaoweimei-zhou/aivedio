"""
剧本资产消费工具

提供 script 资产 JSON 的统一读取能力，供下游 stage（concept/storyboard/video/edit）
识别并消费 script 输入。

契约：
- script 资产的 urls[0] 或 metadata.script_url 指向 JSON 文件
- JSON 结构：
    {
      "title": str,
      "video_type": str,
      "hook": str,
      "characters": [{"name": str, "desc": str, "role": str}],
      "covers": [{"title": str, "subtitle": str, "layout": str}],
      "acts": [{
        "act": int,
        "scene": str,
        "narration": str,
        "dialogues": [{"character": str, "line": str}],
        "tts_texts": [str],
        "duration_seconds": float
      }],
      "raw_text": str,
      "meta": {...}
    }
- 下游 stage 批量生成时，返回第一个资产，并在 metadata.sibling_asset_ids 记录其余资产 ID
- edit stage 识别 sibling_asset_ids 并拼接所有视频
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from services.asset_service import AssetRef

logger = logging.getLogger(__name__)


def find_script_asset(input_assets: List[AssetRef]) -> Optional[AssetRef]:
    """从输入资产列表中查找 script 类型资产"""
    for a in input_assets:
        if a.asset_type == "script":
            return a
    return None


async def load_script_json(script_asset: AssetRef) -> Optional[Dict[str, Any]]:
    """读取 script 资产的 JSON 内容

    优先读本地文件，其次 HTTP
    """
    if not script_asset:
        return None

    script_url = ""
    if script_asset.urls:
        script_url = script_asset.urls[0]
    if not script_url:
        script_url = script_asset.metadata.get("script_url", "")
    if not script_url:
        return None

    # 本地文件
    local = _url_to_local_path(script_url)
    if local and os.path.exists(local):
        try:
            with open(local, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"[script_utils] 读取本地剧本失败 | path={local} | err={e}")

    # HTTP
    if script_url.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(script_url)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"[script_utils] 读取远程剧本失败 | url={script_url} | err={e}")

    return None


def extract_characters(script: Dict[str, Any]) -> List[Dict[str, str]]:
    """提取剧本中的角色列表

    Returns:
        [{"name": str, "desc": str, "role": str}]
    """
    if not script:
        return []
    chars = script.get("characters") or []
    result = []
    for c in chars:
        name = c.get("name", "").strip() if isinstance(c, dict) else str(c).strip()
        if not name:
            continue
        desc = c.get("desc", "").strip() if isinstance(c, dict) else ""
        role = c.get("role", "").strip() if isinstance(c, dict) else ""
        if not desc:
            desc = name
        result.append({"name": name, "desc": desc, "role": role})
    return result


def extract_acts(script: Dict[str, Any]) -> List[Dict[str, Any]]:
    """提取剧本中的幕列表

    Returns:
        [{"act": int, "scene": str, "narration": str,
           "dialogues": [...], "tts_texts": [str], "duration_seconds": float}]
    """
    if not script:
        return []
    return list(script.get("acts") or [])


def extract_scenes(script: Dict[str, Any]) -> List[Dict[str, str]]:
    """提取剧本中的场景描述列表（从 acts 的 scene 字段）

    Returns:
        [{"name": str, "desc": str, "act": int}]
    """
    acts = extract_acts(script)
    result = []
    for act in acts:
        scene_desc = (act.get("scene") or "").strip()
        if not scene_desc:
            continue
        result.append(
            {
                "name": f"第{act.get('act', len(result)+1)}幕场景",
                "desc": scene_desc,
                "act": act.get("act", len(result) + 1),
            }
        )
    return result


def extract_tts_texts(script: Dict[str, Any]) -> List[str]:
    """提取剧本中所有幕的 TTS 配音文本（按幕顺序拼接）"""
    acts = extract_acts(script)
    result = []
    for act in acts:
        for t in act.get("tts_texts") or []:
            if t and t.strip():
                result.append(t.strip())
    return result


def extract_act_durations(script: Dict[str, Any]) -> List[float]:
    """提取每幕时长（秒）"""
    acts = extract_acts(script)
    return [float(act.get("duration_seconds", 5.0)) for act in acts]


# ============================================================
# 内部工具
# ============================================================


def _url_to_local_path(url: str) -> Optional[str]:
    """把 script URL 转换为本地文件路径（如果适用）"""
    try:
        from services.providers.provider_utils import output_file_from_url

        return output_file_from_url(url)
    except Exception:
        return None


__all__ = [
    "find_script_asset",
    "load_script_json",
    "extract_characters",
    "extract_acts",
    "extract_scenes",
    "extract_tts_texts",
    "extract_act_durations",
]
