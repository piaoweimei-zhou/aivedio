"""选题库 API（① 选题层）：CRUD + 自动打分 + 排序"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.models import Dimension, Monetizer, Topic
from app.scoring import (
    DEFAULT_WEIGHTS,
    score_from_topic_weights,
    suggest_dimension_monetizer,
)
from app.storage import get_collection

router = APIRouter(prefix="/api/traffic/topics", tags=["流量侧-选题库"])


def build_topic(topic: Topic) -> Topic:
    """选题规范化（同步纯逻辑，供各入口复用）：自动打标 + 自动打分。

    - 未指定维度/变现时按标题粗判
    - 缺失特征按 0.5 中性打分
    """
    if topic.dimension is None or topic.monetizer is None:
        hint = suggest_dimension_monetizer(topic.title)
        if topic.dimension is None and hint["dimension"]:
            topic.dimension = Dimension(hint["dimension"])  # 字符串→枚举（避免序列化警告）
        if topic.monetizer is None and hint["monetizer"]:
            topic.monetizer = Monetizer(hint["monetizer"])
    result = score_from_topic_weights(topic.weights)
    topic.score = result["score"]
    return topic


@router.post("", response_model=Topic)
async def create_topic(topic: Topic) -> Topic:
    """创建选题，自动打分（缺失特征按 0.5 中性；标题可自动打标）。"""
    return get_collection("topics").insert(build_topic(topic))


@router.get("", response_model=List[Topic])
async def list_topics(
    status: Optional[str] = Query(default=None),
    dimension: Optional[str] = Query(default=None),
    sort: str = Query(default="score", description="score/created_at"),
    limit: int = Query(default=100, le=500),
) -> List[Topic]:
    """选题列表（默认按得分降序）。"""
    col = get_collection("topics")
    records = col.list()
    if status:
        records = [r for r in records if r.get("status") == status]
    if dimension:
        records = [r for r in records if r.get("dimension") == dimension]
    topics = [Topic(**r) for r in records]
    if sort == "score":
        topics.sort(key=lambda t: t.score, reverse=True)
    else:
        topics.sort(key=lambda t: t.created_at, reverse=True)
    return topics[:limit]


@router.get("/meta/score-weights")
async def get_score_weights() -> Dict[str, Any]:
    """当前打分权重（供前端展示/调参）。"""
    return {"weights": DEFAULT_WEIGHTS, "note": "权重经 normalize_weights 自动归一化为和 1"}


@router.get("/{topic_id}", response_model=Topic)
async def get_topic(topic_id: str) -> Topic:
    rec = get_collection("topics").get(topic_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}")
    return Topic(**rec)


@router.put("/{topic_id}", response_model=Topic)
async def update_topic(topic_id: str, patch: Topic) -> Topic:
    """更新选题，自动重打分。"""
    col = get_collection("topics")
    cur = col.get(topic_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}")
    data = patch.model_dump(exclude_unset=True)
    data["id"] = topic_id
    # 重打分（若特征有变）
    weights = data.get("weights") or cur.get("weights") or {}
    result = score_from_topic_weights(weights)
    data["score"] = result["score"]
    updated = col.update(topic_id, data)
    return Topic(**updated)


@router.delete("/{topic_id}")
async def delete_topic(topic_id: str) -> dict:
    if not get_collection("topics").delete(topic_id):
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}")
    return {"success": True}


@router.post("/{topic_id}/score", response_model=Topic)
async def rescore_topic(topic_id: str, features: Dict[str, float]) -> Topic:
    """手动重打分：传入特征覆盖，重算 score。"""
    col = get_collection("topics")
    cur = col.get(topic_id)
    if cur is None:
        raise HTTPException(status_code=404, detail=f"topic not found: {topic_id}")
    weights = dict(cur.get("weights") or {})
    weights.update(features)
    result = score_from_topic_weights(weights)
    updated = col.update(topic_id, {"weights": weights, "score": result["score"]})
    return Topic(**updated)
