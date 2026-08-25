"""
ComfyUI 服务 — 分镜生成 Mixin 主类

P2 治理：批量生成/中间产物方法拆至 comfyui_storyboard_batch.py。
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

from services.comfyui_helpers import (
    ComfyUIGenResult,
    StoryboardStepResult,
    _crop_turnaround_to_front_view,
    _get_ram_pct_safe,
    _get_step_progress_range,
    _mem_log,
    _update_workflow_input,
)

from services.comfyui_storyboard_batch import ComfyUIStoryboardBatchMixin

logger = logging.getLogger(__name__)


class ComfyUIStoryboardMixin(ComfyUIStoryboardBatchMixin):
    async def generate_storyboard(
        self,
        reference_images: Dict[str, str] = None,  # {"character": url, "scene": url, "prop": url}
        prompt_text: str = "",
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        character_desc: str = "",
        scene_desc: str = "",
        prop_desc: str = "",
        full_prompt: Optional[str] = None,
        reference_items: Optional[List[Dict[str, str]]] = None,  # 多参考图列表
        reference_labels: Optional[List[Dict[str, str]]] = None,  # 保留接口兼容，不再使用
        shot_id: Optional[str] = None,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        enable_resume: bool = False,
        denoise: float = 1.0,  # Fish 融合固定 denoise=1，保留参数兼容接口
        cfg: float = 1.0,  # Fish 融合固定 cfg=1，保留参数兼容接口
        character_count: int = 1,
        fusion_mode: str = "3img",
        previous_shot_url: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        template: Optional[
            str
        ] = None,  # ⭐ V6.0: 分镜模板类型 (costume_change/multi_frame/panorama/pose_transfer)
        per_frame_prompts: Optional[List[str]] = None,  # ⭐ V6.0: 多帧分镜的每帧提示词
        pose_reference_image: str = "",  # ⭐ V6.0: 姿态迁移的参考图
        **kwargs,  # 透传额外参数到 storyboard_generation_v2
    ) -> ComfyUIGenResult:
        """
        分镜阶段：支持多模板工作流。

        ⭐ V6.0: 根据 template 参数路由到不同模板：
        - "costume_change": 分镜换装（Fish融合, 3图输入）
        - "multi_frame": 多帧分镜（next-scene LoRA, 逐帧生成）
        - "panorama": 全景图（单图输入, 全景视角）
        - "pose_transfer": 姿态迁移（人物图+姿态参考图）
        - None/默认: 兼容旧版 Fish 融合

        Args:
            reference_images: 3张固定参考图 {"character": url, "scene": url, "prop": url}
            prompt_text: 分镜文本指令
            seed: 随机种子
            progress_callback: 进度回调函数
            full_prompt: 直接使用的完整提示词
            reference_items: 多参考图条目列表
            project_id: 项目 ID
            character_count: 角色数量
            fusion_mode: "2img" 两图融合 | "3img" 三图融合
            previous_shot_url: 基于融合图重新生成

        Returns:
            ComfyUIGenResult: 生成结果
        """
        logger.info(
            f"[ComfyUI][分镜] 方法入口 | shot={shot_id} | project={project_id} | fusion={fusion_mode}"
        )
        _mem_log("分镜入口", f"shot={shot_id} project={project_id}")
        self._mark_generation_active()

        # 解析参考图（需要已解析的文件名）
        all_ref_items = reference_items or []
        if not all_ref_items and reference_images:
            for key in ("character", "scene", "prop"):
                url = (reference_images or {}).get(key, "")
                if url:
                    all_ref_items.append({"type": key, "url": url, "name": key, "desc": ""})

        for item in all_ref_items:
            url = item.get("image_url") or item.get("url", "")
            if url:
                resolved = await self._ensure_image_in_input_dir(url, project_id=project_id or "")
                item["resolved"] = resolved
                _mem_log("参考图解析", f"type={item.get('type', '?')} resolved={resolved}")

        # 构建 type → filename 映射
        workflow_refs: Dict[str, str] = {}
        for item in all_ref_items:
            resolved = item.get("resolved", "")
            if resolved:
                item_type = item.get("type", "")
                if item_type and item_type not in workflow_refs:
                    workflow_refs[item_type] = resolved
                elif item_type and item_type in workflow_refs:
                    suffix = 2
                    while f"{item_type}{suffix}" in workflow_refs:
                        suffix += 1
                    workflow_refs[f"{item_type}{suffix}"] = resolved

        logger.info(
            f"[ComfyUI] generate_storyboard → template={template or 'Fish融合'}"
            f" | chars={character_count}"
            f" | refs={len(all_ref_items)}, workflow_refs={list(workflow_refs.keys())}"
        )
        return await self.storyboard_generation_v2(
            project_id=project_id or "unknown",
            prompt_text=full_prompt or prompt_text,
            reference_images=workflow_refs,
            reference_items=all_ref_items,
            character_count=character_count,
            seed=seed,
            progress_callback=progress_callback,
            fusion_mode=fusion_mode,
            previous_shot_url=previous_shot_url,
            width=width,
            height=height,
            shot_id=shot_id,
            template=template,
            per_frame_prompts=per_frame_prompts,
            pose_reference_image=pose_reference_image,
            **kwargs,  # 透传额外参数到 build_storyboard_workflow_v2
        )

    def _detect_and_crop_turnaround(
        self, all_ref_items, template, reference_images, trace_id, progress_callback
    ) -> int:
        """三视图参考图检测与裁剪（V6.0：从图片本身检测，裁剪为单视图面板）"""
        # 1.4 三视图参考图检测与裁剪（在 Phase-2 之前）
        # ═══════════════════════════════════════════════════════════
        # 根因：三视图参考图作为像素直接输入 Qwen Image Edit 模型，
        # 视觉信号强度远超文本约束 → 必须从像素层面裁剪掉多余面板
        # ⭐ 3视图模板的输入是单张概念图，不需要裁剪
        # ⭐ V3.0 视觉分析已禁用，desc/visual_desc 为空，改为从图片本身检测
        TURNAROUND_PATTERNS = [
            "三张照片拼接",
            "正面.*侧身.*背面",
            "正面.*背面.*侧身",
            "不同角度展示",
            "多视角",
            "三视图",
            "三根造型相似",
            "三个视角",
            "多角度视图",
            "正面照.*侧面照.*背面照",
            "正面、侧面、背面",
            "正面、背面、侧面",
        ]
        input_dir = os.path.join(self.config.comfyui_dir, "input")
        cropped_count = 0
        for item in all_ref_items or []:
            # 3视图模板跳过裁剪
            if template == "3view":
                continue

            # 优先从 desc/visual_desc 检测（V3.0 已禁用，通常为空）
            desc = (item.get("visual_desc", "") or item.get("desc", "")).lower()
            # fallback: 从 item type/role 检测
            is_turnaround = any(
                re.search(pat, desc, re.IGNORECASE) for pat in TURNAROUND_PATTERNS
            )
            # 如果 desc 为空，尝试从图片宽高比检测（三视图通常是宽图）
            if not is_turnaround and not desc:
                resolved_fn = item.get("resolved", "")
                if resolved_fn:
                    try:
                        from PIL import Image

                        img_path = os.path.join(input_dir, resolved_fn)
                        if os.path.exists(img_path):
                            with Image.open(img_path) as img:
                                w, h = img.size
                                # 三视图拼接图通常宽高比 > 2.5
                                if w > h * 2.5:
                                    is_turnaround = True
                                    logger.info(
                                        f"[StoryboardV2] [{trace_id}] "
                                        f"检测到宽图(可能为三视图) | {w}x{h} | ratio={w/h:.2f}"
                                    )
                    except Exception as e:
                        logger.debug(f"[StoryboardV2] 图片宽高比检测失败: {e}")

            if not is_turnaround:
                continue

            item_type = item.get("type", "unknown")
            logger.warning(
                f"[StoryboardV2] [{trace_id}] ⚠️ 检测到三视图参考图"
                f" type={item_type}，将裁剪为单视图面板"
            )

            # 查找该 item 对应的已解析文件名
            resolved_fn = item.get("resolved", "")
            if not resolved_fn:
                # fallback: 遍历 reference_images 找匹配的 type
                for key, ref_fn in reference_images.items():
                    if key == item_type or key.startswith(item_type):
                        # 验证文件存在
                        test_path = os.path.join(input_dir, ref_fn)
                        if os.path.exists(test_path):
                            resolved_fn = ref_fn
                            break

            if resolved_fn:
                cropped_fn = _crop_turnaround_to_front_view(input_dir, resolved_fn, trace_id)
                if cropped_fn:
                    # ⭐ 更新 reference_images（构建工作流时使用）
                    for key, ref_fn in list(reference_images.items()):
                        if ref_fn == resolved_fn:
                            reference_images[key] = cropped_fn
                            logger.info(
                                f"[StoryboardV2] [{trace_id}] "
                                f"reference_images['{key}']: {resolved_fn} → {cropped_fn}"
                            )
                            break
                    # ⭐ 更新 item.resolved（后续引用）
                    item["resolved"] = cropped_fn
                    cropped_count += 1
            else:
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] 无法找到 type={item_type} "
                    f"的已解析文件，跳过裁剪"
                )

        if cropped_count > 0:
            logger.warning(
                f"[StoryboardV2] [{trace_id}] ⚠️ 三视图裁剪完成: "
                f"{cropped_count} 张参考图已替换为单视图面板"
            )
            if progress_callback:
                progress_callback(
                    f"✂️ 已裁剪 {cropped_count} 张三视图参考（保留正视图面板）...",
                    42,
                )
        return cropped_count

    async def storyboard_generation_v2(
        self,
        project_id: str,
        prompt_text: str,
        reference_images: Dict[str, str],
        reference_items: List[Dict[str, Any]],
        character_count: int = 1,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        fusion_mode: str = "3img",
        previous_shot_url: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        shot_id: Optional[str] = None,
        template: Optional[str] = None,  # ⭐ V6.0: 分镜模板类型
        per_frame_prompts: Optional[List[str]] = None,  # ⭐ V6.0: 多帧分镜每帧提示词
        pose_reference_image: str = "",  # ⭐ V6.0: 姿态迁移参考图
        **kwargs,
    ) -> ComfyUIGenResult:
        """分镜生成：支持多模板

        ⭐ V6.0: 根据 template 参数路由到不同模板：
        - "costume_change": 分镜换装（Fish融合, 3图输入）
        - "multi_frame": 多帧分镜（next-scene LoRA, 逐帧生成）
        - "panorama": 全景图（单图输入, 全景视角）
        - "pose_transfer": 姿态迁移（人物图+姿态参考图）
        - None/默认: 兼容旧版 Fish 融合

        Args:
            project_id: 项目ID
            prompt_text: 分镜指令
            reference_images: 参考图片字典 {"character": fn, "scene": fn, ...}
            reference_items: 参考图条目列表
            character_count: 角色数量
            seed: 随机种子
            progress_callback: 进度回调 (msg, pct) → None
            fusion_mode: "2img" 两图融合 | "3img" 三图融合
            previous_shot_url: 基于融合图重新生成
            width: 图像宽度（可选，覆盖工作流默认值）
            height: 图像高度（可选，覆盖工作流默认值）
            template: 模板类型 (costume_change/multi_frame/panorama/pose_transfer)
            per_frame_prompts: 多帧分镜的每帧提示词列表
            pose_reference_image: 姿态迁移的参考图路径
        """
        import secrets
        from services.structured_logging import get_trace_id

        # 复用全局 trace_id（由 batch_task_service 设置），无则生成临时 id
        trace_id = get_trace_id()[:12] if get_trace_id() else secrets.token_hex(6)
        actual_seed = seed or secrets.randbelow(2**31)
        # ⭐ Fix 3: 分镜阶段入口，重置 sd 计数
        self.reset_generation_count("sd")
        # ⭐ V6.0: 步数由模板决定，默认1步
        total_steps = 1
        step_results: List[StoryboardStepResult] = []

        _mem_log("V2入口", f"trace={trace_id} chars={character_count}")

        logger.info(
            f"[StoryboardV2] [{trace_id}] 开始分镜生成"
            f" | chars={character_count}, steps={total_steps}"
            f" | seed={actual_seed}"
            f" | refs={reference_images or {}}"
        )

        try:
            _t0 = time.time()
            all_ref_items = list(reference_items)

            # ═══════════════════════════════════════════════════════════
            # Phase 1: Vision Analysis — ⭐ V3.0 已禁用
            # ═══════════════════════════════════════════════════════════
            # 视觉分析产出被 DeepSeek 转为结构化标签后 Qwen 无法正确解析，
            # 对融合质量无帮助且耗时 1.5~3 分钟（含 ComfyUI 启停）。
            # 改用 V3.0 固定增强提示词（含尺度/透视/光影指令）替代。
            logger.info(
                f"[StoryboardV2] [{trace_id}] ⭐ V3.0 跳过视觉分析阶段" f" | 使用固定增强提示词替代"
            )
            if progress_callback:
                progress_callback("⚡ 跳过视觉分析，直接进入融合...", 40)

            # ═══════════════════════════════════════════════════════════
            self._detect_and_crop_turnaround(
                all_ref_items, template, reference_images, trace_id, progress_callback
            )

            # ═══════════════════════════════════════════════════════════
            # Phase 2: Generation（ComfyUI 独占显存）
            # ═══════════════════════════════════════════════════════════
            # 2. 停止 llama.cpp，释放显存
            logger.info(f"[StoryboardV2] [{trace_id}] Phase-2 开始: 图像生成 (ComfyUI 独占)")
            _mem_log("停止llama前", f"trace={trace_id}")
            await self._release_vram_for_comfyui()
            _mem_log("停止llama后", f"trace={trace_id}")

            # ⭐ V6.0 优化：如果 ComfyUI 已在运行，直接复用，不重启
            # 旧逻辑：每次都 stop → sleep(5s) → ensure_running(加载模型30-60s)
            # 新逻辑：只在内存不足时才重启，否则直接使用
            comfyui_alive = await self._check_alive()
            _ram_now = _get_ram_pct_safe()

            if comfyui_alive and _ram_now < 95:
                # ComfyUI 在运行且内存充足 → 直接复用，跳过重启
                logger.info(
                    f"[StoryboardV2] [{trace_id}] ComfyUI 已在运行且内存充足"
                    f" | RAM={_ram_now:.1f}% | 跳过重启，直接复用"
                )
                _mem_log("ComfyUI复用(跳过重启)", f"trace={trace_id} RAM={_ram_now:.1f}%")
            elif self._process is not None:
                # ComfyUI 在运行但内存紧张(RAM>=95%) → 停止后重启
                logger.info(
                    f"[StoryboardV2] [{trace_id}] 内存紧张 RAM={_ram_now:.1f}%，"
                    f"停止 ComfyUI 释放内存后重启"
                )
                _mem_log("停止ComfyUI前(释放内存)", f"trace={trace_id} RAM={_ram_now:.1f}%")
                await self._close_http_session()
                self.stop()
                await asyncio.sleep(3)
                import gc

                gc.collect()
                await asyncio.sleep(2)
                _ram_after_stop = _get_ram_pct_safe()
                _mem_log(
                    "停止ComfyUI后(内存已释放)",
                    f"trace={trace_id} RAM={_ram_after_stop:.1f}% freed={_ram_now - _ram_after_stop:.1f}%",  # noqa: E501
                )
                logger.info(
                    f"[StoryboardV2] [{trace_id}] ComfyUI 已停止，内存释放"
                    f" | RAM: {_ram_now:.1f}% → {_ram_after_stop:.1f}%"
                    f" | 释放了 {_ram_now - _ram_after_stop:.1f}%"
                )

            if progress_callback:
                progress_callback("⚡ 启动生成引擎...", 45)

            # 3. 确保 ComfyUI 运行（如果已在运行则秒级返回）
            _mem_log("启动ComfyUI前", f"trace={trace_id}")
            ready = await self.ensure_running()
            _mem_log("启动ComfyUI后", f"trace={trace_id} ready={ready}")
            if not ready:
                raise RuntimeError(
                    "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                    "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
                )
            logger.info(
                f"[StoryboardV2] [{trace_id}] ComfyUI 就绪，开始生成工作流"
                f" | RAM={_get_ram_pct_safe():.1f}%"
            )
            # ⭐ 超分模板使用 SeedVR2 模型（sd 族），不需要 qwen 清理
            # 三视图/其他模板使用 Qwen Image Edit 模型（qwen 族）
            # 提取类(姿态/线稿/深度图/三合一)和超分模板使用 sd 模型族，其他使用 qwen
            _sd_templates = {
                "upscale",
                "pose_extraction",
                "lineart_extraction",
                "depth_map",
                "extract_all",
            }
            await self._ensure_clean_state("sd" if template in _sd_templates else "qwen")

            # 1.5 DeepSeek 优化提示词 — ⭐ V5.0 永久禁用
            # V6.0 模板系统直接使用用户 prompt
            optimized_prompt = prompt_text
            logger.info(
                f"[StoryboardV2] [{trace_id}] ⭐ V6.0: 直接使用用户提示词"
                f"（长度={len(prompt_text)}）| template={template}"
            )
            if progress_callback:
                progress_callback("⚡ 分镜生成: 使用用户提示词...", 48)

            # 2. 构建工作流列表
            from services.workflow_builder import build_storyboard_workflow_v2

            logger.info(
                f"[StoryboardV2] [{trace_id}] 构建工作流 | template={template} | refs={reference_images}"
            )
            workflows, step_names, metadata = build_storyboard_workflow_v2(
                reference_images=reference_images,
                prompt_text=optimized_prompt,
                seed=actual_seed,
                filename_prefix=kwargs.pop("filename_prefix", f"{project_id[-6:]}_storyboard"),
                character_count=character_count,
                fusion_mode=fusion_mode,
                previous_shot_url=previous_shot_url,
                width=width,
                height=height,
                template=template,
                per_frame_prompts=per_frame_prompts,
                pose_reference_image=pose_reference_image,
                **kwargs,  # 透传额外参数到 build_storyboard_workflow_v2
            )

            # ⭐ V5.0: 记录每个 step 的 Fish 融合节点10/11/12初始赋值
            for si, (wf, sname) in enumerate(zip(workflows, step_names)):
                # 诊断日志：读取 LoadImage 和 TextEncode 节点的参数
                from services.workflow_builder import (
                    find_node_by_class_type,
                    find_first_node_by_class_type_contains,
                )

                _diag_loads = (
                    find_node_by_class_type(wf, "LoadImage") if isinstance(wf, dict) else []
                )
                _diag_loads.sort(key=lambda x: x[0])
                node10 = (
                    _diag_loads[0][1]["inputs"].get("image", "N/A")
                    if len(_diag_loads) >= 1
                    else "N/A"
                )
                node11 = (
                    _diag_loads[1][1]["inputs"].get("image", "N/A")
                    if len(_diag_loads) >= 2
                    else "N/A"
                )
                node12 = (
                    _diag_loads[2][1]["inputs"].get("image", "N/A")
                    if len(_diag_loads) >= 3
                    else "N/A"
                )
                node22_prompt = ""
                _nid_enc, _ndata_enc = (
                    find_first_node_by_class_type_contains(wf, "QwenImageEditPlusAdvance")
                    if isinstance(wf, dict)
                    else (None, None)
                )
                if _nid_enc and _ndata_enc:
                    node22_prompt = _ndata_enc.get("inputs", {}).get("prompt", "")[:60]
                logger.info(
                    f"[StoryboardV2] [{trace_id}] Step{si+1} '{sname}' 初始赋值: "
                    f"10(图1/角色)={node10}, 11(图2/场景)={node11}, 12(图3/道具)={node12}"
                    f" | prompt={node22_prompt}..."
                )

            # 3. 逐步骤执行（进度: 50%~100%）
            step_count = len(workflows)
            current_image = None
            all_filenames: List[str] = []  # ⭐ V6.0: 收集所有步骤的输出文件名

            logger.info(
                f"[StoryboardV2] [{trace_id}] 开始逐步骤执行"
                f" | total_steps={step_count}"
                f" | step_names={step_names}"
            )

            # ⭐ 系统RAM安全检查：防止OOM导致ComfyUI崩溃
            sys_ram = self._get_system_memory_usage()
            if sys_ram > 95:
                # ⭐ 先尝试 GC + 等待内存释放，而不是直接放弃
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] 系统RAM使用率 {sys_ram:.1f}% 超过95%，"
                    f"尝试GC回收 + 等待内存释放..."
                )
                import gc

                gc.collect()
                self.clear_image_cache()
                # 等待最多30秒让内存释放
                for _wait_i in range(6):
                    await asyncio.sleep(5)
                    sys_ram = self._get_system_memory_usage()
                    logger.info(f"[StoryboardV2] [{trace_id}] 等待内存释放... RAM={sys_ram:.1f}%")
                    if sys_ram <= 92:
                        break
                if sys_ram > 95:
                    # ⭐ GC 无效时，尝试重启 ComfyUI 释放显存+内存（比直接放弃更好）
                    logger.warning(
                        f"[StoryboardV2] [{trace_id}] GC 后内存仍为 {sys_ram:.1f}%，"
                        f"尝试重启 ComfyUI 释放资源..."
                    )
                    try:
                        await self._close_http_session()
                        self.stop()
                        await self.ensure_running()
                        await asyncio.sleep(3)
                        gc.collect()
                        sys_ram = self._get_system_memory_usage()
                        logger.info(f"[StoryboardV2] [{trace_id}] 重启后内存 RAM={sys_ram:.1f}%")
                        if sys_ram > 95:
                            logger.critical(
                                f"[StoryboardV2] [{trace_id}] 重启后内存仍为 {sys_ram:.1f}%，放弃分镜生成"
                            )
                            self._mark_generation_complete()
                            raise RuntimeError(
                                f"系统内存不足（{sys_ram:.1f}%），无法执行分镜生成。"
                                f"请关闭其他程序后重试。"
                            )
                    except RuntimeError:
                        raise
                    except Exception as _restart_err:
                        logger.error(f"[StoryboardV2] [{trace_id}] 重启失败: {_restart_err}")
                        self._mark_generation_complete()
                        raise RuntimeError(
                            f"系统内存不足（{sys_ram:.1f}%），重启 ComfyUI 失败。"
                            f"请关闭其他程序后重试。"
                        )
            elif sys_ram > 90:
                logger.warning(
                    f"[StoryboardV2] [{trace_id}] 系统RAM使用率较高 ({sys_ram:.1f}%)，"
                    f"执行gc回收 + 清理缓存"
                )
                import gc

                gc.collect()
                self.clear_image_cache()

            for i, (wf, step_name) in enumerate(zip(workflows, step_names), 1):
                ng_start, ng_end = _get_step_progress_range(i, step_count)

                if progress_callback:
                    progress_callback(
                        f"🔄 Step{i}/{step_count}: {step_name} (denoise=1.0, cfg=1.0)",
                        ng_start,
                    )

                step_start = time.time()
                _mem_log(f"Step{i}开始", f"trace={trace_id} name={step_name}")

                # ⭐ Step1 融合步骤 — 记录初始节点文件状态
                if i == 1:
                    try:
                        _input_dir = os.path.join(self.config.comfyui_dir, "input")
                        _s1_parts = []
                        for _nid in ("10", "11", "12"):
                            _fname = wf.get(_nid, {}).get("inputs", {}).get("image", "")
                            if isinstance(_fname, str) and _fname:
                                _fpath = os.path.join(_input_dir, _fname)
                                _exists = os.path.exists(_fpath)
                                _sz = os.path.getsize(_fpath) if _exists else 0
                                _s1_parts.append(f"节点{_nid}={_fname}(存在={_exists},大小={_sz}B)")
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] Step{i} 融合步骤 初始文件: "
                            + ", ".join(_s1_parts)
                        )
                    except Exception as diag_err:
                        logger.warning(
                            f"[StoryboardV2] [{trace_id}] Step{i} 诊断记录异常(非致命): {diag_err}"
                        )

                # ⭐ 多步骤时：前一步产物作为输入，但保留场景参考
                # ⭐ BUG#7 修复：分层渲染的 A/B 组是独立工作流，不应链式注入
                if current_image and i > 1 and template != "layered_render":
                    # 保存场景文件名，更新工作流后恢复
                    from services.workflow_builder import find_node_by_class_type

                    load_nodes = find_node_by_class_type(wf, "LoadImage")
                    load_nodes.sort(key=lambda x: x[0])
                    scene_file = ""
                    if len(load_nodes) >= 2:
                        scene_file = wf[load_nodes[1][0]]["inputs"].get("image", "")
                    _update_workflow_input(wf, current_image, task_id=trace_id)
                    if scene_file and len(load_nodes) >= 2:
                        wf[load_nodes[1][0]]["inputs"]["image"] = scene_file
                    # ⭐ 诊断：检查文件状态
                    input_dir = os.path.join(self.config.comfyui_dir, "input")

                    def _check_file(fname):
                        if not fname:
                            return (False, 0)
                        path = os.path.join(input_dir, fname)
                        if os.path.exists(path):
                            return (True, os.path.getsize(path))
                        return (False, 0)

                    scene_ok, scene_sz = _check_file(scene_file)
                    char_file = wf.get("10", {}).get("inputs", {}).get("image", "")
                    char_ok, char_sz = _check_file(char_file)
                    cur_ok, cur_sz = _check_file(current_image) if current_image else (False, 0)
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} 融合步骤 文件状态: "
                        f"节点11(场景)={scene_file} 存在={scene_ok} 大小={scene_sz}B, "
                        f"节点10(角色)={char_file} 存在={char_ok} 大小={char_sz}B, "
                        f"current_image={current_image} 存在={cur_ok} 大小={cur_sz}B"
                    )

                # ⭐ 提交前记录关键节点参数
                try:
                    _nid_enc2, _ndata_enc2 = (
                        find_first_node_by_class_type_contains(wf, "QwenImageEditPlusAdvance")
                        if isinstance(wf, dict)
                        else (None, None)
                    )
                    if _nid_enc2 and _ndata_enc2:
                        _p = _ndata_enc2.get("inputs", {}).get("prompt", "")
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] Step{i} 节点{_nid_enc2}(prompt)={_p[:80]}"
                        )
                    # 查找 KSampler 节点（Fish 模板为节点6，通用查找）
                    for _ks_nid, _ks_node in wf.items():
                        if isinstance(_ks_node, dict) and _ks_node.get("class_type") == "KSampler":
                            _ks = _ks_node["inputs"]
                            logger.info(
                                f"[StoryboardV2] [{trace_id}] Step{i} 节点{_ks_nid}(KSampler)="
                                f"denoise={_ks.get('denoise')}, cfg={_ks.get('cfg')}, steps={_ks.get('steps')}, "  # noqa: E501
                                f"sampler={_ks.get('sampler_name')}, scheduler={_ks.get('scheduler')}"  # noqa: E501
                            )
                            break
                    # ⭐ 提交前重新检查所有输入图文件状态
                    _input_dir = os.path.join(self.config.comfyui_dir, "input")
                    _precheck_parts = []
                    for _nid in ("10", "11", "12"):
                        _fname = wf.get(_nid, {}).get("inputs", {}).get("image", "")
                        if isinstance(_fname, str) and _fname:
                            _fpath = os.path.join(_input_dir, _fname)
                            _exists = os.path.exists(_fpath)
                            _sz = os.path.getsize(_fpath) if _exists else 0
                            _precheck_parts.append(
                                f"节点{_nid}={_fname}(存在={_exists},大小={_sz}B)"
                            )
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} 提交前图片检查: "
                        + ", ".join(_precheck_parts)
                    )
                except Exception as diag_err:
                    logger.warning(
                        f"[StoryboardV2] [{trace_id}] Step{i} 提交前诊断异常(非致命): {diag_err}"
                    )

                # 步骤执行（单次，重试由 DagExecutor._run_single_step 统一控制）
                logger.info(
                    f"[StoryboardV2] [{trace_id}] Step{i} 准备提交工作流 | template={template} | nodes={len(wf)} | wf_type={type(wf).__name__}"  # noqa: E501
                )
                _mem_log(f"Step{i}提交前", f"trace={trace_id} step={step_name}")
                try:
                    prompt_id = await self._queue_prompt_with_retry(wf)
                    filenames = await self._wait_for_completion(
                        prompt_id,
                        progress_callback=(
                            (
                                lambda msg, pct: progress_callback(
                                    msg,
                                    int(ng_start + (pct * (ng_end - ng_start)) / 100),
                                )
                            )
                            if progress_callback
                            else None
                        ),
                        task_type="storyboard",
                    )

                    if not filenames:
                        raise RuntimeError(f"Step{i} {step_name} 无输出文件")

                    # 3视图/全景图模板有多个SaveImage（中间图+最终拼接），取最终拼接图为主图
                    if template == "panorama":
                        # 记录所有输出文件，方便排查拼接图缺失问题
                        logger.info(f"[StoryboardV2] [{trace_id}] 全景图输出文件列表: {filenames}")
                        if len(filenames) > 1:
                            final_files = [
                                f
                                for f in filenames
                                if os.path.basename(f).startswith("panorama_final_")
                            ]
                            if final_files:
                                current_image = final_files[0]
                                logger.info(
                                    f"[StoryboardV2] [{trace_id}] 全景图选择最终拼接 | file={current_image}"
                                )
                            else:
                                current_image = filenames[-1]
                                logger.warning(
                                    f"[StoryboardV2] [{trace_id}] 全景图未找到panorama_final_文件，"
                                    f"回退到最后一个输出 | file={current_image}"
                                )
                        else:
                            current_image = filenames[0]
                            logger.warning(
                                f"[StoryboardV2] [{trace_id}] 全景图仅有1个输出文件，"
                                f"拼接节点可能未执行 | file={current_image}"
                            )
                    elif template == "3view" and len(filenames) > 1:
                        current_image = filenames[-1]
                    else:
                        current_image = filenames[0]
                    all_filenames.extend(filenames)  # ⭐ V6.0: 收集所有输出文件
                    step_elapsed = int((time.time() - step_start) * 1000)
                    _mem_log(
                        f"Step{i}完成",
                        f"trace={trace_id} file={current_image} elapsed={step_elapsed}ms",
                    )
                    step_results.append(
                        StoryboardStepResult(
                            step_index=i,
                            step_name=step_name,
                            filename=current_image,
                            elapsed_ms=step_elapsed,
                        )
                    )

                    # 持久化中间结果
                    await self._save_step_intermediate(
                        project_id=project_id,
                        trace_id=trace_id,
                        step_index=i,
                        step_name=step_name,
                        image_filename=current_image,
                        metadata={
                            "elapsed_ms": step_elapsed,
                            "denoise": 1.0,
                            "cfg": 1.0,
                        },
                    )

                    # 检查输出文件大小
                    _output_path = (
                        os.path.join(self.config.output_dir, current_image) if current_image else ""
                    )
                    _output_sz = (
                        os.path.getsize(_output_path)
                        if _output_path and os.path.exists(_output_path)
                        else 0
                    )
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i}/{step_count} 完成"
                        f" | {step_name} | elapsed={step_elapsed}ms"
                        f" | file={current_image} | 大小={_output_sz}B"
                    )
                    if progress_callback:
                        progress_callback(f"✅ Step{i}/{step_count}: {step_name} 完成", ng_end)
                except Exception as step_err:
                    logger.error(
                        f"[StoryboardV2] [{trace_id}] Step{i} 失败"
                        f" | error={step_err}（重试由 DagExecutor 控制）"
                    )
                    raise

                # ⭐ V1.4: 步骤间释放显存 + VRAM 检查（防 Qwen 模型 OOM）
                if current_image and i < step_count:
                    vram_before = await self._get_vram_usage()
                    await self._quick_release_vram()
                    vram_after = await self._get_vram_usage()
                    logger.info(
                        f"[StoryboardV2] [{trace_id}] Step{i} VRAM: "
                        f"{vram_before:.1f}%→{vram_after:.1f}%"
                    )
                    # ⭐ V1.6: 累积保护 — 每 3 步或 VRAM > 85% 时执行深度清理（重启 ComfyUI）
                    VRAM_CRITICAL = 85
                    CLEANUP_INTERVAL_STEPS = 3
                    if vram_after > VRAM_CRITICAL or (i > 0 and i % CLEANUP_INTERVAL_STEPS == 0):
                        logger.warning(
                            f"[StoryboardV2] [{trace_id}] 触发深度VRAM清理"
                            f" | step={i}, vram={vram_after:.1f}%"
                            + (
                                f", 超过{VRAM_CRITICAL}%阈值"
                                if vram_after > VRAM_CRITICAL
                                else f", 每{CLEANUP_INTERVAL_STEPS}步例行"
                            )
                        )
                        if progress_callback:
                            progress_callback(
                                f"🔄 深度清理显存 ({vram_after:.0f}%)，重启引擎...",
                                ng_end,
                            )
                        # 深度清理：重启 ComfyUI 彻底释放 VRAM
                        await self._notify_restart("restarting", 20)
                        await self._close_http_session()
                        self.stop()
                        # ⭐ 等待进程完全退出，内存释放后再启动
                        await asyncio.sleep(3)
                        import gc as _gc

                        _gc.collect()
                        await asyncio.sleep(2)
                        _mem_log(f"Step{i}深度清理后", f"trace={trace_id}")
                        await self.ensure_running()
                        await self._notify_restart("ready", 0)
                        vram_after = await self._get_vram_usage()
                        logger.info(
                            f"[StoryboardV2] [{trace_id}] 深度清理完成" f" | vram={vram_after:.1f}%"
                        )
                    elif vram_after > 85:
                        logger.warning(
                            f"[StoryboardV2] [{trace_id}] ⚠️ VRAM 偏高（{vram_after:.1f}%），"
                            f"Qwen 模型可能存在 OOM 风险 | step={i}"
                        )
                        if progress_callback:
                            progress_callback(
                                f"⚠️ 显存占用 {vram_after:.0f}%，OOM 风险...",
                                ng_end,
                            )
                    elif progress_callback:
                        progress_callback(
                            f"🧹 显存 {vram_before:.0f}%→{vram_after:.0f}%，准备下一步...",
                            ng_end,
                        )

                # ⭐ 刷新活跃时间，防止长时间多步生成中空闲定时器误杀
                self._last_used = time.time()

            # 4. 构建输出
            image_url = f"/api/comfyui/image?filename={current_image}"

            # 构建 enriched_ref_items
            enriched_ref_items = []
            for item in all_ref_items:
                enriched_ref_items.append(
                    {
                        "type": item.get("type", ""),
                        "url": item.get("image_url") or item.get("url", ""),
                        "name": item.get("name", ""),
                        "desc": item.get("desc", ""),
                        "visual_desc": item.get("visual_desc", ""),
                    }
                )

            # ⭐ 标记生成完成，开始空闲定时器
            self._mark_generation_complete()

            # ⭐ 生成完成后立即清理内存缓存和临时对象，防止多帧累积
            self.clear_image_cache()
            import gc

            gc.collect()
            _ram_after_gc = self._get_system_memory_usage()
            logger.info(
                f"[ComfyUI][分镜] 方法完成 | shot={shot_id}"
                f" | total_elapsed={time.time()-_t0:.1f}s | steps={len(step_results)}"
                f" | RAM_after_gc={_ram_after_gc:.1f}%"
            )

            # ⭐ V6.0: 从 all_filenames 构建多图 URL 列表
            all_image_urls = (
                [f"/api/comfyui/image?filename={fn}" for fn in all_filenames]
                if all_filenames
                else ([image_url] if image_url else [])
            )

            return ComfyUIGenResult(
                image_url=image_url,
                filename=current_image,
                images=all_image_urls,  # ⭐ V6.0: 所有输出图片（多帧分镜/全景多角度）
                filenames=all_filenames,  # ⭐ V6.0: 所有输出文件名
                prompt_id=trace_id,
                elapsed_ms=sum(sr.elapsed_ms for sr in step_results),
                seed=actual_seed,
                prompt=optimized_prompt,
                prompt_sections={"fusion": optimized_prompt},
                ref_items=enriched_ref_items,
            )

        except Exception as e:
            self._mark_generation_complete()
            # ⭐ 异常时也清理缓存，防止内存泄漏
            self.clear_image_cache()
            import gc

            gc.collect()
            logger.error(
                f"[StoryboardV2] [{trace_id}] 分镜生成失败"
                f" | error={e} | completed_steps={len(step_results)}"
            )
            raise
