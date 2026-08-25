"""
ComfyUI 供应商 — 零改动包装 comfyui_service

ComfyUI 是本地开源推理引擎，始终可用。
直接委托给现有 comfyui_service，不重复实现。
"""

import logging
import time
from typing import Dict, List, Optional

from services.provider_service import ProviderPlugin, ProviderResult

logger = logging.getLogger(__name__)


class ComfyUIProvider(ProviderPlugin):
    """ComfyUI 本地供应商"""

    provider_id = "comfyui"
    provider_name = "ComfyUI (本地)"
    capabilities = ["image", "refine", "upscale", "video"]

    def is_available(self) -> bool:
        return True  # ComfyUI 始终可用（本地服务）

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ProviderResult:
        from services.comfyui_service import get_comfyui_service

        service = get_comfyui_service()
        start = time.time()

        # 解析尺寸
        w, h = None, None
        if size and "x" in size:
            parts = size.split("x")
            try:
                w, h = int(parts[0]), int(parts[1])
            except ValueError:
                pass

        # 有参考图时使用分镜融合工作流，否则使用纯文生图
        # ⭐ 注意：reference_images=[] 也可能需要走分镜模式（如指定了template）
        has_refs = reference_images and len(reference_images) > 0
        has_template = kwargs.get("template") is not None
        if has_refs or has_template:
            # 分镜融合模式（需要参考图）
            gen_kwargs = dict(
                reference_items=reference_images,
                prompt_text=prompt,
            )
            if w and h:
                gen_kwargs["width"] = w
                gen_kwargs["height"] = h
            gen_kwargs.update(kwargs)
            result = await service.generate_storyboard(**gen_kwargs)
        else:
            # 纯文生图模式（概念图生成）
            result = await service.generate(
                prompt_json={"type": "concept", "description": prompt},
                custom_text=prompt,
                width=w,
                height=h,
                seed=kwargs.get("seed"),
                project_id=kwargs.get("project_id"),
                asset_tag=kwargs.get("asset_tag"),
                content_type=kwargs.get("content_type", ""),
            )

        elapsed = int((time.time() - start) * 1000)

        return ProviderResult(
            image_url=result.image_url,
            images=result.images or [result.image_url] if result.image_url else [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed,
            prompt=result.prompt or prompt,
            provider_id="comfyui",
            prompt_id=getattr(result, "prompt_id", ""),
        )

    async def refine_image(
        self, image_url: str, prompt: str = "", seed: int = 0, content_type: str = "", **kwargs
    ) -> ProviderResult:
        """精修图像 — 使用 build_refinement_workflow"""
        from services.comfyui_service import get_comfyui_service

        service = get_comfyui_service()
        start = time.time()

        result = await service.refine_image(
            reference_image=image_url,
            full_prompt=prompt or "增强细节，提升画质",
            seed=seed or 0,
            content_type=content_type,
        )

        elapsed = int((time.time() - start) * 1000)

        return ProviderResult(
            image_url=result.image_url,
            images=result.images or [result.image_url] if result.image_url else [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed,
            prompt=prompt,
            provider_id="comfyui",
            prompt_id=getattr(result, "prompt_id", ""),
        )

    async def upscale_image(
        self, image_url: str, upscale_factor: int = 2, seed: int = 0, **kwargs
    ) -> ProviderResult:
        """超分辨率放大 — 使用 upscale/seedvr2 工作流"""
        from services.comfyui_service import get_comfyui_service

        service = get_comfyui_service()
        start = time.time()

        # 使用 template="upscale" 路由到放大工作流
        result = await service.generate_storyboard(
            prompt_text=kwargs.get("prompt", "超分辨率放大"),
            reference_items=[{"url": image_url, "role": "reference", "type": "reference"}],
            template="upscale",
            seed=seed or 0,
        )

        elapsed = int((time.time() - start) * 1000)

        return ProviderResult(
            image_url=result.image_url,
            images=result.images or [result.image_url] if result.image_url else [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed,
            prompt="upscale",
            provider_id="comfyui",
            prompt_id=getattr(result, "prompt_id", ""),
        )

    async def generate_3view(
        self,
        image_url: str,
        seed: int = 0,
    ) -> ProviderResult:
        """三视图生成 — 使用 3视图.json 工作流（Qwen Image Edit + multiple-angles LoRA）

        使用模板内置的默认提示词，无需外部传递。
        """
        from services.comfyui_service import get_comfyui_service

        service = get_comfyui_service()
        start = time.time()

        # 使用 template="3view" 路由到三视图工作流
        result = await service.generate_storyboard(
            prompt_text="生成三视图",
            reference_items=[{"url": image_url, "role": "reference", "type": "reference"}],
            template="3view",
            seed=seed or 0,
        )

        elapsed = int((time.time() - start) * 1000)

        return ProviderResult(
            image_url=result.image_url,
            images=result.images or [result.image_url] if result.image_url else [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed,
            prompt="3view",
            provider_id="comfyui",
            prompt_id=getattr(result, "prompt_id", ""),
        )

    async def generate_video(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：显式声明视频核心参数（ComfyUI 完全使用这些参数注入工作流节点）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs,
    ) -> ProviderResult:
        """视频生成 — 调用 LTX-2.3 工作流（图生视频）

        Args:
            prompt: 视频提示词
            images: 参考图 URL 列表（首帧引导图）
            duration: 视频时长（秒），转换为帧数（若 frame_count 已提供则忽略）
            resolution: 分辨率（480p/720p/1080p），当 width/height 未提供时使用
            width/height: 视频分辨率（像素，优先于 resolution）
            frame_count: 视频总帧数（优先于 duration）
            seed: 随机种子
            fps: 帧率（默认 24）
        """
        from services.comfyui_service import get_comfyui_service
        from services.video_resolution import resolve_video_resolution

        service = get_comfyui_service()
        start = time.time()

        # ⭐ 修复 B2：使用统一分辨率解析工具（消除 resolution/width/height 三重表述）
        width, height = resolve_video_resolution(
            width=width, height=height, resolution=resolution, aspect_ratio=aspect_ratio
        )

        # 优先使用显式参数；其次 kwargs；最后默认值
        frame_rate = int(fps or kwargs.get("frame_rate") or 24)

        # 优先使用显式 frame_count；否则由 duration × fps 推导
        if frame_count is None or frame_count <= 0:
            frame_count = max(int(duration * frame_rate), 25)

        # 优先使用显式 seed；否则 kwargs；最后 None（由 ComfyUI 工作流自决）
        effective_seed = seed if seed is not None else kwargs.get("seed")

        # 长视频分段配置（每段默认 15 秒）
        segment_seconds_cfg = int(kwargs.get("segment_seconds") or 15)

        # 参考图
        reference_image = images[0] if images else ""

        # 工作流文件（可通过 model 参数指定）
        workflow_file = model or "LTX-2.3_MSR_sample_workflow_V2.json"

        logger.info(
            f"[ComfyUIProvider] 视频生成 | prompt={prompt[:50]}... | "
            f"ref={reference_image[:50] if reference_image else 'none'} | "
            f"size={width}x{height} | frames={frame_count} | duration={duration}s | "
            f"fps={frame_rate} | seed={effective_seed} | seg_sec={segment_seconds_cfg}"
        )

        # 长视频（>segment_seconds_cfg）自动走分段生成+拼接路径
        # 如果有分段提示词，按提示词数量分段；否则按 segment_seconds_cfg 一段
        if duration > segment_seconds_cfg:
            segment_prompts = kwargs.get("segment_prompts", [])
            if segment_prompts:
                segment_count = len(segment_prompts)
            else:
                segment_count = max(int(duration // segment_seconds_cfg), 2)
            segment_seconds = duration / segment_count
            logger.info(
                f"[ComfyUIProvider] 长视频分段模式 | segments={segment_count} "
                f"× {segment_seconds:.1f}s = {segment_count * segment_seconds:.0f}s"
            )
            # prompt 作为 global_prompt 注入工作流（角色/场景描述）
            # local_prompts 用 segment_prompts 或 prompt 构建（分镜叙事）
            seg_prompts = segment_prompts if segment_prompts else [prompt]
            local_prompts_str = " | ".join(seg_prompts) if seg_prompts else prompt

            result = await service.generate_long_video(
                prompt=prompt,
                reference_image=reference_image,
                reference_images=kwargs.get("reference_images", {}),
                segment_prompts=segment_prompts,
                workflow_file=workflow_file,
                segment_count=segment_count,
                segment_seconds=int(segment_seconds),
                frame_rate=frame_rate,
                width=width,
                height=height,
                project_id=kwargs.get("project_id"),
                asset_tag=kwargs.get("asset_tag"),
                global_prompt=kwargs.get("global_prompt", prompt),
                local_prompts=kwargs.get("local_prompts", local_prompts_str),
                tts_audios=kwargs.get("tts_audios"),
                tts_mode=kwargs.get("tts_mode", "replace"),
                tts_volume=kwargs.get("tts_volume", 1.0),
                bgm_url=kwargs.get("bgm_url", ""),
                bgm_volume=kwargs.get("bgm_volume", 0.2),
            )
        else:
            result = await service.generate_video(
                prompt=prompt,
                reference_image=reference_image,
                workflow_file=workflow_file,
                width=width,
                height=height,
                frame_count=frame_count,
                frame_rate=frame_rate,
                seed=effective_seed,
                project_id=kwargs.get("project_id"),
                asset_tag=kwargs.get("asset_tag"),
                global_prompt=kwargs.get("global_prompt", prompt),
                local_prompts=kwargs.get("local_prompts", prompt),
            )

        elapsed = int((time.time() - start) * 1000)

        return ProviderResult(
            video_url=result.image_url,
            image_url=result.image_url,
            images=result.images or [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed,
            prompt=prompt,
            provider_id="comfyui",
            prompt_id=getattr(result, "prompt_id", ""),
        )
