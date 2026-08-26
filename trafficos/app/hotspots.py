"""热点自动采集（P1a）：多源拉取 → 去重 → 写入选题库(source=hot) → 自动打分。

设计要点：
- 内置公开免费热点源（微博热搜 / 抖音热榜，经 vvhan 聚合接口），可配置扩展
- urllib 拉取（零新依赖），超时 + 失败自动降级，不阻塞主流程
- 写入复用 topics 的 build_topic（自动建议维度/变现 + 自动打分）
- 手动 sync 为主；定时刷新由 main.py lifespan 按 env 开关控制（默认关）
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.models import Topic
from app.storage import get_collection

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TrafficOS-Hotspot/0.1"
_TIMEOUT = 8


@dataclass
class HotspotItem:
    """单条热点（源无关的中间表示）"""
    title: str
    heat: float = 0.0
    url: str = ""
    source: str = ""                     # wbHot/douyinHot/manual
    extra: Dict[str, Any] = field(default_factory=dict)


# 内置源：name -> (接口路径, 解析函数)
# 均为大陆可达的公开接口（2026-08 实测：百度热搜 / 头条热榜可用；微博403/知乎401已排除）
_SOURCES: Dict[str, str] = {
    "baidu": "https://top.baidu.com/api/board?platform=wise&tab=realtime",
    "toutiao": "https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc",
}


def _http_get_json(url: str) -> Optional[Dict[str, Any]]:
    """拉取 JSON，失败返回 None（降级）"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:  # noqa: BLE001 外部网络问题不阻断
        logger.warning("[hotspots] 源拉取失败 %s: %s", url, exc)
        return None


def parse_baidu(payload: Optional[Dict[str, Any]]) -> List[HotspotItem]:
    """百度热搜：data.cards[0].content[0].content[] → {word, url}"""
    if not payload:
        return []
    items: List[HotspotItem] = []
    try:
        cards = payload.get("data", {}).get("cards", [])
        for card in cards:
            outer = card.get("content", [])
            for block in outer:
                for row in block.get("content", []):
                    word = (row.get("word") or "").strip()
                    if not word:
                        continue
                    items.append(HotspotItem(
                        title=word,
                        heat=0.0,
                        url=row.get("url") or "",
                        source="baidu",
                        extra={"isTop": row.get("isTop")},
                    ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[hotspots] baidu 解析异常: %s", exc)
    return items


def parse_toutiao(payload: Optional[Dict[str, Any]]) -> List[HotspotItem]:
    """头条热榜：data[] → {Title, HotValue, Url}"""
    if not payload or not isinstance(payload.get("data"), list):
        return []
    items: List[HotspotItem] = []
    for row in payload["data"]:
        title = (row.get("Title") or "").strip()
        if not title:
            continue
        items.append(HotspotItem(
            title=title,
            heat=float(row.get("HotValue") or 0),
            url=row.get("Url") or "",
            source="toutiao",
            extra={"cluster_id": row.get("ClusterId"), "label": row.get("Label")},
        ))
    return items


_PARSERS = {
    "baidu": parse_baidu,
    "toutiao": parse_toutiao,
}


def fetch_from_source(name: str) -> List[HotspotItem]:
    """拉取单个源，失败返回 []"""
    url = _SOURCES.get(name)
    if not url:
        logger.warning("[hotspots] 未知源: %s", name)
        return []
    parser = _PARSERS.get(name, lambda _p: [])
    return parser(_http_get_json(url))


def fetch_all(source_names: Optional[List[str]] = None) -> Dict[str, List[HotspotItem]]:
    """拉取全部/指定源，按源分组返回"""
    names = source_names or list(_SOURCES.keys())
    out: Dict[str, List[HotspotItem]] = {}
    for name in names:
        out[name] = fetch_from_source(name)
    return out


def _existing_titles() -> set:
    col = get_collection("topics")
    return {t.get("title", "") for t in col.list()}


def items_to_topics(items: List[HotspotItem], dimension=None, monetizer=None) -> List[Topic]:
    """热点项 → Topic（source=hot，热度写入 weights.hot）"""
    topics: List[Topic] = []
    for it in items:
        topic = Topic(
            title=it.title,
            source="hot",
            note=f"热点源: {it.source}" + (f" | {it.url}" if it.url else ""),
            weights={"hot": min(it.heat / 100.0, 1.0)} if it.heat > 0 else {},
        )
        if dimension is not None:
            topic.dimension = dimension
        if monetizer is not None:
            topic.monetizer = monetizer
        topics.append(topic)
    return topics


def sync(limit: int = 50, source_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """同步热点 → 选题库。返回统计（含各源失败降级信息）。"""
    from app.api.topics import build_topic  # 复用打分/维度建议（避免循环导入）

    grouped = fetch_all(source_names)
    existing = _existing_titles()
    col = get_collection("topics")

    stats: Dict[str, Any] = {"fetched": 0, "new": 0, "dup": 0, "by_source": {}}
    for name, items in grouped.items():
        stats["by_source"][name] = {"fetched": len(items)}
        for it in items:
            if stats["new"] >= limit:
                break
            stats["fetched"] += 1
            if it.title in existing:
                stats["dup"] += 1
                continue
            topic = items_to_topics([it], dimension=None, monetizer=None)[0]
            topic = build_topic(topic)
            col.insert(topic)
            existing.add(it.title)
            stats["new"] += 1
    return stats
