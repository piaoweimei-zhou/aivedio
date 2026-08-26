"""B8 工具传感器测试：tool-event 埋点 + keyword 提取 + suggest-topics + SDK 端到端"""
import os
import sys
import tempfile
import threading

import pytest
from fastapi.testclient import TestClient

_tmp = tempfile.mkdtemp(prefix="trafficos_test_b8_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402
from app.api.signals import _extract_keyword  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolated():
    os.environ["TRAFFICOS_DATA_DIR"] = _tmp
    yield
    from app.storage import _store
    for key, col in list(_store.items()):
        if key[1] == _tmp:
            col.clear()


# ---------- keyword 提取 ----------

def test_extract_keyword():
    assert _extract_keyword("某明星演唱会视频") == "某明星演唱会"
    assert _extract_keyword("") == "general"
    assert _extract_keyword("去水印 剪辑教程") == "剪辑"
    # 工具显式传 keyword 时优先
    assert _extract_keyword("剪辑", fallback="剪辑") == "剪辑"


# ---------- tool-event 埋点 ----------

def test_tool_event_to_signal():
    r = client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "watermark-remover",
        "action": "download",
        "title": "明星采访视频",
        "url": "https://example.com/v1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "tool:watermark-remover"
    assert body["heat"] == 1.0  # download
    # 标题去噪提取关键词
    assert "明星采访" in body["keyword"]


def test_tool_event_action_heat():
    # save 加权 1.2，analyze 0.8
    r = client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "save", "title": "某学习资料", "keyword": "学习"
    })
    assert r.json()["heat"] == 1.2
    r = client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "analyze", "title": "某内容", "keyword": "内容"
    })
    assert r.json()["heat"] == 0.8


def test_tool_event_aggregates_to_top_keywords():
    for i in range(3):
        client.post("/api/traffic/signals/tool-event", json={
            "tool_name": "t", "action": "download", "keyword": "跳舞",
        })
    tk = client.get("/api/traffic/signals/top-keywords").json()
    assert tk["top"][0]["keyword"] == "跳舞"
    assert tk["top"][0]["count"] == 3
    assert tk["top"][0]["heat"] == pytest.approx(3.0)


# ---------- suggest-topics ----------

def test_suggest_topics():
    # 灌信号：学习类 + 工具类
    client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "save", "keyword": "学剪辑",
    })
    client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "save", "keyword": "学剪辑",
    })
    client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "download", "keyword": "去水印",
    })
    r = client.get("/api/traffic/signals/suggest-topics").json()
    assert len(r["suggestions"]) >= 2
    first = r["suggestions"][0]
    assert first["keyword"] == "学剪辑"  # 热度最高
    assert first["score"] > 0
    assert first["dimension"] in ("knowledge", "pure_content", "soft_ad")
    # 不 save 时不入库
    assert client.get("/api/traffic/topics").json() == []


def test_suggest_topics_save():
    client.post("/api/traffic/signals/tool-event", json={
        "tool_name": "t", "action": "download", "keyword": "去水印",
    })
    r = client.get("/api/traffic/signals/suggest-topics", params={"save": "true"}).json()
    assert r["saved"] is True
    topics = client.get("/api/traffic/topics").json()
    assert len(topics) >= 1
    # 入库选题 source=signal
    assert topics[0]["source"] == "signal"


# ---------- SDK 端到端 ----------

def test_sdk_end_to_end():
    """SDK → 本地 FastAPI → 信号聚合，全链路。"""
    from sdk.tool_tracker import ToolTracker
    # SDK 需要指向运行中的服务：用线程起 uvicorn
    import uvicorn

    port = 8123
    server_cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(server_cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        import time
        for _ in range(50):
            if server.started:
                break
            time.sleep(0.1)
        tracker = ToolTracker(f"http://127.0.0.1:{port}", "watermark-remover")
        assert tracker.track(action="download", title="美食教程视频", url="u") is True
        assert tracker.track(action="save", keyword="做菜") is True
        assert tracker.track_many([
            {"action": "download", "keyword": "做菜"},
            {"action": "download", "keyword": "做菜"},
        ]) == 2
        # 服务器侧聚合
        tk = client.get("/api/traffic/signals/top-keywords").json()
        kws = {k["keyword"]: k for k in tk["top"]}
        assert kws["做菜"]["count"] == 3
    finally:
        server.should_exit = True
        t.join(timeout=5)


def test_sdk_failure_graceful():
    """SDK 对不可达服务不抛异常。"""
    from sdk.tool_tracker import ToolTracker
    tracker = ToolTracker("http://127.0.0.1:1", "t")  # 端口 1 不可达
    assert tracker.track(action="download", title="x") is False
    assert tracker.track_many([{"action": "download"}]) == 0
