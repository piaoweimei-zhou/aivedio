"""
分层渲染阶段

4-5人场景分A/B组生成+合成，解决AI注意力稀释问题。
输入：A组人物(1-2) + B组人物(1-2) + 蒙版 + 深度图
输出：storyboard_layered 类型资产（A组图层 + B组图层 + 最终合成图）
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import (
    StageDef,
    StagePlugin,
    build_reference_images,
    collect_content_type,
)

logger = logging.getLogger(__name__)


class LayeredRenderStage(StagePlugin):
    """分层渲染阶段 — 4-5人场景分A/B组生成+合成"""

    stage_def = StageDef(
        stage_id="layered_render",
        name="分层渲染",
        input_types=[
            "concept",
            "multi_view",
            "storyboard",
            "storyboard_multi",
            "storyboard_layered",
            "pose",
            "depth",
            "lineart",
        ],
        input_content_types=[],
        output_type="storyboard_layered",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="4-5人场景分A/B组生成+合成，解决AI注意力稀释问题",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        err = self._require_input(input_assets, min_count=2)
        if err:
            return self._error_result(err)

        # 校验至少前两个资产有 URL
        for asset in input_assets[:2]:
            err = self._require_urls(asset)
            if err:
                return self._error_result(err)

        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        # 构建参考图列表（分层渲染：前2个角色为A组，后续为B组）
        reference_images = build_reference_images(input_assets, multi_group=True)

        prompt_a = params.get("prompt_a", "Group A characters in scene, cinematic lighting")
        prompt_b = params.get("prompt_b", "Group B characters in scene, cinematic lighting")
        size = params.get("size", "1024x1024")
        model = params.get("model", "")
        template_name = params.get("template_name", "")

        logger.info(
            f"[LayeredRenderStage] 分层渲染 | provider={provider_id} | "
            f"refs={len(reference_images)} | template_name={template_name}"
        )

        try:
            gen_kwargs = {
                "template": "layered_render",
                "template_name": template_name,
                "prompt_a": prompt_a,
                "prompt_b": prompt_b,
            }

            result = await provider_svc.generate_image(
                provider_id=provider_id,
                prompt=prompt_a,  # 主提示词用A组
                size=size,
                model=model,
                reference_images=reference_images,
                **gen_kwargs,
            )

            parent_ids = [a.asset_id for a in input_assets]
            parent_id = parent_ids[0] if len(parent_ids) == 1 else ""

            content_type = collect_content_type(input_assets)

            new_asset = await self._register_asset(
                asset_svc,
                result,
                asset_type="storyboard_layered",
                name=params.get("name", f"分层渲染 {template_name}".strip()),
                parent_id=parent_id,
                extra_metadata={
                    "source_asset_ids": parent_ids,
                    "prompt_a": prompt_a,
                    "prompt_b": prompt_b,
                    "template": "layered_render",
                    "template_name": template_name,
                    "size": size,
                    "reference_count": len(reference_images),
                },
                content_type=content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[LayeredRenderStage] 分层渲染生成失败: {e}")
            return self._error_result(str(e))
