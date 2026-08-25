"""
ComfyUI 服务 — 图像精修/标准化 Mixin（从 comfyui_generation.py 拆分，P2 治理）

被 ComfyUIGenerationMixin 继承（MRO），精修与三视图标准化，
依赖主类的队列提交/完成等待方法。
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Callable, List, Optional

from services.comfyui_helpers import (
    ComfyUIGenResult,
)
from services.workflow_builder import (
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
)

logger = logging.getLogger(__name__)


def _project_prefix(project_id: Optional[str]) -> str:
    """项目前缀（安全化）→ ComfyUI 输出文件命名

    修复 F821：原代码直接调用未定义的 _project_prefix。
    风格对齐 comfyui_storyboard 的 project_id 短尾 + 语义后缀。
    """
    pid = str(project_id or "project")
    safe = re.sub(r"[^0-9a-zA-Z_\-]", "_", pid)
    return f"proj_{safe[-24:]}" if safe else "proj_project"


class ComfyUIGenerationVisionMixin:
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
        width: Optional[int] = None,  # 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,  # 图像高度（可选，覆盖工作流默认值）
        content_type: str = "",  # 内容类型（character/scene/prop/""），驱动 LoRA 强度和缩放尺寸
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
        logger.info(
            f"[ComfyUI][精修] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | asset={asset_tag}"  # noqa: E501
        )
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
        _check_text = (
            (full_prompt or "")
            + (_original_full_prompt or "")
            + (role_desc or "")
            + (refinement_desc or "")
            + (scene_desc or "")
        )
        _full_body_kw = [
            "全身",
            "全身像",
            "全貌",
            "从头到脚",
            "完整身体",
            "完整全身",
            "全身照",
            "站立全身",
            "正面全身",
            "全身站立",
            "下半身",
            "腿部",
            "腿",
            "鞋子",
            "脚",
            "小腿",
            "大腿",
            "膝盖",
            "露出全身",
            "展示全身",
            "全身图",
            "向下扩展",
            "画布扩展",
            "扩展画面",
            "生成下半身",
            "补全身体",
            "full body",
            "full-body",
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
            logger.info("[ComfyUI] 精修阶段 - 标准单图编辑模式")

        # 3.6 全身扩展预处理：Python 侧填充参考图到底部（替代 ComfyUI letterbox 黑边）
        #     ImageScaleByAspectRatio V2 的 letterbox 会在上下两端加黑边，
        #     ReferenceLatent 锚定后模型把黑边当"图像内容"保留 → 无法 outpainting。
        #     解决：用 nude 镜像填充仅在底部延伸，让模型看到自然过渡。
        if expand_full_body:
            padded_filename = await self._prepare_fullbody_reference(
                resolved_image, project_id or ""
            )
            # 使用填充后的图片作为参考图（宽高已为 9:16，无需节点169再处理）
            resolved_image = padded_filename

        # 4. 构建精修工作流（同时获取优化提示词）
        prefix = f"{_project_prefix(project_id)}_{asset_tag or 'refine'}"
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

        logger.info(
            f"[ComfyUI] 精修阶段 - {'全身扩展模式' if expand_full_body else '标准单图编辑模式'} | prompt开头: {full_prompt[:120] if full_prompt else '(空)'}..."  # noqa: E501
        )

        # 3. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 4. 等待生成完成
        filenames = await self._wait_for_completion(
            prompt_id, progress_callback, task_type="refine"
        )
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

        logger.info(f"[ComfyUI] 精修完成: elapsed={total_elapsed}ms, filename={filename}")

        # 7. 记录使用时间
        self._mark_generation_complete()
        logger.info(
            f"[ComfyUI][精修] 方法完成 | asset={asset_tag} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms"  # noqa: E501
        )

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
            logger.info(
                f"[ComfyUI][全身扩展] 图片已足够 ({w}x{h}), target={target_w}x{target_h}, 无需填充"
            )
            del img  # ⭐ Fix 6: 显式释放 PIL 对象
            return image_filename

        pad_bottom = target_h - h
        arr = np.array(img, dtype=np.float32)
        del img  # ⭐ Fix 6: numpy 数组已创建，释放 PIL 对象

        # 填充策略：MirrorPad 底部区域 → 模拟场景向下延伸
        # 取底部 1/4 区域（或至少 32px）做镜像翻转作为填充内容
        mirror_height = max(32, h // 4)
        mirror_strip = arr[h - mirror_height : h, :, :]  # (mirror_height, w, 3)
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
        asset_name: str = "",  # 资产名称（场景名/角色名，用于文件名和兜底标签）
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
        width: Optional[int] = None,  # ⭐ 图像宽度（可选，覆盖工作流默认值）
        height: Optional[int] = None,  # ⭐ 图像高度（可选，覆盖工作流默认值）
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
        logger.info(
            f"[ComfyUI][标准化] 方法入口 | ref={reference_image[:30] if reference_image else 'none'} | views={views} | type={view_type} | asset={asset_name}"  # noqa: E501
        )
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

        logger.info(
            f"[ComfyUI] 标准化视图配置 | views={views} | view_names={view_names} | view_type={view_type} | asset_name='{asset_name}' | role_desc='{role_desc[:80] if role_desc else None}' | scene_dna='{scene_dna[:80] if scene_dna else None}'"  # noqa: E501
        )

        # 4. 构建标准化工作流（多视图生成）
        # 场景走专用多角度工作流（双通道约束），角色/道具走标准单图编辑（在一张图中生成三视图）
        if view_type == "scene":
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
                concept_scene_desc = (
                    concept_prompt_json.get("scene") or concept_prompt_json.get("description") or ""
                )

            if concept_scene_desc and concept_scene_desc.strip():
                scene_label = concept_scene_desc.strip()[:80]
                logger.info(
                    f"[ComfyUI] 场景标准化 | 使用 concept_prompt_json 的场景描述: '{scene_label[:60]}'"
                )
            elif scene_dna and scene_dna.strip():
                scene_label = scene_dna.strip()[:80]
                # 兜底：scene_dna 可能包含多个场景的合并描述（以；分隔）
                # 尝试根据 asset_name 提取匹配的段落
                if asset_name and "；" in scene_dna:
                    parts = [p.strip() for p in scene_dna.split("；") if p.strip()]
                    matched = [
                        p
                        for p in parts
                        if asset_name in p
                        or any(kw in p for kw in [asset_name[:4]] if len(asset_name) > 2)
                    ]
                    if matched:
                        scene_label = matched[0][:80]
                        logger.info(
                            f"[ComfyUI] 场景标准化 | 从 scene_dna 中匹配到段落: '{scene_label[:60]}'"
                        )
                logger.info(
                    f"[ComfyUI] 场景标准化 | 使用 scene_dna 作为场景标签: '{scene_label[:60]}'"
                )
            elif role_desc and role_desc.strip() and role_desc.strip() not in ("角色", "场景", ""):
                # 关键修复：role_desc 包含 _execute_standardize_stage 中精心构建的 scene_user_prompt
                # （如"哥特式黑暗城堡，阴云密布的天空..."），必须回退到这里！
                scene_label = role_desc.strip()[:80]
                logger.info(
                    f"[ComfyUI] 场景标准化 | 使用 role_desc(scene_user_prompt) 作为场景标签: '{scene_label[:60]}'"  # noqa: E501
                )
            elif asset_name and asset_name not in ("角色", "场景", ""):
                scene_label = asset_name[:80]
                logger.info(
                    f"[ComfyUI] 场景标准化 | 使用 asset_name 作为场景标签: '{scene_label[:60]}'"
                )
            else:
                logger.info(
                    f"[ComfyUI] 场景标准化 | 使用默认场景标签: '{scene_label}' | scene_dna='{scene_dna[:60] if scene_dna else None}' | concept_scene='{concept_scene_desc[:60] if concept_scene_desc else None}' | role_desc='{role_desc[:60] if role_desc else None}' | asset_name='{asset_name}'"  # noqa: E501
                )

            # 生成英文场景标签（用于 instruction，避免英文指令中混入中文）

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
            safe_name = re.sub(r'[\\/:*?"<>|]', "_", asset_name[:32]) if asset_name else "unknown"

            # 逐帧提交：ComfyUI batch模式下 promptLine 只输出第一行
            # 改为循环6次，每次使用一行提示词
            all_scene_filenames = []
            all_scene_image_urls = []
            scene_start = time.time()
            for frame_idx, frame_prompt in enumerate(local_frame_prompts):
                frame_seed = actual_seed + frame_idx  # 每帧不同seed
                frame_instruction = (
                    _frame_instructions[frame_idx]
                    if frame_idx < len(_frame_instructions)
                    else _frame_instructions[0]
                )
                frame_workflow = build_scene_multiangle_workflow(
                    reference_image=resolved_image,
                    scene_dna=scene_label,
                    per_frame_prompts=[frame_prompt],  # 只传当前帧
                    instruction=frame_instruction,
                    seed=frame_seed,
                    filename_prefix=f"{_project_prefix(project_id)}_{safe_name}_{asset_tag or 'std'}_{frame_idx+1}of6",  # noqa: E501
                )
                prompt_id = await self._queue_prompt_with_retry(frame_workflow)
                filenames = await self._wait_for_completion(
                    prompt_id, progress_callback, task_type="standardize_3"
                )
                if filenames:
                    fn = filenames[0]
                    await self._cache_image(fn)
                    all_scene_filenames.append(fn)
                    all_scene_image_urls.append(
                        f"/api/comfyui/image?filename={fn}&pipeline_id={project_id or ''}"
                    )
                    # ⭐ 不在此处保存到项目文件夹，由 pipeline_executor._save_stage_images 统一保存
                    # 避免重复下载+重复磁盘写入（每帧图片被写2次 → 只写1次）
                    logger.debug(f"[ComfyUI] 场景多角度 第{frame_idx+1}/6帧完成: {fn}")
                    # ⭐ 每帧生成后释放显存，避免6帧连续生成累积 OOM
                    if frame_idx < len(local_frame_prompts) - 1:  # 最后一帧不需要释放
                        await self._quick_release_vram()
                if progress_callback:
                    try:
                        progress_callback(
                            f"帧 {frame_idx+1}/6 完成", int((frame_idx + 1) / 6 * 100)
                        )
                    except Exception:
                        pass

            total_elapsed = int((time.time() - scene_start) * 1000)
            filename = all_scene_filenames[0] if all_scene_filenames else ""
            image_url = all_scene_image_urls[0] if all_scene_image_urls else ""
            # result.prompt 只保留简短摘要（避免前端每张图都显示6帧拼接的长文本）
            # 每帧的独立提示词通过 frame_prompts 字段传递
            opt_prompt = f"场景多角度: 6帧 ({scene_label})"
            prompt_sections = {"scene_dna": scene_label}
            logger.info(f"[ComfyUI] 场景标准化完成: 6角度, elapsed={total_elapsed}ms")
            logger.info(
                f"[ComfyUI][标准化] 场景方法完成 | asset={asset_name} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms"  # noqa: E501
            )

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

        safe_name = re.sub(r'[\\/:*?"<>|]', "_", asset_name[:32]) if asset_name else "unknown"
        workflow, opt_prompt, prompt_sections = build_standardization_workflow(
            reference_image=resolved_image,
            views=views,
            character_name=asset_name,
            seed=actual_seed,
            full_prompt=full_prompt,
            filename_prefix=f"{_project_prefix(project_id)}_{safe_name}_{asset_tag or 'std'}",
            view_type=view_type or "character",
            role_desc=role_desc,  # 传递优化后的描述
            width=width,  # ⭐ 传递自定义尺寸
            height=height,  # ⭐ 传递自定义尺寸
        )
        logger.info(
            f"[ComfyUI] 标准化阶段 - {views}视图生成 | width={width} | height={height} | view_type={view_type}"  # noqa: E501
        )

        # 调试：记录工作流关键节点参数，排查 "name 'w' is not defined" 错误
        _debug_nodes = {
            "177": "LoadImage",
            "169": "ImageScale",
            "180": "TextEncode",
            "174": "KSampler",
            "500": "ImageScale(out)",
        }
        for _nid, _ntitle in _debug_nodes.items():
            if _nid in workflow:
                _inputs = workflow[_nid].get("inputs", {})
                logger.info(
                    f"[ComfyUI][调试] 节点{_nid}({_ntitle}): class={workflow[_nid].get('class_type', '?')} | inputs_keys={list(_inputs.keys())}"  # noqa: E501
                )

        # 5. 提交到 ComfyUI
        start_time = time.time()
        prompt_id = await self._queue_prompt_with_retry(workflow)

        # 6. 等待生成完成
        std_timeout = "standardize_6" if views >= 6 else "standardize_3"
        filenames = await self._wait_for_completion(
            prompt_id, progress_callback, task_type=std_timeout
        )
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
        logger.info(
            f"[ComfyUI][标准化] 角色道具方法完成 | asset={asset_name} | views={views} | total_elapsed={time.time()-_t0:.1f}s | comfyui_elapsed={total_elapsed}ms"  # noqa: E501
        )

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
