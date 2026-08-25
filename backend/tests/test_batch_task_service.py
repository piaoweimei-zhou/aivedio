"""BatchTaskService 单元测试：CRUD / 持久化 / dry-run / cancel / retry / DAG 执行

mock 掉 stage 执行与 WS 通知，验证批量编排核心链路（不触发真实 ComfyUI）。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.batch_task_service import BatchTaskService  # noqa: E402


def make_svc(tmp_path):
    return BatchTaskService(batch_dir=str(tmp_path))


async def test_create_and_get_persistence(tmp_path):
    svc = make_svc(tmp_path)
    batch = svc.create(
        "测试批次",
        [
            {"stage_id": "concept", "name": "概念"},
            {"stage_id": "storyboard", "input_from_steps": ["step_1"]},
        ],
        project_id="proj_1",
    )
    assert batch.batch_id.startswith("batch_")
    assert len(batch.steps) == 2

    got = await svc.get(batch.batch_id)
    assert got is not None and got.name == "测试批次"
    # 持久化文件已写入
    files = list(tmp_path.glob("batch_*.json"))
    assert len(files) == 1

    # 新实例从磁盘恢复（running → failed 中断标记不触发，因为状态是 pending）
    svc2 = make_svc(tmp_path)
    got2 = await svc2.get(batch.batch_id)
    assert got2 is not None and len(got2.steps) == 2


async def test_load_marks_running_as_failed(tmp_path):
    svc = make_svc(tmp_path)
    batch = svc.create("x", [{"stage_id": "concept"}])
    # 手动改为 running 并落盘，模拟服务重启前正在执行
    batch.status = "running"
    svc._save_batch(batch)

    svc2 = make_svc(tmp_path)
    got = await svc2.get(batch.batch_id)
    assert got.status == "failed"
    assert "中断" in got.error


async def test_get_dag_and_list(tmp_path):
    svc = make_svc(tmp_path)
    b1 = svc.create("A", [{"stage_id": "concept"}])
    b2 = svc.create("B", [{"stage_id": "storyboard"}], project_id="p")
    await svc.get(b1.batch_id)

    dag = await svc.get_dag(b1.batch_id)
    assert dag is not None and len(dag["nodes"]) == 1

    listed = await svc.list_batches(project_id="p")
    ids = [b.batch_id for b in listed]
    assert b2.batch_id in ids and b1.batch_id not in ids


async def test_delete(tmp_path):
    svc = make_svc(tmp_path)
    batch = svc.create("x", [{"stage_id": "concept"}])
    assert await svc.delete(batch.batch_id) is True
    assert (tmp_path / f"{batch.batch_id}.json").exists() is False
    assert await svc.delete(batch.batch_id) is False

    # running 批次不可删除
    b2 = svc.create("y", [{"stage_id": "concept"}])
    b2.status = "running"
    assert await svc.delete(b2.batch_id) is False


async def test_dry_run_passes(tmp_path):
    svc = make_svc(tmp_path)
    batch = svc.create(
        "x",
        [
            {"stage_id": "concept"},
            {"stage_id": "storyboard", "input_from_steps": ["step_1"]},
        ],
    )
    ok = await svc.start(batch.batch_id, dry_run=True)
    assert ok is True


async def test_dry_run_fails_on_unknown_stage(tmp_path):
    svc = make_svc(tmp_path)
    batch = svc.create("x", [{"stage_id": "no_such_stage_xyz"}])
    # 未知 stage → 资产类型校验报"未知阶段"
    ok = await svc.start(batch.batch_id, dry_run=True)
    assert ok is False
    got = await svc.get(batch.batch_id)
    assert "no_such_stage_xyz" in (got.error or "")


async def test_start_missing_batch(tmp_path):
    svc = make_svc(tmp_path)
    assert await svc.start("batch_nope") is False
    assert await svc.cancel("batch_nope") is False
    assert await svc.retry("batch_nope") is False


async def test_cancel_running_and_retry_reset(tmp_path):
    svc = make_svc(tmp_path)

    batch = svc.create(
        "x",
        [
            {"stage_id": "concept"},
            {"stage_id": "storyboard", "input_from_steps": ["step_1"]},
        ],
    )
    # 伪造 running 状态 + 假运行任务，验证 cancel/retry 逻辑不触发真实执行
    batch.status = "running"
    svc._save_batch(batch)

    cancelled = await svc.cancel(batch.batch_id)
    assert cancelled is True
    got = await svc.get(batch.batch_id)
    assert got.status == "cancelled"

    # retry from step_2：step_1 保持原状态，step_2 重置为 pending
    ok = await svc.retry(batch.batch_id, from_step="step_2")
    assert ok is True
    # start() 会立即把 status → running；断言 reset 效果（current_step_index）
    got2 = await svc.get(batch.batch_id)
    assert got2.current_step_index == 1


async def test_dag_execution_success(tmp_path, monkeypatch):
    """DAG 执行：mock stage_service.execute，验证步骤状态推进与完成通知"""
    from services import ws_service
    from services.stage_service import AssetProduceResult

    svc = make_svc(tmp_path)
    batch = svc.create(
        "x",
        [
            {"stage_id": "concept"},
            {"stage_id": "storyboard", "input_from_steps": ["step_1"]},
        ],
    )

    calls = []

    import services.batch_task_service as bts

    async def fake_execute(stage_id, input_asset_ids, provider_id="", params=None):
        calls.append((stage_id, list(input_asset_ids)))
        asset = type("A", (), {"asset_id": f"asset_{stage_id}"})()
        return AssetProduceResult(asset=asset, success=True)

    class _FakeStageSvc:
        async def execute(self2, stage_id, input_asset_ids, provider_id="", params=None):
            return await fake_execute(stage_id, input_asset_ids, provider_id, params)

    # ⚠️ 必须 mock batch_task_service 命名空间里的 get_stage_service：
    # _run_batch_dag_impl 直接调用模块级名字（L34 from import 已绑定），
    # mock ss(stage_service) 模块属性不影响 batch_task_service 已绑定的引用。
    monkeypatch.setattr(
        bts, "get_stage_service", lambda: _FakeStageSvc()
    )
    notified = []

    async def _noop(*a, **k):
        return None

    async def _noop_completed(*a, **k):
        notified.append(a)

    async def _noop_batch_done(*a, **k):
        notified.append(("batch_done",) + a)

    # 注意：代码用 await 调用这些 notify，mock 必须返回 coroutine（async def），
    # 不能是普通 lambda（await None 会 TypeError 导致后台 DAG 任务失败）
    monkeypatch.setattr(ws_service, "notify_batch_started", _noop)
    monkeypatch.setattr(ws_service, "notify_step_started", _noop)
    monkeypatch.setattr(ws_service, "notify_step_failed", _noop, raising=False)
    monkeypatch.setattr(ws_service, "notify_step_completed", _noop_completed)
    monkeypatch.setattr(ws_service, "notify_batch_completed", _noop_batch_done)
    monkeypatch.setattr(ws_service, "notify_batch_failed", _noop, raising=False)

    ok = await svc.start(batch.batch_id)
    assert ok is True
    # start() 是 fire-and-forget：先断言 running，再等 DAG 跑完（mock execute 很快）
    got = await svc.get(batch.batch_id)
    assert got.status in ("running", "completed")

    for _ in range(100):
        g = await svc.get(batch.batch_id)
        if g.status == "completed":
            break
        await asyncio.sleep(0.2)
    got = await svc.get(batch.batch_id)
    assert got.status == "completed", f"status={got.status} err={got.error}"
    steps = {s.step_id: s for s in got.steps}
    assert steps["step_1"].status == "completed"
    assert steps["step_2"].status == "completed"
    # 步骤间资产传递：step_2 收到 step_1 的输出资产 ID
    assert calls[0][0] == "concept"
    assert calls[1][0] == "storyboard"
    assert calls[1][1] == ["asset_concept"]
