"""
分镜生成阶段

角色+场景融合生成分镜帧。
复用 V6.0 分镜模板系统（通过 pipeline_executor）。

Script 感知：当输入包含 script 资产时，自动提取剧本的 acts 列表，
结合 concept 参考图，批量生成所有幕的分镜帧，返回第一个，
其余通过 metadata.sibling_asset_ids 传递。
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin, collect_content_type

logger = logging.getLogger(__name__)


class StoryboardStage(StagePlugin):
    """分镜生成阶段"""

    stage_def = StageDef(
        stage_id="storyboard",
        name="分镜生成",
        input_types=["concept", "multi_view", "script"],
        input_content_types=[],  # Script 模式下不强制 content_type
        output_type="storyboard",
        default_provider="comfyui",
        supported_providers=["comfyui", "runninghub", "openai_compat", "gemini", "volcengine"],
        description="角色+场景融合生成分镜帧（复用 V6.0 分镜模板系统，支持 script 批量生成）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        err = self._require_input(input_assets)
        if err:
            return self._error_result(err)

        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        # ── Script 感知：如果输入包含 script 资产，批量生成所有幕的分镜 ──
        from services.stages.script_utils import find_script_asset
        script_asset = find_script_asset(input_assets)
        if script_asset:
            return await self._execute_from_script(
                script_asset, input_assets, provider_id, params,
                asset_svc, provider_svc,
            )

        # ── 原有逻辑：单张分镜生成 ──
        reference_images = self._collect_reference_images(input_assets)

        prompt = params.get("prompt", "Storyboard scene composition")
        size = params.get("size", "1365x768")
        model = params.get("model", "")
        template = params.get("template", "")
        fusion_mode = params.get("fusion_mode", "3img")

        logger.info(
            f"[StoryboardStage] 分镜 | provider={provider_id} | "
            f"refs={len(reference_images)} | template={template or 'default'}"
        )

        try:
            gen_kwargs = {}
            if provider_id == "comfyui" and template:
                gen_kwargs["template"] = template
                gen_kwargs["fusion_mode"] = fusion_mode

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
                asset_svc, result,
                asset_type="storyboard",
                name=params.get("name", "分镜帧"),
                parent_id=parent_id,
                extra_metadata={
                    "source_asset_ids": parent_ids,
                    "prompt": prompt,
                    "template": template,
                    "fusion_mode": fusion_mode,
                    "size": size,
                },
                content_type=content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[StoryboardStage] 分镜生成失败: {e}")
            return self._error_result(str(e))

    def _collect_reference_images(self, input_assets: List[AssetRef]) -> List[Dict[str, str]]:
        """从输入资产收集参考图（排除 script 资产）"""
        refs = []
        for asset in input_assets:
            if asset.asset_type == "script":
                continue
            for url in asset.urls:
                if url:
                    refs.append({
                        "url": url,
                        "role": asset.asset_type,
                        "name": asset.name,
                    })
        return refs

    async def _execute_from_script(
        self,
        script_asset: AssetRef,
        input_assets: List[AssetRef],
        provider_id: str,
        params: Dict[str, Any],
        asset_svc,
        provider_svc,
    ) -> AssetProduceResult:
        """从 script 资产批量生成所有幕的分镜帧

        策略：
        - 读取剧本 JSON，提取 acts 列表
        - 对每幕用 scene 描述作为 prompt + concept 参考图生成分镜
        - 全部注册到资产库
        - 返回第一个，metadata.sibling_asset_ids 记录其余
        """
        import time
        start = time.time()
        from services.stages.script_utils import load_script_json, extract_acts
        script = await load_script_json(script_asset)
        if not script:
            return self._error_result(f"无法读取剧本 JSON: {script_asset.asset_id}")

        acts = extract_acts(script)
        if not acts:
            return self._error_result("剧本中无 acts，无法生成分镜")

        # 收集参考图（concept 资产 + sibling）
        reference_images = self._collect_reference_images_with_siblings(input_assets, asset_svc)

        size = params.get("size", "1365x768")
        model = params.get("model", "")
        template = params.get("template", "")
        fusion_mode = params.get("fusion_mode", "3img")

        logger.info(
            f"[StoryboardStage] Script 感知 | script={script_asset.asset_id} | "
            f"acts={len(acts)} | refs={len(reference_images)}"
        )

        created_assets: List[AssetRef] = []
        errors: List[str] = []

        for i, act in enumerate(acts):
            scene_desc = (act.get("scene") or "").strip()
            narration = (act.get("narration") or "").strip()
            prompt = scene_desc or narration or f"第{act.get('act', i+1)}幕分镜"
            # 拼接旁白作为画面提示
            if narration and narration != prompt:
                prompt = f"{prompt}。{narration}"

            try:
                gen_kwargs = {}
                if provider_id == "comfyui" and template:
                    gen_kwargs["template"] = template
                    gen_kwargs["fusion_mode"] = fusion_mode

                result = await provider_svc.generate_image(
                    provider_id=provider_id,
                    prompt=prompt,
                    size=size,
                    model=model,
                    reference_images=reference_images if reference_images else None,
                    **gen_kwargs,
                )
                new_asset = await self._register_asset(
                    asset_svc, result,
                    asset_type="storyboard",
                    name=f"第{act.get('act', i+1)}幕分镜",
                    parent_id=script_asset.asset_id,
                    extra_metadata={
                        "prompt": prompt,
                        "template": template,
                        "fusion_mode": fusion_mode,
                        "size": size,
                        "script_asset_id": script_asset.asset_id,
                        "act_index": i,
                        "act_number": act.get("act", i + 1),
                        "scene": scene_desc,
                    },
                    content_type="",
                )
                created_assets.append(new_asset)
                logger.info(f"[StoryboardStage] 幕{i+1}/{len(acts)} 完成 | id={new_asset.asset_id}")
            except Exception as e:
                logger.error(f"[StoryboardStage] 幕{i+1} 生成失败 | err={e}")
                errors.append(f"幕{i+1}: {e}")

        if not created_assets:
            return self._error_result(f"所有分镜生成失败 | errors={errors}")

        primary = created_assets[0]
        sibling_ids = [a.asset_id for a in created_assets[1:]]
        sibling_meta = {
            "sibling_asset_ids": sibling_ids,
            "script_asset_id": script_asset.asset_id,
            "total_acts": len(created_assets),
        }
        if errors:
            sibling_meta["batch_errors"] = errors
        await asset_svc.update(primary.asset_id, metadata=sibling_meta)
        primary.metadata.update(sibling_meta)

        elapsed = int((time.time() - start) * 1000)
        logger.info(
            f"[StoryboardStage] Script 批量完成 | primary={primary.asset_id} | "
            f"siblings={len(sibling_ids)} | errors={len(errors)} | elapsed={elapsed}ms"
        )

        return AssetProduceResult(asset=primary, success=True, elapsed_ms=elapsed)

    def _collect_reference_images_with_siblings(
        self, input_assets: List[AssetRef], asset_svc,
    ) -> List[Dict[str, str]]:
        """收集参考图，包括 concept 主资产 + sibling_asset_ids 中的角色图"""
        refs = []
        seen_ids = set()
        for asset in input_assets:
            if asset.asset_type == "script":
                continue
            if asset.asset_id in seen_ids:
                continue
            seen_ids.add(asset.asset_id)
            for url in asset.urls:
                if url:
                    refs.append({
                        "url": url,
                        "role": asset.asset_type,
                        "name": asset.name,
                    })
            # 展开 sibling_asset_ids
            for sid in asset.metadata.get("sibling_asset_ids", []):
                if sid in seen_ids:
                    continue
                sibling = asset_svc.get(sid) if hasattr(asset_svc, "get") else None
                if sibling:
                    seen_ids.add(sid)
                    for url in sibling.urls:
                        if url:
                            refs.append({
                                "url": url,
                                "role": sibling.asset_type,
                                "name": sibling.name,
                            })
        return refs
