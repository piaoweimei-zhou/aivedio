"""
MiniMax H3 供应商 — 本地 FL2VA 文本→视频

MiniMax H3 是音视频统一模型，通过本地 ComfyUI 节点（MiniMaxH3AudioConditioningT8
+ MultiRateSamplerEXPT8 + AVDecodeT8）运行，无需云端 API。
纯文本即可生成带同步环境音的视频，是"一键成片"最直接的视频生成源。

能力：
- video：文本→视频（native 模式，含环境音）
"""
import time

from services.provider_service import ProviderPlugin, ProviderResult

import logging

logger = logging.getLogger(__name__)


class MinimaxProvider(ProviderPlugin):
    """MiniMax H3 本地视频供应商（文本→视频）"""

    provider_id = "minimax_h3"
    provider_name = "MiniMax H3 (本地 FL2VA)"
    capabilities = ["video"]

    def is_available(self) -> bool:
        return True  # 本地 ComfyUI 服务，由 ComfyUI 生命周期统一管理

    async def generate_image(self, prompt, size="1024x1024", model="",
                             reference_images=None, **kwargs) -> ProviderResult:
        raise NotImplementedError("MiniMax H3 目前仅支持视频生成（文本→视频）")

    async def generate_video(
        self,
        prompt: str,
        images=None,
        videos=None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "9:16",
        resolution: str = "480p",
        width=None,
        height=None,
        frame_count=None,
        seed=None,
        fps=None,
        **kwargs,
    ) -> ProviderResult:
        """文本→视频生成（首版：纯文本模式，忽略参考图）"""
        from services.comfyui_service import get_comfyui_service

        service = get_comfyui_service()
        start = time.time()

        if (not width or not height) and aspect_ratio:
            _map = {"9:16": (480, 864), "16:9": (864, 486), "1:1": (648, 648),
                    "4:3": (720, 540), "3:4": (540, 720)}
            w, h = _map.get(aspect_ratio, (480, 864))
            width = width or w
            height = height or h

        audio_mode = kwargs.get("audio_mode") or "native"
        result = await service.generate_minimax_h3(
            prompt=prompt,
            width=width,
            height=height,
            duration_seconds=duration,
            seed=int(seed) if seed is not None else None,
            audio_mode=audio_mode,
            video_steps=int(kwargs.get("video_steps") or 8),
            audio_steps=int(kwargs.get("audio_steps") or 10),
            filename_prefix=kwargs.get("filename_prefix") or "minimax_h3",
        )

        elapsed_ms = int((time.time() - start) * 1000)

        return ProviderResult(
            provider_id=self.provider_id,
            video_url=result.image_url,
            image_url=result.image_url,
            images=result.images or [],
            filenames=result.filenames or [],
            seed=result.seed or 0,
            elapsed_ms=elapsed_ms,
            prompt=prompt,
            prompt_id=getattr(result, "prompt_id", ""),
            duration=duration,
        )