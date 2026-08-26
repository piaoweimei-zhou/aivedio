"""需求信号 API（ⓢ 工具传感器，B3+B8）：工具上报（脱敏聚合）+ 查询 + top 关键词 + 选题建议"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from app.models import Signal, ToolEvent
from app.scoring import compute_score, suggest_dimension_monetizer
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/signals", tags=["流量侧-需求信号"])

# 注：工具端上报时应为脱敏聚合数据（领域/关键词热度），不采集个人信息。
# 生产环境应给本组接口加独立鉴权（TRAFFICOS_SIGNAL_API_KEY）。

# 动作 → 信号热度加权（download/save 强需求，analyze 弱）
_ACTION_HEAT = {"download": 1.0, "search": 1.0, "save": 1.2, "analyze": 0.8}

# 从标题里剔除的杂质词（服务端 keyword 粗提取用）
_NOISE = re.compile(
    r"(去水印|无水印|下载|免费|视频|高清|在线|批量|工具|神器|教程|合集|资源|怎么|如何|是什么)",
)


def _extract_keyword(title: str, fallback: str = "") -> str:
    """从标题粗提取关键词：去杂质词后取前 8 字符。"""
    cleaned = _NOISE.sub("", title or "").strip(" 的：:，,。！？")
    if cleaned:
        return cleaned[:8]
    return fallback or "general"


@router.post("", response_model=Signal)
async def report_signal(sig: Signal) -> Signal:
    """工具上报需求信号（去水印等工具，脱敏聚合）。"""
    return get_collection("signals").insert(sig)


@router.post("/batch", response_model=Dict[str, int])
async def report_signal_batch(sigs: List[Signal]) -> Dict[str, int]:
    """批量上报（工具端定时聚合一并上报）。"""
    col = get_collection("signals")
    for s in sigs:
        col.insert(s)
    return {"inserted": len(sigs)}


@router.post("/tool-event", response_model=Signal)
async def report_tool_event(evt: ToolEvent) -> Signal:
    """工具行为事件 → 自动转需求信号（B8：工具即传感器）。

    服务端自动：keyword 粗提取 + field 兜底 + 按 action 加权 heat。
    """
    keyword = _extract_keyword(evt.keyword or evt.title)
    heat = _ACTION_HEAT.get(evt.action or "", 1.0)
    sig = Signal(
        field=evt.field or "general",
        keyword=keyword,
        heat=heat,
        source=f"tool:{evt.tool_name or 'unknown'}",
    ).touch()
    return get_collection("signals").insert(sig)


@router.get("", response_model=List[Signal])
async def list_signals(
    field: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=1000),
) -> List[Signal]:
    col = get_collection("signals")
    records = col.list()
    if field:
        records = [r for r in records if r.get("field") == field]
    records.sort(key=lambda r: r.get("heat", 0.0), reverse=True)
    return [Signal(**r) for r in records[:limit]]


def _aggregate_top_keywords(
    records: List[dict],
    field: Optional[str],
    top: int,
) -> Dict[str, Any]:
    """聚合 top 关键词（纯函数，handler 与 suggest-topics 复用）。

    注意：不要直接 await 带 Query 默认值的 handler，会把 Query 对象当值。
    """
    if field:
        records = [r for r in records if r.get("field") == field]
    agg: Dict[str, Dict[str, Any]] = {}
    for r in records:
        kw = r.get("keyword", "")
        if not kw:
            continue
        entry = agg.setdefault(kw, {
            "keyword": kw, "heat": 0.0, "count": 0, "field": r.get("field", ""),
        })
        entry["heat"] += float(r.get("heat", 0.0))
        entry["count"] += 1
    items = sorted(agg.values(), key=lambda e: e["heat"], reverse=True)[:top]
    return {"top": items, "total_keywords": len(agg)}


@router.get("/top-keywords")
async def top_keywords(
    field: Optional[str] = Query(default=None),
    top: int = Query(default=20, le=100),
) -> Dict[str, Any]:
    """聚合 top 关键词（按关键词去重求和 heat），供选题引擎消费。"""
    return _aggregate_top_keywords(get_collection("signals").list(), field, top)


@router.get("/suggest-topics")
async def suggest_topics(
    top: int = Query(default=10, le=50),
    save: bool = False,
) -> Dict[str, Any]:
    """从工具信号 top 关键词 → 选题建议（自动打标 + 打分），反哺选题库。

    信号特征按热度归一为 signal 特征喂入打分器；其余特征取中性 0.5。
    """
    kws = _aggregate_top_keywords(get_collection("signals").list(), None, top)
    suggestions = []
    for item in kws["top"]:
        keyword = item["keyword"]
        title = keyword if len(keyword) >= 4 else f"{keyword}教程"
        tag = suggest_dimension_monetizer(title)
        # 信号热度 → 0.0~1.0（按 top 内最大值归一）
        signal_feat = min(item["heat"] / (kws["top"][0]["heat"] or 1.0), 1.0)
        feats = {
            "hot": 0.5, "competition": 0.5, "fit": 0.5,
            "timeliness": 0.5, "convert_value": 0.5, "signal": signal_feat,
        }
        sc = compute_score(feats)
        suggestions.append({
            "keyword": keyword,
            "title": title,
            "dimension": tag["dimension"],
            "monetizer": tag["monetizer"],
            "signal_heat": item["heat"],
            "signal_count": item["count"],
            "score": sc["score"],
            "breakdown": sc["breakdown"],
        })
        if save:
            from app.api.topics import build_topic
            from app.models import Topic
            t = build_topic(Topic(
                title=title,
                dimension=tag["dimension"],
                monetizer=tag["monetizer"],
                source="signal",
                weights=feats,
                score=sc["score"],
            ))
            get_collection("topics").insert(t)
    suggestions.sort(key=lambda s: s["score"], reverse=True)
    return {"suggestions": suggestions, "saved": save}
