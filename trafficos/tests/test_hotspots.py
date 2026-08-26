"""P1a 热点自动采集单测：解析/去重/入库/打分/降级。"""
from __future__ import annotations

import pytest

from app.hotspots import (HotspotItem, items_to_topics, parse_baidu,
                          parse_toutiao, sync)
from app.storage import get_collection


def test_parse_baidu_normal():
    payload = {
        "data": {"cards": [{"content": [
            {"content": [
                {"word": "热点A", "url": "https://x/1", "isTop": True},
                {"word": "", "url": "https://x/2"},          # 空标题跳过
                {"word": "热点B"},
            ]}
        ]}]}
    }
    items = parse_baidu(payload)
    assert len(items) == 2
    assert items[0].title == "热点A"
    assert items[0].source == "baidu"
    assert items[0].url.startswith("https://")
    assert items[0].extra.get("isTop") is True


def test_parse_toutiao_normal():
    payload = {"data": [
        {"Title": "热点X", "HotValue": "1234567", "Url": "https://t/1"},
        {"Title": ""},                                        # 空标题跳过
        {"Title": "热点Y", "HotValue": 999},
    ]}
    items = parse_toutiao(payload)
    assert len(items) == 2
    assert items[0].title == "热点X"
    assert items[0].heat == 1234567
    assert items[0].source == "toutiao"


def test_parse_malformed():
    assert parse_baidu(None) == []
    assert parse_baidu({"data": {"cards": []}}) == []
    assert parse_toutiao(None) == []
    assert parse_toutiao({"data": "not-a-list"}) == []


def test_items_to_topics_maps_source_and_weights():
    items = [HotspotItem(title="测试热点", heat=100.0, url="https://x", source="baidu")]
    topics = items_to_topics(items)
    assert len(topics) == 1
    t = topics[0]
    assert t.title == "测试热点"
    assert t.source == "hot"          # 入库 source 标记为 hot
    assert t.note and "baidu" in t.note
    assert t.weights.get("hot") == pytest.approx(1.0)  # heat 100 → 归一 1.0


def test_items_to_topics_heat_cap():
    items = [HotspotItem(title="T", heat=10000.0)]
    t = items_to_topics(items)[0]
    assert t.weights["hot"] == 1.0   # 超过 100 封顶


def test_sync_dedup_and_persist(monkeypatch):
    """同标题热点只入库一次（去重）；无热度时 weights 为空但可打分。"""
    # 用 env 隔离数据目录（storage 内置支持），不污染真实 data/
    tmp = tmp_storage(monkeypatch)
    fetched = {
        "baidu": [
            HotspotItem(title="热点X", heat=50.0, url="u1", source="baidu"),
            HotspotItem(title="热点X", heat=60.0, url="u2", source="baidu"),  # dup
            HotspotItem(title="热点Y", heat=0.0, url="u3", source="baidu"),
        ]
    }
    monkeypatch.setattr("app.hotspots.fetch_all", lambda _sn=None: fetched)
    stats = sync(limit=50)
    assert stats["fetched"] == 3
    assert stats["new"] == 2
    assert stats["dup"] == 1
    rows = get_collection("topics").list()
    assert len(rows) == 2
    titles = {r["title"] for r in rows}
    assert titles == {"热点X", "热点Y"}
    # 入库后自动打分（score 存在）
    for r in rows:
        assert r["source"] == "hot"
        assert "score" in r
    tmp.cleanup()


def test_sync_empty_source_degrades(monkeypatch):
    tmp_storage(monkeypatch)
    monkeypatch.setattr("app.hotspots.fetch_all", lambda _sn=None: {"baidu": []})
    stats = sync()
    assert stats["fetched"] == 0
    assert stats["new"] == 0


def test_import_api_rejects_empty():
    from app.api.hotspots import import_hotspots
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        # 空列表直接 400（无需 DB）
        import asyncio
        asyncio.run(import_hotspots(items=[], dimension=None, monetizer=None))


def tmp_storage(monkeypatch):
    """把 storage 数据目录指到临时目录（隔离，避免污染真实 data/）。"""
    import tempfile

    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("TRAFFICOS_DATA_DIR", tmp.name)
    return tmp
