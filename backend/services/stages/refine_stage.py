"""
精修/超分阶段

对图像进行精修或超分辨率放大。
- ComfyUI: 使用 build_refinement_workflow (精修) 或 upscale 工作流 (超分)
- 其他供应商: 通用图生图接口
"""

import logging
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.provider_service import get_provider_service
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)

# content_type 驱动的精修约束后缀（自动追加到用户指令后面）
_REFINE_CONSTRAINTS = {
    "character": "保持原图的脸部特征、发型和肤色完全不变，仅修改指定部位。",
    "scene": "保持场景的核心结构、建筑布局和物体位置不变。",
    "prop": "保留原图的轮廓外形不变，仅修改材质、颜色或表面纹理。",
    "": "",
}


async def _get_image_size_from_url(image_url: str) -> Optional[str]:
    """从 ComfyUI 图片 URL 读取实际图片的分辨率

    通过 HTTP 请求 ComfyUI /view 端点获取图片并读取尺寸，
    不依赖本地文件路径（COMFYUI_DIR 可能未设置或不一致）。
    """
    if not image_url:
        return None
    try:
        import os
        import aiohttp
        from urllib.parse import urlparse, parse_qs

        # 从 URL 中解析 filename
        parsed = urlparse(image_url)
        qs = parse_qs(parsed.query)
        filename = qs.get("filename", [None])[0]
        if not filename:
            logger.warning(f"[RefineStage] 无法从 URL 解析文件名: {image_url}")
            return None

        # 通过 HTTP 请求 ComfyUI /view 端点读取图片尺寸
        from services.comfyui.config import COMFYUI_BASE_URL

        base_url = COMFYUI_BASE_URL
        view_url = f"{base_url}/view?filename={filename}&type=output"

        async with aiohttp.ClientSession() as session:
            async with session.get(view_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    from PIL import Image
                    import io

                    with Image.open(io.BytesIO(data)) as img:
                        w, h = img.size
                        logger.info(f"[RefineStage] 读取到实际图片尺寸: {w}x{h} | file={filename}")
                        return f"{w}x{h}"
                else:
                    logger.warning(
                        f"[RefineStage] HTTP 获取图片失败: status={resp.status} | url={view_url}"
                    )  # noqa: E501

        # Fallback: 本地文件读取（从 ComfyUI output 目录）
        from services.comfyui.config import COMFYUI_DIR, COMFYUI_OUTPUT_DIR

        if COMFYUI_DIR and os.path.isdir(COMFYUI_OUTPUT_DIR):
            filepath = os.path.join(COMFYUI_OUTPUT_DIR, filename)
            if os.path.exists(filepath):
                from PIL import Image

                with Image.open(filepath) as img:
                    w, h = img.size
                    logger.info(f"[RefineStage] 本地读取图片尺寸: {w}x{h} | file={filepath}")
                    return f"{w}x{h}"
    except Exception as e:
        logger.warning(f"[RefineStage] 读取图片尺寸异常: {e}")
    return None


class RefineStage(StagePlugin):
    """精修/超分阶段"""

    stage_def = StageDef(
        stage_id="refine",
        name="精修/超分",
        input_types=["concept", "storyboard", "edit"],
        output_type="edit",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="对图像进行精修或超分辨率放大",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc = get_asset_service()
        provider_svc = get_provider_service()

        if not input_assets:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="精修需要至少一个输入资产",
            )

        source = input_assets[0]
        if not source.urls:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=f"资产 {source.asset_id} 无图片 URL",
            )

        if not provider_id:
            provider_id = self.stage_def.default_provider

        # 精修参数
        mode = params.get("mode", "refine")  # refine / upscale
        prompt = params.get("prompt", f"Enhance and refine: {source.name}")
        size = params.get("size", "1024x1024")
        upscale_factor = int(params.get("upscale_factor", 2))
        # 超分模式下，先不推算尺寸，等生成后读取实际图片文件
        model = params.get("model", "")
        # ⭐ 超分使用固定 seed，避免扩散模型随机性导致结果不一致
        # SeedVR2 是扩散模型，不同 seed 会产生不同去噪路径，导致脸部等细节变化
        # 使用模板默认 seed (341080070)，与 ComfyUI 直接执行一致
        seed = int(params.get("seed", 341080070)) if mode != "upscale" else 341080070
        content_type = params.get("content_type", "") or source.content_type

        # 精修约束补全：根据内容类型自动追加保持类约束（类似概念图的 prompt 前缀）
        enhance_prompt = params.get("enhance_prompt", True)
        if enhance_prompt and content_type and prompt:
            constraint = _REFINE_CONSTRAINTS.get(content_type, "")
            if constraint and constraint not in prompt:
                prompt = prompt + " " + constraint

        logger.info(
            f"[RefineStage] 精修 | provider={provider_id} | mode={mode} | content_type={content_type} | asset={source.asset_id}"  # noqa: E501
        )

        try:
            # ComfyUI 专用路径
            if provider_id == "comfyui":
                result = await self._refine_via_comfyui(
                    source, mode, prompt, size, upscale_factor, seed, params
                )
            else:
                # 其他供应商：通用图生图
                result = await self._refine_via_generic(
                    provider_svc, provider_id, source, mode, prompt, size, model, upscale_factor
                )

            if mode == "upscale":
                # 读取实际输出图片的分辨率
                actual_size = await _get_image_size_from_url(result.image_url)
                if actual_size:
                    size = actual_size

            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="edit",
                name=f"{source.name} ({mode})",
                urls=result.images or ([result.image_url] if result.image_url else []),
                input_assets=[source],
                extra_metadata={
                    "mode": mode,
                    "source_asset_id": source.asset_id,
                    "provider_id": provider_id,
                    "prompt": prompt,
                    "seed": result.seed,
                    "elapsed_ms": result.elapsed_ms,
                    "size": size,
                    "upscale_factor": upscale_factor if mode == "upscale" else 1,
                },
                content_type=source.content_type,
            )

            return AssetProduceResult(
                asset=new_asset,
                success=True,
                elapsed_ms=result.elapsed_ms,
            )

        except Exception as e:
            logger.error(f"[RefineStage] 精修失败: {e}")
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=str(e),
            )

    async def _refine_via_comfyui(
        self,
        source: AssetRef,
        mode: str,
        prompt: str,
        size: str,
        upscale_factor: int,
        seed: int,
        params: Dict[str, Any],
    ) -> Any:
        """ComfyUI 专用精修/超分"""
        from services.providers.comfyui_provider import ComfyUIProvider

        provider = ComfyUIProvider()
        image_url = source.urls[0] if source.urls else ""

        if mode == "upscale":
            return await provider.upscale_image(
                image_url=image_url,
                upscale_factor=upscale_factor,
                seed=seed,
                prompt=prompt,
            )
        else:
            return await provider.refine_image(
                image_url=image_url,
                prompt=prompt,
                seed=seed,
                content_type=source.content_type,
            )

    async def _refine_via_generic(
        self,
        provider_svc,
        provider_id: str,
        source: AssetRef,
        mode: str,
        prompt: str,
        size: str,
        model: str,
        upscale_factor: int,
    ) -> Any:
        """通用供应商精修（图生图）"""
        # 如果是 upscale 模式，调整尺寸
        if mode == "upscale":
            import re

            match = re.fullmatch(r"\s*(\d+)\s*[xX*]\s*(\d+)\s*", size)
            if match:
                w, h = int(match.group(1)), int(match.group(2))
                size = f"{w * upscale_factor}x{h * upscale_factor}"

        reference_images = [{"url": url, "role": "reference"} for url in source.urls if url]

        return await provider_svc.generate_image(
            provider_id=provider_id,
            prompt=prompt,
            size=size,
            model=model,
            reference_images=reference_images,
        )
