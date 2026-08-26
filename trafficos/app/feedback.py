"""迭代回灌（P2a）：周 ROI 数据 → 自动调整 scoring 全局权重 → 重打选题分。

飞轮闭环：
    metrics(周ROI) → 计算维度/变现 ROI 相对基准 → 调整 convert/fit 权重
    → 写 score_weights（生效权重，可审计）→ 重打所有 pending topics 分数

设计：纯启发式 + 全量可审计（每次调整写 weight_history），不覆盖人工配置
（人工显式设置过权重时，跳过自动回灌，见 API 参数 force）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.analytics import aggregate, group_by
from app.models import Topic
from app.scoring import DEFAULT_WEIGHTS, normalize_weights, score_from_topic_weights
from app.storage import get_collection


class ScoreWeightRecord(BaseModel):
    """生效权重记录（P2a，可审计）"""
    id: str = ""
    weights: Dict[str, float] = Field(default_factory=dict)
    source: str = "feedback"
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "ScoreWeightRecord":
        ts = time.time()
        self.id = self.id or f"sw_{int(ts * 1000)}"
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


class WeightHistoryRecord(BaseModel):
    id: str = ""
    weights: Dict[str, float] = Field(default_factory=dict)
    source: str = "feedback"
    note: str = ""
    applied_at: float = 0.0

    def touch(self) -> "WeightHistoryRecord":
        ts = time.time()
        self.id = self.id or f"wh_{int(ts * 1000)}"
        self.applied_at = self.applied_at or ts
        return self


# 权重调整幅度钳制（避免单周极端 ROI 导致权重剧烈波动）
RATIO_MIN, RATIO_MAX = 0.5, 2.0


def _within_days(records: List[dict], days: int) -> List[dict]:
    cutoff = time.time() - days * 86400
    return [r for r in records if float(r.get("collected_at", 0.0) or 0.0) >= cutoff]


def _best_ratio(roi_map: Dict[str, float], baseline: float) -> float:
    """最佳组 ROI 相对基准的比率（聚焦高 ROI 变现/维度），封顶 RATIO_MAX。"""
    if baseline <= 0 or not roi_map:
        return 1.0
    ratios = [v / baseline for v in roi_map.values() if v > 0]
    if not ratios:
        return 1.0
    best = max(ratios)
    return max(1.0, min(best, RATIO_MAX))


def _clamp(v: float, lo: float = RATIO_MIN, hi: float = RATIO_MAX) -> float:
    return max(lo, min(hi, v))


def get_active_weights() -> Dict[str, float]:
    """当前生效权重：人工配置 > 回灌结果 > DEFAULT_WEIGHTS。"""
    col = get_collection("score_weights")
    recs = col.list()
    recs.sort(key=lambda r: r.get("updated_at", 0.0), reverse=True)
    if recs and recs[0].get("weights"):
        return dict(recs[0]["weights"])
    return dict(DEFAULT_WEIGHTS)


def compute_adjustment(
    records: List[dict],
    days: int = 7,
    base: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """基于周 ROI 计算权重调整。

    Returns:
        {"weights": 新权重, "changed": bool, "detail": {roi_total, by_monetizer, by_dimension,
         convert_ratio, fit_ratio, reason}}
    """
    base = dict(base or DEFAULT_WEIGHTS)
    recent = _within_days(records, days)
    total = aggregate(recent)
    if total["contents"] == 0:
        return {"weights": base, "changed": False,
                "detail": {"reason": "no metrics in period"}}
    if total["roi"] <= 0:
        return {"weights": base, "changed": False,
                "detail": {"reason": "total roi is zero", "roi_total": total["roi"]}}

    by_mon = group_by(recent, "monetizer")
    by_dim = group_by(recent, "dimension")
    mon_roi = {m["group"]: m["roi"] for m in by_mon if m["contents"] > 0}
    dim_roi = {d["group"]: d["roi"] for d in by_dim if d["contents"] > 0}

    convert_ratio = _best_ratio(mon_roi, total["roi"])
    fit_ratio = _best_ratio(dim_roi, total["roi"])

    new = dict(base)
    new["convert"] = base["convert"] * _clamp(convert_ratio)
    new["fit"] = base["fit"] * _clamp(fit_ratio)
    new = normalize_weights(new)

    changed = any(abs(new[k] - base[k]) > 1e-6 for k in ("convert", "fit"))
    return {
        "weights": new,
        "changed": changed,
        "detail": {
            "reason": "ok",
            "roi_total": total["roi"],
            "contents": total["contents"],
            "convert_ratio": round(convert_ratio, 4),
            "fit_ratio": round(fit_ratio, 4),
            "by_monetizer": mon_roi,
            "by_dimension": dim_roi,
        },
    }


def apply_weights(weights: Dict[str, float], source: str = "feedback",
                  note: str = "") -> Dict[str, Any]:
    """写入生效权重（可审计：记录当前值 + 历史追加）。"""
    col = get_collection("score_weights")
    record = ScoreWeightRecord(weights=dict(weights), source=source, note=note)
    col.insert(record)
    # 历史审计
    hist = get_collection("weight_history")
    hist.insert(WeightHistoryRecord(
        weights=dict(weights), source=source, note=note,
    ))
    return {"applied": record.id, "weights": dict(weights), "source": source}


def rescore_pending(weights: Optional[Dict[str, float]] = None) -> int:
    """用（新）权重重算所有 pending 选题的分数。返回重算条数。"""
    w = weights or get_active_weights()
    col = get_collection("topics")
    n = 0
    for t in col.list():
        if t.get("status") != "pending":
            continue
        topic = Topic(**t)
        topic.score = score_from_topic_weights(topic.weights, w)["score"]
        col.update(topic.id, {"score": topic.score, "updated_at": time.time()})
        n += 1
    return n


def run_feedback(days: int = 7, force: bool = False) -> Dict[str, Any]:
    """一键回灌：读 metrics → 算调整 → 写权重 → 重打分。返回完整审计信息。"""
    records = get_collection("metrics").list()
    adj = compute_adjustment(records, days=days)
    if not adj["changed"] and not force:
        return {
            "applied": False,
            "reason": adj["detail"]["reason"],
            "active_weights": get_active_weights(),
            "adjustment": adj,
            "rescore_count": 0,
        }
    active_before = get_active_weights()
    note = (f"feedback:{adj['detail']['reason']} "
            f"roi={adj['detail'].get('roi_total')}")
    applied = apply_weights(adj["weights"], source="feedback", note=note)
    rescored = rescore_pending(adj["weights"])
    return {
        "applied": True,
        "weights_before": active_before,
        "adjustment": adj,
        "applied_id": applied["applied"],
        "rescore_count": rescored,
    }
