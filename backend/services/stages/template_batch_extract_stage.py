"""
模板批量提取阶段

Phase 1 工业化流程核心：从参考构图图批量生成模板三件套
- Pose 骨架图 (pose)
- 深度图 (depth)
- 线稿图 (lineart)

输出文件自动按规范命名：TXX_模板名称_pose.png / depth.png / lineart.png
完成后自动更新 templates_manifest.json
"""

import logging
import os
import time
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.template_utils import (
    TEMPLATE_DIR,
    validate_template_id,
    safe_filename_prefix,
    match_asset_type_by_filename,
)

logger = logging.getLogger(__name__)


class TemplateBatchExtractStage(StagePlugin):
    """模板批量提取阶段 — 从参考构图图批量生成模板三件套"""

    stage_def = StageDef(
        stage_id="template_batch_extract",
        name="模板批量提取",
        input_types=["concept", "storyboard", "template_production"],
        input_content_types=[],  # 接受任意内容类型
        output_type="template_production",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从参考构图图批量提取 Pose/深度/线稿，自动命名并更新模板清单",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        # 参数解析
        template_id = params.get("template_id", "")  # 如 "T01_双人正面对话"
        template_name = params.get("template_name", "")  # 如 "T01 双人正面对话"
        person_count = params.get("person_count", 2)
        description = params.get("description", "")
        scene = params.get("scene", "")

        if not template_id:
            return self._error_result("缺少 template_id 参数（如 T01_双人正面对话）")

        if not validate_template_id(template_id):
            return self._error_result(f"template_id 格式不合法: {template_id}（不允许包含路径分隔符）")

        err = self._require_input(input_assets) or self._require_urls(input_assets[0])
        if err:
            return self._error_result(err)

        source = input_assets[0]
        # 取第一个有效 URL
        source_url = next((u for u in (source.urls or []) if u), "")
        if not source_url:
            return self._error_result(f"资产 {source.asset_id} 无有效图片 URL")

        asset_svc, _ = self._get_services()

        logger.info(
            f"[TemplateBatchExtract] 开始 | template_id={template_id} | "
            f"source={source.asset_id}"
        )

        start_time = time.time()
        created_assets: List[AssetRef] = []

        try:
            from services.providers.comfyui_provider import ComfyUIProvider

            provider = ComfyUIProvider()

            # 调用 extract_all 模板，使用安全化的 filename_prefix（中文替换为下划线）
            result = await provider.generate_image(
                prompt="模板三图提取",
                reference_images=[{"url": source_url, "role": "reference", "type": "reference"}],
                template="extract_all",
                filename_prefix=safe_filename_prefix(template_id),
            )

            if not result or not result.filenames:
                return self._error_result("模板三图提取未返回结果")

            filenames = result.filenames or []
            all_urls = result.images or []

            # 根据 filename prefix 识别输出类型并注册资产
            # 使用公共 match_asset_type_by_filename，跳过 depth_clean（属于 Phase 2 输出）
            # 安全配对 filenames 和 urls（长度可能不一致）
            for i, fn in enumerate(filenames):
                url = all_urls[i] if i < len(all_urls) else ""
                if not url:
                    logger.warning(f"[TemplateBatchExtract] 文件 {fn} 无对应 URL，跳过资产注册")
                    continue

                matched_type = match_asset_type_by_filename(
                    fn, skip_keywords=["depth_clean"]
                )
                if not matched_type:
                    logger.debug(f"[TemplateBatchExtract] 跳过未识别文件: {fn}")
                    continue

                asset_type, label = matched_type
                new_asset = await self._register_asset_direct(
                    asset_svc,
                    asset_type=asset_type,
                    name=f"{template_name or template_id} {label}",
                    urls=[url],
                    input_assets=[source],
                    extra_metadata={
                        "source_asset_id": source.asset_id,
                        "extraction_type": "template_batch_extract",
                        "template_id": template_id,
                    },
                    content_type="template_production",
                )
                if new_asset:
                    created_assets.append(new_asset)
                    logger.info(
                        f"[TemplateBatchExtract] 创建资产 | type={asset_type} "
                        f"name={new_asset.name} id={new_asset.asset_id}"
                    )

            if not created_assets:
                return self._error_result("未能创建任何提取结果")

            # 检查是否所有3种类型都成功注册
            created_types = {a.asset_type for a in created_assets}
            expected_types = {"lineart", "depth", "pose"}
            missing_types = expected_types - created_types
            if missing_types:
                logger.warning(
                    f"[TemplateBatchExtract] 部分类型未注册 | "
                    f"missing={missing_types} | template_id={template_id}"
                )

            # 自动复制到模板目录并重命名
            self._copy_to_template_dir(filenames, template_id)

            # 自动更新 templates_manifest.json
            await self._update_manifest(
                template_id=template_id,
                template_name=template_name,
                description=description,
                scene=scene,
                person_count=person_count,
            )

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"[TemplateBatchExtract] 完成 | template_id={template_id} | "
                f"创建 {len(created_assets)} 个资产 | {elapsed_ms}ms"
            )

            # 返回第一个创建的资产，而非输入资产
            return AssetProduceResult(
                asset=created_assets[0] if created_assets else source,
                success=True,
                elapsed_ms=elapsed_ms,
            )

        except Exception as e:
            logger.error(f"[TemplateBatchExtract] 失败: {e}", exc_info=True)
            return self._error_result(str(e) or f"未知错误（{type(e).__name__}）")

    def _copy_to_template_dir(self, filenames: List[str], template_id: str):
        """将提取结果复制到模板目录并按规范重命名

        命名规则：
          - lineart_xxx  → TXX_名称_lineart.png
          - depth_xxx    → TXX_名称_depth_raw.png
          - pose_xxx     → TXX_名称_pose.png
        """
        from services.workflow_builder import _COMFYUI_OUTPUT_DIR
        from services.template_utils import (
            ensure_template_dir, remove_old_files, match_and_copy_files,
        )

        output_dir = _COMFYUI_OUTPUT_DIR
        if not output_dir or not os.path.isdir(output_dir):
            logger.warning(f"[TemplateBatchExtract] ComfyUI output 目录不存在: {output_dir}")
            return

        ensure_template_dir()

        # 幂等性：清除上次生成的主文件
        remove_old_files(
            template_id,
            ["pose.png", "depth_raw.png", "lineart.png"],
            tag="TemplateBatchExtract",
        )

        rename_map = {
            "lineart": f"{template_id}_lineart.png",
            "depth_raw": f"{template_id}_depth_raw.png",
            "pose": f"{template_id}_pose.png",
        }

        # depth 前缀匹配 depth_raw（通过 skip_keywords 排除 depth_clean）
        # 注意：depth_raw 需要优先匹配，因此单独处理
        match_and_copy_files(
            filenames=filenames,
            output_dir=output_dir,
            rename_map={"lineart": rename_map["lineart"], "depth_raw": rename_map["depth_raw"], "pose": rename_map["pose"]},
            tag="TemplateBatchExtract",
            skip_keywords=["depth_clean"],
        )

        # depth_raw 未匹配时尝试 depth 前缀
        from services.template_utils import TEMPLATE_DIR
        if not (TEMPLATE_DIR / rename_map["depth_raw"]).exists():
            match_and_copy_files(
                filenames=filenames,
                output_dir=output_dir,
                rename_map={"depth": rename_map["depth_raw"]},
                tag="TemplateBatchExtract",
                skip_keywords=["depth_clean", "depth_raw"],
            )

    async def _update_manifest(
        self,
        template_id: str,
        template_name: str,
        description: str,
        scene: str,
        person_count: int,
    ):
        """自动更新 templates_manifest.json（原子写入，异步互斥锁保护）"""
        from services.template_utils import atomic_manifest_update, TEMPLATE_DIR
        asset_svc, _ = self._get_services()

        def _do_update(manifest):
            templates = manifest.get("templates", [])

            # 检查模板文件是否已生成
            pose_exists = (TEMPLATE_DIR / f"{template_id}_pose.png").exists()
            depth_exists = (TEMPLATE_DIR / f"{template_id}_depth_raw.png").exists()
            lineart_exists = (TEMPLATE_DIR / f"{template_id}_lineart.png").exists()

            existing_count = sum([pose_exists, depth_exists, lineart_exists])
            if existing_count == 3:
                status = "extracted"
            elif existing_count > 0:
                status = "partial"
            else:
                status = "pending"

            found = False
            for tmpl in templates:
                if tmpl.get("id") == template_id:
                    tmpl["status"] = status
                    tmpl["person_count"] = person_count
                    if description:
                        tmpl["description"] = description
                    if scene:
                        tmpl["scene"] = scene
                    if "files" not in tmpl:
                        tmpl["files"] = {}
                    if pose_exists:
                        tmpl["files"]["pose"] = f"{template_id}_pose.png"
                    if depth_exists:
                        tmpl["files"]["depth_raw"] = f"{template_id}_depth_raw.png"
                    if lineart_exists:
                        tmpl["files"]["lineart"] = f"{template_id}_lineart.png"

                    # 幂等性：重新提取时，清除下游阶段的文件引用和状态
                    for downstream_key in ["depth_clean", "mask", "pose_simplified"]:
                        if downstream_key in tmpl.get("files", {}):
                            downstream_path = TEMPLATE_DIR / tmpl["files"][downstream_key]
                            if downstream_path.exists():
                                try:
                                    os.remove(str(downstream_path))
                                    logger.info(
                                        f"[TemplateBatchExtract] 清除下游文件: {tmpl['files'][downstream_key]}"
                                    )
                                except OSError:
                                    pass
                            del tmpl["files"][downstream_key]
                    tmpl.pop("pose_simplified", None)
                    tmpl.pop("pose_corrected", None)
                    found = True
                    break

            if not found:
                new_entry = {
                    "id": template_id,
                    "name": template_name or template_id,
                    "description": description or "",
                    "scene": scene or "",
                    "person_count": person_count,
                    "files": {
                        "pose": f"{template_id}_pose.png" if pose_exists else "",
                        "depth_raw": f"{template_id}_depth_raw.png" if depth_exists else "",
                        "lineart": f"{template_id}_lineart.png" if lineart_exists else "",
                    },
                    "recommended_params": {
                        "depth_weight": 0.8,
                        "pose_weight": 0.45,
                        "mask_blur": 2,
                    },
                    "status": status,
                }
                templates.append(new_entry)

            manifest["templates"] = templates
            return manifest

        success = await atomic_manifest_update(_do_update)
        if not success:
            logger.error(f"[TemplateBatchExtract] manifest 更新失败 | template_id={template_id}")
            return

        # 清除下游资产记录（在锁外执行，避免长时间持锁）
        downstream_extraction_types = [
            "template_clean",
            "template_pose",
            "template_pose_corrected",
        ]
        ids_to_delete = [
            a.asset_id for a in asset_svc.list_assets()
            if (a.metadata
                and a.metadata.get("template_id") == template_id
                and a.metadata.get("extraction_type") in downstream_extraction_types)
        ]
        for aid in ids_to_delete:
            try:
                await asset_svc.delete(aid)
                logger.info(f"[TemplateBatchExtract] 清除下游资产: id={aid}")
            except Exception as del_err:
                logger.warning(f"[TemplateBatchExtract] 清除下游资产失败: {del_err}")

        logger.info(f"[TemplateBatchExtract] manifest 已更新 | template_id={template_id}")
