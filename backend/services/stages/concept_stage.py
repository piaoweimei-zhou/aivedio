"""
概念图生成阶段

从文本描述生成角色/场景/道具概念图。
支持 ComfyUI（本地）和云端供应商。

Script 感知：当输入包含 script 资产时，自动提取剧本中的角色列表，
批量生成所有角色概念图，返回第一个，其余通过 metadata.sibling_asset_ids 传递。
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.style_registry import get_style

logger = logging.getLogger(__name__)

# content_type 驱动的默认尺寸
_DEFAULT_SIZES = {
    "character": "1080x1920",
    "scene":     "1920x1080",
    "prop":      "1920x1080",
    "emotional_scene": "1080x1350",  # 情感共鸣图，竖版4:5 适合社媒
    "":          "1080x1920",
}

# content_type 驱动的提示词增强前缀（中文，Z-Image 对中文敏感）
# 注意：workflow_builder 也会按 content_type 添加触发词和质量前缀
# 这里只做轻量补充，避免与 workflow_builder 重复
_PROMPT_PREFIX = {
    "character": "",  # 触发词由 workflow_builder 统一添加
    "scene":     "",
    "prop":      "",
    "emotional_scene": "情绪共鸣场景图，强调光线氛围与人物情绪表达，电影质感，",
    "":          "",
}


def _apply_style_prompt(prompt: str, style_id: str) -> str:
    """追加网感风格的视觉提示词（英文关键词，兼容 ComfyUI 与云端）"""
    if not style_id:
        return prompt
    style = get_style(style_id)
    if not style:
        return prompt
    visual = style.get("visual_prompt", "")
    if visual and visual not in prompt:
        return f"{prompt}, {visual}"
    return prompt


class ConceptStage(StagePlugin):
    """概念图生成阶段"""

    stage_def = StageDef(
        stage_id="concept",
        name="概念图生成",
        input_types=[],
        output_type="concept",
        default_provider="comfyui",
        supported_providers=["comfyui", "openai_compat", "modelscope", "gemini", "volcengine"],
        description="从文本描述生成角色/场景/道具概念图",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc, provider_svc = self._get_services()

        # ── Script 感知：如果输入包含 script 资产，批量生成角色概念图 ──
        from services.stages.script_utils import find_script_asset
        script_asset = find_script_asset(input_assets)
        if script_asset:
            return await self._execute_from_script(
                script_asset, input_assets, provider_id, params,
                asset_svc, provider_svc,
            )

        # ── 原有逻辑：从 prompt 生成单张概念图 ──
        prompt = params.get("prompt", "")
        content_type = params.get("content_type", "")
        name = params.get("name", "概念图")
        size = params.get("size") or _DEFAULT_SIZES.get(content_type, "1024x1024")
        model = params.get("model", "")
        parent_id = params.get("parent_id", "")
        enhance_prompt = params.get("enhance_prompt", True)
        style_id = params.get("style_id", "")
        style = get_style(style_id) if style_id else None

        if not prompt:
            if input_assets:
                prompt = input_assets[0].metadata.get("description", "")
                if not prompt:
                    prompt = input_assets[0].name
            if not prompt:
                return self._error_result("概念图生成需要 prompt 参数")

        if enhance_prompt and content_type in _PROMPT_PREFIX:
            prefix = _PROMPT_PREFIX[content_type]
            if prefix and not prompt.startswith(prefix):
                prompt = prefix + prompt

        # 追加网感风格视觉提示词
        prompt = _apply_style_prompt(prompt, style_id)

        provider_id = self._resolve_provider(provider_id)

        logger.info(f"[ConceptStage] 生成概念图 | provider={provider_id} | content_type={content_type} | size={size} | prompt={prompt[:60]}")

        try:
            reference_images = None
            if input_assets:
                reference_images = [
                    {"url": url, "role": "reference"}
                    for asset in input_assets
                    for url in asset.urls
                    if url
                ]

            result = await provider_svc.generate_image(
                provider_id=provider_id,
                prompt=prompt,
                size=size,
                model=model,
                reference_images=reference_images,
                content_type=content_type,
            )

            new_asset = await self._register_asset(
                asset_svc, result,
                asset_type="concept",
                name=name,
                parent_id=parent_id,
                extra_metadata={"prompt": prompt, "model": model or result.model, "size": size, "content_type": content_type, "style_id": style["style_id"] if style else "", "style_name": style["name"] if style else ""},
                content_type=content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[ConceptStage] 生成失败: {e}")
            return self._error_result(str(e))

    async def _execute_from_script(
        self,
        script_asset: AssetRef,
        input_assets: List[AssetRef],
        provider_id: str,
        params: Dict[str, Any],
        asset_svc,
        provider_svc,
    ) -> AssetProduceResult:
        """从 script 资产批量生成角色概念图

        策略：
        - 读取剧本 JSON，提取 characters 列表
        - 对每个角色用 desc 作为 prompt 生成概念图
        - 全部注册到资产库
        - 返回第一个，metadata.sibling_asset_ids 记录其余
        - content_type="character"
        """
        import time
        start = time.time()
        from services.stages.script_utils import load_script_json, extract_characters
        script = await load_script_json(script_asset)
        if not script:
            return self._error_result(f"无法读取剧本 JSON: {script_asset.asset_id}")

        characters = extract_characters(script)
        if not characters:
            # 回退：用 topic 作为单张场景概念图
            topic = script.get("meta", {}).get("topic") or script_asset.metadata.get("topic", "")
            if not topic:
                return self._error_result("剧本中无角色且无 topic，无法生成概念图")
            characters = [{"name": "场景", "desc": topic, "role": "scene"}]

        provider_id = self._resolve_provider(provider_id)
        content_type = params.get("content_type", "character")
        size = params.get("size") or _DEFAULT_SIZES.get(content_type, "1024x1024")
        model = params.get("model", "")
        enhance_prompt = params.get("enhance_prompt", True)
        style_id = params.get("style_id", "") or script.get("meta", {}).get("style_id", "")
        style = get_style(style_id) if style_id else None

        logger.info(
            f"[ConceptStage] Script 感知 | script={script_asset.asset_id} | "
            f"characters={len(characters)} | provider={provider_id} | style={style['style_id'] if style else ''}"
        )

        created_assets: List[AssetRef] = []
        errors: List[str] = []

        for i, char in enumerate(characters):
            prompt = char["desc"]
            if enhance_prompt and content_type in _PROMPT_PREFIX:
                prefix = _PROMPT_PREFIX[content_type]
                if prefix and not prompt.startswith(prefix):
                    prompt = prefix + prompt

            # 追加网感风格视觉提示词
            prompt = _apply_style_prompt(prompt, style_id)

            try:
                result = await provider_svc.generate_image(
                    provider_id=provider_id,
                    prompt=prompt,
                    size=size,
                    model=model,
                    reference_images=None,
                    content_type=content_type,
                )
                new_asset = await self._register_asset(
                    asset_svc, result,
                    asset_type="concept",
                    name=char["name"],
                    parent_id=script_asset.asset_id,
                    extra_metadata={
                        "prompt": prompt,
                        "model": model or result.model,
                        "size": size,
                        "content_type": content_type,
                        "role": char.get("role", ""),
                        "script_asset_id": script_asset.asset_id,
                        "character_index": i,
                        "style_id": style["style_id"] if style else "",
                        "style_name": style["name"] if style else "",
                    },
                    content_type=content_type,
                )
                created_assets.append(new_asset)
                logger.info(f"[ConceptStage] 角色{i+1}/{len(characters)} 完成 | name={char['name']} | id={new_asset.asset_id}")
            except Exception as e:
                logger.error(f"[ConceptStage] 角色{i+1} 生成失败 | name={char['name']} | err={e}")
                errors.append(f"{char['name']}: {e}")

        if not created_assets:
            return self._error_result(f"所有角色概念图生成失败 | errors={errors}")

        # 返回第一个资产，其余通过 sibling_asset_ids 传递
        primary = created_assets[0]
        sibling_ids = [a.asset_id for a in created_assets[1:]]
        # 把 sibling 信息写入主资产的 metadata
        sibling_meta = {
            "sibling_asset_ids": sibling_ids,
            "script_asset_id": script_asset.asset_id,
            "total_characters": len(created_assets),
        }
        if errors:
            sibling_meta["batch_errors"] = errors
        await asset_svc.update(primary.asset_id, metadata=sibling_meta)
        primary.metadata.update(sibling_meta)

        elapsed = int((time.time() - start) * 1000)
        logger.info(
            f"[ConceptStage] Script 批量完成 | primary={primary.asset_id} | "
            f"siblings={len(sibling_ids)} | errors={len(errors)} | elapsed={elapsed}ms"
        )

        return AssetProduceResult(asset=primary, success=True, elapsed_ms=elapsed)
