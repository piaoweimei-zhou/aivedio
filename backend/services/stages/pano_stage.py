"""
360全景生成阶段

从场景图生成360度全景图。
使用 ComfyUI 全景模板工作流。
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class PanoStage(StagePlugin):
    """360全景生成阶段"""

    stage_def = StageDef(
        stage_id="pano",
        name="360全景生成",
        input_types=[],
        input_content_types=[],  # 接受任意内容类型（右键任何图片都可生成全景图）
        output_type="pano",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从场景图生成360度全景图",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        err = self._require_input(input_assets) or self._require_urls(input_assets[0])
        if err:
            return self._error_result(err)

        source = input_assets[0]
        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        prompt = params.get("prompt", f"360 degree panoramic view: {source.name}")
        size = params.get("size", "2048x1024")
        model = params.get("model", "")

        logger.info(f"[PanoStage] 全景 | provider={provider_id} | asset={source.asset_id}")

        try:
            reference_images = [{"url": url, "type": "scene"} for url in source.urls if url]

            gen_kwargs = {}
            if provider_id == "comfyui":
                gen_kwargs["template"] = params.get("template", "panorama")

            result = await provider_svc.generate_image(
                provider_id=provider_id,
                prompt=prompt,
                size=size,
                model=model,
                reference_images=reference_images,
                **gen_kwargs,
            )

            # 全景图只保留最终拼接图（image_url 已指向 panorama_final_ 文件）
            if result.image_url:
                result.images = [result.image_url]

            # 全景图最终输出是拼接结果，分辨率 = input_width*2 x input_height
            parts = size.split("x")
            output_size = f"{int(parts[0]) * 2}x{parts[1]}" if len(parts) == 2 else "4096x1024"

            new_asset = await self._register_asset(
                asset_svc, result,
                asset_type="pano",
                name=f"{source.name} 全景",
                parent_id=source.asset_id,
                extra_metadata={"source_asset_id": source.asset_id, "prompt": prompt, "size": output_size},
                content_type=source.content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[PanoStage] 全景生成失败: {e}")
            return self._error_result(str(e))
