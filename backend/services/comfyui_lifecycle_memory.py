"""
ComfyUI 服务 — 内存/显存协调 Mixin（从 comfyui_lifecycle.py 拆分，P2 治理）

为 ComfyUI / llama.cpp 交替释放显存、系统内存/显存占用检测、参考图预分析。
被 ComfyUILifecycleMixin 继承（MRO），方法用 self.xxx 调用主类其他 mixin。
"""

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui.config import (
    MEMORY_HIGH_THRESHOLD,
    VRAM_HIGH_THRESHOLD,
)
from services.comfyui_helpers import (
    DISABLE_PROCESS_MANAGEMENT,
    _analyze_reference_images,
    _apply_vision_cache,
    _get_ram_pct_safe,
    _load_vision_cache,
    _mem_log,
    _save_vision_cache,
)

logger = logging.getLogger(__name__)


class ComfyUILifecycleMemoryMixin:
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
                    f"[VRAM] 停止 llama.cpp → 为 ComfyUI 释放显存" f" | RAM_before={_ram:.1f}%"
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
                    logger.info(f"[VisionPreAnalyze] 全部命中缓存 ({total}/{total})，跳过分析")
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
                if os.name == "nt":
                    import subprocess as sp

                    result = sp.run(
                        [
                            "wmic",
                            "OS",
                            "get",
                            "FreePhysicalMemory,TotalVisibleMemorySize",
                            "/format:csv",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    lines = [x.strip() for x in result.stdout.strip().split("\n") if x.strip()]
                    if len(lines) > 1:
                        parts = lines[1].split(",")
                        if len(parts) >= 3:
                            free = float(parts[1])
                            total = float(parts[2])
                            if total > 0:
                                return ((total - free) / total) * 100
                else:
                    with open("/proc/meminfo", "r") as f:
                        lines = f.readlines()
                        mem = {}
                        for line in lines:
                            parts = line.split()
                            if len(parts) >= 2:
                                mem[parts[0]] = int(parts[1])
                        if "MemTotal" in mem and "MemAvailable" in mem:
                            used = mem["MemTotal"] - mem["MemAvailable"]
                            return (used / mem["MemTotal"]) * 100
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
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.split(",")
                    if len(parts) >= 2:
                        used = float(parts[0].strip())
                        total = float(parts[1].strip())
                        if total > 0:
                            pct = (used / total) * 100
                            logger.debug(
                                f"[ComfyUI] nvidia-smi VRAM: {used}/{total} MB ({pct:.1f}%)"
                            )
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
            logger.warning(
                f"[ComfyUI] 内存使用率 {mem_percent:.1f}% 超过阈值 {MEMORY_HIGH_THRESHOLD}%，清理图片缓存..."
            )
            self.clear_image_cache()
            # ⭐ 强制 Python 回收内存
            import gc

            gc.collect()
            # 等待 2 秒让 OS 回收内存
            await asyncio.sleep(2)
            mem_after = self._get_system_memory_usage()
            logger.info(
                f"[ComfyUI] gc.collect() 后内存: {mem_after:.1f}% (释放了 {mem_percent - mem_after:.1f}%)"
            )
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
            logger.warning(
                f"[ComfyUI] 显存使用率 {vram_percent:.1f}% 超过阈值 {VRAM_HIGH_THRESHOLD}%，将通过重启释放 VRAM"
            )
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

            logger.warning(
                f"[ComfyUI] 已连续生成 {count} 次，显存使用率 {vram_percent:.1f}%，重启释放 VRAM"
            )
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
