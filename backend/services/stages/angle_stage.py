"""
三视图生成阶段

从概念图生成正面/侧面/背面三视图。
- ComfyUI: 使用 3视图.json 工作流（Qwen Image Edit + multiple-angles LoRA）
- ModelScope: 三视图专用模型
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class AngleStage(StagePlugin):
    """三视图生成阶段"""

    stage_def = StageDef(
        stage_id="angle",
        name="三视图生成",
        input_types=["concept"],
        input_content_types=["character"],
        output_type="multi_view",
        default_provider="comfyui",
        supported_providers=["comfyui", "modelscope"],
        description="从概念图生成正面/侧面/背面三视图",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        # 校验输入
        err = self._require_input(input_assets) or self._require_urls(input_assets[0])
        if err:
            return self._error_result(err)

        source = input_assets[0]
        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        prompt = params.get("prompt", f"Multi-angle views of: {source.name}")
        size = params.get("size", "1024x1024")
        model = params.get("model", "")
        seed = int(params.get("seed") or 0)

        logger.info(f"[AngleStage] 三视图 | provider={provider_id} | asset={source.asset_id}")

        try:
            if provider_id == "comfyui":
                result = await self._generate_via_comfyui(source, seed, params)
            elif provider_id == "modelscope":
                from services.providers.modelscope_provider import ModelScopeProvider
                ms_provider = ModelScopeProvider()
                result = await ms_provider.generate_angle(
                    prompt=prompt,
                    image_urls=source.urls,
                    model=model or "Qwen/Qwen-Image-Edit-2511",
                    resolution=size,
                )
            else:
                reference_images = [{"url": url, "role": "reference"} for url in source.urls if url]
                result = await provider_svc.generate_image(
                    provider_id=provider_id,
                    prompt=prompt,
                    size=size,
                    model=model,
                    reference_images=reference_images,
                )

            new_asset = await self._register_asset(
                asset_svc, result,
                asset_type="multi_view",
                name=f"{source.name} 三视图",
                parent_id=source.asset_id,
                extra_metadata={
                    "source_asset_id": source.asset_id,
                    "prompt": prompt,
                    "provider": provider_id,
                    "seed": result.seed if hasattr(result, 'seed') else seed,
                },
                content_type=source.content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[AngleStage] 三视图生成失败: {e}")
            return self._error_result(str(e))

    async def _generate_via_comfyui(self, source: AssetRef, seed: int, params: Dict[str, Any]):
        """ComfyUI 专用三视图生成 — 使用 3视图.json 模板"""
        from services.providers.comfyui_provider import ComfyUIProvider

        provider = ComfyUIProvider()
        image_url = source.urls[0] if source.urls else ""

        return await provider.generate_3view(
            image_url=image_url,
            seed=seed,
        )
