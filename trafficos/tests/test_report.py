"""P1d ROI 周报单测：周过滤/分组/建议/markdown。"""
from __future__ import annotations

import time

from app.report import to_markdown, weekly_report, _within_days


def _mk(content_id, account, dim, mon, views=100, revenue=0.0, conversions=None,
        ts=None, likes=0):
    return {
        "content_id": content_id,
        "account_id": account,
        "dimension": dim,
        "monetizer": mon,
        "views": views,
        "likes": likes,
        "comments": 0,
        "shares": 0,
        "follows": 0,
        "conversions": conversions or {},
        "revenue": revenue,
        "collected_at": ts if ts is not None else time.time(),
    }


def test_within_days_filters_old():
    now = time.time()
    old = _mk("old", "a1", "knowledge", "course", ts=now - 8 * 86400)
    new = _mk("new", "a1", "knowledge", "course", ts=now - 1 * 86400)
    kept = _within_days([old, new], 7)
    assert [r["content_id"] for r in kept] == ["new"]


def test_weekly_report_structure_and_roi():
    records = [
        _mk("c1", "accA", "knowledge", "course", views=1000, revenue=20.0,
            conversions={"course_signup": 2}),
        _mk("c2", "accA", "soft_ad", "tool", views=500, revenue=5.0,
            conversions={"download": 1}),
        _mk("c3", "accB", "pure_content", "adshare", views=200, revenue=0.0),
    ]
    report = weekly_report(records, days=7, cost=1.0)
    assert report["total"]["contents"] == 3
    assert report["total"]["revenue"] == 25.0
    assert report["total"]["cost"] == 3.0
    assert report["total"]["roi"] == round(25.0 / 3.0, 4)
    # 分组：账号 2 个、维度 3 个、变现 3 个
    assert len(report["by_account"]) == 2
    assert len(report["by_dimension"]) == 3
    assert len(report["by_monetizer"]) == 3
    # Top 内容按播放降序
    assert report["top_contents"][0]["content_id"] == "c1"
    # 建议非空（有高 ROI 维度/账号）
    assert report["suggestions"]


def test_weekly_report_empty_suggestions():
    report = weekly_report([], days=7)
    assert report["total"]["contents"] == 0
    assert any("无内容表现数据" in s for s in report["suggestions"])


def test_suggestions_zero_roi_flags_review():
    records = [
        _mk("c1", "a1", "knowledge", "course", views=10, revenue=0.0),
        _mk("c2", "a2", "knowledge", "course", views=20, revenue=0.0),
    ]
    report = weekly_report(records, days=7, cost=1.0)
    tips = " ".join(report["suggestions"])
    assert "复盘" in tips or "0" in tips


def test_to_markdown_contains_sections():
    records = [_mk("c1", "accA", "knowledge", "course", views=100, revenue=5.0)]
    report = weekly_report(records, days=7, cost=1.0)
    md = to_markdown(report, unit_cost=1.0)
    assert "# ROI 周报" in md
    assert "## 按账号" in md
    assert "## 按维度" in md
    assert "## 按变现" in md
    assert "## Top 内容" in md
    assert "## 建议" in md
    assert "ROI：5.0" in md or "ROI：5" in md
