"""
ComfyUI 服务 — 图像生成 Mixin

文生图/图生图/精修/标准化/三视图生成，队列提交与完成等待。
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui_helpers import (
    ComfyUIGenResult,
    MAX_POLL_TIME,
    POLL_INTERVAL,
    TASK_TIMEOUTS,
    _extract_clip_text,
    _mem_log,
    logger,
)
from services.workflow_builder import (
    build_comfyui_workflow,
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)
from services.qwen_workflow import YAOGUANG_DEFAULT_NEGATIVE

logger = logging.getLogger(__name__)


class ComfyUIGenerationMixin:
    async def _cache_image(self, filename: str):
        """确保图片存在于磁盘（委托到 file_handler 子模块）"""
        await self._file_handler.cache_image(filename)
    async def _ensure_image_in_input_dir(self, image_url: str, project_id: Optional[str] = None) -> str:
        """确保参考图像存在于 ComfyUI input 目录中（委托到 file_handler 子模块）"""
        return await self._file_handler.ensure_image_in_input_dir(image_url, project_id)
    def get_cached_image(self, filename: str) -> Optional[bytes]:
        """获取缓存的图片数据（委托到 file_handler 子模块）"""
        return self._file_handler.get_cached_image(filename)
    def clear_image_cache(self):
        """清理内存中的图片缓存（委托到 file_handler 子模块）"""
        self._file_handler.clear_image_cache()
    async def _normalize_reference_images(self, ref_items: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """检测并统一参考图的尺寸比例（委托到 file_handler 子模块）"""
        return await self._file_handler.normalize_reference_images(ref_items)
    async def _generate_scene_prompts(
        self,
        concept_prompt_json: Optional[dict] = None,
        refined_prompt: str = "",
        user_scene_desc: str = "",
    ) -> Dict[str, Any]:
        """调用 DeepSeek 从文生图+精修优化提示词生成场景多角度提示词"""
        from services.prompt_service import get_prompt_service
        psvc = get_prompt_service()
        return await psvc.generate_scene_prompts(
            concept_prompt_json=concept_prompt_json,
            refined_prompt=refined_prompt,
            user_scene_desc=user_scene_desc,
        )
    async def check_health(self) -> Dict[str, Any]:
        """检查 ComfyUI 是否在线（委托到 client 子模块）"""
        return await self._client.check_health()
    async def get_queue_progress(self, prompt_id: str) -> Dict[str, Any]:
        """查询 ComfyUI 队列中指定 prompt 的生成进度（委托到 client 子模块）"""
        return await self._client.get_queue_progress(prompt_id)
    async def generate(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",  # yaoguang | qwen_refinement | qwen_standardization
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """
        通过 ComfyUI 生成图像（自动等待服务就绪）

        Args:
            prompt_json: 结构化提示词
            custom_text: 自定义文本
            negative_prompt: 负向提示词
            width: 图像宽度
            height: 图像高度
            seed: 随机种子
            steps: 采样步数
            cfg: CFG 强度（Control Free Guidance）
            progress_callback: 进度回调函数
            reference_image: 参考图路径（图生图模式）
            workflow_type: 工作流类型（yaoguang/qwen_refinement/qwen_standardization）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        # 并发控制：通过 _queue_prompt_with_retry 的 _semaphore 统一限制
        # （移除 _generate_lock，避免与 _semaphore 双重串行化）
        return await self._generate_impl(
            prompt_json=prompt_json,
            custom_text=custom_text,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            seed=seed,
            steps=steps,
            cfg=cfg,
            progress_callback=progress_callback,
            reference_image=reference_image,
            project_id=project_id,
            asset_tag=asset_tag,
            workflow_type=workflow_type,
            content_type=content_type,
        )
    async def _generate_impl(
        self,
        prompt_json: dict,
        custom_text: str = "",
        negative_prompt: str = "",
        width: Optional[int] = None,
        height: Optional[int] = None,
        seed: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,  # ⭐ 修复 A1：新增 cfg 参数
        progress_callback: Optional[Callable] = None,
        reference_image: str = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        workflow_type: str = "yaoguang",
        content_type: str = "",
    ) -> ComfyUIGenResult:
        """generate() 的实际实现（已获取互斥锁）"""
        self._mark_generation_active()
        # ⭐ Fix 3: 概念探索阶段入口，重置 sd 计数
        self.reset_generation_count("sd")
        # 0. 释放显存 + 检查内存/显存使用情况
        logger.info(f"[ComfyUI] generate() 入口 | workflow={workflow_type} | ref={reference_image[:30] if reference_image else 'none'}")
        await self._release_vram_for_comfyui()
        logger.info(f"[ComfyUI] generate() VRAM释放完成")
        await self.check_and_release_memory()
        logger.info(f"[ComfyUI] generate() 内存检查完成")

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        logger.info(f"[ComfyUI] generate() ensure_running={ready}")
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（按模型类型分别计数）
        model_family = "sd" if workflow_type in (None, "", "yaoguang") else "qwen"
        await self._ensure_clean_state(model_family)

        # 预解析参考图（qwen 工作流需要 input 目录下的文件）
        resolved_image = reference_image
        if workflow_type in ("qwen_refinement", "qwen_standardization") and reference_image:
            resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 根据工作流类型构建工作流
        if workflow_type == "qwen_refinement":
            # Qwen精修模式（单图编辑）
            prefix = f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'refine' }"
            workflow, opt_prompt, prompt_sections = build_refinement_workflow(
                reference_image=resolved_image,
                role_desc=custom_text,
                seed=actual_seed,
                filename_prefix=prefix,
            )
            logger.info(f"[ComfyUI] 使用 Qwen 精修工作流")

        elif workflow_type == "qwen_standardization":
            # Qwen标准化模式（多视图生成）
            workflow, _, _ = build_standardization_workflow(
                reference_image=resolved_image,
                views=3,
                character_name=custom_text or "角色",
                seed=actual_seed,
                filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'std' }",
            )
            logger.info(f"[ComfyUI] 使用 Qwen 标准化工作流（3视图）")

        else:
            # 默认使用 Z-Image 瑶光版（文生图）
            positive_text = structured_prompt_to_comfyui_prompt(
                prompt_json, custom_text
            )
            neg_text = negative_prompt or YAOGUANG_DEFAULT_NEGATIVE

            # 打印最终发给 ComfyUI 的 prompt 文本，方便排查模型不按类型生成的问题
            logger.info(f"[ComfyUI] 正向提示词文本: {positive_text[:200]}")
            # 选择工作流模板：character 用影视级（25步/AuraFlow），scene 用标准版
            # prop 用道具专用版（+SeedVR2超分管线）
            # steps/cfg 使用 workflow_builder 函数的默认值（影视级=25/2，标准=8/1）
            if content_type in ("prop", "scene"):
                _workflow_type = "prop"
                # 道具/场景工作流：不传默认尺寸，让模板自带的尺寸生效
                _wf_width = width
                _wf_height = height
            else:
                _workflow_type = "cinematic" if content_type in ("character", "") else "standard"
                _wf_width = width or self.config.default_width
                _wf_height = height or self.config.default_height
            workflow = build_comfyui_workflow(
                positive_prompt=positive_text,
                negative_prompt=neg_text,
                width=_wf_width,
                height=_wf_height,
                seed=actual_seed,
                steps=steps,  # ⭐ 修复 A1：传递 steps 到工作流
                cfg=cfg,      # ⭐ 修复 A1：传递 cfg 到工作流
                reference_image=reference_image,
                content_type=content_type,
                workflow=_workflow_type,
            )
            # 打印工作流中最终的 CLIP 提示词，方便排查生成与预期不符的问题
            clip_text = _extract_clip_text(workflow)
            if clip_text:
                logger.info(f"[ComfyUI] 最终 CLIP 正向提示词: {clip_text[:300]}")
            logger.info(f"[ComfyUI] 使用 Z-Image 瑶光工作流")

        # ⭐ 断裂点3修复：ParamInjector 补漏注入
        # build_comfyui_workflow 已手写注入核心参数，此处用 ParamInjector 做二次校验+补漏
        # 确保 schema 中定义的所有参数都被注入，不依赖手写逻辑的完整性
        try:
            from services.workflow_params import inject_workflow_params
            schema_name = "文生图影视级" if _workflow_type == "cinematic" else None
            if schema_name:
                user_params = {
                    "prompt": positive_text,
                    "negative": neg_text,
                    "width": _wf_width,
                    "height": _wf_height,
                    "steps": steps,
                    "cfg": cfg,
                    "seed": actual_seed,
                }
                # 过滤 None 值（避免覆盖工作流模板默认值）
                user_params = {k: v for k, v in user_params.items() if v is not None}
                _, injected = inject_workflow_params(schema_name, workflow, user_params)
                if injected:
                    logger.info(f"[ComfyUI] ParamInjector 补漏注入: {list(injected.keys())}")
        except Exception as pie:
            logger.warning(f"[ComfyUI] ParamInjector 补漏失败（不影响主流程）: {pie}")

        # 3. 提交到 ComfyUI（含重试）
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        logger.info(
            f"[ComfyUI] 已提交: prompt_id={prompt_id[:8]}..., "
            f"seed={actual_seed}, workflow={workflow_type}"
        )

        # 4. 等待生成完成（带进度回调）
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='generate')
        if not filenames:
            raise RuntimeError("ComfyUI 生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 5. 缓存所有图片到内存（ComfyUI 停止后仍可访问）
        for fn in filenames:
            await self._cache_image(fn)

        # 6. 构建图像 URL（通过后端代理，避免 CSP 阻止）
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 完成: prompt_id={prompt_id[:8]}..., "
            f"elapsed={total_elapsed}ms, filenames={filenames}"
        )

        # 7. 记录使用时间并调度空闲自停
        self._mark_generation_complete()

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
        )
    async def refine_image(
        self,
        reference_image: str,
        role_desc: str = "",
        scene_desc: str = "",
        prop_desc: str = "",
        refinement_desc: str = "",  # 用户自定义的精修指令（如"增强面部细节"）
        lock_elements: Optional[List[str]] = None,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        full_prompt: Optional[str] = None,  # 直接使用完整 5 段式提示词，跳过重建
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        width: Optional[int] = None,   # 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,  # 图像高度（可选，覆盖工作流默认值）
        content_type: str = "",        # 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
    ) -> ComfyUIGenResult:
        """
        精修阶段：单图编辑模式（基于Qwen Image Edit）

        Args:
            reference_image: 参考图像路径
            role_desc: 角色描述
            scene_desc: 场景描述
            prop_desc: 道具描述
            refinement_desc: 用户自定义的精修指令（如"增强面部细节，让眼睛更有神采"）
            lock_elements: 需要锁定的元素列表
            seed: 随机种子
            progress_callback: 进度回调函数
            full_prompt: 直接使用的完整 5 段式提示词（不为空时跳过 format_qwen_prompt 重建）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        _t0 = time.time()
        logger.info(f"[ComfyUI][精修] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | asset={asset_tag}")
        # ⭐ Fix 3: 精修阶段入口，重置 qwen 计数
        self.reset_generation_count("qwen")
        # 0. 释放显存 + 检查内存/显存使用情况
        await self._release_vram_for_comfyui()
        await self.check_and_release_memory()

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（Qwen 模型）
        await self._ensure_clean_state("qwen")

        # 3. 解析参考图：将 URL 转为 ComfyUI input 目录下的本地文件名
        resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 构建精修提示词
        #    ─────────────────────────────────────────────────────
        #    Fisher 配置：简洁自然语言提示词，直接传给 Qwen VL
        #    Qwen VL 对简单指令理解远优于复杂的5段式结构化格式
        #    denoise=1 + ReferenceLatent 天然保证一致性，无需反复强调"保持不变"
        #    ─────────────────────────────────────────────────────
        #    优先级（从高到低）：
        #    1. 用户直接输入的 refinement_desc → 直接使用
        #    2. full_prompt 已含编辑指令 → 直接使用
        #    3. role_desc/scene_desc/prop_desc → 拼接为简洁自然语言
        #    4. 无任何描述 → 使用默认精修指令
        #    ─────────────────────────────────────────────────────
        _original_full_prompt = full_prompt  # 保存原始输入，全身扩展检测用

        if refinement_desc and refinement_desc.strip():
            # ★ 优先级1：用户直接输入的精修指令
            full_prompt = refinement_desc.strip()
            logger.info(f"[ComfyUI] 使用用户精修指令 | {full_prompt[:100]}...")

        elif full_prompt:
            # ★ 优先级2：full_prompt 直接使用
            #   Fisher 配置下不需要 DeepSeek 优化为编辑指令
            #   简洁自然语言直接喂给 Qwen VL 效果最好
            logger.info(f"[ComfyUI] 使用 full_prompt | {full_prompt[:80]}...")

        # ★ 优先级3 & 4 由 build_refinement_workflow 内部处理
        #   将 role_desc/scene_desc/prop_desc 拼接为简洁自然语言

        # 3.5 检测全身扩展意图（半身→全身）
        expand_full_body = False
        _check_text = (full_prompt or "") + (_original_full_prompt or "") + (role_desc or "") + (refinement_desc or "") + (scene_desc or "")
        _full_body_kw = [
            '全身', '全身像', '全貌', '从头到脚', '完整身体', '完整全身',
            '全身照', '站立全身', '正面全身', '全身站立',
            '下半身', '腿部', '腿', '鞋子', '脚', '小腿', '大腿', '膝盖',
            '露出全身', '展示全身', '全身图',
            '向下扩展', '画布扩展', '扩展画面', '生成下半身', '补全身体',
            'full body', 'full-body',
        ]
        if any(kw in _check_text for kw in _full_body_kw):
            expand_full_body = True
            # 构建专用全身扩展指令（简洁自然语言，Fisher 风格）
            body_desc = role_desc or refinement_desc or scene_desc or ""
            full_prompt = self._build_full_body_expansion_prompt(body_desc)
            logger.info(
                f"[ComfyUI] 全身扩展模式 | Fisher配置(denoise=1) | "
                f"prompt: {full_prompt[:100]}..."
            )
        else:
            logger.info(f"[ComfyUI] 精修阶段 - 标准单图编辑模式")

        # 3.6 全身扩展预处理：Python 侧填充参考图到底部（替代 ComfyUI letterbox 黑边）
        #     ImageScaleByAspectRatio V2 的 letterbox 会在上下两端加黑边，
        #     ReferenceLatent 锚定后模型把黑边当"图像内容"保留 → 无法 outpainting。
        #     解决：用 nude 镜像填充仅在底部延伸，让模型看到自然过渡。
        if expand_full_body:
            padded_filename = await self._prepare_fullbody_reference(resolved_image, project_id or "")
            # 使用填充后的图片作为参考图（宽高已为 9:16，无需节点169再处理）
            resolved_image = padded_filename

        # 4. 构建精修工作流（同时获取优化提示词）
        prefix = f"{ (project_id or 'unknown')[-6:] }_{ asset_tag or 'refine' }"
        workflow, opt_prompt, prompt_sections = build_refinement_workflow(
            reference_image=resolved_image,
            role_desc=role_desc,
            scene_desc=scene_desc,
            prop_desc=prop_desc,
            lock_elements=lock_elements,
            seed=actual_seed,
            filename_prefix=prefix,
            full_prompt=full_prompt,
            expand_full_body=expand_full_body,
            width=width,
            height=height,
            content_type=content_type,
        )

        logger.info(f"[ComfyUI] 精修阶段 - {'全身扩展模式' if expand_full_body else '标准单图编辑模式'} | prompt开头: {full_prompt[:120] if full_prompt else '(空)'}...")

        # 3. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 4. 等待生成完成
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='refine')
        if not filenames:
            raise RuntimeError("ComfyUI 精修生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 5. 缓存所有图片
        for fn in filenames:
            await self._cache_image(fn)

        # 6. 构建图像 URL
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 精修完成: elapsed={total_elapsed}ms, filename={filename}"
        )

        # 7. 记录使用时间
        self._mark_generation_complete()
        logger.info(f"[ComfyUI][精修] 方法完成 | asset={asset_tag} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
            prompt=opt_prompt,
            prompt_sections=prompt_sections,
        )
    async def _prepare_fullbody_reference(
        self, image_filename: str, project_id: Optional[str] = None
    ) -> str:
        """
        预处理参考图：底部填充到 9:16（替代 ComfyUI 内部 letterbox 黑边方案）
        
        核心问题：ImageScaleByAspectRatio V2 的 fit="letterbox" 
        会在上下两端各加黑边，ReferenceLatent 锚定后模型将黑边视为"图像内容"保留。
        
        解决方案：在 Python 侧用 PIL+numpy 仅在底部填充，填充区域用镜像+渐变
        模拟自然场景延伸，避免纯黑边被模型保留。
        
        Args:
            image_filename: ComfyUI input 目录下的参考图文件名
            project_id: 项目 ID（预留）
        
        Returns:
            填充后图片在 ComfyUI input 目录下的文件名
        """
        import numpy as np
        from PIL import Image
        
        input_dir = os.path.join(self.config.comfyui_dir, "input")
        source_path = os.path.join(input_dir, image_filename)
        
        if not os.path.exists(source_path):
            logger.warning(f"[ComfyUI][全身扩展] 参考图不存在: {source_path}，跳过预处理")
            return image_filename
        
        # ⭐ Fix 6: 使用 with 语句确保文件句柄正确关闭
        with Image.open(source_path) as _img:
            img = _img.convert("RGB")
            w, h = img.size
        
        # 目标: 9:16 竖屏 (宽高比 9:16)
        target_w = w
        target_h = int(w * 16 / 9)
        target_h = (target_h // 8) * 8  # 对齐 8 的倍数
        
        if target_h <= h:
            logger.info(f"[ComfyUI][全身扩展] 图片已足够 ({w}x{h}), target={target_w}x{target_h}, 无需填充")
            del img  # ⭐ Fix 6: 显式释放 PIL 对象
            return image_filename
        
        pad_bottom = target_h - h
        arr = np.array(img, dtype=np.float32)
        del img  # ⭐ Fix 6: numpy 数组已创建，释放 PIL 对象
        
        # 填充策略：MirrorPad 底部区域 → 模拟场景向下延伸
        # 取底部 1/4 区域（或至少 32px）做镜像翻转作为填充内容
        mirror_height = max(32, h // 4)
        mirror_strip = arr[h - mirror_height:h, :, :]  # (mirror_height, w, 3)
        flipped = mirror_strip[::-1, :, :]  # 垂直翻转
        
        # 用 tile 方式填满 pad_bottom 高度
        repeats = (pad_bottom // mirror_height) + 1
        fill_arr = np.tile(flipped, (repeats, 1, 1))[:pad_bottom, :, :]
        
        # 底部渐隐：越往下越暗（防止镜像痕迹明显）
        fade = np.linspace(1.0, 0.15, pad_bottom, dtype=np.float32).reshape(-1, 1, 1)
        fill_arr = fill_arr * fade + 30 * (1 - fade)  # 趋向深色
        
        # 拼接：原图 + 填充
        padded_arr = np.concatenate([arr, fill_arr.astype(np.uint8)], axis=0)
        
        # 保存
        stem = Path(image_filename).stem
        padded_filename = f"{stem}_fullbody_916.png"
        padded_path = os.path.join(input_dir, padded_filename)
        Image.fromarray(padded_arr.astype(np.uint8)).save(padded_path, "PNG")
        del arr, fill_arr, padded_arr  # ⭐ Fix 6: 释放中间 numpy 数组（约 3-10MB）
        
        logger.info(
            f"[ComfyUI][全身扩展] 预处理完成: {image_filename} → {padded_filename}"
            f" ({w}x{h} → {target_w}x{target_h}, 底部+{pad_bottom}px 镜像填充)"
        )
        return padded_filename
    def _build_full_body_expansion_prompt(self, source_desc: str = "") -> str:
        """
        构建全身扩展编辑指令（半身→全身 outpainting）
        
        Fisher 配置：简洁自然语言提示词。
        Qwen VL 对简单指令理解远优于冗长的约束列表。
        denoise=1 + ReferenceLatent 天然保证上半身一致性，无需反复强调"保持不变"。
        
        Args:
            source_desc: 角色/场景描述文本，用于提取风格参考
        
        Returns:
            编辑指令字符串
        """
        # 从描述中提取角色特征，附加到提示词
        feature_hint = ""
        if source_desc:
            clean = source_desc.replace("基于参考图的", "").replace("基于参考图", "").strip()
            clean = clean.rstrip("，。、；：")
            if clean:
                feature_hint = f"，风格：{clean[:60]}"
        
        return f"人物全身像，正面对摄像机，能看到鞋子{feature_hint}"
    async def standardize_views(
        self,
        reference_image: str,
        views: int = 3,
        view_names: Optional[List[str]] = None,  # 自定义视图名称列表
        asset_name: str = "",                    # 资产名称（场景名/角色名，用于文件名和兜底标签）
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        view_type: Optional[str] = "",
        full_prompt: Optional[str] = None,
        role_desc: Optional[str] = "",
        scene_dna: Optional[str] = "",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        concept_prompt_json: Optional[dict] = None,
        refined_prompt: str = "",
        width: Optional[int] = None,            # ⭐ 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,           # ⭐ 图像高度（可选，覆盖工作流默认值）
    ) -> ComfyUIGenResult:
        """
        标准化阶段：多视图生成（基于Qwen Image Edit融合模式）

        Args:
            reference_image: 参考图像路径
            views: 视图数量（3或6）
            view_names: 自定义视图名称列表（如 ["正面视图", "侧面45度", "背面视图"]）
            asset_name: 资产名称（场景名/角色名，用于文件名标识）
            seed: 随机种子
            progress_callback: 进度回调函数
            view_type: 视图类型 character/scene/prop
            full_prompt: 直接使用的完整提示词
            role_desc: 用户输入描述（用于 DeepSeek 优化）
            scene_dna: 场景DNA（从文生图提示词提取，仅 scene 类型用）

        Returns:
            ComfyUIGenResult: 生成结果
        """
        _t0 = time.time()
        logger.info(f"[ComfyUI][标准化] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | views={views} | type={view_type} | asset={asset_name}")
        # ⭐ Fix 3: 标准化阶段入口，重置 qwen 计数
        self.reset_generation_count("qwen")
        # 0. 智能显存管理：_ensure_clean_state 内部已包含显存检查和重启逻辑
        # 不再单独调用 _release_vram_for_comfyui 和 check_and_release_memory，
        # 避免与 _ensure_clean_state 重复触发 ComfyUI 重启（每次重启耗时 30~60s）
        # 仅在 llama.cpp 确实运行时才停止它
        try:
            from services.process_manager import get_llm_manager
            llm_mgr = get_llm_manager()
            if llm_mgr.is_running:
                await self._release_vram_for_comfyui()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[VRAM] 释放显存时出错: {e}")

        # 1. 确保 ComfyUI 在运行
        ready = await self.ensure_running()
        if not ready:
            raise RuntimeError(
                "ComfyUI 不可用。请确保 ComfyUI 在 localhost:8188 运行，"
                "或设置 COMFYUI_DIR 环境变量让系统自动启动。"
            )

        actual_seed = seed or int(time.time() * 1000) % (2**31)

        # 2. 检查连续生成次数，必要时重启释放 VRAM（Qwen 模型）
        # _ensure_clean_state 内部会智能判断：显存充足则只调用 /api/free，不足才重启
        await self._ensure_clean_state("qwen")

        # 3. 解析参考图：将 URL 转为 ComfyUI input 目录下的本地文件名
        resolved_image = await self._ensure_image_in_input_dir(reference_image)

        # 4. 优化标准化提示词（通过 DeepSeek），根据 view_names 生成动态提示词
        opt_prompt = ""
        prompt_sections = {}
        
        # 标准化阶段始终使用本地模板提示词，确保三视图布局正确
        # 不再使用 DeepSeek，因为它可能会丢失三视图布局描述
        full_prompt = None  # 强制使用本地模板提示词
        
        logger.info(f"[ComfyUI] 标准化视图配置 | views={views} | view_names={view_names} | view_type={view_type} | asset_name='{asset_name}' | role_desc='{role_desc[:80] if role_desc else None}' | scene_dna='{scene_dna[:80] if scene_dna else None}'")

        # 4. 构建标准化工作流（多视图生成）
        # 场景走专用多角度工作流（双通道约束），角色/道具走标准单图编辑（在一张图中生成三视图）
        if view_type == 'scene':
            # 场景标准化也禁用 DeepSeek，避免幻觉
            # 使用本地模板生成多角度提示词
            # 优先级（从具体到通用）：
            # 1. concept_prompt_json 中的场景描述（每个资产独立，最准确）
            # 2. scene_dna（项目级合并场景描述，回退使用）
            # 3. role_desc（调用方传入的实际场景描述，_execute_standardize_stage 中构建的 scene_user_prompt）
            # 4. asset_name（资产名称）
            # 5. 兜底"场景"
            scene_label = "场景"

            # 从 concept_prompt_json 中提取当前资产的场景描述（最准确，避免用合并的 scene_dna）
            concept_scene_desc = ""
            if concept_prompt_json and isinstance(concept_prompt_json, dict):
                concept_scene_desc = concept_prompt_json.get("scene") or concept_prompt_json.get("description") or ""

            if concept_scene_desc and concept_scene_desc.strip():
                scene_label = concept_scene_desc.strip()[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 concept_prompt_json 的场景描述: '{scene_label[:60]}'")
            elif scene_dna and scene_dna.strip():
                scene_label = scene_dna.strip()[:80]
                # 兜底：scene_dna 可能包含多个场景的合并描述（以；分隔）
                # 尝试根据 asset_name 提取匹配的段落
                if asset_name and '；' in scene_dna:
                    parts = [p.strip() for p in scene_dna.split('；') if p.strip()]
                    matched = [p for p in parts if asset_name in p or any(kw in p for kw in [asset_name[:4]] if len(asset_name) > 2)]
                    if matched:
                        scene_label = matched[0][:80]
                        logger.info(f"[ComfyUI] 场景标准化 | 从 scene_dna 中匹配到段落: '{scene_label[:60]}'")
                logger.info(f"[ComfyUI] 场景标准化 | 使用 scene_dna 作为场景标签: '{scene_label[:60]}'")
            elif role_desc and role_desc.strip() and role_desc.strip() not in ("角色", "场景", ""):
                # 关键修复：role_desc 包含 _execute_standardize_stage 中精心构建的 scene_user_prompt
                # （如"哥特式黑暗城堡，阴云密布的天空..."），必须回退到这里！
                scene_label = role_desc.strip()[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 role_desc(scene_user_prompt) 作为场景标签: '{scene_label[:60]}'")
            elif asset_name and asset_name not in ("角色", "场景", ""):
                scene_label = asset_name[:80]
                logger.info(f"[ComfyUI] 场景标准化 | 使用 asset_name 作为场景标签: '{scene_label[:60]}'")
            else:
                logger.info(f"[ComfyUI] 场景标准化 | 使用默认场景标签: '{scene_label}' | scene_dna='{scene_dna[:60] if scene_dna else None}' | concept_scene='{concept_scene_desc[:60] if concept_scene_desc else None}' | role_desc='{role_desc[:60] if role_desc else None}' | asset_name='{asset_name}'")

            # 生成英文场景标签（用于 instruction，避免英文指令中混入中文）
            scene_label_en = "scene shown in the reference image"

            # 本地模板：生成6个标准角度的提示词（详细中文，参考原始多场景工作流风格）
            # 包含具体场景标签（scene_label），确保生成的多角度图片与原始场景保持一致
            _angle_names = [
                "wide angle",
                "front medium shot",
                "left 45 degree view",
                "right 45 degree view",
                "close-up",
                "top-down 90 degree view",
            ]
            _angle_descs = [
                "广角全景，展示完整场景空间",
                "正面中景，标准构图",
                "左侧45度斜侧，增加空间纵深",
                "右侧45度斜侧，对称展示",
                "特写镜头，聚焦核心区域",
                "正上方90度俯视，展示平面布局",
            ]
            local_frame_prompts = [
                f"{scene_label} | Scene：{desc}。仅改变视角，场景内容与参考图完全一致，严禁添加人物或新物体。"
                for desc in _angle_descs
            ]
            # 每帧独立的 instruction（只指定角度，保留规则由 workflow_builder 自动追加）
            _frame_instructions = [
                f"Generate a {angle} view of the scene shown in the reference image."
                for angle in _angle_names
            ]
            
            # 将资产名称 sanitize 后加入文件名便于识别
            safe_name = re.sub(r'[\\/:*?"<>|]', '_', asset_name[:32]) if asset_name else 'unknown'

            # 逐帧提交：ComfyUI batch模式下 promptLine 只输出第一行
            # 改为循环6次，每次使用一行提示词
            all_scene_filenames = []
            all_scene_image_urls = []
            scene_start = time.time()
            for frame_idx, frame_prompt in enumerate(local_frame_prompts):
                frame_seed = actual_seed + frame_idx  # 每帧不同seed
                frame_instruction = _frame_instructions[frame_idx] if frame_idx < len(_frame_instructions) else _frame_instructions[0]
                frame_workflow = build_scene_multiangle_workflow(
                    reference_image=resolved_image,
                    scene_dna=scene_label,
                    per_frame_prompts=[frame_prompt],  # 只传当前帧
                    instruction=frame_instruction,
                    seed=frame_seed,
                    filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ safe_name }_{ asset_tag or 'std' }_{frame_idx+1}of6",
                )
                prompt_id = await self._queue_prompt_with_retry(frame_workflow)
                filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type='standardize_3')
                if filenames:
                    fn = filenames[0]
                    await self._cache_image(fn)
                    all_scene_filenames.append(fn)
                    all_scene_image_urls.append(f"/api/comfyui/image?filename={fn}&pipeline_id={project_id or ''}")
                    # ⭐ 不在此处保存到项目文件夹，由 pipeline_executor._save_stage_images 统一保存
                    # 避免重复下载+重复磁盘写入（每帧图片被写2次 → 只写1次）
                    logger.debug(f"[ComfyUI] 场景多角度 第{frame_idx+1}/6帧完成: {fn}")
                    # ⭐ 每帧生成后释放显存，避免6帧连续生成累积 OOM
                    if frame_idx < len(local_frame_prompts) - 1:  # 最后一帧不需要释放
                        await self._quick_release_vram()
                if progress_callback:
                    try:
                        progress_callback(f"帧 {frame_idx+1}/6 完成", int((frame_idx + 1) / 6 * 100))
                    except Exception:
                        pass

            total_elapsed = int((time.time() - scene_start) * 1000)
            filename = all_scene_filenames[0] if all_scene_filenames else ""
            image_url = all_scene_image_urls[0] if all_scene_image_urls else ""
            # 构建场景多角度 URL 列表，跳过空文件名
            scene_image_urls = [f"/api/comfyui/image?filename={fn}&pipeline_id={project_id or ''}" for fn in all_scene_filenames if fn]
            # result.prompt 只保留简短摘要（避免前端每张图都显示6帧拼接的长文本）
            # 每帧的独立提示词通过 frame_prompts 字段传递
            opt_prompt = f"场景多角度: 6帧 ({scene_label})"
            prompt_sections = {"scene_dna": scene_label}
            logger.info(f"[ComfyUI] 场景标准化完成: 6角度, elapsed={total_elapsed}ms")
            logger.info(f"[ComfyUI][标准化] 场景方法完成 | asset={asset_name} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

            self._mark_generation_complete()
            return ComfyUIGenResult(
                image_url=image_url,
                filename=filename,
                images=all_scene_image_urls,
                filenames=all_scene_filenames,
                prompt_id="",
                elapsed_ms=total_elapsed,
                seed=actual_seed,
                prompt=opt_prompt,
                prompt_sections=prompt_sections,
                frame_prompts=local_frame_prompts,
            )

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', asset_name[:32]) if asset_name else 'unknown'
        workflow, opt_prompt, prompt_sections = build_standardization_workflow(
            reference_image=resolved_image,
            views=views,
            character_name=asset_name,
            seed=actual_seed,
            full_prompt=full_prompt,
            filename_prefix=f"{ (project_id or 'unknown')[-6:] }_{ safe_name }_{ asset_tag or 'std' }",
            view_type=view_type or 'character',
            role_desc=role_desc,  # 传递优化后的描述
            width=width,   # ⭐ 传递自定义尺寸
            height=height,  # ⭐ 传递自定义尺寸
        )
        logger.info(f"[ComfyUI] 标准化阶段 - {views}视图生成 | width={width} | height={height} | view_type={view_type}")

        # 调试：记录工作流关键节点参数，排查 "name 'w' is not defined" 错误
        _debug_nodes = {"177": "LoadImage", "169": "ImageScale", "180": "TextEncode", "174": "KSampler", "500": "ImageScale(out)"}
        for _nid, _ntitle in _debug_nodes.items():
            if _nid in workflow:
                _inputs = workflow[_nid].get("inputs", {})
                logger.info(f"[ComfyUI][调试] 节点{_nid}({_ntitle}): class={workflow[_nid].get('class_type','?')} | inputs_keys={list(_inputs.keys())}")

        # 5. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 6. 等待生成完成
        std_timeout = 'standardize_6' if views >= 6 else 'standardize_3'
        filenames = await self._wait_for_completion(prompt_id, progress_callback, task_type=std_timeout)
        if not filenames:
            raise RuntimeError("ComfyUI 标准化生成完成但未返回任何图片文件")
        filename = filenames[0]
        total_elapsed = int((time.time() - start_time) * 1000)

        # 7. 缓存所有图片
        for fn in filenames:
            await self._cache_image(fn)

        # 8. 构建图像 URL
        pipe_param = f"&pipeline_id={project_id}" if project_id else ""
        image_url = f"/api/comfyui/image?filename={filename}{pipe_param}"
        image_urls = [f"/api/comfyui/image?filename={fn}{pipe_param}" for fn in filenames]

        logger.info(
            f"[ComfyUI] 标准化完成: {views}视图, elapsed={total_elapsed}ms, filenames={filenames}"
        )

        # 9. 记录使用时间
        self._mark_generation_complete()
        logger.info(f"[ComfyUI][标准化] 角色道具方法完成 | asset={asset_name} | views={views} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms")

        return ComfyUIGenResult(
            image_url=image_url,
            filename=filename,
            images=image_urls,
            filenames=filenames,
            prompt_id=prompt_id,
            elapsed_ms=total_elapsed,
            seed=actual_seed,
            prompt=opt_prompt or full_prompt,
            prompt_sections=prompt_sections,
        )
    async def _queue_prompt_with_retry(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（带并发控制信号量，最多 2 个并发生成）"""
        async with self._semaphore:
            logger.debug(f"[ComfyUI] 获取并发生成许可")
            return await self._queue_prompt_with_retry_impl(workflow)
    async def _queue_prompt_with_retry_impl(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI（先快速释放显存，再检查队列，3 次重试）"""
        _mem_log("提交工作流前", f"nodes={len(workflow)}")
        # 每次提交前快速释放显存
        try:
            await self._quick_release_vram()
        except Exception:
            pass
        
        # 提交前检查队列是否有堆积
        try:
            q_session = self._get_http_session()
            async with q_session.get(
                f"{self.config.base_url}/queue",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    running = len(data.get("queue_running", []))
                    pending = len(data.get("queue_pending", []))
                    if pending > 2:
                        logger.warning(
                            f"[ComfyUI] 队列堆积 ({pending} pending, {running} running)，"
                            "清空中..."
                        )
                        async with q_session.post(
                            f"{self.config.base_url}/queue",
                            json={"clear": True},
                        ) as clear_resp:
                            pass
        except Exception:
            pass

        last_error = None
        for attempt in range(3):
            try:
                return await self._queue_prompt(workflow)
            except (aiohttp.ClientConnectorError, ConnectionRefusedError) as e:
                last_error = e
                wait = 2 ** attempt
                logger.warning(
                    f"[ComfyUI] 连接失败 (attempt {attempt + 1}), "
                    f"{wait}s 后重试: {e}"
                )
                await asyncio.sleep(wait)
                # 重试前再检查一下 ComfyUI
                if not await self._check_alive():
                    await self.ensure_running()
            except RuntimeError as e:
                last_error = e
                if attempt < 2:
                    wait = 2 ** attempt
                    logger.warning(f"[ComfyUI] 提交失败 (attempt {attempt + 1}), {wait}s 后重试: {e}")
                    await asyncio.sleep(wait)
                    # 检测 HTTP 500 错误，强制重启恢复内部状态
                    if "500" in str(e) or "Server got itself" in str(e):
                        logger.error("[ComfyUI] 检测到 HTTP 500 错误，强制重启恢复...")
                        await self._close_http_session()
                        self.stop()
                        await self.ensure_running()
                    elif not await self._check_alive():
                        await self.ensure_running()
                else:
                    raise
        last_msg = str(last_error) if last_error else "未知错误"
        raise RuntimeError(
            f"ComfyUI 提交失败（已重试3次）: {last_msg}"
        )
    @staticmethod
    def _strip_workflow_meta(workflow: dict) -> dict:
        """深拷贝工作流并剥离可能引起自定义节点崩溃的 _meta / _comment 字段
        
        同时剥离工作流顶层的非节点键（如 _meta、_comment），
        避免被 ComfyUI 当作节点 ID 解析导致 missing_node_type 错误。
        """
        cleaned = {}
        for nid, ndata in workflow.items():
            # 跳过顶层非节点键（以下划线开头且值不是标准节点 dict）
            if nid.startswith("_") and not (isinstance(ndata, dict) and "class_type" in ndata):
                continue
            if not isinstance(ndata, dict):
                cleaned[nid] = ndata
                continue
            cleaned[nid] = {k: v for k, v in ndata.items() if k not in ("_meta", "_comment")}
        return cleaned
    async def _queue_prompt(self, workflow: dict) -> str:
        """提交工作流到 ComfyUI"""
        # 剥离 _meta / _comment（某些自定义节点遇到这些字段会崩溃）
        workflow = self._strip_workflow_meta(workflow)

        # 提交前检查关键节点的图片值（Fish 融合模板节点11 = 场景槽位）
        if "11" in workflow:
            _img = workflow["11"].get("inputs", {}).get("image", "")
            logger.info(f"[ComfyUI] 提交前节点11(场景)图片: {_img}")
        payload = {"prompt": workflow}

        session = self._get_http_session()
        async with session.post(
            f"{self.config.base_url}/prompt",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"[ComfyUI] 提交失败 | status={resp.status} | body={text[:500]}")
                raise RuntimeError(
                    f"ComfyUI 提交失败 ({resp.status}): {text[:300]}"
                )
            data = await resp.json()
            # 检查 node_errors（ComfyUI 验证失败时返回 200 但包含 node_errors）
            node_errors = data.get("node_errors", {})
            if node_errors:
                error_details = []
                for nid, errs in node_errors.items():
                    if isinstance(errs, dict) and "errors" in errs:
                        for e in errs["errors"]:
                            msg = e.get("message", str(e)) if isinstance(e, dict) else str(e)
                            error_details.append(f"node {nid}: {msg}")
                    else:
                        error_details.append(f"node {nid}: {errs}")
                error_summary = "; ".join(error_details[:5])
                logger.error(f"[ComfyUI] 工作流验证失败 | node_errors={node_errors}")
                raise RuntimeError(f"ComfyUI 工作流验证失败: {error_summary}")
            prompt_id = data.get("prompt_id", "")
            if not prompt_id:
                logger.error(f"[ComfyUI] 提交返回空 prompt_id | data={data}")
                raise RuntimeError(f"ComfyUI 提交返回空 prompt_id: {str(data)[:300]}")
            logger.info(f"[ComfyUI] 工作流已提交 | prompt_id={prompt_id}")
            return prompt_id
    async def _wait_for_completion(
        self, prompt_id: str, progress_callback: Optional[callable] = None,
        task_type: str = 'generate',
    ) -> List[str]:
        """
        等待 ComfyUI 生成完成并获取所有输出文件名（含多图片场景）。
        带进度回调，自动恢复 ComfyUI 崩溃。
        增加错误检测：如果 ComfyUI 返回执行错误，立即抛出。
        
        Args:
            task_type: 任务类型，用于设置不同的超时时间
                       'generate'=300s / 'refine'=600s / 'standardize_3'=600s
                       'standardize_6'=1200s / 'storyboard'=900s
        
        Returns:
            所有输出文件的文件名列表（优先非 temp 文件）
        """
        _mem_log("等待生成开始", f"prompt={prompt_id[:8]} task={task_type} timeout={TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)}s")
        max_time = TASK_TIMEOUTS.get(task_type, MAX_POLL_TIME)
        elapsed = 0
        consecutive_failures = 0
        last_queue_log = -30  # 每 30s 打印一次队列状态
        logger.info(
            f"[ComfyUI] 开始等待生成完成 | prompt_id={prompt_id}"
            f" | task_type={task_type} | timeout={max_time}s"
        )
        while elapsed < max_time:
            try:
                # 先查历史（生成完成后）
                session = self._get_http_session()
                url = f"{self.config.base_url}/history/{prompt_id}"
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        consecutive_failures = 0  # 连接成功则重置计数
                        try:
                            data = await resp.json()
                        except (json.JSONDecodeError, ValueError) as json_err:
                            logger.warning(
                                f"[ComfyUI] history JSON解析失败 (t={elapsed}s): {json_err}"
                            )
                            data = None
                        if data:
                            history = data.get(prompt_id, {})
                            # 检测执行错误 — ComfyUI history 格式：
                            #   {"status": {"status_str": "error", "completed": bool, "messages": [[event, data], ...]}}
                            #   messages 中每个元素是 [event_name, {exception_message, exception_type, ...}]
                            status_info = history.get("status", {})
                            if isinstance(status_info, dict):
                                status_str = status_info.get("status_str", "")
                                status_messages = status_info.get("messages", [])
                                if status_str == "error":
                                    error_msgs = []
                                    for msg in status_messages[:5]:
                                        if isinstance(msg, (list, tuple)) and len(msg) >= 2:
                                            event_name, msg_data = msg[0], msg[1]
                                            if isinstance(msg_data, dict):
                                                exc_msg = msg_data.get("exception_message", "")
                                                exc_type = msg_data.get("exception_type", "")
                                                node_id = msg_data.get("node_id", "")
                                                node_type = msg_data.get("node_type", "")
                                                error_msgs.append(f"[{node_type}#{node_id}] {exc_type}: {exc_msg}"[:200])
                                            else:
                                                error_msgs.append(str(msg_data)[:200])
                                        else:
                                            error_msgs.append(str(msg)[:200])
                                    if not error_msgs:
                                        error_msgs = ["unknown error"]
                                    logger.error(f"[ComfyUI] 执行错误详情 | status={status_str} | messages={status_messages[:3]}")
                                    raise RuntimeError(
                                        f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                                    )
                            # 兼容旧版 errors 字段
                            errors = history.get("errors", [])
                            if errors:
                                error_msgs = [str(e)[:200] for e in errors[:5]]
                                logger.error(f"[ComfyUI] 执行错误详情(errors字段) | errors={errors[:5]}")
                                raise RuntimeError(
                                    f"ComfyUI 执行错误: {'; '.join(error_msgs)}"
                                )
                            # 检测节点错误状态
                            outputs = history.get("outputs", {})
                            # 收集所有 SaveImage / SaveAudio 节点的输出（跳过 PreviewImage 的 temp 文件）
                            all_filenames: List[str] = []
                            temp_filenames: List[str] = []
                            for node_id, node_output in outputs.items():
                                media_items = []
                                media_items.extend(node_output.get("images", []))
                                media_items.extend(node_output.get("audio", []))
                                for item in media_items:
                                    fname = item.get("filename", "")
                                    subfolder = item.get("subfolder", "")
                                    if subfolder:
                                        self._output_subfolders[fname] = subfolder
                                    if not fname.startswith("ComfyUI_temp"):
                                        all_filenames.append(fname)
                                    elif not temp_filenames:
                                        # 兜底：只保留第一个 temp 文件名
                                        temp_filenames.append(fname)
                            if all_filenames:
                                _mem_log("等待生成完成", f"prompt={prompt_id[:8]} files={all_filenames} elapsed={elapsed}s")
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(all_filenames)
                                return all_filenames
                            if temp_filenames:
                                if progress_callback:
                                    try:
                                        progress_callback(f"生成完成 ({elapsed}s)", 100)
                                    except Exception:
                                        pass
                                # 持久化到独立目录
                                await self._persist_output_files(temp_filenames)
                                return temp_filenames

                # 还在生成中，查队列获取进度
                consecutive_failures = 0
                if progress_callback:
                    try:
                        prog = await self.get_queue_progress(prompt_id)
                        if prog.get("in_queue"):
                            progress_callback(f"队列处理中 ({elapsed}s)", prog["progress"])
                        elif elapsed >= 5:
                            estimated_pct = min(int(elapsed / 60 * 100), 99)
                            progress_callback(f"等待中 ({elapsed}s)", estimated_pct)
                    except Exception:
                        pass

                # 每 30s 打印一次队列状态（方便排障）
                if elapsed - last_queue_log >= 30:
                    last_queue_log = elapsed
                    try:
                        q_session = self._get_http_session()
                        async with q_session.get(
                            f"{self.config.base_url}/queue",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as qresp:
                            if qresp.status == 200:
                                qdata = await qresp.json()
                                logger.debug(
                                    f"[ComfyUI] 队列状态 (t={elapsed}s): "
                                    f"running={len(qdata.get('queue_running', []))}, "
                                    f"pending={len(qdata.get('queue_pending', []))}"
                                )
                    except Exception as qe:
                        logger.warning(f"[ComfyUI] 查询队列失败 (t={elapsed}s): {qe}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                consecutive_failures += 1
                logger.warning(f"[ComfyUI] 轮询失败 ({consecutive_failures}x): {e}")
                # 连续失败 5 次（~2.5s）后检查 ComfyUI 是否还活着
                # 用 _check_alive() 区分"模型加载中暂时断连"和"真正崩溃"
                # - /history 超时但 _check_alive 成功 = 临时波动，继续等
                # - _check_alive 也失败 = ComfyUI 崩溃，立即重启
                if consecutive_failures >= 5:
                    alive = await self._check_alive()
                    if not alive:
                        logger.warning(
                            f"[ComfyUI] ComfyUI 确认为崩溃状态（{consecutive_failures}x 失败, "
                            f"{elapsed:.0f}s），尝试重启..."
                        )
                        await self._close_http_session()  # ⭐ 关闭旧 session，重启后自动创建新的
                        self._kill_process_on_port(8188)
                        await self.ensure_running()
                        # ⭐ 重启后 prompt_id 已丢失，新实例不认旧 ID，必须立即失败
                        raise RuntimeError(
                            f"ComfyUI 在生成过程中崩溃并已自动重启，当前任务（prompt_id={prompt_id[:8]}）"
                            f"已丢失，请重新发起生成"
                        )
                    else:
                        logger.info(
                            f"[ComfyUI] ComfyUI 仍在线（{consecutive_failures}x 暂时波动），继续等待..."
                        )

            # 自适应轮询：
            # - 前 10s：0.5s（快速反馈，适合文生图等短任务）
            # - 10-30s：1.0s
            # - 30-60s：2.0s
            # - 60s+：5.0s（长任务如视频，减少无效请求）
            # - 连接失败时：退避到 max 5s
            if consecutive_failures > 0:
                poll_interval = min(POLL_INTERVAL * (1.5 ** consecutive_failures), 5.0)
            elif elapsed < 10:
                poll_interval = POLL_INTERVAL  # 0.5s
            elif elapsed < 30:
                poll_interval = 1.0
            elif elapsed < 60:
                poll_interval = 2.0
            else:
                poll_interval = 5.0
            
            # ⭐ 每 30 秒打印一次内存状态，便于追踪内存泄漏
            _elapsed_int = int(elapsed)
            if _elapsed_int > 0 and _elapsed_int % 30 == 0 and (_elapsed_int - 30) < poll_interval + 1:
                _mem_log("轮询中", f"prompt={prompt_id[:8]} elapsed={_elapsed_int}s")
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise TimeoutError(
            f"ComfyUI 生成超时 ({max_time}s, task={task_type})，prompt_id={prompt_id[:8]}"
        )
    def get_available_models(self) -> List[Dict[str, Any]]:
        """获取可用模型信息"""
        return [
            {
                "id": "comfyui_yaoguang",
                "name": "Z-Image 瑶光版",
                "provider": "comfyui",
                "description": "本地ComfyUI + Z-Image瑶光LoRA，超真实细节增强，8步出图",
                "width": 1080,
                "height": 1920,
                "steps": 8,
            },
            {
                "id": "qwen_refinement",
                "name": "Qwen 精修",
                "provider": "comfyui",
                "description": "Qwen Image Edit单图编辑模式，用于角色/场景/道具精修定妆",
                "width": 1536,
                "height": 1024,
                "steps": 20,
            },
            {
                "id": "qwen_standardization",
                "name": "Qwen 标准化",
                "provider": "comfyui",
                "description": "Qwen Image Edit多图融合模式，用于3视图/6视图标准化生成",
                "width": 1536,
                "height": 512,
                "steps": 20,
            },
        ]
