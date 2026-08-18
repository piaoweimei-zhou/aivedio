"""
单图提取阶段基类

PoseExtraction / DepthMap / LineartExtraction 三个阶段逻辑高度相似，
仅 stage_id / template / asset_type / name_suffix 等配置不同。
本基类统一通用流程，派生类只需声明配置常量。
"""

import logging
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class SingleExtractionStage(StagePlugin):
    """单图提取阶段基类 — 派生类通过类属性声明差异化配置"""

    # ── 派生类必须覆盖的配置 ──
    stage_id: str = ""
    stage_name: str = ""
    output_type: str = ""
    template: str = ""
    asset_type: str = ""
    name_suffix: str = ""               # 资产名后缀，如 "姿态" / "深度图" / "线稿"
    prompt_text: str = ""               # 传给 provider 的 prompt
    description: str = ""
    default_method: Optional[str] = None   # None 表示不传 method（如 pose 固定 openpose）
    fixed_extraction_type: Optional[str] = None  # 若设置，extraction_type 用此值而非 method
    input_content_types: List[str] = []     # 空列表表示不限 content_type

    @classmethod
    def _build_stage_def(cls) -> StageDef:
        return StageDef(
            stage_id=cls.stage_id,
            name=cls.stage_name,
            input_types=["concept", "storyboard"],
            input_content_types=cls.input_content_types,
            output_type=cls.output_type,
            default_provider="comfyui",
            supported_providers=["comfyui"],
            description=cls.description,
        )

    def __init__(self):
        self.stage_def = self._build_stage_def()

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

        # method 处理：default_method 为 None 时不接受 method 参数
        method = params.get("method", self.default_method) if self.default_method else None
        extraction_type = self.fixed_extraction_type or method

        log_tag = self.__class__.__name__
        method_part = f" | method={method}" if method else ""
        logger.info(f"[{log_tag}] {self.stage_name} | asset={source.asset_id}{method_part}")

        try:
            from services.providers.comfyui_provider import ComfyUIProvider

            provider = ComfyUIProvider()
            gen_kwargs: Dict[str, Any] = {
                "prompt": self.prompt_text,
                "reference_images": [
                    {"url": source.urls[0], "role": "reference", "type": "reference"}
                ],
                "template": self.template,
            }
            if method is not None:
                gen_kwargs["method"] = method

            result = await provider.generate_image(**gen_kwargs)

            # 资产名：有 method 时附加，否则只显示后缀
            asset_name = (
                f"{source.name} {self.name_suffix}({method})"
                if method else f"{source.name} {self.name_suffix}"
            )

            new_asset = await self._register_asset(
                asset_svc, result,
                asset_type=self.asset_type,
                name=asset_name,
                parent_id=source.asset_id,
                extra_metadata={
                    "source_asset_id": source.asset_id,
                    "extraction_type": extraction_type,
                },
                content_type=source.content_type,
            )

            return AssetProduceResult(
                asset=new_asset, success=True, elapsed_ms=result.elapsed_ms
            )

        except Exception as e:
            logger.error(f"[{log_tag}] {self.stage_name}失败: {e}")
            return self._error_result(str(e))
