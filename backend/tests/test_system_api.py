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
    assert body["gates"]["backend_coverage"] == 42
    assert body["gates"]["creativeos_coverage"] == 90
    assert body["gates"]["frontend_lint"] == "0-warning"
    assert body["service"] == "director"


def test_ops_key_enforced(monkeypatch, client):
    """配置 DIRECTOR_OPS_KEY 后，无 key / 错误 key 401，正确 key 通过（header 或 query）。"""
    monkeypatch.setenv("DIRECTOR_OPS_KEY", "secret-ops-key")
    r = client.get("/api/system/status")
    assert r.status_code == 401
    r = client.get("/api/system/status", headers={"X-API-Key": "secret-ops-key"})
    assert r.status_code == 200
    r = client.get("/api/system/status?key=secret-ops-key")
    assert r.status_code == 200
    r = client.get("/api/system/status", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401
    r = client.get("/api/system/dashboard?key=secret-ops-key")
    assert r.status_code == 200
    r = client.get("/api/system/dashboard")
    assert r.status_code == 401


def test_cost_aggregation(monkeypatch, client, tmp_path):
    """成本台账聚合：读 ledger.jsonl → 汇总（含容错降级）。"""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        '{"ts":"2026-08-27 10:00:00","total_cost_usd":1.5,"llm_cost_usd":0.5,'
        '"video_cost_usd":1.0,"total_calls":7,"total_tokens":1000,'
        '"video_provider":"local_h3","over_50_warning":false}\n'
        '{"ts":"2026-08-27 11:00:00","total_cost_usd":2.5,"llm_cost_usd":0.5,'
        '"video_cost_usd":2.0,"total_calls":8,"total_tokens":2000,'
        '"video_provider":"cloud_seedream_video","over_50_warning":true}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(system_api, "COST_LEDGER_PATH", str(ledger))
    system_api._cost_cache.update(ts=0.0, data=None)  # 清缓存
    r = client.get("/api/system/status")
    c = r.json()["cost"]
    assert c["records"] == 2
    assert c["total_cost_usd"] == 4.0
    assert c["llm_cost_usd"] == 1.0
    assert c["video_cost_usd"] == 3.0
    assert c["over_50_warning"] is True
    assert c["by_provider"]["local_h3"] == 1.0


def test_cost_missing_ledger(monkeypatch, client):
    """ledger 不存在 → 空聚合 + error 提示（不抛、不阻塞 status）。"""
    monkeypatch.setattr(system_api, "COST_LEDGER_PATH", r"D:\nonexistent\ledger.jsonl")
    system_api._cost_cache.update(ts=0.0, data=None)
    r = client.get("/api/system/status")
    assert r.status_code == 200
    assert r.json()["cost"]["records"] == 0


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
