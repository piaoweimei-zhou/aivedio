"""选题打分器（B3，流量侧大脑核心）。

公式（规划 v1.4 §6.3）：
    score = w1×hot + w2×(1-competition) + w3×fit + w4×timeliness
            + w5×convert_value + w6×signal

所有特征归一化为 0.0~1.0；权重可配置（默认值见 DEFAULT_WEIGHTS）。
纯函数，便于单测。
"""
from __future__ import annotations

from typing import Dict, Optional

# 默认权重（可经 /api/traffic/topics/score-weights 覆盖）
DEFAULT_WEIGHTS: Dict[str, float] = {
    "hot": 0.25,           # w1 热度
    "fit": 0.20,           # w3 契合（账号/人设匹配）
    "convert": 0.25,       # w5 转化价值（变现目标贡献）
    "signal": 0.10,        # w6 需求信号（工具传感器）
    "competition": 0.10,   # w2 竞争（1-competition 参与加权）
    "timeliness": 0.10,    # w4 时效
}

_FEATURE_KEYS = list(DEFAULT_WEIGHTS.keys())


def clamp01(value: Optional[float]) -> float:
    """钳制到 0.0~1.0；None/非数按 0.5（中性）。"""
    if value is None:
        return 0.5
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, v))


def normalize_weights(weights: Optional[Dict[str, float]]) -> Dict[str, float]:
    """校验/归一化权重：缺失键用默认，和为 1。"""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k, v in weights.items():
            if k in _FEATURE_KEYS and isinstance(v, (int, float)) and v >= 0:
                w[k] = float(v)
    total = sum(w.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {k: v / total for k, v in w.items()}


def compute_score(
    features: Optional[Dict[str, float]],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """计算选题得分。

    Args:
        features: {hot, competition, fit, timeliness, convert_value, signal}
                  各 0.0~1.0；缺失按 0.5 中性。
        weights: 权重覆盖（可选），自动归一化。

    Returns:
        {"score": 0.0~1.0, "breakdown": {key: contribution, ...}}
    """
    w = normalize_weights(weights)
    feats = features or {}

    # 竞争维度取 (1-competition)，其余取原值
    hot = clamp01(feats.get("hot"))
    competition = 1.0 - clamp01(feats.get("competition"))
    fit = clamp01(feats.get("fit"))
    timeliness = clamp01(feats.get("timeliness"))
    convert_value = clamp01(feats.get("convert_value"))
    signal = clamp01(feats.get("signal"))

    contributions = {
        "hot": hot * w["hot"],
        "competition": competition * w["competition"],
        "fit": fit * w["fit"],
        "timeliness": timeliness * w["timeliness"],
        "convert": convert_value * w["convert"],
        "signal": signal * w["signal"],
    }
    score = round(sum(contributions.values()), 4)
    return {"score": score, "breakdown": contributions}


def score_from_topic_weights(
    weights: Dict[str, float],
    wconfig: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """从 Topic.weights（存储的原始特征）计算得分。"""
    feats = {
        "hot": weights.get("hot"),
        "competition": weights.get("competition"),
        "fit": weights.get("fit"),
        "timeliness": weights.get("timeliness"),
        "convert_value": weights.get("convert_value"),
        "signal": weights.get("signal"),
    }
    return compute_score(feats, wconfig)


def suggest_dimension_monetizer(topic_title: str) -> Dict[str, Optional[str]]:
    """基于标题关键词粗判维度/变现（用于选题自动打标）。

    纯规则启发式，后续可用 LLM 精确化。
    """
    title = topic_title.lower()
    dim, mon = None, None

    # 变现关键词
    if any(k in title for k in ("免费", "资源", "合集", "打包", "模板", "素材")):
        mon = "netdisk" if any(k in title for k in ("合集", "打包", "资源", "素材")) else "resource"
    elif any(k in title for k in ("教程", "课程", "学会", "入门", "进阶", "避坑")):
        mon = "course"
        dim = "knowledge"
    elif any(k in title for k in ("工具", "神器", "去水印", "批量", "效率")):
        mon = "tool"
        dim = "soft_ad"
    elif any(k in title for k in ("案例", "方案", "企业", "saas", "系统")):
        mon = "saas"
        dim = "soft_ad"
    elif any(k in title for k in ("赚钱", "副业", "收益", "分成")):
        mon = "adshare"

    # 维度兜底：含疑问/教程类归知识，其余默认纯内容
    if dim is None:
        if any(k in title for k in ("?", "怎么", "如何", "为什么", "教程")):
            dim = "knowledge"
        else:
            dim = "pure_content"
    return {"dimension": dim, "monetizer": mon}
