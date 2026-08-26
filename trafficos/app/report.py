"""ROI 周报（P1d）：基于 analytics 的周度聚合报告 + 策略建议。

输入：metrics 记录（MetricRecord），按 collected_at 过滤最近 N 天。
输出：结构化 JSON（总览/按账号/按维度/按变现/Top内容/建议）+ markdown 渲染。
建议规则为纯启发式（高 ROI 增产、零 ROI 复盘、高转化聚焦），供 P2a 回灌引用。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.analytics import aggregate, cost_per_content, group_by


def _within_days(records: List[dict], days: int) -> List[dict]:
    """按 collected_at 过滤最近 days 天。"""
    if not records:
        return []
    cutoff = time.time() - days * 86400
    return [r for r in records if float(r.get("collected_at", 0.0) or 0.0) >= cutoff]


def _top_contents(records: List[dict], n: int = 5) -> List[Dict[str, Any]]:
    """Top 内容（按 views 降序，附 ROI 摘要）。"""
    rows = []
    for r in records:
        m = r
        revenue = float(m.get("revenue", 0.0) or 0.0)
        rows.append({
            "content_id": m.get("content_id", ""),
            "account_id": m.get("account_id", ""),
            "dimension": str(m.get("dimension") or ""),
            "views": int(m.get("views", 0) or 0),
            "likes": int(m.get("likes", 0) or 0),
            "revenue": revenue,
        })
    rows.sort(key=lambda x: x["views"], reverse=True)
    return rows[:n]


def _suggestions(
    total: Dict[str, Any],
    by_dimension: List[Dict[str, Any]],
    by_monetizer: List[Dict[str, Any]],
    by_account: List[Dict[str, Any]],
) -> List[str]:
    """启发式策略建议（供人工采纳 / P2a 回灌参考）。"""
    tips: List[str] = []

    # 维度 ROI 排序：最高增产，零 ROI 复盘
    dims = [(d["group"], d["roi"], d["contents"]) for d in by_dimension if d["contents"] > 0]
    dims.sort(key=lambda x: x[1], reverse=True)
    if dims and dims[0][1] > 0:
        tips.append(f"维度「{dims[0][0]}」ROI 最高（{dims[0][1]}），建议增产")
    zeros = [d[0] for d in dims if d[1] == 0]
    if zeros:
        tips.append(f"维度「{'/'.join(zeros[:3])}」ROI 为 0，建议复盘或减产")

    # 变现转化聚焦
    mons = [(m["group"], m["conversion_total"]) for m in by_monetizer if m["contents"] > 0]
    mons.sort(key=lambda x: x[1], reverse=True)
    if mons and mons[0][1] > 0:
        tips.append(f"变现「{mons[0][0]}」转化最突出（{mons[0][1]} 次），建议聚焦")

    # 账号：最高 ROI 账号可加产
    accs = [(a["group"], a["roi"], a["contents"]) for a in by_account if a["contents"] > 0]
    accs.sort(key=lambda x: x[1], reverse=True)
    if accs and accs[0][1] > 0:
        tips.append(f"账号「{accs[0][0]}」ROI 最高（{accs[0][1]}），建议加产")

    # 整体兜底
    if total["contents"] == 0:
        tips.append("本周期无内容表现数据，先发布并回传指标")
    elif total["roi"] == 0:
        tips.append("整体 ROI 为 0，检查转化打点是否回传（conversions/revenue）")
    return tips


def weekly_report(
    records: List[dict],
    days: int = 7,
    cost: Optional[float] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    """生成周报（结构化 JSON）。

    Returns:
        {"period_days", "total", "by_account", "by_dimension", "by_monetizer",
         "top_contents", "suggestions"}
    """
    recent = _within_days(records, days)
    total = aggregate(recent, cost)
    by_account = group_by(recent, "account_id", cost)
    by_dimension = group_by(recent, "dimension", cost)
    by_monetizer = group_by(recent, "monetizer", cost)
    suggestions = _suggestions(total, by_dimension, by_monetizer, by_account)
    return {
        "period_days": days,
        "generated_at": time.time(),
        "total": total,
        "by_account": by_account,
        "by_dimension": by_dimension,
        "by_monetizer": by_monetizer,
        "top_contents": _top_contents(recent, top_n),
        "suggestions": suggestions,
    }


def to_markdown(report: Dict[str, Any], unit_cost: Optional[float] = None) -> str:
    """周报 markdown 渲染（供人工阅读 / 交付）。"""
    unit = unit_cost if unit_cost is not None else cost_per_content()
    t = report["total"]
    lines = [
        f"# ROI 周报（近 {report['period_days']} 天）",
        "",
        f"- 内容数：**{t['contents']}** 条（单条成本 {unit} 元）",
        f"- 播放：**{t['views']}** | 互动率：{t['engagement_rate']}",
        f"- 转化：{t['conversion_total']} 次 | 收益：**{t['revenue']} 元**",
        f"- 成本：{t['cost']} 元 | **ROI：{t['roi']}**",
        "",
        "## 按账号",
        "",
        "| 账号 | 内容 | 播放 | 收益 | ROI |",
        "|---|---|---|---|---|",
    ]
    for a in report["by_account"]:
        lines.append(
            f"| {a['group']} | {a['contents']} | {a['views']} | "
            f"{a['revenue']} | {a['roi']} |"
        )
    lines += ["", "## 按维度", "", "| 维度 | 内容 | 播放 | 收益 | ROI |", "|---|---|---|---|---|"]
    for d in report["by_dimension"]:
        lines.append(
            f"| {d['group']} | {d['contents']} | {d['views']} | "
            f"{d['revenue']} | {d['roi']} |"
        )
    lines += ["", "## 按变现", "", "| 变现 | 内容 | 转化 | 收益 | ROI |", "|---|---|---|---|---|"]
    for m in report["by_monetizer"]:
        lines.append(
            f"| {m['group']} | {m['contents']} | {m['conversion_total']} | "
            f"{m['revenue']} | {m['roi']} |"
        )
    lines += ["", "## Top 内容", "", "| 内容 | 账号 | 播放 | 收益 |", "|---|---|---|---|"]
    for c in report["top_contents"]:
        lines.append(
            f"| {c['content_id'][:20]} | {c['account_id']} | "
            f"{c['views']} | {c['revenue']} |"
        )
    lines += ["", "## 建议", ""]
    for s in report["suggestions"]:
        lines.append(f"- {s}")
    lines.append("")
    return "\n".join(lines)
