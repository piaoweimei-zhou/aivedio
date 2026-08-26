"""P2a 迭代回灌单测：权重计算/写入/重打分/审计。"""
from __future__ import annotations

import tempfile
import time

import pytest

from app.feedback import (compute_adjustment, get_active_weights,
                          rescore_pending, run_feedback)
from app.models import Topic
from app.scoring import DEFAULT_WEIGHTS, normalize_weights
from app.storage import get_collection


def _mk(content_id, dim, mon, views=100, revenue=0.0, conversions=None, ts=None):
    return {
        "content_id": content_id,
        "account_id": "accA",
        "dimension": dim,
        "monetizer": mon,
        "views": views,
        "likes": 0, "comments": 0, "shares": 0, "follows": 0,
        "conversions": conversions or {},
        "revenue": revenue,
        "collected_at": ts if ts is not None else time.time(),
    }


def _tmp(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    return tmp


def test_compute_adjustment_raises_convert_weight_when_course_roi_high():
    # course 变现 ROI 高 → convert 权重应上调
    records = [
        _mk("c1", "knowledge", "course", views=1000, revenue=50.0,
            conversions={"course_signup": 3}),
        _mk("c2", "soft_ad", "tool", views=500, revenue=2.0,
            conversions={"download": 1}),
    ]
    adj = compute_adjustment(records, days=7)
    assert adj["changed"] is True
    assert adj["weights"]["convert"] > DEFAULT_WEIGHTS["convert"]
    assert adj["weights"]["fit"] > DEFAULT_WEIGHTS["fit"]


def test_compute_adjustment_no_data_unchanged():
    adj = compute_adjustment([], days=7)
    assert adj["changed"] is False
    assert adj["detail"]["reason"] == "no metrics in period"


def test_compute_adjustment_zero_roi_unchanged():
    records = [_mk("c1", "knowledge", "course", views=100, revenue=0.0)]
    adj = compute_adjustment(records, days=7)
    assert adj["changed"] is False
    assert "zero" in adj["detail"]["reason"]


def test_apply_weights_auditable(monkeypatch):
    tmp = _tmp(monkeypatch)
    from app.feedback import apply_weights

    res = apply_weights(normalize_weights({"convert": 0.5}), source="feedback",
                        note="test")
    assert res["source"] == "feedback"
    assert get_active_weights()["convert"] == pytest.approx(
        normalize_weights({"convert": 0.5})["convert"])
    hist = get_collection("weight_history").list()
    assert len(hist) == 1
    assert hist[0]["note"] == "test"
    tmp.cleanup()


def test_rescore_pending_uses_new_weights(monkeypatch):
    tmp = _tmp(monkeypatch)
    col = get_collection("topics")
    t = Topic(title="热点测试", source="manual",
              weights={"hot": 0.9, "convert_value": 0.8}, status="pending")
    col.insert(t)
    # 用极端的 convert 权重重打分，分数应与默认不同
    w = normalize_weights({"hot": 0.0, "convert": 1.0})
    n = rescore_pending(w)
    assert n == 1
    updated = col.list()[0]
    assert updated["score"] > 0
    tmp.cleanup()


def test_run_feedback_no_data_no_apply(monkeypatch):
    tmp = _tmp(monkeypatch)
    res = run_feedback(days=7)
    assert res["applied"] is False
    assert "no metrics" in res["reason"]
    tmp.cleanup()


def test_run_feedback_applies_and_rescores(monkeypatch):
    tmp = _tmp(monkeypatch)
    from app.models import MetricRecord

    records = [
        MetricRecord(content_id="c1", account_id="accA",
                     dimension="knowledge", monetizer="course",
                     views=1000, revenue=50.0,
                     conversions={"course_signup": 3}),
        MetricRecord(content_id="c2", account_id="accA",
                     dimension="soft_ad", monetizer="tool",
                     views=500, revenue=2.0,
                     conversions={"download": 1}),
    ]
    col = get_collection("metrics")
    for r in records:
        col.insert(r)
    # 放一条 pending 选题
    tcol = get_collection("topics")
    tcol.insert(Topic(title="测试", source="manual", status="pending"))
    res = run_feedback(days=7)
    assert res["applied"] is True
    assert res["rescore_count"] == 1
    # 权重已生效且可审计
    assert "weights_before" in res
    assert get_active_weights()["convert"] != DEFAULT_WEIGHTS["convert"]
    tmp.cleanup()
