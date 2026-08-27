# -*- coding: utf-8 -*-
"""采集器单元测试：热度打分、平台识别、容错、上报计数（mock 网络）。"""
import time

from app.collectors.collector import (_PARSERS, _heat, _platform_of, fetch_bilibili_popular,
                                      fetch_bilibili_ranking, parse_one, report_topics,
                                      report_tool_events)


def test_platform_of():
    assert _platform_of("https://www.bilibili.com/video/BV1xx411c7mD") == "bilibili"
    assert _platform_of("https://v.douyin.com/abc/") == "douyin"
    assert _platform_of("https://www.kuaishou.com/short-video/xyz") == "kuaishou"
    assert _platform_of("https://www.xiaohongshu.com/explore/xyz") == "xiaohongshu"
    assert _platform_of("http://example.com/x") == "unknown"


def test_heat_high_plays_dominates():
    # 高播放量应显著拉高热度
    rec = {"plays": 1_000_000, "likes": 1000, "comments": 100, "shares": 50,
           "collects": 200, "create_time": time.time()}
    h = _heat(rec)
    assert h > 70  # 1M 播放 + 新视频 → 高热度


def test_heat_engagement_bonus():
    base = {"plays": 100_000, "likes": 1000, "comments": 100, "shares": 50,
            "collects": 200, "create_time": time.time()}
    low = dict(base)
    high = dict(base, likes=20_000, comments=3000, shares=2000, collects=5000)
    assert _heat(high) > _heat(low)


def test_heat_recency_penalty():
    now = time.time()
    fresh = {"plays": 100_000, "likes": 1000, "comments": 100, "shares": 50,
             "collects": 200, "create_time": now}
    old = dict(fresh, create_time=now - 60 * 86400)  # 60 天前
    assert _heat(fresh) > _heat(old)


def test_heat_zero_plays():
    rec = {"plays": 0, "likes": 0, "comments": 0, "shares": 0,
           "collects": 0, "create_time": time.time()}
    # 无播放不崩溃，热度仅剩时效分量
    assert 0 <= _heat(rec) <= 100


def test_parse_one_unsupported():
    r = parse_one("https://example.com/foo")
    assert r["error"] == "unsupported_platform"


def test_parse_one_parser_error(monkeypatch):
    class _Fake:
        def parse(self, url):
            raise RuntimeError("boom")

    monkeypatch.setitem(_PARSERS, "bilibili", _Fake)
    r = parse_one("https://www.bilibili.com/video/BV1xx411c7mD")
    assert r["error"].startswith("RuntimeError: boom")


def test_parse_one_unsupported_douyin_without_playwright(monkeypatch):
    # 抖音在无 playwright 环境不应崩溃（douyin.py 顶层 try/except 可选导入）
    r = parse_one("https://v.douyin.com/abc/")
    assert "error" in r  # 返回 error 而非抛异常


def test_report_tool_events_success(monkeypatch):
    called = []

    class _Resp:
        status = 200

    class _FakeUrlopen:
        def __init__(self, req, timeout=0):
            called.append(req)
            self._r = _Resp()

        def __enter__(self):
            return self._r

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", _FakeUrlopen)
    recs = [
        {"url": "https://www.bilibili.com/video/BV1xx411c7mD", "title": "A",
         "platform": "bilibili", "plays": 100, "heat": 50.0},
        {"url": "https://www.bilibili.com/video/BV1GJ411x7h7", "error": "x"},
    ]
    ok = report_tool_events("http://127.0.0.1:8001", recs)
    assert ok == 1
    assert len(called) == 1
    assert "/api/traffic/signals/tool-event" in called[0].full_url


def test_fetch_bilibili_ranking(monkeypatch):
    class _FakeResp:
        def json(self):
            return {"code": 0, "data": {"list": [
                {"bvid": "BV1aaa", "title": "A", "stat": {"view": 100, "like": 10}},
                {"bvid": "BV1bbb", "title": "B", "stat": {"view": 200, "like": 20}},
            ]}}

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp())
    out = fetch_bilibili_ranking(limit=2, rid=0)
    assert len(out) == 2
    assert out[0]["bvid"] == "BV1aaa"
    assert out[0]["plays"] == 100


def test_fetch_bilibili_ranking_error(monkeypatch):
    class _FakeResp:
        def json(self):
            return {"code": -352, "message": "-352"}

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp())
    assert fetch_bilibili_ranking(limit=2, rid=0) == []


def test_fetch_bilibili_popular(monkeypatch):
    class _FakeResp:
        def json(self):
            return {"code": 0, "data": {"list": [
                {"bvid": "BV1pop1", "title": "当日热门A", "stat": {"view": 500, "like": 30}},
                {"bvid": "BV1pop2", "title": "当日热门B", "stat": {"view": 600, "like": 40}},
            ]}}

    monkeypatch.setattr("requests.get", lambda *a, **k: _FakeResp())
    out = fetch_bilibili_popular(limit=2)
    assert len(out) == 2
    assert out[1]["bvid"] == "BV1pop2"


def test_report_topics(monkeypatch):
    calls = []

    class _Resp:
        status = 200

    class _FakeUrlopen:
        def __init__(self, req, timeout=0):
            calls.append(req.full_url)

        def __enter__(self):
            return _Resp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", _FakeUrlopen)
    recs = [
        {"title": "热点A", "plays": 100, "heat": 80.0},
        {"title": "", "error": "x"},
    ]
    ok = report_topics("http://127.0.0.1:8001", recs)
    assert ok == 1
    assert calls[0].endswith("/api/traffic/topics")


def test_write_snapshot(tmp_path, monkeypatch):
    import json

    import app.collectors.collector as c

    recs = [
        {"url": "https://www.bilibili.com/video/BV1snapAAA11", "platform": "bilibili",
         "title": "热点甲", "plays": 100, "likes": 5, "comments": 2, "shares": 1,
         "collects": 3, "heat": 80.0, "collected_at": 1111111111},
        {"url": "https://www.bilibili.com/video/BV1snapAAA11", "platform": "bilibili",
         "title": "热点甲(重复)", "plays": 200},
        {"url": "", "error": "x"},
    ]
    path = c.write_snapshot(recs, str(tmp_path))
    assert path.endswith(".json")
    data = json.loads(open(path, encoding="utf-8").read())
    assert len(data["items"]) == 1  # 同 bvid 去重
    assert data["items"][0]["bvid"] == "BV1snapAAA11"


def test_bvid_of():
    import app.collectors.collector as c

    assert c._bvid_of({"bvid": "BV1abc"}) == "BV1abc"
    assert c._bvid_of({"url": "https://www.bilibili.com/video/BV1xyz1234ab"}) == "BV1xyz1234ab"
