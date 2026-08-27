# -*- coding: utf-8 -*-
"""process_manager.py 全量 mock 测试（A 项：核心链路 mock——进程生命周期状态机）。
覆盖：启动/停止/健康检查/空闲自停/端口清理/VRAM 交替/重启回调。

关键设计：**不 patch 全局 asyncio.sleep**（会破坏 pytest-asyncio 的 event loop 管理，
导致 CI Linux/Python 3.12 上 hang）。改为给被测模块注入 FakeAsyncIO——
pm 模块内所有 `asyncio.sleep` / `asyncio.ensure_future` 走 fake（sleep 缩短为 0.5ms），
全局 asyncio 完全不受影响。测试代码用 _ORIG_SLEEP（真实 sleep）等待。
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.comfyui.process_manager as pm  # noqa: E402

_ORIG_SLEEP = asyncio.sleep


class FakeAsyncIO:
    """注入到 pm.asyncio 的替身：只加速 sleep，其余委托真实 asyncio。

    这样 pm 里的 `asyncio.sleep` 走这里（快速返回），而全局 asyncio 保持原样，
    pytest-asyncio 的事件循环管理与测试代码的 asyncio.sleep 不受影响。

    max_sleeps：可选 sleep 调用上限。超过后抛 CancelledError，
    让"无限 while / 无限自我重调度"的后台 task 在测试等待期内自行终止，
    避免测试结束时残留大量 pending task 导致 event loop 关闭卡死（CI Linux 复现）。
    """

    def __init__(self, real, max_sleeps=None):
        self._real = real
        self.sleep_secs = 0.0005
        self.max_sleeps = max_sleeps
        self._n = 0

    async def sleep(self, _secs):
        if self.max_sleeps is not None:
            self._n += 1
            if self._n > self.max_sleeps:
                raise asyncio.CancelledError()
        await self._real.sleep(self.sleep_secs)

    def ensure_future(self, coro, *a, **k):
        return self._real.ensure_future(coro, *a, **k)

    def create_task(self, coro, *a, **k):
        return self._real.create_task(coro, *a, **k)


@pytest.fixture
def mgr(monkeypatch):
    monkeypatch.setattr(pm, "COMFYUI_PYTHON", "py")
    monkeypatch.setattr(pm, "COMFYUI_SCRIPT", "main.py")
    monkeypatch.setattr(pm, "COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    monkeypatch.setattr(pm, "asyncio", FakeAsyncIO(asyncio))
    return pm.ComfyUIProcessManager(
        comfyui_dir=r"D:\comfy_test",
        base_url="http://127.0.0.1:8188",
        check_alive_fn=None,
    )


class FakePopen:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self._poll_result = None

    def poll(self):
        return self._poll_result

    def terminate(self):
        self.returncode = 1
        self._poll_result = 1

    def kill(self):
        self.returncode = 1
        self._poll_result = 1

    def wait(self, timeout=None):
        self._poll_result = self.returncode
        return self.returncode


class FakeRun:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


# ==================== 基础属性 ====================

def test_is_running_property():
    m = pm.ComfyUIProcessManager(comfyui_dir="", base_url="")
    assert m.is_running is False
    assert m.active_generation is False
    assert m.comfyui_dir == ""
    m._process = FakePopen(pid=1)
    m._process._poll_result = 1
    assert m.is_running is False
    m._process = FakePopen(pid=2)
    assert m.is_running is True


# ==================== ensure_running ====================

@pytest.mark.asyncio
async def test_ensure_running_already_alive():
    async def alive():
        return True

    m = pm.ComfyUIProcessManager(comfyui_dir="x", base_url="", check_alive_fn=alive)
    assert await m.ensure_running() is True


@pytest.mark.asyncio
async def test_ensure_running_no_dir():
    m = pm.ComfyUIProcessManager(comfyui_dir="", base_url="")
    assert await m.ensure_running() is False


@pytest.mark.asyncio
async def test_ensure_running_restart_in_progress(mgr):
    mgr._restart_in_progress = True
    calls = {"n": 0}

    async def alive():
        calls["n"] += 1
        return calls["n"] > 30

    mgr._check_alive_fn = alive
    assert await mgr.ensure_running() is True


@pytest.mark.asyncio
async def test_ensure_running_restart_timeout(mgr):
    mgr._restart_in_progress = True
    mgr._check_alive_fn = None
    assert await mgr.ensure_running() is False
    assert mgr._restart_in_progress is False


@pytest.mark.asyncio
async def test_ensure_running_starts_process(monkeypatch, mgr):
    started = {"v": False}

    async def alive():
        return False  # 不在运行 → 触发 _start_process

    async def fake_start():
        started["v"] = True
        return True

    mgr._check_alive_fn = alive
    monkeypatch.setattr(mgr, "_start_process", fake_start)
    assert await mgr.ensure_running() is True
    assert started["v"] is True


# ==================== _start_process ====================

@pytest.mark.asyncio
async def test_start_process_success(monkeypatch, mgr):
    fake = FakePopen(pid=777)
    monkeypatch.setattr(pm.subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(pm, "COMFYUI_START_TIMEOUT", 3)

    async def alive():
        return True

    mgr._check_alive_fn = alive
    mgr._stop_process = lambda: None
    mgr._kill_process_on_port = lambda *a: None
    assert await mgr._start_process() is True
    assert mgr.process is fake
    assert mgr._restart_in_progress is False


@pytest.mark.asyncio
async def test_start_process_skip_if_restarting(mgr):
    mgr._restart_in_progress = True
    assert await mgr._start_process() is False


@pytest.mark.asyncio
async def test_start_process_popen_failure(monkeypatch, mgr):
    def boom(*a, **k):
        raise FileNotFoundError("no python")

    monkeypatch.setattr(pm.subprocess, "Popen", boom)
    mgr._stop_process = lambda: None
    mgr._kill_process_on_port = lambda *a: None
    assert await mgr._start_process() is False
    assert mgr._restart_in_progress is False


@pytest.mark.asyncio
async def test_start_process_proc_exits(monkeypatch, mgr):
    fake = FakePopen(pid=778)
    fake._poll_result = 1
    monkeypatch.setattr(pm.subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(pm, "COMFYUI_START_TIMEOUT", 3)
    mgr._check_alive_fn = None
    mgr._stop_process = lambda: None
    mgr._kill_process_on_port = lambda *a: None
    assert await mgr._start_process() is False
    assert mgr._process is None


@pytest.mark.asyncio
async def test_start_process_timeout(monkeypatch, mgr):
    fake = FakePopen(pid=779)
    monkeypatch.setattr(pm.subprocess, "Popen", lambda *a, **k: fake)
    monkeypatch.setattr(pm, "COMFYUI_START_TIMEOUT", 2)
    mgr._check_alive_fn = None
    mgr._stop_process = lambda: None
    mgr._kill_process_on_port = lambda *a: None
    assert await mgr._start_process() is False
    assert mgr._restart_in_progress is False


# ==================== stop / _stop_process ====================

def test_stop_closes_log_and_cancels_health(mgr):
    closed = {"cancel": False}
    mgr._process = FakePopen(pid=1)
    mgr._comfyui_log_f = open(os.devnull, "w")

    class _FakeTask:
        def done(self):
            return False

        def cancel(self):
            closed["cancel"] = True

    mgr._health_check_task = _FakeTask()
    mgr._stop_process = lambda: None
    mgr._kill_process_on_port = lambda *a: None
    mgr.stop()
    assert mgr._comfyui_log_f is None
    assert closed["cancel"] is True


def test_stop_process_win32_taskkill(monkeypatch, mgr):
    mgr._process = FakePopen(pid=999)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return FakeRun(stdout="ok", stderr="")

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    monkeypatch.setattr(pm.sys, "platform", "win32")
    mgr._stop_process()
    assert mgr._process is None
    assert any("taskkill" in c for c in calls)


def test_stop_process_non_win32(monkeypatch, mgr):
    mgr._process = FakePopen(pid=1000)
    killed = []
    monkeypatch.setattr(pm.sys, "platform", "linux")

    def fake_killpg(gid, sig):
        killed.append(("killpg", sig))

    # Windows 的 os 模块没有 killpg 属性，需 raising=False
    monkeypatch.setattr(pm.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(pm.os, "getpgid", lambda pid: 2000, raising=False)
    mgr._stop_process()
    assert mgr._process is None
    assert killed and killed[0][0] == "killpg"


def test_kill_process_on_port(monkeypatch):
    captured = []

    def fake_run(cmd, **kw):
        captured.append(cmd)
        if "netstat" in cmd:
            return FakeRun(stdout="  TCP    127.0.0.1:8188   0.0.0.0:0    LISTENING    1111\n")
        return FakeRun(stdout="ok")

    monkeypatch.setattr(pm.subprocess, "run", fake_run)
    pm.ComfyUIProcessManager._kill_process_on_port(8188)
    assert any("netstat" in c for c in captured)
    assert any("taskkill" in c and "1111" in c for c in captured)


def test_kill_process_on_port_no_output(monkeypatch):
    monkeypatch.setattr(pm.subprocess, "run", lambda cmd, **kw: FakeRun(stdout=""))
    pm.ComfyUIProcessManager._kill_process_on_port(8188)


# ==================== 生成标记 / 空闲自停 ====================

def test_mark_generation(mgr):
    mgr.mark_generation_active()
    assert mgr.active_generation is True


@pytest.mark.asyncio
async def test_mark_generation_complete(mgr):
    mgr.mark_generation_active()
    assert mgr.active_generation is True
    mgr.mark_generation_complete()
    assert mgr.active_generation is False


@pytest.mark.asyncio
async def test_idle_shutdown_fires(monkeypatch, mgr):
    monkeypatch.setattr(pm, "IDLE_SHUTDOWN_TIMEOUT", 0.01)
    stopped = {"v": False}
    mgr._process = FakePopen(pid=1)
    mgr._last_used = 0
    mgr.stop = lambda: stopped.update(v=True)
    mgr.mark_generation_complete()
    await _ORIG_SLEEP(0.2)
    assert stopped["v"] is True


@pytest.mark.asyncio
async def test_idle_shutdown_skip_active(monkeypatch, mgr):
    # 模拟"活跃生成中"：_active_generation=True 时空闲定时器跳过停止。
    # 注意 skip 会触发 _schedule_idle_shutdown 无限自我重调度，
    # 必须给 FakeAsyncIO 设 sleep 上限让重调度链在等待期内自行终止（否则 Linux loop 关闭卡死）
    monkeypatch.setattr(pm, "IDLE_SHUTDOWN_TIMEOUT", 0.01)
    monkeypatch.setattr(pm, "asyncio", FakeAsyncIO(asyncio, max_sleeps=100))
    stopped = {"v": False}
    mgr._process = FakePopen(pid=1)
    mgr._active_generation = True
    mgr._last_used = time.time()
    mgr.stop = lambda: stopped.update(v=True)
    mgr._schedule_idle_shutdown()
    await _ORIG_SLEEP(0.3)
    assert stopped["v"] is False


# ==================== 健康检查 ====================

@pytest.mark.asyncio
async def test_health_check_restart_on_3_fails(monkeypatch, mgr):
    # 带 sleep 上限：while True 后台 task 在等待期内自行终止，避免残留
    monkeypatch.setattr(pm, "asyncio", FakeAsyncIO(asyncio, max_sleeps=100))
    fails = {"stopped": False, "restarted": False}

    async def alive():
        return False

    def fake_stop():
        fails["stopped"] = True

    async def fake_ensure():
        fails["restarted"] = True
        return True

    mgr._process = FakePopen(pid=1)
    mgr._check_alive_fn = alive
    mgr.stop = fake_stop
    mgr.ensure_running = fake_ensure
    mgr._start_health_check()
    await _ORIG_SLEEP(0.3)  # 足够覆盖 3 次失败 + 重启 + task 因上限自行终止
    assert fails["stopped"] is True
    assert fails["restarted"] is True


@pytest.mark.asyncio
async def test_health_check_ok_resets_fail(monkeypatch, mgr):
    monkeypatch.setattr(pm, "asyncio", FakeAsyncIO(asyncio, max_sleeps=100))
    seq = [False, False, True, True]
    fails = {"n": 0, "stopped": False}

    async def alive():
        fails["n"] += 1
        return seq[min(fails["n"] - 1, len(seq) - 1)]

    mgr._process = FakePopen(pid=1)
    mgr._check_alive_fn = alive
    mgr.stop = lambda: fails.update(stopped=True)
    mgr.ensure_running = lambda: True
    mgr._start_health_check()
    await _ORIG_SLEEP(0.3)
    assert fails["stopped"] is False


# ==================== VRAM 交替 / 回调 ====================

def test_release_vram_for_llama(mgr):
    mgr._process = FakePopen(pid=1)
    closed = {"v": False}
    mgr.release_vram_for_llama(close_session_fn=lambda: closed.update(v=True))
    assert closed["v"] is True


def test_release_vram_for_llama_not_running(mgr):
    mgr._process = None
    mgr.release_vram_for_llama()


@pytest.mark.asyncio
async def test_release_vram_for_comfyui(monkeypatch, mgr):
    # services.process_manager 模块在仓库中不存在（get_llm_manager 未实现），
    # 通过 sys.modules 注入 fake 模块，验证正常路径
    import types

    fake_mod = types.ModuleType("services.process_manager")

    class FakeLLM:
        is_running = True

        async def stop_for_comfyui(self):
            return None

    fake_mod.get_llm_manager = lambda: FakeLLM()
    monkeypatch.setitem(sys.modules, "services.process_manager", fake_mod)
    await mgr.release_vram_for_comfyui()  # 不抛异常


@pytest.mark.asyncio
async def test_release_vram_for_comfyui_import_fail(mgr):
    # get_llm_manager 模块缺失时（当前真实状态）应静默 pass 不抛异常
    await mgr.release_vram_for_comfyui()


@pytest.mark.asyncio
async def test_restart_callbacks(mgr):
    got = []
    mgr.set_restart_callback(lambda s, e: got.append((s, e)))
    mgr.set_restart_callback(lambda s, e: (_ for _ in ()).throw(RuntimeError("boom")))
    await mgr.notify_restart("restarting", 15)
    assert got == [("restarting", 15)]
    mgr.clear_restart_callbacks()
    await mgr.notify_restart()
