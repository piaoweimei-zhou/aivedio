"""
ComfyUI 进程管理器 — 进程启停 + 健康检查 + 空闲自停

职责：
- 启动/停止 ComfyUI 进程（含子进程树清理）
- 健康检查循环（每 10s 检查，连续 3 次失败自动重启）
- 空闲自停（30 分钟无使用自动停止释放显存）
- 端口占用清理（Windows taskkill /T）
- VRAM 交替管理（ComfyUI ↔ llama.cpp 互斥）
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from typing import Optional, Callable, Awaitable, List

from services.comfyui.config import COMFYUI_DIR, COMFYUI_PYTHON, COMFYUI_SCRIPT

logger = logging.getLogger(__name__)


# ── 配置常量 ──────────────────────────────────────────────────

from services.comfyui.config import COMFYUI_BASE_URL as _CFG_BASE_URL  # noqa: E402

COMFYUI_START_TIMEOUT = 60  # 秒
COMFYUI_BASE_URL = _CFG_BASE_URL  # 复用 config.py 单一来源

# 内存/显存监控配置（从 config.py 复用，单一来源）
# MEMORY_HIGH_THRESHOLD / VRAM_HIGH_THRESHOLD / MEMORY_CHECK_INTERVAL 已从 config 导入

# 空闲自停超时（秒）
IDLE_SHUTDOWN_TIMEOUT = 1800  # 30 分钟


class ComfyUIProcessManager:
    """ComfyUI 进程管理器

    管理 ComfyUI 进程的生命周期，包括：
    - 启动/停止进程
    - 健康检查
    - 空闲自停
    - VRAM 交替管理
    """

    def __init__(
        self,
        comfyui_dir: str = "",
        base_url: str = COMFYUI_BASE_URL,
        check_alive_fn: Optional[Callable] = None,
        on_restart: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """
        Args:
            comfyui_dir: ComfyUI 安装目录
            base_url: ComfyUI HTTP 地址
            check_alive_fn: 异步检查 ComfyUI 是否在线的函数
            on_restart: ComfyUI 重启时的回调
        """
        self._comfyui_dir = comfyui_dir or COMFYUI_DIR
        self._base_url = base_url
        self._check_alive_fn = check_alive_fn
        self._on_restart = on_restart

        self._process: Optional[subprocess.Popen] = None
        self._comfyui_log_f = None
        self._last_used: float = 0
        self._active_generation: bool = False
        self._restart_in_progress: bool = False
        self._idle_shutdown_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._restart_callbacks: List[Callable[[str, int], Awaitable[None]]] = []

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._process

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def active_generation(self) -> bool:
        return self._active_generation

    @property
    def comfyui_dir(self) -> str:
        return self._comfyui_dir

    # ── 启动/停止 ──────────────────────────────────────────────

    async def ensure_running(self) -> bool:
        """确保 ComfyUI 正在运行，不在运行则自动启动"""
        _boot_t0 = time.time()

        if self._restart_in_progress:
            logger.debug("[ComfyUI] 启动正在进行中，等待完成...")
            for _ in range(60):
                await asyncio.sleep(2)
                if self._check_alive_fn and await self._check_alive_fn():
                    return True
                if not self._restart_in_progress:
                    break
            logger.warning("[ComfyUI] 等待启动完成超时(60s)，强制重置标志后重新启动")
            self._restart_in_progress = False

        if self._check_alive_fn and await self._check_alive_fn():
            _boot_elapsed = (time.time() - _boot_t0) * 1000
            logger.info(f"[BOOT] ensure_running | running=True | elapsed={_boot_elapsed:.0f}ms")
            return True

        logger.warning("[ComfyUI] 服务不在运行")
        if not self._comfyui_dir:
            logger.warning("[ComfyUI] 未配置 COMFYUI_DIR，请设置环境变量或手动启动")
            return False

        result = await self._start_process()
        _boot_elapsed = (time.time() - _boot_t0) * 1000
        logger.info(f"[BOOT] ensure_running | running={result} | elapsed={_boot_elapsed:.0f}ms")
        return result

    async def _start_process(self) -> bool:
        """启动 ComfyUI 进程"""
        if self._restart_in_progress:
            logger.info("[ComfyUI] 启动正在进行中，跳过重复启动")
            return False

        self._restart_in_progress = True
        self._stop_process()
        self._kill_process_on_port(8188)

        # 环境变量
        env = os.environ.copy()
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

        cmd = [
            COMFYUI_PYTHON,
            COMFYUI_SCRIPT,
            "--use-sage-attention",
            "--bf16-unet",
            "--fast",
        ]
        logger.info(f"[ComfyUI] 启动: {self._comfyui_dir}> {' '.join(cmd)}")

        # 日志重定向
        _comfyui_log_path = os.path.join(self._comfyui_dir, "comfyui_backend.log")
        try:
            self._comfyui_log_f = open(_comfyui_log_path, "a", encoding="utf-8")
            _stdout = self._comfyui_log_f
            _stderr = subprocess.STDOUT
            logger.info(f"[ComfyUI] 日志输出: {_comfyui_log_path}")
        except Exception:
            _stdout = subprocess.DEVNULL
            _stderr = subprocess.DEVNULL
            self._comfyui_log_f = None

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=self._comfyui_dir,
                env=env,
                stdout=_stdout,
                stderr=_stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            logger.error(f"[ComfyUI] 启动失败（找不到 Python）: {e}")
            self._restart_in_progress = False
            return False
        except Exception as e:
            logger.error(f"[ComfyUI] 启动失败: {e}")
            self._restart_in_progress = False
            return False

        # 等待就绪
        logger.info(f"[ComfyUI] 等待就绪（最长 {COMFYUI_START_TIMEOUT}s）...")
        for _ in range(COMFYUI_START_TIMEOUT):
            await asyncio.sleep(2)
            proc = self._process
            if proc is None:
                logger.error("[ComfyUI] 进程引用已丢失，终止启动")
                self._restart_in_progress = False
                return False
            if proc.poll() is not None:
                logger.error(f"[ComfyUI] 进程已退出，返回码: {proc.returncode}")
                self._process = None
                self._restart_in_progress = False
                return False
            if self._check_alive_fn and await self._check_alive_fn():
                logger.info("[ComfyUI] 就绪")
                self._restart_in_progress = False
                self._start_health_check()
                return True

        logger.error("[ComfyUI] 启动超时")
        self._stop_process()
        self._restart_in_progress = False
        return False

    def stop(self):
        """停止 ComfyUI 进程"""
        self._stop_process()
        self._kill_process_on_port(8188)

        # 关闭日志文件句柄
        if self._comfyui_log_f:
            try:
                self._comfyui_log_f.close()
            except Exception:
                pass
            self._comfyui_log_f = None

        # 取消健康检查任务
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            self._health_check_task = None

        logger.info("[ComfyUI] 已停止，显存已释放")

    def _stop_process(self):
        """停止 ComfyUI 进程（含子进程树）"""
        if self._process is not None:
            try:
                proc = self._process
                if sys.platform == "win32":
                    import subprocess as sp

                    result = sp.run(
                        f"taskkill /F /T /PID {proc.pid}",
                        capture_output=True,
                        shell=True,
                        timeout=5,
                        encoding="gbk",
                        errors="replace",
                    )
                    logger.info(
                        f"[WINDOWS] taskkill /T PID={proc.pid}"
                        f" | stdout={result.stdout.strip()}"
                        f" | stderr={result.stderr.strip()}"
                    )
                else:
                    import os as _os

                    try:
                        _os.killpg(_os.getpgid(proc.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except Exception:
                        pass
                    if proc.poll() is None:
                        try:
                            _os.killpg(_os.getpgid(proc.pid), signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            proc.kill()
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

    @staticmethod
    def _kill_process_on_port(port: int):
        """强制释放指定端口（Windows），防止端口占用导致重启失败"""
        import subprocess as sp

        try:
            result = sp.run(
                f'netstat -ano | findstr ":{port} "',
                capture_output=True,
                shell=True,
                timeout=5,
                encoding="gbk",
                errors="replace",
            )
            if not result.stdout.strip():
                return
            seen = set()
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and (parts[1] or "").endswith(f":{port}"):
                    pid = parts[-1]
                    if pid not in seen:
                        seen.add(pid)
                        sp.run(
                            f"taskkill /F /T /PID {pid}",
                            capture_output=True,
                            shell=True,
                            timeout=5,
                            encoding="gbk",
                            errors="replace",
                        )
                        logger.info(f"[WINDOWS] 已释放端口 {port} (PID={pid}, 含子进程树)")
        except Exception as e:
            logger.warning(f"[ComfyUI] 释放端口 {port} 失败: {e}")

    # ── 活跃生成标记 ──────────────────────────────────────────

    def mark_generation_active(self):
        """标记活跃生成开始，防止空闲定时器误杀"""
        self._active_generation = True
        self._last_used = time.time()

    def mark_generation_complete(self):
        """标记活跃生成结束"""
        self._active_generation = False
        self._last_used = time.time()
        self._schedule_idle_shutdown()

    # ── 空闲自停 ──────────────────────────────────────────────

    def _schedule_idle_shutdown(self):
        """调度空闲自停：30 分钟未使用后自动停止"""
        if self._idle_shutdown_task and not self._idle_shutdown_task.done():
            self._idle_shutdown_task.cancel()

        async def _idle_check():
            await asyncio.sleep(IDLE_SHUTDOWN_TIMEOUT)
            if self._process is not None and self._last_used > 0:
                idle_secs = time.time() - self._last_used
                if idle_secs >= IDLE_SHUTDOWN_TIMEOUT - 5:
                    if self._active_generation:
                        logger.info(
                            "[ComfyUI] 空闲定时器触发但存在活跃生成，跳过停止"
                            f" | idle={idle_secs:.0f}s | 重新调度30分钟检查"
                        )
                        self._schedule_idle_shutdown()
                        return
                    logger.info("[ComfyUI] 空闲超时（30分钟），停止 ComfyUI 释放显存")
                    self.stop()

        self._idle_shutdown_task = asyncio.ensure_future(_idle_check())

    # ── 健康检查 ──────────────────────────────────────────────

    def _start_health_check(self):
        """启动健康检查后台任务"""
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()

        async def _health_check_loop():
            fail_count = 0
            while True:
                await asyncio.sleep(10)
                if self._process is None:
                    continue
                if self._restart_in_progress:
                    continue
                try:
                    if self._check_alive_fn and await self._check_alive_fn():
                        fail_count = 0
                    else:
                        fail_count += 1
                        logger.warning(f"[ComfyUI] 健康检查失败 {fail_count}/3")
                        if fail_count >= 3:
                            logger.error("[ComfyUI] 连续 3 次健康检查失败，自动重启...")
                            self.stop()
                            await self.ensure_running()
                            fail_count = 0
                except Exception as e:
                    logger.warning(f"[ComfyUI] 健康检查异常: {e}")

        self._health_check_task = asyncio.ensure_future(_health_check_loop())
        logger.info("[ComfyUI] 健康检查任务已启动")

    # ── VRAM 交替管理 ──────────────────────────────────────────

    async def release_vram_for_comfyui(self):
        """为 ComfyUI 释放显存：停止 llama.cpp"""
        try:
            from services.process_manager import get_llm_manager

            llm_mgr = get_llm_manager()
            if llm_mgr.is_running:
                logger.info("[VRAM] 停止 llama.cpp → 为 ComfyUI 释放显存")
                await llm_mgr.stop_for_comfyui()
                logger.info("[VRAM] llama.cpp 已停止，显存已释放给 ComfyUI")
            else:
                logger.info("[VRAM] llama.cpp 未在运行，无需停止")
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[VRAM] 释放显存时出错: {e}")

    def release_vram_for_llama(self, close_session_fn=None):
        """为 llama.cpp 释放显存：停止 ComfyUI

        Args:
            close_session_fn: 关闭 HTTP session 的回调（同步方法无法 await）
        """
        if self._process is not None and self._process.poll() is None:
            logger.info("[VRAM] 停止 ComfyUI → 为 llama.cpp 释放显存")
            if close_session_fn:
                close_session_fn()
            self.stop()

    # ── 重启回调 ──────────────────────────────────────────────

    def set_restart_callback(self, cb: Callable[[str, int], Awaitable[None]]):
        self._restart_callbacks.append(cb)

    def clear_restart_callbacks(self):
        self._restart_callbacks.clear()

    async def notify_restart(self, status: str = "restarting", estimated_secs: int = 15):
        for cb in self._restart_callbacks:
            try:
                await cb(status, estimated_secs)
            except Exception as e:
                logger.warning(f"[ComfyUI] 重启回调异常: {e}")
