# -*- coding: utf-8 -*-
"""TrafficOS ↔ CreativeOS 适配层（M4）。

CreativeOS（编剧/导演）生成 Content Spec → 转 TrafficOS 可提交的 script。
失败返回 None → 调用方兜底原模板（无缝降级，保持现有链路可用）。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

CREATIVEOS_URL = os.getenv("CREATIVEOS_URL", "http://127.0.0.1:8002")


def generate_spec(
    title: str,
    platform: str = "douyin",
    dimension: str = "soft_ad",
    monetizer: str = "tool",
    duration_s: float = 5.0,
    segments: int = 1,
    use_llm: bool = True,
) -> Optional[Dict[str, Any]]:
    """调 CreativeOS /generate 生成 Content Spec。失败返回 None（调用方兜底）。"""
    body = {
        "topic": title,
        "platform": platform,
        "dimension": dimension,
        "monetizer": monetizer,
        "segments": segments,
        "duration_per_seg": duration_s,
        "use_llm": use_llm,
    }
    req = urllib.request.Request(
        f"{CREATIVEOS_URL}/api/creative/generate",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[CreativeOS adapter] 调用失败，兜底模板: %s", exc)
        return None


def spec_to_script(spec: Dict[str, Any], duration_s: float) -> Dict[str, Any]:
    """CreativeOS Content Spec → TrafficOS 可提交 script（video_script_mixin）。

    与 director contract 兼容（acts: narration/emotion/duration/visual）。
    """
    script = spec.get("script", {}) or {}
    acts = []
    for a in script.get("acts", []) or []:
        acts.append({
            "narration": a.get("narration", ""),
            "emotion": a.get("emotion", ""),
            "duration": a.get("duration", duration_s),
            "visual": a.get("visual", ""),
        })
    return {
        "type": "video_script_mixin",
        "hook": script.get("hook", ""),
        "acts": acts,
        "cta": script.get("cta", ""),
    }
