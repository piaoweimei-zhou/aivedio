"""
TTS 音频生成阶段

基于 Qwen3-TTS 工作流，从文本生成语音。
支持音色设计（voice_design）和音色克隆（voice_clone）两种模式。
"""
import logging
import time
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class TtsStage(StagePlugin):
    """TTS 音频生成阶段"""

    stage_def = StageDef(
        stage_id="tts",
        name="TTS 音频生成",
        input_types=[],
        output_type="audio",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从文本生成语音（支持音色设计和音色克隆）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc, _ = self._get_services()

        text = params.get("text", "")
        if not text:
            return self._error_result("TTS 文本不能为空")

        mode = params.get("mode", "voice_design")
        voice_description = params.get("voice_description", "")
        ref_audio_url = params.get("ref_audio_url", "")
        ref_text = params.get("ref_text", "")
        language = params.get("language", "Auto")

        logger.info(
            f"[TtsStage] 生成音频 | text_len={len(text)} | mode={mode} | "
            f"voice_desc={voice_description[:30] if voice_description else '无'}"
        )

        try:
            from services.comfyui_service import get_comfyui_service

            comfyui_svc = get_comfyui_service()
            result = await comfyui_svc.generate_tts_audio(
                text=text,
                mode=mode,
                voice_description=voice_description,
                ref_audio_url=ref_audio_url,
                ref_text=ref_text,
                language=language,
                asset_tag="tts_standalone",
            )

            new_asset = await self._register_asset(
                asset_svc, result,
                asset_type="audio",
                name=f"TTS_{mode}",
                extra_metadata={
                    "text": text,
                    "mode": mode,
                    "voice_description": voice_description,
                    "language": language,
                },
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[TtsStage] 生成失败: {e}")
            return self._error_result(str(e))
