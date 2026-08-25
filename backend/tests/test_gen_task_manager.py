"""gen_task_manager 单元测试：任务生命周期 / 持久化 / TTL 清理 / 取消 / 超时"""

import asyncio
import time

import pytest

from services.gen_task_manager import GenTask, GenTaskManager


@pytest.fixture
def manager(tmp_path):
    return GenTaskManager(persist_dir=str(tmp_path), task_timeout=0)


async def _wait_status(m, task_id, statuses, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = m.get_task(task_id)
        if task and task.status in statuses:
            return task
        await asyncio.sleep(0.02)
    return m.get_task(task_id)


async def test_create_task(manager):
    async def work():
        return "ok"

    task = await manager.create_task(stage_id="concept", execute_fn=work)
    assert task.task_id
    assert task.status == "pending"
    assert task.stage_id == "concept"
    assert (manager._persist_dir / f"{task.task_id}.json").exists()


async def test_submit_and_complete(manager):
    async def work():
        return {"url": "http://x/1.png"}

    task = await manager.create_task(execute_fn=work)
    await manager.submit_task(task.task_id)
    got = await _wait_status(manager, task.task_id, ("completed", "failed"))
    assert got.status == "completed"
    assert got.result == {"url": "http://x/1.png"}
    assert got.progress == 100.0
    assert got.elapsed_ms >= 0


async def test_execute_failure(manager):
    async def boom():
        raise RuntimeError("生成失败")

    task = await manager.create_task(execute_fn=boom)
    await manager.submit_task(task.task_id)
    got = await _wait_status(manager, task.task_id, ("completed", "failed"))
    assert got.status == "failed"
    assert "生成失败" in got.error


async def test_cancel_pending(manager):
    async def work():
        return "x"

    task = await manager.create_task(execute_fn=work)
    ok = await manager.cancel_task(task.task_id)
    assert ok
    assert manager.get_task(task.task_id).status == "cancelled"


async def test_cancel_running_not_overwritten(manager):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(0.5)
        return "result"

    task = await manager.create_task(execute_fn=slow)
    await manager.submit_task(task.task_id)
    await started.wait()
    ok = await manager.cancel_task(task.task_id)
    assert ok
    await asyncio.sleep(0.8)
    got = manager.get_task(task.task_id)
    assert got.status == "cancelled"  # 完成结果不覆盖 cancelled


async def test_cancel_completed_rejected(manager):
    async def work():
        return "x"

    task = await manager.create_task(execute_fn=work)
    await manager.submit_task(task.task_id)
    got = await _wait_status(manager, task.task_id, ("completed", "failed"))
    assert got.status == "completed"
    assert await manager.cancel_task(task.task_id) is False


async def test_timeout_marks_failed(tmp_path):
    m = GenTaskManager(persist_dir=str(tmp_path), task_timeout=0.2)

    async def slow():
        await asyncio.sleep(5)

    task = await m.create_task(execute_fn=slow)
    await m.submit_task(task.task_id)
    got = await _wait_status(m, task.task_id, ("completed", "failed"))
    assert got.status == "failed"
    assert "超时" in got.error


async def test_timeout_disabled_when_zero(manager):
    m = manager  # task_timeout=0

    async def slow():
        await asyncio.sleep(0.3)
        return "done"

    task = await m.create_task(execute_fn=slow)
    await m.submit_task(task.task_id)
    got = await _wait_status(m, task.task_id, ("completed", "failed"))
    assert got.status == "completed"
    assert got.result == "done"


async def test_ttl_cleanup(tmp_path):
    m = GenTaskManager(persist_dir=str(tmp_path), completed_ttl=0.1)
    task = await m.create_task(execute_fn=lambda: "x")
    task.status = "completed"
    task.updated_at = time.time() - 10
    m._tasks[task.task_id] = task
    await m._cleanup_expired_tasks()
    assert task.task_id not in m._tasks
    assert not (m._persist_dir / f"{task.task_id}.json").exists()


async def test_max_tasks_limit(tmp_path):
    m = GenTaskManager(persist_dir=str(tmp_path), max_tasks=2)
    await m.create_task(execute_fn=lambda: "a")
    await m.create_task(execute_fn=lambda: "b")
    with pytest.raises(RuntimeError):
        await m.create_task(execute_fn=lambda: "c")


def test_persist_utf8(tmp_path):
    m = GenTaskManager(persist_dir=str(tmp_path))
    task = GenTask(
        task_id="t_utf8",
        status="failed",
        error="中文错误信息乱码测试",
        created_at=time.time(),
        updated_at=time.time(),
    )
    m._save_task_to_disk(task)
    content = (tmp_path / "t_utf8.json").read_text(encoding="utf-8")
    assert "中文错误信息乱码测试" in content


async def test_restore_running_as_failed(tmp_path):
    m1 = GenTaskManager(persist_dir=str(tmp_path))
    task = await m1.create_task(execute_fn=lambda: "x")
    task.status = "running"
    task.updated_at = time.time()
    m1._save_task_to_disk(task)

    m2 = GenTaskManager(persist_dir=str(tmp_path))
    restored = m2.get_task(task.task_id)
    assert restored is not None
    assert restored.status == "failed"
    assert "服务重启" in restored.error


async def test_restore_expired_removed(tmp_path):
    m1 = GenTaskManager(persist_dir=str(tmp_path), completed_ttl=0.1)
    task = await m1.create_task(execute_fn=lambda: "x")
    task.status = "failed"
    task.updated_at = time.time() - 100
    m1._save_task_to_disk(task)

    m2 = GenTaskManager(persist_dir=str(tmp_path), completed_ttl=0.1)
    assert m2.get_task(task.task_id) is None
    assert not (m2._persist_dir / f"{task.task_id}.json").exists()


async def test_list_tasks(manager):
    async def work():
        return "x"

    t1 = await manager.create_task(execute_fn=work)
    t2 = await manager.create_task(execute_fn=work)
    tasks = await manager.list_tasks()
    ids = {t.task_id for t in tasks}
    assert {t1.task_id, t2.task_id} <= ids
