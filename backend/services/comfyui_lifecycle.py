"""
ComfyUI 服务 — 生命周期 Mixin

进程启动/停止、内存与显存协调、健康检查、空闲自停、输出清理。
"""

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiohttp

from services.comfyui.config import (
    COMFYUI_DIR,
    COMFYUI_PYTHON,
    MEMORY_HIGH_THRESHOLD,
    VRAM_HIGH_THRESHOLD,
)
from services.comfyui_helpers import (
    COMFYUI_BASE_URL,
    COMFYUI_SCRIPT,
    COMFYUI_START_TIMEOUT,
    DISABLE_PROCESS_MANAGEMENT,
    GENERATED_DIR,
    _analyze_reference_images,
    _apply_vision_cache,
    _get_ram_pct_safe,
    _load_vision_cache,
    _mem_log,
    _save_vision_cache,
    logger,
)

logger = logging.getLogger(__name__)


class ComfyUILifecycleMixin:
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
            logger.info(f"[Fix3] reset_generation_count | all | {self._model_generation_count}→{{'sd':0,'qwen':0}}")
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
        global GENERATED_DIR
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
            src = os.path.join(self.config.output_dir, subfolder, actual_name) if subfolder else os.path.join(self.config.output_dir, actual_name)
            dst = os.path.join(GENERATED_DIR, actual_name)
            if os.path.isfile(src) and (not os.path.isfile(dst) or os.path.getsize(src) != os.path.getsize(dst)):
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
                    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
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
        task = getattr(self, '_output_cleanup_task', None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("[ComfyUI] 输出文件定期清理已停止")
    async def _release_vram_for_comfyui(self):
        """
        为 ComfyUI 释放显存：停止 llama.cpp
        16GB 显存无法同时运行 llama + ComfyUI，必须交替。
        """
        try:
            from services.process_manager import get_llm_manager
            llm_mgr = get_llm_manager()
            if llm_mgr.is_running:
                _ram = _get_ram_pct_safe()
                logger.info(
                    f"[VRAM] 停止 llama.cpp → 为 ComfyUI 释放显存"
                    f" | RAM_before={_ram:.1f}%"
                )
                await llm_mgr.stop_for_comfyui()
                _ram_after = _get_ram_pct_safe()
                logger.info(
                    f"[VRAM] llama.cpp 已停止，显存已释放给 ComfyUI | RAM_after={_ram_after:.1f}%"
                )
            else:
                logger.info("[VRAM] llama.cpp 未在运行，无需停止（显存已归 ComfyUI）")
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[VRAM] 释放显存时出错: {e}")
    def _release_vram_for_llama(self):
        """
        ⭐ 为 llama.cpp VL 模型释放显存：停止 ComfyUI
        16GB 显存无法同时运行 llama + ComfyUI，必须交替。
        在视觉分析前调用，确保 Qwen3VL-8B 有足够显存。
        """
        if self._process is not None and self._process.poll() is None:
            _ram = _get_ram_pct_safe()
            logger.info(
                f"[VRAM] 停止 ComfyUI → 为 llama.cpp (Qwen3VL-8B) 释放显存"
                f" | RAM_before={_ram:.1f}% | ComfyUI_PID={self._process.pid}"
            )
            # ⭐ 标记 session 需要重建（同步方法无法 await close）
            self._http_session = None
            self.stop()
            _ram_after = _get_ram_pct_safe()
            logger.info(
                f"[VRAM] ComfyUI 已停止，显存已释放给 llama.cpp | RAM_after={_ram_after:.1f}%"
            )
        else:
            logger.info("[VRAM] ComfyUI 未在运行，无需停止（llama.cpp 可直接使用显存）")
    async def _pre_analyze_references(
        self,
        all_ref_items: List[Dict[str, Any]],
        project_id: Optional[str] = None,
        force_reanalyze: bool = False,
        progress_callback: Optional[Callable] = None,
    ) -> bool:
        """统一预分析所有参考图（带缓存 + 崩溃恢复）。

        流程：
        1. 尝试从项目目录 JSON 加载缓存
        2. 缓存命中 → 直接应用 visual_desc（跳过分析）
        3. 缓存未命中：
           a. 停止 ComfyUI（如运行中）释放显存
           b. 并行分析所有唯一参考图
           c. 结果写入 all_ref_items 的 visual_desc 字段
           d. 保存到项目目录 JSON（崩溃后可二次加载）

        Args:
            all_ref_items: 参考图列表（原地修改，添加 visual_desc）
            project_id: 项目 ID
            force_reanalyze: 强制重新分析（忽略缓存）
            progress_callback: 进度回调

        Returns:
            True 如果分析成功完成（或缓存命中）
        """
        if not all_ref_items:
            logger.info("[VisionPreAnalyze] 无参考图需要分析")
            return True

        total = len(all_ref_items)

        # ═══════════════════════════════════════════════════════════════
        # Step 1: 尝试加载缓存
        # ═══════════════════════════════════════════════════════════════
        items_to_analyze = list(all_ref_items)  # 待分析子集
        if not force_reanalyze:
            cache = _load_vision_cache(project_id) if project_id else None
            if cache:
                applied = _apply_vision_cache(all_ref_items, cache)
                remaining = total - applied
                if remaining == 0:
                    logger.info(
                        f"[VisionPreAnalyze] 全部命中缓存 ({total}/{total})，跳过分析"
                    )
                    return True
                # 部分命中：只需要分析未缓存的
                items_to_analyze = [item for item in all_ref_items if not item.get("visual_desc")]
                logger.info(
                    f"[VisionPreAnalyze] 部分命中: {applied}/{total} 缓存，"
                    f"剩余 {len(items_to_analyze)} 待分析"
                )
            else:
                logger.info(f"[VisionPreAnalyze] 无缓存，需分析 {total} 张参考图")
        else:
            logger.info(f"[VisionPreAnalyze] 强制重新分析 {total} 张参考图")

        if not items_to_analyze:
            return True

        # ═══════════════════════════════════════════════════════════════
        # Step 2: 停止 ComfyUI，释放显存给 llama.cpp VL
        # ═══════════════════════════════════════════════════════════════
        if progress_callback:
            try:
                progress_callback("🧹 停止生成引擎，准备视觉分析...", 0)
            except Exception:
                pass
        self._release_vram_for_llama()

        # ═══════════════════════════════════════════════════════════════
        # Step 3: 并行视觉分析（仅分析未缓存条目）
        # ═══════════════════════════════════════════════════════════════
        if progress_callback:
            try:
                progress_callback(f"🔍 开始分析 {len(items_to_analyze)} 张参考图...", 2)
            except Exception:
                pass

        await _analyze_reference_images(
            items_to_analyze,
            project_id=project_id,
            progress_callback=progress_callback,
        )

        # ═══════════════════════════════════════════════════════════════
        # Step 4: 保存缓存到项目目录 JSON（合并旧缓存 + 新分析结果）
        # ═══════════════════════════════════════════════════════════════
        if project_id:
            # 保存全部条目（含缓存命中的 + 新分析的）
            _save_vision_cache(project_id, all_ref_items)

        analyzed = sum(1 for item in items_to_analyze if item.get("visual_desc"))
        total_with_desc = sum(1 for item in all_ref_items if item.get("visual_desc"))
        logger.info(
            f"[VisionPreAnalyze] 分析完成: new={analyzed}/{len(items_to_analyze)}"
            f" | total_with_desc={total_with_desc}/{total}"
        )
        return total_with_desc > 0
    def _get_system_memory_usage(self) -> float:
        """获取系统内存使用百分比（基于 psutil）"""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except ImportError:
            logger.warning("[ComfyUI] psutil 未安装，使用 WMIC 回退")
            try:
                if os.name == 'nt':
                    import subprocess as sp
                    result = sp.run(
                        ['wmic', 'OS', 'get', 'FreePhysicalMemory,TotalVisibleMemorySize', '/format:csv'],
                        capture_output=True, text=True, timeout=5
                    )
                    lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
                    if len(lines) > 1:
                        parts = lines[1].split(',')
                        if len(parts) >= 3:
                            free = float(parts[1])
                            total = float(parts[2])
                            if total > 0:
                                return ((total - free) / total) * 100
                else:
                    with open('/proc/meminfo', 'r') as f:
                        lines = f.readlines()
                        mem = {}
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 2:
                                mem[parts[0]] = int(parts[1])
                        if 'MemTotal' in mem and 'MemAvailable' in mem:
                            used = mem['MemTotal'] - mem['MemAvailable']
                            return (used / mem['MemTotal']) * 100
            except Exception as e:
                logger.warning(f"[ComfyUI] WMIC/proc 内存获取失败: {e}")
            return 0.0
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取系统内存使用率失败: {e}")
            return 0.0
    async def _get_vram_usage(self) -> float:
        """获取 ComfyUI 显存使用百分比
        优先通过 system_stats API，回退到 nvidia-smi 命令行
        """
        # 优先 ComfyUI API
        try:
            session = self._get_http_session()
            async with session.get(
                f"{self.config.base_url}/system_stats",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if "devices" in data and len(data["devices"]) > 0:
                        device = data["devices"][0]
                        vram_total = device.get("vram_total", 0)
                        vram_free = device.get("vram_free", 0)
                        if vram_total > 0:
                            vram_used = vram_total - vram_free
                            return (vram_used / vram_total) * 100
                    # 旧版格式
                    if "vram_total" in data and data["vram_total"] > 0:
                        vram_used = data.get("vram_used", 0)
                        return (vram_used / data["vram_total"]) * 100
        except Exception as e:
            logger.debug(f"[ComfyUI] system_stats VRAM 获取失败，走 nvidia-smi: {e}")

        # 回退：nvidia-smi 命令行
        try:
            import subprocess as sp
            result = sp.run(
                ['nvidia-smi', '--query-gpu=memory.used,memory.total', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if not line.strip():
                        continue
                    parts = line.split(',')
                    if len(parts) >= 2:
                        used = float(parts[0].strip())
                        total = float(parts[1].strip())
                        if total > 0:
                            pct = (used / total) * 100
                            logger.debug(f"[ComfyUI] nvidia-smi VRAM: {used}/{total} MB ({pct:.1f}%)")
                            return pct
        except FileNotFoundError:
            logger.debug("[ComfyUI] nvidia-smi 不可用，跳过 VRAM 监测")
        except Exception as e:
            logger.warning(f"[ComfyUI] nvidia-smi 失败: {e}")

        return -1.0
    async def check_and_release_memory(self) -> bool:
        """
        检查内存和显存使用情况，如果过高则自动释放资源。
        返回 True 表示进行了释放操作。
        """
        released = False
        mem_percent = self._get_system_memory_usage()
        vram_percent = await self._get_vram_usage()

        logger.info(f"[ComfyUI] 内存使用: {mem_percent:.1f}%, 显存使用: {vram_percent:.1f}%")

        if mem_percent > MEMORY_HIGH_THRESHOLD:
            logger.warning(f"[ComfyUI] 内存使用率 {mem_percent:.1f}% 超过阈值 {MEMORY_HIGH_THRESHOLD}%，清理图片缓存...")
            self.clear_image_cache()
            # ⭐ 强制 Python 回收内存
            import gc
            gc.collect()
            # 等待 2 秒让 OS 回收内存
            await asyncio.sleep(2)
            mem_after = self._get_system_memory_usage()
            logger.info(f"[ComfyUI] gc.collect() 后内存: {mem_after:.1f}% (释放了 {mem_percent - mem_after:.1f}%)")
            # ⭐ GC 后内存仍 >95%，重启 ComfyUI 释放显存+内存
            if mem_after > 95:
                logger.warning(f"[ComfyUI] GC 后内存仍为 {mem_after:.1f}%，重启 ComfyUI 释放资源")
                await self._close_http_session()
                self.stop()
                # ⭐ 等待进程完全退出，内存释放后再启动
                await asyncio.sleep(3)
                gc.collect()
                await asyncio.sleep(2)
                await self.ensure_running()
                await asyncio.sleep(3)
                gc.collect()
                mem_after_restart = self._get_system_memory_usage()
                logger.info(f"[ComfyUI] 重启后内存: {mem_after_restart:.1f}%")
            released = True

        if vram_percent > VRAM_HIGH_THRESHOLD:
            logger.warning(f"[ComfyUI] 显存使用率 {vram_percent:.1f}% 超过阈值 {VRAM_HIGH_THRESHOLD}%，将通过重启释放 VRAM")
            await self._notify_restart("restarting", 20)
            await self._close_http_session()  # ⭐ 异步关闭 session
            self.stop()
            await self.ensure_running()
            await self._notify_restart("ready", 0)
            self._model_generation_count = {"sd": 0, "qwen": 0}
            released = True

        return released
    async def _quick_release_vram(self, unload_models: bool = False):
        """
        快速释放显存（调用 ComfyUI /free 端点，不重启进程）
        适用于流程切换时的轻量级清理

        Args:
            unload_models: 是否卸载模型（True=彻底释放显存，False=仅释放缓存）
        """
        # 先检查显存使用率，低于阈值则跳过
        vram_pct = await self._get_vram_usage()
        VRAM_QUICK_RELEASE_THRESHOLD = 70  # 仅在 VRAM>70% 时释放，避免不必要的等待
        if not unload_models and 0 <= vram_pct < VRAM_QUICK_RELEASE_THRESHOLD:
            logger.debug(f"[ComfyUI] 显存充足 ({vram_pct:.1f}%)，跳过 /free")
            return

        try:
            await self._client.free_vram(unload_models=unload_models)
        except Exception as e:
            logger.debug(f"[ComfyUI] /free 调用失败（可能不支持）: {e}")
            return

        # 等待显存释放完成（最多 3 秒）
        for _ in range(3):
            await asyncio.sleep(1)
            vram_pct = await self._get_vram_usage()
            if vram_pct >= 0 and vram_pct < 60:
                logger.info(f"[ComfyUI] 显存已释放: {vram_pct:.1f}%")
                return
        logger.info("[ComfyUI] /free 后显存仍较高，但不阻塞继续执行")
    async def _ensure_clean_state(self, model_family: str = "qwen"):
        """
        生成前确保 ComfyUI 处于干净状态。
        智能判断是否需要重启：
        1. 按模型类型分别计数
        2. 达到阈值时检查显存使用
        3. 显存充足则跳过重启，不足则重启
        
        Args:
            model_family: 模型类型 "sd" (Z-Image/瑶光) 或 "qwen" (Qwen Image Edit)
        """
        # 通过环境变量配置连续生成阈值
        # Qwen模型更大，默认5次（从3次提高，减少不必要的重启，每次重启耗时30~60s）
        # SD模型默认8次
        default_max = {"sd": 8, "qwen": 5}
        env_key = f"COMFYUI_MAX_{model_family.upper()}_GENERATIONS"
        max_gen = int(os.environ.get(env_key, default_max.get(model_family, 5)))
        
        count = self._model_generation_count.get(model_family, 0) + 1
        self._model_generation_count[model_family] = count

        logger.info(f"[ComfyUI] {model_family} 第 {count}/{max_gen} 次连续生成")

        if count >= max_gen:
            # 智能判断：先检查显存使用情况
            vram_percent = await self._get_vram_usage()
            
            if vram_percent >= 0 and vram_percent < 80:
                # 显存充足（<80%），仅释放缓存，不卸载模型（避免下次生成重载延迟）
                logger.info(f"[ComfyUI] 显存充足 ({vram_percent:.1f}%)，释放缓存")
                await self._quick_release_vram(unload_models=False)
                self._model_generation_count[model_family] = 0
                return
            
            # 显存不足或获取失败，执行重启
            if DISABLE_PROCESS_MANAGEMENT:
                logger.warning(
                    f"[ComfyUI] 已连续生成 {count} 次，显存使用率 {vram_percent:.1f}%，"
                    "但 DISABLE_PROCESS_MANAGEMENT=True 跳过重启"
                )
                self._model_generation_count[model_family] = 0
                return

            logger.warning(f"[ComfyUI] 已连续生成 {count} 次，显存使用率 {vram_percent:.1f}%，重启释放 VRAM")
            await self._notify_restart("restarting", 15)
            await self._close_http_session()  # ⭐ 异步关闭 session，避免 stop() 中的同步关闭问题
            self.stop()
            # ⭐ 等待进程完全退出，内存释放后再启动新进程
            await asyncio.sleep(3)
            import gc
            gc.collect()
            await asyncio.sleep(2)
            _mem_log("重启ComfyUI(内存已释放)", f"model={model_family}")
            await self.ensure_running()
            await self._notify_restart("ready", 0)
            self._model_generation_count[model_family] = 0
            logger.info("[ComfyUI] 重启完成，VRAM 已释放")
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
            logger.warning(
                "[ComfyUI] 未配置 COMFYUI_DIR，请设置环境变量或手动启动"
            )
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
            COMFYUI_PYTHON, COMFYUI_SCRIPT,
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
            self._process = subprocess.Popen(
                cmd,
                cwd=COMFYUI_DIR,
                env=env,
                stdout=_stdout,
                stderr=_stderr,
               # stdin=subprocess.DEVNULL,  # ⭐ Fix: 关闭 stdin 防止子进程卡住
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
            # 使用局部变量避免 TOCTOU 竞态条件
            proc = self._process
            if proc is None:
                logger.error("[ComfyUI] 进程引用已丢失（被其他协程停止），终止启动")
                self._restart_in_progress = False
                return False
            if proc.poll() is not None:
                logger.error(
                    f"[ComfyUI] 进程已退出，返回码: {proc.returncode}"
                )
                self._process = None
                self._restart_in_progress = False
                return False
            if await self._check_alive():
                logger.info("[ComfyUI] 就绪")
                _mem_log("ComfyUI就绪", "ComfyUI进程已启动并响应")
                self._restart_in_progress = False
                self._start_health_check()  # 启动健康检查任务
                return True

        logger.error(f"[ComfyUI] 启动超时")
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
                        f'taskkill /F /T /PID {proc.pid}',
                        capture_output=True, shell=True, timeout=5,
                        encoding='gbk', errors='replace',  # Windows 中文环境
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
    def _kill_process_on_port(port: int):
        """强制释放指定端口（Windows），防止端口占用导致重启失败
        
        ⭐ Fix 8 配套: 使用 taskkill /F /T /PID 杀进程树，避免孤儿进程残留。
        """
        import subprocess as sp  # 避免与 aiohttp 的 subprocess 混淆
        try:
            # 查找占用该端口的 PID
            result = sp.run(
                f'netstat -ano | findstr ":{port} "',
                capture_output=True, shell=True, timeout=5,
                encoding='gbk', errors='replace',  # Windows 中文环境用 gbk
            )
            if not result.stdout.strip():
                return
            seen = set()
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and (parts[3] or '').endswith(f':{port}'):
                    pid = parts[-1]
                    if pid not in seen:
                        seen.add(pid)
                        # ⭐ Fix 8: /T 杀进程树，避免孤儿进程
                        sp.run(f'taskkill /F /T /PID {pid}', capture_output=True, shell=True, timeout=5,
                               encoding='gbk', errors='replace')
                        logger.info(f"[WINDOWS] 已释放端口 {port} (PID={pid}, 含子进程树)")
        except Exception as e:
            logger.warning(f"[ComfyUI] 释放端口 {port} 失败: {e}")
    def _mark_generation_active(self):
        """标记活跃生成开始，防止空闲定时器误杀"""
        self._active_generation = True
        self._last_used = time.time()  # 刷新使用时间（双重保护）
    def _mark_generation_complete(self):
        """标记活跃生成结束"""
        self._active_generation = False
        self._last_used = time.time()
        self._schedule_idle_shutdown()
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
