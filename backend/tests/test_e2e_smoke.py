"""端到端冒烟测试：提交任务 → 阶段执行 → 资产生成 → 前端数据接口

用 httpx.AsyncClient + ASGITransport 驱动真实 FastAPI 应用（与测试共用事件循环，
后台 asyncio 任务可被调度），覆盖单测未覆盖的完整链路：
1. POST /api/director/stages/execute（异步提交）→ 202 + task_id
2. GET /api/director/stages/task/{task_id}（轮询）→ completed + 资产
3. GET /api/director/assets（资产列表，前端素材库数据源）
4. GET /output/...（生成文件可访问，前端展示数据源）

使用本地 SmokeStage 模拟真实阶段（写真实文件 + 注册资产），
不依赖 ComfyUI / 云端供应商，CI 离线可跑。
"""

import asyncio
import os
import time
import uuid

import httpx
import pytest
from httpx import ASGITransport

from services.asset_service import AssetProduceResult
from services.stage_service import StageDef, StagePlugin

# 冒烟阶段生成的文件（用于测试后清理）
_CREATED_FILES: list = []


class SmokeStage(StagePlugin):
    """冒烟测试阶段：模拟真实阶段，生成本地文件并注册资产"""

    stage_def = StageDef(
        stage_id="smoke",
        name="冒烟测试阶段",
        input_types=[],
        output_type="concept",
        default_provider="local",
        supported_providers=["local"],
    )

    async def execute(self, input_assets, provider_id="", params=None):
        from services.providers.provider_utils import output_path_for, output_url_for

        params = params or {}
        delay = float(params.get("delay", 0))
        if delay:
            await asyncio.sleep(delay)

        fname = f"smoke_{uuid.uuid4().hex[:8]}.png"
        fpath = output_path_for(fname, "output")
        with open(fpath, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        _CREATED_FILES.append(fpath)

        asset_svc, _ = self._get_services()
        asset = await self._register_asset_direct(
            asset_svc,
            asset_type="concept",
            name="冒烟测试产物",
            urls=[output_url_for(fname, "output")],
            content_type="scene",
        )
        return AssetProduceResult(asset=asset, success=True)


@pytest.fixture
async def client(tmp_path):
    """隔离全局单例 + 注册冒烟阶段 + 返回 AsyncClient"""
    import services.asset_service as asset_mod
    import services.gen_task_manager as gtm_mod
    import services.stage_service as ss_mod

    asset_mod._instance = asset_mod.AssetService(storage_dir=str(tmp_path / "assets"))
    gtm_mod._instance = gtm_mod.GenTaskManager(
        persist_dir=str(tmp_path / "tasks"),
        task_timeout=30,
    )
    ss_mod.reset_stage_service()
    svc = ss_mod.get_stage_service()
    svc.register(SmokeStage())

    from main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # 清理冒烟阶段生成的文件
    for fpath in _CREATED_FILES:
        try:
            os.remove(fpath)
        except OSError:
            pass
    _CREATED_FILES.clear()
    # 还原单例，避免影响其他测试模块
    asset_mod._instance = None
    gtm_mod._instance = None
    ss_mod.reset_stage_service()


async def _poll_task(client, task_id, timeout=10.0):
    """轮询任务直到终态（completed/failed/cancelled）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"/api/director/stages/task/{task_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 轮询超时")


async def test_async_task_full_chain(client):
    """核心冒烟：异步提交 → 轮询完成 → 资产入库 → 文件可访问"""
    resp = await client.post(
        "/api/director/stages/execute",
        json={"stage_id": "smoke", "input_asset_ids": [], "params": {}},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    task_id = body["task_id"]
    assert body["status"] == "running"

    data = await _poll_task(client, task_id)
    assert data["status"] == "completed"
    assert data["success"] is True
    assert data["elapsed_ms"] >= 0

    asset = data["asset"]
    assert asset["asset_id"]
    assert asset["asset_type"] == "concept"
    assert asset["content_type"] == "scene"
    assert asset["urls"]

    # 资产入库：前端素材库数据源
    resp = await client.get("/api/director/assets")
    assert resp.status_code == 200
    assets = resp.json()["assets"]
    assert any(a["asset_id"] == asset["asset_id"] for a in assets)

    # 生成文件可访问：前端展示数据源
    url = asset["urls"][0]
    resp = await client.get(url)
    assert resp.status_code == 200
    assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"


async def test_sync_execute_returns_asset(client):
    """同步模式：async_mode=False 直接返回资产"""
    resp = await client.post(
        "/api/director/stages/execute",
        json={
            "stage_id": "smoke",
            "input_asset_ids": [],
            "params": {},
            "async_mode": False,
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["asset"]["asset_type"] == "concept"
    assert data["asset"]["urls"]


async def test_unknown_stage_marks_task_failed(client):
    """未知阶段：任务创建后标记为 failed，错误信息可查询"""
    resp = await client.post(
        "/api/director/stages/execute",
        json={"stage_id": "no_such_stage", "input_asset_ids": [], "params": {}},
    )
    assert resp.status_code == 202, resp.text
    task_id = resp.json()["task_id"]

    data = await _poll_task(client, task_id)
    assert data["status"] == "failed"
    assert "未知阶段" in data["error"]


async def test_param_validation_returns_422(client):
    """concept 阶段缺少必填 prompt → 422"""
    resp = await client.post(
        "/api/director/stages/execute",
        json={"stage_id": "concept", "input_asset_ids": [], "params": {}},
    )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    assert any("缺少必填参数" in e for e in errors)


async def test_task_not_found_returns_404(client):
    """查询不存在的任务 → 404"""
    resp = await client.get("/api/director/stages/task/nonexistent")
    assert resp.status_code == 404


async def test_cancel_running_task(client):
    """取消运行中的任务 → cancelled（不覆盖完成结果）"""
    resp = await client.post(
        "/api/director/stages/execute",
        json={"stage_id": "smoke", "input_asset_ids": [], "params": {"delay": 5}},
    )
    assert resp.status_code == 202
    task_id = resp.json()["task_id"]

    resp = await client.post(f"/api/director/stages/task/{task_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    data = await _poll_task(client, task_id)
    assert data["status"] == "cancelled"
