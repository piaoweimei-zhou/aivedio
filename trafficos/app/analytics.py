"""数据分析核心（⑤ 数据层，B7）：指标聚合 + ROI 归因。

纯函数，便于单测。ROI 归因逻辑（规划 v1.4 §5.5）：
- 每条内容按 content_id/dimension/monetizer/account_id 归因
- ROI = 变现收益(revenue) / 内容投入(cost)
- 内容投入 P0：cost_per_content（可配置，默认 1.0 元/条）
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional


def cost_per_content() -> float:
    """单条内容投入成本（元），可经 env 覆盖。"""
    try:
        return float(os.environ.get("TRAFFICOS_COST_PER_CONTENT", "1.0"))
    except ValueError:
        return 1.0


# 转化事件（conversions dict 的已知键；未知键计入 total）
KNOWN_CONVERSIONS = (
    "product_click", "download", "activate",      # 工具/产品
    "course_signup", "course_pay",                # 课程
    "netdisk_save", "netdisk_new",                # 网盘
    "xianyu_deal",                                # 闲鱼
    "saas_trial", "saas_pay",                     # SaaS
    "resource_claim",                             # 资源
    "follow",                                     # 关注
)


def sum_conversions(conversions: Optional[Dict[str, int]]) -> int:
    """转化事件总数（已知键之和，容错）。"""
    if not conversions:
        return 0
    total = 0
    for k, v in conversions.items():
        if isinstance(v, (int, float)):
            total += int(v)
    return total


def _metric_dict(record: dict) -> Dict[str, object]:
    """从 MetricRecord dict 提取聚合字段。"""
    return {
        "content_id": record.get("content_id", ""),
        "account_id": record.get("account_id", ""),
        "dimension": record.get("dimension"),
        "monetizer": record.get("monetizer"),
        "views": int(record.get("views", 0) or 0),
        "likes": int(record.get("likes", 0) or 0),
        "comments": int(record.get("comments", 0) or 0),
        "shares": int(record.get("shares", 0) or 0),
        "follows": int(record.get("follows", 0) or 0),
        "conversions": record.get("conversions") or {},
        "revenue": float(record.get("revenue", 0.0) or 0.0),
    }


def aggregate(records: List[dict], cost: Optional[float] = None) -> Dict[str, object]:
    """聚合一组内容表现（维度/账号/整体通用）。

    Returns:
        {contents, views, likes, comments, shares, follows,
         conversions, conversion_total, revenue, cost, roi, engagement_rate}
    """
    unit_cost = cost if cost is not None else cost_per_content()
    out = {
        "contents": len(records),
        "views": 0, "likes": 0, "comments": 0, "shares": 0, "follows": 0,
        "conversions": {},
        "conversion_total": 0,
        "revenue": 0.0,
        "cost": 0.0,
        "roi": 0.0,
        "engagement_rate": 0.0,
    }
    for r in records:
        m = _metric_dict(r)
        out["views"] += m["views"]
        out["likes"] += m["likes"]
        out["comments"] += m["comments"]
        out["shares"] += m["shares"]
        out["follows"] += m["follows"]
        out["revenue"] += m["revenue"]
        for k, v in m["conversions"].items():
            if isinstance(v, (int, float)):
                out["conversions"][k] = out["conversions"].get(k, 0) + int(v)
    out["conversion_total"] = sum_conversions(out["conversions"])
    out["cost"] = round(unit_cost * out["contents"], 4)
    out["roi"] = round(out["revenue"] / out["cost"], 4) if out["cost"] > 0 else 0.0
    if out["views"] > 0:
        out["engagement_rate"] = round(
            (out["likes"] + out["comments"] + out["shares"]) / out["views"], 4
        )
    return out


def group_by(
    records: List[dict],
    key: str,
    cost: Optional[float] = None,
) -> List[Dict[str, object]]:
    """按 key（dimension/account_id/monetizer）分组聚合。

    Returns:
        [{"group": key_value, **aggregate}, ...] 按 revenue 降序
    """
    groups: Dict[str, List[dict]] = {}
    for r in records:
        g = r.get(key) or "unknown"
        groups.setdefault(g, []).append(r)
    result = []
    for g, recs in groups.items():
        agg = aggregate(recs, cost)
        result.append({"group": g, **agg})
    result.sort(key=lambda x: x["revenue"], reverse=True)
    return result
