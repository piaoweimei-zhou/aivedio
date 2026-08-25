"""
多人分镜三元约束阶段

使用 蒙版+深度图+OpenPose 三重约束生成多人分镜帧。
输入：人物A + 人物B + （可选）蒙版/深度图/OpenPose模板图
输出：storyboard_multi 类型资产
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


class MultiPersonStage(StagePlugin):
    """多人分镜三元约束阶段"""

    stage_def = StageDef(
        stage_id="multi_person",
        name="多人分镜（三元约束）",
        input_types=[
            "concept",
            "multi_view",
            "storyboard",
            "storyboard_multi",
            "pose",
            "depth",
            "lineart",
        ],
        input_content_types=[],  # 接受任意内容类型
        output_type="storyboard_multi",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="蒙版+深度图+OpenPose 三重约束生成多人分镜帧",
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

        # 构建参考图列表，按 asset_type 映射到 workflow_refs 的 key
        reference_images = build_reference_images(input_assets, multi_group=False)

        prompt = params.get(
            "prompt", "Two characters in a scene, natural interaction, cinematic lighting"
        )  # noqa: E501
        size = params.get("size", "1024x1024")
        model = params.get("model", "")
        template_name = params.get("template_name", "")

        logger.info(
            f"[MultiPersonStage] 多人分镜 | provider={provider_id} | "
            f"refs={len(reference_images)} | template_name={template_name}"
        )

        try:
            gen_kwargs = {
                "template": "multi_person",
                "template_name": template_name,
            }

            result = await provider_svc.generate_image(
                provider_id=provider_id,
                prompt=prompt,
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
                asset_type="storyboard_multi",
                name=params.get("name", f"多人分镜 {template_name}".strip()),
                parent_id=parent_id,
                extra_metadata={
                    "source_asset_ids": parent_ids,
                    "prompt": prompt,
                    "template": "multi_person",
                    "template_name": template_name,
                    "size": size,
                    "reference_count": len(reference_images),
                },
                content_type=content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[MultiPersonStage] 多人分镜生成失败: {e}")
            return self._error_result(str(e))
