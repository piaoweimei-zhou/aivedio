"""缺陷修复批次回归：/contract/tasks 列表 + /start 状态守卫 + auto_retry 注入。"""
import os
import sys
import types
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.contract_api as ca  # noqa: E402
from api.contract_api import (  # noqa: E402
    ContentSpec,
    StartResponse,
    TasksResponse,
    start_produce,
    list_tasks,
)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class FakeBatch:
    def __init__(self, batch_id, status="pending", platform="", account="",
                 error=None, content_id=None, progress=0, steps=None,
                 current_step_index=-1, created_at=None):
        self.batch_id = batch_id
        self.status = status
        self.metadata = {
            "platform": platform, "account_id": account,
            "content_id": content_id, "dimension": "soft_ad", "monetizer": "tool",
            "auto_retry": 1, "auto_retry_left": 1,
        }
        self.progress = progress
        self.steps = steps or []
        self.current_step_index = current_step_index
        self.error = error
        self.created_at = created_at or datetime.now()


class FakeSvc:
    def __init__(self, batches):
        self._b = {b.batch_id: b for b in batches}
        self.started = []

    async def get(self, task_id):
        return self._b.get(task_id)

    async def list_batches(self):
        return list(self._b.values())

    async def start(self, task_id):
        self.started.append(task_id)
        return True


def _patch_svc(monkeypatch, svc):
    monkeypatch.setattr(ca, "get_batch_task_service", lambda: svc)


# ---------- /start 状态守卫 ----------

def test_start_rejects_failed(monkeypatch):
    """failed 任务不可启动（仅 pending 可 start）。"""
    svc = FakeSvc([FakeBatch("b1", status="failed")])
    _patch_svc(monkeypatch, svc)
    resp = _run(start_produce("b1"))
    assert isinstance(resp, StartResponse)
    assert resp.started is False
    assert "only pending can start" in resp.message
    assert svc.started == []


def test_start_rejects_done(monkeypatch):
    """done 终态不可启动。"""
    svc = FakeSvc([FakeBatch("b2", status="completed")])
    _patch_svc(monkeypatch, svc)
    resp = _run(start_produce("b2"))
    assert resp.started is False
    assert svc.started == []


def test_start_accepts_pending(monkeypatch):
    """pending 任务可启动，透传给 svc.start。"""
    svc = FakeSvc([FakeBatch("b3", status="pending")])
    _patch_svc(monkeypatch, svc)
    resp = _run(start_produce("b3"))
    assert resp.started is True
    assert svc.started == ["b3"]


def test_start_returns_404(monkeypatch):
    """不存在的任务 → HTTPException 404。"""
    import fastapi
    svc = FakeSvc([])
    _patch_svc(monkeypatch, svc)
    try:
        _run(start_produce("ghost"))
        assert False, "应抛 404"
    except fastapi.HTTPException as e:
        assert e.status_code == 404


# ---------- /contract/tasks 列表与过滤 ----------

def _tasks_svc():
    return FakeSvc([
        FakeBatch("b_done", status="completed", platform="douyin", account="a1",
                  content_id="c1", progress=100,
                  steps=[types.SimpleNamespace(stage_id="export")], current_step_index=0),
        FakeBatch("b_run", status="running", platform="bilibili", account="a2",
                  content_id="c2", progress=50),
        FakeBatch("b_fail", status="failed", platform="douyin", account="a1",
                  content_id="c3", error="concept timeout"),
    ])


def test_list_tasks_returns_all(monkeypatch):
    svc = _tasks_svc()
    _patch_svc(monkeypatch, svc)
    resp = _run(list_tasks(limit=50))
    assert isinstance(resp, TasksResponse)
    assert resp.total == 3
    ids = {t.task_id for t in resp.tasks}
    assert ids == {"b_done", "b_run", "b_fail"}


def test_list_tasks_filter_status(monkeypatch):
    svc = _tasks_svc()
    _patch_svc(monkeypatch, svc)
    resp = _run(list_tasks(status="running", limit=50))
    assert resp.total == 1
    assert resp.tasks[0].task_id == "b_run"


def test_list_tasks_filter_platform_and_account(monkeypatch):
    svc = _tasks_svc()
    _patch_svc(monkeypatch, svc)
    resp = _run(list_tasks(platform="douyin", account_id="a1", limit=50))
    assert resp.total == 2
    assert {t.task_id for t in resp.tasks} == {"b_done", "b_fail"}


def test_list_tasks_exposes_error_and_progress(monkeypatch):
    svc = _tasks_svc()
    _patch_svc(monkeypatch, svc)
    resp = _run(list_tasks(status="failed", limit=50))
    t = resp.tasks[0]
    assert t.error == "concept timeout"
    done = _run(list_tasks(status="done", limit=50))
    assert done.tasks[0].progress == 1.0


def test_list_tasks_limit(monkeypatch):
    svc = _tasks_svc()
    _patch_svc(monkeypatch, svc)
    resp = _run(list_tasks(limit=1))
    assert len(resp.tasks) == 1
    assert resp.total == 1


# ---------- auto_retry 注入（produce metadata） ----------

def test_produce_metadata_injects_auto_retry(monkeypatch):
    """produce 时 metadata 应含 auto_retry 默认值与 platform。"""
    captured = {}

    class CreateSvc(FakeSvc):
        def create(self, name, steps, metadata=None):
            captured["metadata"] = metadata
            return FakeBatch("b_retry", status="pending")

    svc = CreateSvc([])
    _patch_svc(monkeypatch, svc)
    spec = ContentSpec(
        content_id="c_retry",
        script={"type": "video_script_mixin",
                "acts": [{"narration": "x", "duration_s": 5}]},
        params={"platform": "douyin"},
    )
    resp = _run(ca.produce(spec))
    m = captured["metadata"]
    assert m["auto_retry"] == 1
    assert m["auto_retry_left"] == 1
    assert m["platform"] == "douyin"
    # auto_start=False → status=created（不再误导为 queued）
    assert resp.status == "created"
