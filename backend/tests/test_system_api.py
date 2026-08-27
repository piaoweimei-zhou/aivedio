"""/api/system/status 聚合端点测试（问题4 透明化）。

mock BatchTaskService + ComfyUI 探测 + git 降级，验证：
- 任务按状态聚合计数 + 最近 5 条
- ComfyUI 探测带 30s TTL 缓存（第二次调用不重探测）
- 门禁基线表返回
- git 不可用时优雅降级（error 字段，不抛）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import api.system_api as system_api  # noqa: E402


class FakeBatch:
    def __init__(self, batch_id, status, progress=100.0, meta=None,
                 error=None, created_at="2026-08-27T10:00:00"):
        self.batch_id = batch_id
        self.status = status
        self.progress = progress
        self.metadata = meta or {}
        self.error = error
        self.created_at = created_at


class FakeService:
    def __init__(self, batches):
        self._batches = batches

    async def list_batches(self):
        return self._batches


@pytest.fixture
def client(monkeypatch):
    batches = [
        FakeBatch("b1", "completed", meta={"platform": "douyin", "dimension": "soft_ad"}),
        FakeBatch("b2", "running", progress=50.0,
                  meta={"platform": "bilibili", "dimension": "knowledge"}),
        FakeBatch("b3", "failed", error="ComfyUI timeout",
                  meta={"platform": "douyin"}),
        FakeBatch("b4", "completed"),
        FakeBatch("b5", "queued", progress=0.0),
        FakeBatch("b6", "completed"),  # 超过 recent=5，不应出现在 recent
    ]
    monkeypatch.setattr(system_api, "get_batch_task_service",
                        lambda: FakeService(batches))
    from main import app  # backend 根在 sys.path，复用真实 app 挂载
    return TestClient(app)


def test_status_task_aggregation(client):
    r = client.get("/api/system/status")
    assert r.status_code == 200
    body = r.json()
    tasks = body["tasks"]
    assert tasks["total"] == 6
    assert tasks["by_status"] == {"completed": 3, "running": 1, "failed": 1, "queued": 1}
    assert len(tasks["recent"]) == 5  # 只取最近 5
    assert tasks["recent"][0]["task_id"] == "b1"
    assert tasks["recent"][2]["error"] == "ComfyUI timeout"
    assert tasks["recent"][2]["progress"] == 1.0
    assert tasks["recent"][1]["progress"] == 0.5


def test_status_gates(monkeypatch, client):
    r = client.get("/api/system/status")
    body = r.json()
    assert body["gates"]["backend_coverage"] == 40
    assert body["gates"]["creativeos_coverage"] == 90
    assert body["service"] == "director"


def test_comfy_probe_cached(monkeypatch, client):
    """探测结果 30s 内缓存：第二次 /status 不重复探测。"""
    calls = []

    async def fake_alive(force=False):
        calls.append(force)
        return True

    monkeypatch.setattr(system_api, "_comfy_alive", fake_alive)
    system_api._comfy_cache.update(ts=0.0, ok=False)  # 重置缓存
    client.get("/api/system/status")
    client.get("/api/system/status?refresh=true")
    # 第一次默认 force=False；refresh=true 时 force=True
    assert calls == [False, True]


def test_git_degrade(monkeypatch, client):
    def boom(*a, **kw):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(system_api.subprocess, "run", boom)
    r = client.get("/api/system/status")
    body = r.json()
    assert body["git"]["head"] is None
    assert "error" in body["git"]
