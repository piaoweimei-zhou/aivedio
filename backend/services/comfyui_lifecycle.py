"""
ComfyUI 服务 — 生命周期 Mixin 主类

进程启动/停止、内存与显存协调、健康检查、空闲自停、输出清理。
P2 治理：内存/进程方法拆分至 comfyui_lifecycle_memory / _process 子模块，
本文件保留核心方法并组合继承。
"""

import asyncio
import logging
import os
import shutil
import time
from typing import Awaitable, Callable, List

import aiohttp

from services.comfyui_helpers import (
    GENERATED_DIR,
)

from services.comfyui_lifecycle_memory import ComfyUILifecycleMemoryMixin
from services.comfyui_lifecycle_process import ComfyUILifecycleProcessMixin

logger = logging.getLogger(__name__)


class ComfyUILifecycleMixin(ComfyUILifecycleMemoryMixin, ComfyUILifecycleProcessMixin):
    def reset_generation_count(self, model_family: str = None):
        """⭐ Fix 3: 重置生成计数，防止跨管线阶段误触 ComfyUI 重启

        问题：_model_generation_count 在 ComfyUIService 单例中永不重置。
        前一轮管线执行 5 次 Qwen（概念+精修），新管线第一次分镜时
        count=6 > max_gen=5，_ensure_clean_state 触发不必要的 ComfyUI 重启
        → 双进程争抢显存 → 系统 RAM 爆。

        调用时机：每个阶段入口处调用。
        """
        if model_family:
            old = self._model_generation_count.get(model_family, 0)
            self._model_generation_count[model_family] = 0
            logger.info(f"[Fix3] reset_generation_count | model={model_family} | {old}→0")
        else:
            logger.info(  # noqa: E501
                f"[Fix3] reset_generation_count | all | {self._model_generation_count}→{{'sd':0,'qwen':0}}"  # noqa: E501
            )
            self._model_generation_count = {"sd": 0, "qwen": 0}

    def set_restart_callback(self, cb: Callable[[str, int], Awaitable[None]]):
        """注册重启事件回调，在 ComfyUI 重启时广播 status + estimated_secs"""
        self._restart_callbacks.append(cb)

    def clear_restart_callbacks(self):
        """清除所有重启回调"""
        self._restart_callbacks.clear()
        self._process_mgr.clear_restart_callbacks()

    def client(self):
        """ComfyUI HTTP 客户端子模块"""
        return self._client

    def process_manager(self):
        """ComfyUI 进程管理子模块"""
        return self._process_mgr

    def file_handler(self):
        """ComfyUI 文件处理子模块"""
        return self._file_handler

    async def _notify_restart(self, status: str = "restarting", estimated_secs: int = 15):
        """通知所有注册回调：ComfyUI 正在重启"""
        for cb in self._restart_callbacks:
            try:
                await cb(status, estimated_secs)
            except Exception as e:
                logger.debug(f"[ComfyUI] 重启回调执行失败: {e}")

    def _get_http_session(self) -> aiohttp.ClientSession:
        """获取共享 aiohttp session（复用 client 的 session，避免重复创建）"""
        # 复用 ComfyUIClient 的 session（统一连接池管理）
        if self._client is not None:
            client_session = self._client.get_http_session()
            if client_session is not None and not client_session.closed:
                return client_session
        # 兜底：client 未初始化时自建（仅用于启动前的早期请求）
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def _close_http_session(self):
        """关闭共享 aiohttp session（由 client 统一管理，此处仅关闭兜底 session）"""
        if self._http_session and not self._http_session.closed:
            try:
                await self._http_session.close()
            except Exception:
                pass
            self._http_session = None

    async def _persist_output_files(self, filenames: List[str]) -> None:
        """将 ComfyUI output 目录的生成图片复制到持久化目录

        避免 ComfyUI output 定期清理导致图片丢失。
        持久化目录由 GENERATED_DIR 定义，在 main.py 中也挂载了静态文件服务。
        """
        if not filenames or not GENERATED_DIR:
            return
        from urllib.parse import urlparse, parse_qs

        os.makedirs(GENERATED_DIR, exist_ok=True)
        copied = 0
        for fname in filenames:
            if not fname:
                continue
            # 处理 URL 格式：/api/comfyui/image?filename=xxx.png → xxx.png
            parsed = urlparse(fname if "?" in fname else f"?filename={fname}")
            params = parse_qs(parsed.query)
            actual_name = params.get("filename", [None])[0] or fname
            actual_name = os.path.basename(actual_name)
            if not actual_name:
                continue

            subfolder = self._output_subfolders.get(actual_name, "")
            src = (
                os.path.join(self.config.output_dir, subfolder, actual_name)
                if subfolder
                else os.path.join(self.config.output_dir, actual_name)
            )
            dst = os.path.join(GENERATED_DIR, actual_name)
            if os.path.isfile(src) and (
                not os.path.isfile(dst) or os.path.getsize(src) != os.path.getsize(dst)
            ):
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                    logger.debug(f"[Persist] 已持久化 | {actual_name}")
                except OSError as e:
                    logger.warning(f"[Persist] 复制失败: {actual_name} | {e}")
            elif not os.path.isfile(dst):
                # 本地文件不存在，通过 HTTP 从 ComfyUI /view 回退拉取
                try:
                    import aiohttp
                    from services.comfyui.config import COMFYUI_BASE_URL

                    comfyui_base = COMFYUI_BASE_URL
                    view_url = f"{comfyui_base}/view?filename={actual_name}&type=output"
                    if subfolder:
                        view_url += f"&subfolder={subfolder}"
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as session:
                        async with session.get(view_url) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                with open(dst, "wb") as f:
                                    f.write(data)
                                copied += 1
                                logger.debug(f"[Persist] HTTP 回退持久化 | {actual_name}")
                except Exception as e:
                    logger.warning(f"[Persist] HTTP 回退失败: {actual_name} | {e}")
        if copied > 0:
            logger.info(f"[Persist] 本次持久化 {copied}/{len(filenames)} 个文件")

    async def start_output_cleanup_task(self, interval_hours: int = 6):
        """启动输出文件定期清理后台任务

        Args:
            interval_hours: 清理间隔（小时），默认 6 小时
        """

        async def _cleanup_loop():
            while True:
                await asyncio.sleep(interval_hours * 3600)
                try:
                    self._file_handler.cleanup_old_output_files()
                except Exception as e:
                    logger.warning(f"[ComfyUI] 输出文件定期清理失败: {e}")

        self._output_cleanup_task = asyncio.create_task(_cleanup_loop())
        logger.info(f"[ComfyUI] 输出文件定期清理已启动 | interval={interval_hours}h")

    async def stop_output_cleanup_task(self):
        """停止输出文件定期清理后台任务"""
        task = getattr(self, "_output_cleanup_task", None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("[ComfyUI] 输出文件定期清理已停止")

    def _mark_generation_active(self):
        """标记活跃生成开始，防止空闲定时器误杀"""
        self._active_generation = True
        self._last_used = time.time()  # 刷新使用时间（双重保护）

    def _mark_generation_complete(self):
        """标记活跃生成结束"""
        self._active_generation = False
        self._last_used = time.time()
        self._schedule_idle_shutdown()
