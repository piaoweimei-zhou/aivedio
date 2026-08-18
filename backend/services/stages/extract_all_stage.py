"""
三合一提取阶段

使用 三个骨架图.json 工作流，从单张图同时提取：
- 线稿图 (lineart)
- 深度图 (depth)
- 姿态骨架图 (pose)

SaveImage 节点顺序（按节点 ID）：
  节点18 → lineart_{seed}  (AIO_Preprocessor LineArt)
  节点19 → depth_{seed}    (AIO_Preprocessor DepthAnythingV2)
  节点26 → pose_{seed}     (DWPreprocessor)
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.template_utils import match_asset_type_by_filename

logger = logging.getLogger(__name__)


class ExtractAllStage(StagePlugin):
    """三合一提取阶段"""

    stage_def = StageDef(
        stage_id="extract_all",
        name="三合一提取",
        input_types=["concept", "storyboard"],
        input_content_types=[],  # 接受任意内容类型（右键任何图片都可提取三图）
        output_type="multi_extract",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从图像同时提取线稿、深度图、姿态骨架图",
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
        asset_svc, _ = self._get_services()

        logger.info(f"[ExtractAllStage] 三合一提取 | asset={source.asset_id}")

        try:
            from services.providers.comfyui_provider import ComfyUIProvider

            provider = ComfyUIProvider()
            result = await provider.generate_image(
                prompt="提取三图",
                reference_images=[{"url": source.urls[0], "role": "reference", "type": "reference"}],
                template="extract_all",
            )

            if not result or not result.filenames:
                return self._error_result("三合一提取未返回结果")

            filenames = result.filenames or []
            all_urls = result.images or []

            # 根据 filename prefix 识别输出类型（使用公共匹配函数）
            created_assets = []
            for fn, url in zip(filenames, all_urls):
                matched_type = match_asset_type_by_filename(fn)
                if not matched_type:
                    logger.debug(f"[ExtractAllStage] 跳过未识别文件: {fn}")
                    continue
                asset_type, label = matched_type

                new_asset = await self._register_asset_direct(
                    asset_svc,
                    asset_type=asset_type,
                    name=f"{source.name} {label}",
                    urls=[url],
                    input_assets=[source],
                    extra_metadata={
                        "source_asset_id": source.asset_id,
                        "extraction_type": "extract_all",
                    },
                    content_type=source.content_type,
                )
                if new_asset:
                    created_assets.append(new_asset)
                    logger.info(f"[ExtractAllStage] 创建资产 | type={asset_type} name={new_asset.name} id={new_asset.asset_id}")

            if not created_assets:
                return self._error_result("未能创建任何提取结果")

            logger.info(f"[ExtractAllStage] 三合一提取完成 | 创建 {len(created_assets)} 个资产")

            # 返回优先级最高的新创建资产用于前端展示
            # 优先级: pose > depth > lineart
            result_asset = created_assets[0]
            for priority_type in ["pose", "depth", "lineart"]:
                for a in created_assets:
                    if a.asset_type == priority_type:
                        result_asset = a
                        break
                if result_asset.asset_type == priority_type:
                    break

            return AssetProduceResult(
                asset=result_asset,
                success=True,
                elapsed_ms=result.elapsed_ms,
            )

        except Exception as e:
            logger.error(f"[ExtractAllStage] 三合一提取失败: {e}", exc_info=True)
            return self._error_result(str(e) or f"未知错误（{type(e).__name__}）")
