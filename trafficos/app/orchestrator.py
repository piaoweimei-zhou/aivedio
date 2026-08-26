# -*- coding: utf-8 -*-
"""热点自动编排：从 topics 选题 → 生成脚本 → 提交 director contract 生产。

打通"热点 → 内容"断点（P0 闭环经验沉淀 §5）：
- 选题：topics 集合按 dimension/monetizer 抽取（含敏感词过滤）
- 脚本：话题 → 5s 单段 narration + visual_hint（模板化，5s 台词需短）
- 生产：UTF-8 编码 POST director /contract/produce（规避 PowerShell 编码坑）
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 敏感政治话题黑名单（自动选题必须规避）
SENSITIVE_KEYWORDS = [
    "总书记", "习近平", "国家主席", "国务院", "政府", "党", "政治",
    "领导人", "外交", "台湾", "台独", "港独", "疆独", "藏独", "抗议",
    "游行", "示威", "敏感", "舆情",
]

CONTRACT_URL = "http://127.0.0.1:8000/contract/produce"
CONTRACT_API_KEY = "dev-contract-key-not-for-prod"  # P0 dev key；生产需从环境注入


def _is_sensitive(title: str) -> bool:
    return any(k in title for k in SENSITIVE_KEYWORDS)


def pick_topic(
    dimension: str = "soft_ad",
    monetizer: Optional[str] = None,
    exclude_content_ids: Optional[List[str]] = None,
    limit: int = 20,
) -> Optional[Dict[str, Any]]:
    """从 topics 集合挑一条可用话题（过滤敏感 + 已生产）。"""
    from app.storage import get_collection

    exclude = set(exclude_content_ids or [])
    col = get_collection("topics")
    for t in col.list():
        title = t.get("title", "")
        if _is_sensitive(title):
            continue
        if t.get("dimension") != dimension:
            continue
        if monetizer and t.get("monetizer") not in (None, monetizer):
            continue
        tid = t.get("_id") or t.get("id") or title
        if tid in exclude:
            continue
        return t
    return None


def build_script_from_topic(
    topic: Dict[str, Any],
    duration_s: float = 5.0,
    platform: str = "douyin",
    use_creative: bool = True,
    segments: int = 1,
) -> Dict[str, Any]:
    """话题 → 脚本。优先 CreativeOS 生成（多段/LLM 文案），失败兜底模板。

    CreativeOS 不可达或 LLM 失败时，回落到原 5s 单段模板（链路不中断）。
    """
    title = (topic.get("title") or "").strip()
    if use_creative:
        try:
            from .creative_adapter import generate_spec, spec_to_script
            spec = generate_spec(
                title,
                platform=platform,
                duration_s=duration_s,
                segments=segments,
            )
            if spec:
                script = spec_to_script(spec, duration_s)
                if script.get("acts"):
                    logger.info("[TrafficOS] CreativeOS 生成脚本: %s (%d 段)",
                                title, len(script["acts"]))
                    return script
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TrafficOS] CreativeOS 适配失败，兜底模板: %s", exc)

    # —— 原模板兜底：5s 单段 ——
    # 去标点与空格、去"神器/工具/数字+秒搞定"等后缀，提炼核心动作词
    core = re.sub(r"[，。！？、,.!?\s]", "", title)
    suffixes = (
        "神器|工具|软件|App|APP|方法|技巧|教程|攻略|全搞定|一个工具全搞定|"
        "秒搞定|秒解决|直接解决|\\d+秒搞定|\\d+秒解决"
    )
    core = re.sub(rf"({suffixes})$", "", core)
    core = re.sub(r"\d+$", "", core).strip(" ，。")
    if len(core) > 12:
        core = core[:12]

    visual_hint = (
        "手机/电脑屏幕清晰展示操作过程，简洁高效，画面干净"
    )
    return {
        "type": "video_act",
        "acts": [{
            "narration": f"{core}，快看这里",
            "visual_hint": visual_hint,
            "duration_s": duration_s,
        }],
    }


def submit_contract(
    content_id: str,
    script: Dict[str, Any],
    platform: str = "douyin",
    dimension: str = "soft_ad",
    monetizer: str = "tool",
    account_id: str = "tool_1",
    topic_title: str = "",
    resolution: str = "720p",
) -> Dict[str, Any]:
    """UTF-8 编码提交 director contract。返回 task_id / 错误。"""
    spec = {
        "content_id": content_id,
        "dimension": dimension,
        "monetizer": monetizer,
        "account_id": account_id,
        "script": script,
        "packaging": {"title": topic_title, "hook": "以前要半小时，现在三秒"},
        "params": {
            "provider_id": "minimax_h3",
            "platform": platform,
            "resolution": resolution,
            "tts_enabled": True,
            "tts_mode": "voice_design",
            "hook_text": f"{topic_title}，亲测可用",
        },
        "auto_start": True,
    }
    body = json.dumps(spec, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CONTRACT_URL,
        data=body,
        headers={
            "X-API-Key": CONTRACT_API_KEY,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=20)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def orchestrate_produce(
    dimension: str = "soft_ad",
    monetizer: str = "tool",
    platform: str = "douyin",
    duration_s: float = 5.0,
    account_id: str = "tool_1",
) -> Dict[str, Any]:
    """一键：选题 → 脚本 → 生产。返回 {topic, content_id, task_id}。"""
    topic = pick_topic(dimension=dimension, monetizer=monetizer)
    if not topic:
        return {"error": f"无可用话题（dimension={dimension}, monetizer={monetizer}）"}

    title = topic.get("title", "")
    content_id = f"hot_{platform}_{uuid.uuid4().hex[:8]}"
    script = build_script_from_topic(topic, duration_s=duration_s, platform=platform)
    result = submit_contract(
        content_id=content_id,
        script=script,
        platform=platform,
        dimension=dimension,
        monetizer=monetizer,
        account_id=account_id,
        topic_title=title,
    )
    if "error" in result:
        return {"error": result["error"], "topic": title}
    return {
        "topic": title,
        "content_id": content_id,
        "task_id": result.get("task_id"),
        "status": result.get("status"),
    }
