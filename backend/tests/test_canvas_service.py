"""CanvasService / canvas_api 单元测试：CRUD、乐观锁冲突、节点/连线管理、MSR 任务

mock 掉 ComfyUI，验证画布服务与 API 层核心链路。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _fresh_svc(tmp_path):
    """独立 CanvasService 实例（隔离数据目录，避免污染单例）"""
    from services.canvas_service import CanvasService  # noqa: F401

    svc = CanvasService.__new__(CanvasService)
    import asyncio as aio

    svc._canvases = {}
    svc._lock = aio.Lock()
    svc._debounce_timers = {}
    svc._debounce_broadcast_timers = {}
    os.makedirs(str(tmp_path), exist_ok=True)
    # 重定向数据目录
    import services.canvas_service as cs

    orig = cs.CANVAS_DATA_DIR
    cs.CANVAS_DATA_DIR = str(tmp_path)
    return svc, orig


def _restore_svc(orig):
    import services.canvas_service as cs

    cs.CANVAS_DATA_DIR = orig


async def test_create_and_get_layout(tmp_path):
    from services.canvas_service import CanvasLayout, CanvasNode, CanvasViewport  # noqa: F401

    svc, orig = _fresh_svc(tmp_path)
    try:
        layout = await svc.create("测试画布")
        assert layout.canvas_id.startswith("canvas_")
        got = svc.get(layout.canvas_id)
        assert got is not None and got.name == "测试画布"

        listed = svc.list_canvases()
        assert any(c["canvas_id"] == layout.canvas_id for c in listed)
    finally:
        _restore_svc(orig)


async def test_update_layout_nodes_edges_viewport(tmp_path):
    svc, orig = _fresh_svc(tmp_path)
    try:
        layout = await svc.create("x")
        cid = layout.canvas_id
        result = await svc.update_layout(
            cid,
            {
                "nodes": [
                    {"node_id": "n1", "asset_id": "a1", "x": 10, "y": 20},
                    {"node_id": "n2", "custom_field": "v"},
                ],
                "edges": [{"edge_id": "e1", "source_id": "n1", "target_id": "n2"}],
                "viewport": {"x": 5, "y": 6, "zoom": 1.5},
            },
        )
        assert len(result.nodes) == 2
        # 未知字段落入 metadata
        assert result.nodes[1].metadata.get("custom_field") == "v"
        assert len(result.edges) == 1
        assert result.viewport.zoom == 1.5

        got = svc.get(cid)
        d = got.to_dict()
        assert d["nodes"][0]["id"] == "n1"  # canvas.js 别名
    finally:
        _restore_svc(orig)


async def test_update_layout_conflict(tmp_path):
    svc, orig = _fresh_svc(tmp_path)
    try:
        layout = await svc.create("x")
        cid = layout.canvas_id
        old_ts = layout.updated_at

        # 强制拉开时间差（模拟客户端拿到旧版本后服务端已更新）
        layout.updated_at = old_ts + 5.0
        res = await svc.update_layout(cid, {"name": "y", "base_updated_at": old_ts})
        assert isinstance(res, dict) and res.get("_conflict") is True
    finally:
        _restore_svc(orig)


async def test_add_update_remove_node(tmp_path):
    svc, orig = _fresh_svc(tmp_path)
    try:
        layout = await svc.create("x")
        cid = layout.canvas_id

        node = await svc.add_node(cid, {"node_id": "n1", "label": "A"})
        assert node is not None and node.node_id == "n1"

        # update：metadata 深度合并 + 属性更新
        updated = await svc.update_node(cid, "n1", {"x": 99.0, "metadata": {"k": "v"}})
        assert updated.x == 99.0 and updated.metadata.get("k") == "v"

        # 未找到节点
        miss = await svc.update_node(cid, "nope", {"x": 1})
        assert miss is None

        # remove（连带删除相关连线）
        await svc.add_edge(cid, {"edge_id": "e1", "source_id": "n1", "target_id": "n2"})
        ok = await svc.remove_node(cid, "n1")
        assert ok is True
        got = svc.get(cid)
        assert all(n.node_id != "n1" for n in got.nodes)
        assert len(got.edges) == 0

        # remove_edge 对不存在的 edge_id 也返回 True（幂等删除）
        assert await svc.remove_edge(cid, "nope") is True
    finally:
        _restore_svc(orig)


async def test_delete_canvas(tmp_path):
    svc, orig = _fresh_svc(tmp_path)
    try:
        layout = await svc.create("x")
        cid = layout.canvas_id
        assert await svc.delete(cid) is True
        assert svc.get(cid) is None
        assert (tmp_path / f"{cid}.json").exists() is False
        # 重复删除
        assert await svc.delete(cid) is False
    finally:
        _restore_svc(orig)


# ============================================================
# API 层（TestClient）
# ============================================================


def _api_client(tmp_path, monkeypatch):
    import services.canvas_service as cs

    # 让单例指向隔离目录
    orig = cs.CANVAS_DATA_DIR
    cs.CANVAS_DATA_DIR = str(tmp_path)
    cs.reset_canvas_service()

    from api.canvas_api import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    return client, lambda: (setattr(cs, "CANVAS_DATA_DIR", orig), cs.reset_canvas_service())


def test_api_canvas_crud(tmp_path):
    client, cleanup = _api_client(tmp_path, None)
    try:
        # create
        r = client.post("/api/canvas/", json={"name": "API画布"})
        assert r.status_code == 200 and r.json()["success"]
        cid = r.json()["canvas"]["canvas_id"]

        # list
        r = client.get("/api/canvas/")
        ids = [c["canvas_id"] for c in r.json()["canvases"]]
        assert cid in ids

        # get with aliases (connections / title)
        r = client.get(f"/api/canvas/{cid}")
        body = r.json()
        assert body["success"] and body["canvas"]["title"] == "API画布"
        assert "connections" in body["canvas"]

        # add node + update node via API
        r = client.post(
            f"/api/canvas/{cid}/nodes", json={"node_id": "n1", "label": "A"}
        )
        assert r.status_code == 200 and r.json()["success"]

        r = client.put(f"/api/canvas/{cid}/nodes/n1", json={"x": 42.0})
        assert r.status_code == 200

        # delete node / canvas
        r = client.delete(f"/api/canvas/{cid}/nodes/n1")
        assert r.json()["success"] is True

        r = client.delete(f"/api/canvas/{cid}")
        assert r.json()["success"] is True

        # 404 after delete
        r = client.get(f"/api/canvas/{cid}")
        assert r.status_code == 404
    finally:
        cleanup()


def test_api_msr_video_submit_and_poll(tmp_path, monkeypatch):
    # MSR workflow file must exist (use real repo workflows dir)
    from api.canvas_api import _WF_DIR

    wf = os.path.join(_WF_DIR, "LTX-2.3_MSR_sample_workflow_V2.json")
    if not os.path.exists(wf):
        import pytest as _pt

        _pt.skip("MSR workflow JSON 不存在（未随仓库提供）")

    client, cleanup = _api_client(tmp_path, monkeypatch)
    try:
        # mock comfyui service
        import services.comfyui_service as cvs

        class FakeComfy:
            config = type("C", (), {"output_dir": str(tmp_path), "comfyui_dir": str(tmp_path)})()

            async def _check_alive(self2):
                return True

            async def _queue_prompt_with_retry(self2, wf_data):
                return "prompt_1"

            async def _wait_for_completion(self2, prompt_id, task_type="generate"):
                # 预置输出文件让 persistence path hit GENERATED_DIR check
                from services.paths import GENERATED_DIR

                os.makedirs(GENERATED_DIR, exist_ok=True)
                fn = "msr_test_output.mp4"
                with open(os.path.join(GENERATED_DIR, fn), "wb") as f:
                    f.write(b"x")
                return [fn]

        monkeypatch.setattr(
            cvs, "get_comfyui_service", lambda *a, **k: FakeComfy()
        )

        # asset service mock（避免 real DB）
        import services.asset_service as aas

        class FakeAssetSvc:
            async def create(self2, **kw):
                return type("A", (), {"asset_id": "asset_msr_1"})()

        monkeypatch.setattr(aas, "get_asset_service", lambda *a, **k: FakeAssetSvc())

        r = client.post(
            "/api/canvas/msr-video",
            json={
                "ref1_image_url": "http://x/a.png",
                "global_prompt": "测试",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True and body["task_id"].startswith("msr_")

        # poll until succeeded (async task)
        tid = body["task_id"]
        status = None
        for _ in range(60):
            pr = client.get(f"/api/canvas/msr-video/{tid}")
            assert pr.status_code == 200
            status = pr.json()["status"]
            if status in ("succeeded", "failed"):
                break
            import time as t

            t.sleep(0.1)
        assert status == "succeeded", f"final={pr.text}"
        assert pr.json()["result"]["videos"][0]["url"].startswith("/api/comfyui/image")
    finally:
        cleanup()


def test_api_msr_task_not_found(tmp_path):
    client, cleanup = _api_client(tmp_path, None)
    try:
        r = client.get("/api/canvas/msr-video/nope")
        assert r.status_code == 404
    finally:
        cleanup()
