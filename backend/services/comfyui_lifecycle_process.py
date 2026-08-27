"""
ComfyUI 服务 — 进程管理 Mixin（从 comfyui_lifecycle.py 拆分，P2 治理）

ComfyUI 进程启动/停止/健康检查/空闲自停/端口清理。
被 ComfyUILifecycleMixin 继承（MRO），方法用 self.xxx 调用主类其他 mixin。
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time

from services.comfyui.config import (
    COMFYUI_DIR,
    COMFYUI_PYTHON,
)
from services.comfyui_helpers import (
    COMFYUI_SCRIPT,
    COMFYUI_START_TIMEOUT,
    DISABLE_PROCESS_MANAGEMENT,
    _mem_log,
)

logger = logging.getLogger(__name__)


class ComfyUILifecycleProcessMixin:
    async def ensure_running(self) -> bool:
        """
        确保 ComfyUI 正在运行。
        如果不在运行且配置了 COMFYUI_DIR，自动启动。
        使用 _restart_in_progress 标志防止竞态重入。
        当 DISABLE_PROCESS_MANAGEMENT=True 时，跳过自动启动，仅检查是否在线。
        """
        _boot_t0 = time.time()

        if self._restart_in_progress:
            logger.debug("[ComfyUI] 启动正在进行中，等待完成...")
            for _ in range(60):  # ⭐ V6.0: 最多等 60s（2分钟安全上限）
                await asyncio.sleep(2)
                if await self._check_alive():
                    return True
                if not self._restart_in_progress:
                    break
            # 等待超时，直接尝试启动（不再被动等待）
            logger.warning("[ComfyUI] 等待启动完成超时(60s)，强制重置标志后重新启动")
            self._restart_in_progress = False

        if await self._check_alive():
            _boot_elapsed = (time.time() - _boot_t0) * 1000
            logger.info(f"[BOOT] ensure_running | running=True | elapsed={_boot_elapsed:.0f}ms")
            return True

        # 进程管理被禁用时，仅检查在线状态，不自动启动
        if DISABLE_PROCESS_MANAGEMENT:
            logger.warning(
                "[ComfyUI] 服务不在运行，DISABLE_PROCESS_MANAGEMENT=True 跳过自动启动"
                "（由外部 Supervisor/Systemd 管理）"
            )
            return False

        logger.warning("[ComfyUI] 服务不在运行")
        if not COMFYUI_DIR:
            logger.warning("[ComfyUI] 未配置 COMFYUI_DIR，请设置环境变量或手动启动")
            return False

        result = await self._start_process()
        _boot_elapsed = (time.time() - _boot_t0) * 1000
        logger.info(f"[BOOT] ensure_running | running={result} | elapsed={_boot_elapsed:.0f}ms")
        return result

    async def _check_alive(self) -> bool:
        """检查 ComfyUI 是否在线（委托到 client 子模块）"""
        return await self._client.check_alive()

    async def _start_process(self) -> bool:
        """启动 ComfyUI（python main.py）"""
        _mem_log("ComfyUI启动前", "即将启动ComfyUI进程")
        # 先检查是否已在启动中（防重入）
        if self._restart_in_progress:
            logger.info("[ComfyUI] 启动正在进行中，跳过重复启动")
            return False

        # 先清理已有进程和端口占用
        self._restart_in_progress = True
        self._stop_process()
        self._kill_process_on_port(8188)

        # 环境变量：启用 CUDA 扩展段 + 其他优化
        env = os.environ.copy()
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

        cmd = [
            COMFYUI_PYTHON,
            COMFYUI_SCRIPT,
            "--use-sage-attention",
            "--bf16-unet",
            "--fast",
        ]
        logger.info(f"[ComfyUI] 启动: {COMFYUI_DIR}> {' '.join(cmd)}")

        # ⭐ Fix 10: 将 ComfyUI stdout/stderr 重定向到日志文件
        # 之前 DEVNULL 丢弃了模型加载失败、CUDA OOM 等关键诊断信息
        _comfyui_log_path = os.path.join(COMFYUI_DIR, "comfyui_backend.log")
        try:
            self._comfyui_log_f = open(_comfyui_log_path, "a", encoding="utf-8")
            _stdout = self._comfyui_log_f
            _stderr = subprocess.STDOUT  # stderr 合并到 stdout
            logger.info(f"[ComfyUI] 日志输出: {_comfyui_log_path}")
        except Exception:
            _stdout = subprocess.DEVNULL
            _stderr = subprocess.DEVNULL
            self._comfyui_log_f = None

        try:
            # CREATE_NO_WINDOW 仅 Windows 存在；Linux/macOS 传入会抛 AttributeError
            _popen_kwargs: dict = {}
            if sys.platform == "win32":
                _popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._process = subprocess.Popen(
                cmd,
                cwd=COMFYUI_DIR,
                env=env,
                stdout=_stdout,
                stderr=_stderr,
                # stdin=subprocess.DEVNULL,  # ⭐ Fix: 关闭 stdin 防止子进程卡住
                **_popen_kwargs,
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
            # 使用局部变量避免 TOCTOU 竞态条件
            proc = self._process
            if proc is None:
                logger.error("[ComfyUI] 进程引用已丢失（被其他协程停止），终止启动")
                self._restart_in_progress = False
                return False
            if proc.poll() is not None:
                logger.error(f"[ComfyUI] 进程已退出，返回码: {proc.returncode}")
                self._process = None
                self._restart_in_progress = False
                return False
            if await self._check_alive():
                logger.info("[ComfyUI] 就绪")
                _mem_log("ComfyUI就绪", "ComfyUI进程已启动并响应")
                self._restart_in_progress = False
                self._start_health_check()  # 启动健康检查任务
                return True

        logger.error("[ComfyUI] 启动超时")
        self._stop_process()
        self._restart_in_progress = False
        return False

    def stop(self):
        """停止 ComfyUI 进程，释放显存"""
        if DISABLE_PROCESS_MANAGEMENT:
            logger.info("[ComfyUI] DISABLE_PROCESS_MANAGEMENT=True，跳过停止进程")
            return
        self._stop_process()
        # 无论如何，强制清理端口上的残余进程
        self._kill_process_on_port(8188)
        # ⭐ Fix 10: 关闭 ComfyUI 日志文件句柄
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
        # ⭐ 关闭共享 HTTP session（ComfyUI 重启后旧 session 不可用）
        # 注意：stop() 是同步方法，不能 await，标记为需要关闭
        if self._http_session and not self._http_session.closed:
            # 同步关闭不可用，标记为需要重建
            try:
                # 尝试同步关闭（aiohttp 不推荐但可以工作）
                import asyncio

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 在运行中的 loop 上不能同步 close，标记重建
                        self._http_session = None
                    else:
                        loop.run_until_complete(self._http_session.close())
                        self._http_session = None
                except RuntimeError:
                    self._http_session = None
            except Exception:
                self._http_session = None
        logger.info("[ComfyUI] 已停止，显存已释放")

    def _stop_process(self):
        """停止 ComfyUI 进程（含子进程树）

        ⭐ Fix 8: Windows 上 proc.terminate() = TerminateProcess()，只杀主进程，
        不杀子进程树。ComfyUI 的 model loader、CUDA workers、onnx runtime 后台线程
        全变成孤儿进程，继续消耗系统内存和显存。多轮重启后累积数十个孤儿 Python 进程，
        这是 64GB 内存被占满的最主要根因。

        修复：Windows 平台直接执行 taskkill /F /T 杀进程树。
        """
        if self._process is not None:
            try:
                proc = self._process
                if sys.platform == "win32":
                    # Windows: terminate() 不杀子进程，必须用 taskkill /T
                    import subprocess as sp

                    result = sp.run(
                        f"taskkill /F /T /PID {proc.pid}",
                        capture_output=True,
                        shell=True,
                        timeout=5,
                        encoding="gbk",
                        errors="replace",  # Windows 中文环境
                    )
                    logger.info(
                        f"[WINDOWS] taskkill /T PID={proc.pid}"
                        f" | stdout={result.stdout.strip()}"
                        f" | stderr={result.stderr.strip()}"
                    )
                else:
                    # Linux/macOS: 先 SIGTERM，再 SIGKILL 进程组
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

    def _kill_process_on_port(self, port: int):
        """强制释放指定端口（Windows），防止端口占用导致重启失败

        ⭐ Fix 8 配套: 使用 taskkill /F /T /PID 杀进程树，避免孤儿进程残留。
        ⭐ 修复: 拆分自 process_manager 时遗漏 self 参数，导致
          self._kill_process_on_port(8188) 调用 TypeError（启动/停止均崩溃）。
        """
        import subprocess as sp  # 避免与 aiohttp 的 subprocess 混淆

        try:
            # 查找占用该端口的 PID
            result = sp.run(
                f'netstat -ano | findstr ":{port} "',
                capture_output=True,
                shell=True,
                timeout=5,
                encoding="gbk",
                errors="replace",  # Windows 中文环境用 gbk
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
                        # ⭐ Fix 8: /T 杀进程树，避免孤儿进程
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

    def _schedule_idle_shutdown(self):
        """
        调度空闲自停：30 分钟未使用后自动停止 ComfyUI 释放显存，
        使得后续 LLM 调用有足够 VRAM。
        ⭐ 如果当前有活跃生成，跳过停止。
        ⭐ DISABLE_PROCESS_MANAGEMENT=True 时跳过空闲自停。
        """
        if DISABLE_PROCESS_MANAGEMENT:
            return

        if self._idle_shutdown_task and not self._idle_shutdown_task.done():
            self._idle_shutdown_task.cancel()

        async def _idle_check():
            await asyncio.sleep(1800)  # 30 分钟空闲超时（原5分钟，避免生成间隔被误杀）
            if self._process is not None and self._last_used > 0:
                idle_secs = time.time() - self._last_used
                if idle_secs >= 1795:
                    if self._active_generation:
                        logger.info(
                            "[ComfyUI] 空闲定时器触发但存在活跃生成，跳过停止"
                            f" | idle={idle_secs:.0f}s | 重新调度30分钟检查"
                        )
                        self._schedule_idle_shutdown()  # 重新调度
                        return
                    logger.info("[ComfyUI] 空闲超时（30分钟），停止 ComfyUI 释放显存")
                    await self._close_http_session()
                    self.stop()

        self._idle_shutdown_task = asyncio.ensure_future(_idle_check())

    def _start_health_check(self):
        """
        启动健康检查后台任务：每 10 秒检查一次 ComfyUI 是否健康，
        不健康时自动重启。
        ⭐ DISABLE_PROCESS_MANAGEMENT=True 时跳过健康检查（由外部管理）。
        """
        if DISABLE_PROCESS_MANAGEMENT:
            logger.info("[ComfyUI] DISABLE_PROCESS_MANAGEMENT=True，跳过健康检查")
            return

        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()

        async def _health_check_loop():
            fail_count = 0
            while True:
                await asyncio.sleep(10)  # 每 10 秒检查一次
                if self._process is None:
                    continue  # 进程未启动，不检查
                if self._restart_in_progress:
                    continue  # 正在启动中，跳过检查避免竞态
                try:
                    if await self._check_alive():
                        fail_count = 0
                    else:
                        fail_count += 1
                        logger.warning(f"[ComfyUI] 健康检查失败 {fail_count}/3")
                        if fail_count >= 3:
                            logger.error("[ComfyUI] 连续 3 次健康检查失败，自动重启...")
                            await self._close_http_session()
                            self.stop()
                            await self.ensure_running()
                            fail_count = 0
                except Exception as e:
                    logger.warning(f"[ComfyUI] 健康检查异常: {e}")

        self._health_check_task = asyncio.ensure_future(_health_check_loop())
        logger.info("[ComfyUI] 健康检查任务已启动")
