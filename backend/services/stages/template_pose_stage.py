"""
模板Pose优化阶段

Phase 3 工业化流程：从完整 OpenPose 骨架图生成简化7节点骨架
- 自动去除手指、面部等冗余关节点
- 只保留头、肩、肘、胯、膝、脚 7个关键节点
- 输出规范命名的简化 Pose 图

输入：原始 Pose 骨架图
输出：简化 Pose 图（TXX_名称_pose.png）
"""

import logging
import os
import time
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.template_utils import (
    TEMPLATE_DIR,
    atomic_manifest_update,
    validate_template_id,
    safe_filename_prefix,
    match_asset_type_by_filename,
)

logger = logging.getLogger(__name__)


class TemplatePoseStage(StagePlugin):
    """模板Pose优化阶段 — 简化7节点骨架渲染"""

    stage_def = StageDef(
        stage_id="template_pose",
        name="模板Pose优化",
        input_types=["pose", "concept", "storyboard", "template_production"],
        input_content_types=[],
        output_type="template_production",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从完整OpenPose骨架图生成简化7节点骨架，去除冗余关节点",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        template_id = params.get("template_id", "")
        template_name = params.get("template_name", "")
        joint_radius = params.get("joint_radius", 5)
        line_width = params.get("line_width", 3)
        head_radius = params.get("head_radius", 8)

        if not template_id:
            return self._error_result("缺少 template_id 参数")

        if not validate_template_id(template_id):
            return self._error_result(
                f"template_id 格式不合法: {template_id}（不允许包含路径分隔符）"
            )

        # 幂等性：重新生成简化Pose时，先清除上次生成的资产记录
        asset_svc, _ = self._get_services()
        # 先收集要删除的资产 ID，再逐一删除，避免遍历中删除导致跳过
        ids_to_delete = [
            a.asset_id
            for a in asset_svc.list_assets()
            if (
                a.metadata
                and a.metadata.get("template_id") == template_id
                and a.metadata.get("extraction_type") == "template_pose"
            )
        ]
        for aid in ids_to_delete:
            try:
                await asset_svc.delete(aid)
                logger.info(f"[TemplatePose] 清除旧资产: id={aid}")
            except Exception as del_err:
                logger.warning(f"[TemplatePose] 清除旧资产失败: {del_err}")

        err = self._require_input(input_assets) or self._require_urls(input_assets[0])
        if err:
            return self._error_result(err)

        source = input_assets[0]
        source_url = next((u for u in (source.urls or []) if u), "")
        if not source_url:
            return self._error_result(f"资产 {source.asset_id} 无有效图片 URL")

        logger.info(
            f"[TemplatePose] 开始 | template_id={template_id} | " f"source={source.asset_id}"
        )

        start_time = time.time()

        try:
            from services.providers.comfyui_provider import ComfyUIProvider

            provider = ComfyUIProvider()

            result = await provider.generate_image(
                prompt="模板Pose简化",
                reference_images=[{"url": source_url, "role": "reference", "type": "reference"}],
                template="template_pose",
                filename_prefix=safe_filename_prefix(template_id),
                joint_radius=joint_radius,
                line_width=line_width,
                head_radius=head_radius,
            )

            if not result or not result.filenames:
                return self._error_result("Pose简化未返回结果")

            filenames = result.filenames or []
            all_urls = result.images or []

            # 注册资产（安全配对 filenames 和 urls）
            # 收集所有匹配 pose 前缀的 URL，合并注册为一个资产
            pose_urls = []
            for i, fn in enumerate(filenames):
                matched = match_asset_type_by_filename(fn)
                if not matched or matched[0] != "pose":
                    continue

                url = all_urls[i] if i < len(all_urls) else ""
                if not url:
                    logger.warning(f"[TemplatePose] 文件 {fn} 无对应 URL，跳过")
                    continue
                pose_urls.append(url)

            created_pose_asset = None
            if pose_urls:
                new_asset = await self._register_asset_direct(
                    asset_svc,
                    asset_type="pose",
                    name=f"{template_name or template_id} 简化Pose",
                    urls=pose_urls,
                    input_assets=[source],
                    extra_metadata={
                        "source_asset_id": source.asset_id,
                        "extraction_type": "template_pose",
                        "template_id": template_id,
                        "simplified": True,
                    },
                    content_type="template_production",
                )
                if new_asset:
                    created_pose_asset = new_asset
                    logger.info(
                        f"[TemplatePose] 创建资产 | name={new_asset.name} "
                        f"id={new_asset.asset_id} urls={len(pose_urls)}"
                    )

            # 复制到模板目录
            self._copy_to_template_dir(filenames, template_id)

            # 更新 manifest
            await self._update_manifest(template_id)

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(f"[TemplatePose] 完成 | template_id={template_id} | {elapsed_ms}ms")

            # 如果资产注册失败，返回错误（文件已复制但资产表无记录）
            if not created_pose_asset:
                logger.error(
                    f"[TemplatePose] 资产注册失败 | template_id={template_id} | "
                    f"文件已复制到模板目录但未注册到资产表"
                )
                return AssetProduceResult(
                    asset=source,
                    success=False,
                    error="Pose简化图已生成但资产注册失败",
                    elapsed_ms=elapsed_ms,
                )

            return AssetProduceResult(
                asset=created_pose_asset,
                success=True,
                elapsed_ms=elapsed_ms,
            )

        except Exception as e:
            logger.error(f"[TemplatePose] 失败: {e}", exc_info=True)
            return self._error_result(str(e))

    def _copy_to_template_dir(self, filenames: List[str], template_id: str):
        """复制简化Pose图到模板目录

        保存为 {template_id}_pose_simplified.png，不覆盖原始 Pose 图
        """
        from services.workflow_builder import _COMFYUI_OUTPUT_DIR
        from services.template_utils import (
            ensure_template_dir,
            remove_old_files,
            match_and_copy_files,
        )

        output_dir = _COMFYUI_OUTPUT_DIR
        if not output_dir or not os.path.isdir(output_dir):
            return

        ensure_template_dir()

        # 幂等性：清除上次生成的简化Pose文件
        remove_old_files(
            template_id,
            ["pose_simplified.png"],
            tag="TemplatePose",
        )

        # 只复制第一个匹配的简化Pose文件（避免多batch输出互相覆盖）
        match_and_copy_files(
            filenames=filenames,
            output_dir=output_dir,
            rename_map={"pose": f"{template_id}_pose_simplified.png"},
            tag="TemplatePose",
            first_only=True,
        )

    async def _update_manifest(self, template_id: str):
        """更新 manifest 中 Pose 文件状态（原子写入，异步互斥锁保护）

        Phase 3 完成时检查全部 6 个模板文件是否存在，齐全则设 status=ready。
        """

        def _do_update(manifest):
            pose_simplified_exists = (TEMPLATE_DIR / f"{template_id}_pose_simplified.png").exists()

            for tmpl in manifest.get("templates", []):
                if tmpl.get("id") == template_id:
                    if "files" not in tmpl:
                        tmpl["files"] = {}
                    if pose_simplified_exists:
                        tmpl["files"]["pose_simplified"] = f"{template_id}_pose_simplified.png"
                        tmpl["pose_simplified"] = True

                    # Phase 3 完成：检查全部 6 个文件齐全后设 status=ready
                    all_files = [
                        TEMPLATE_DIR / f"{template_id}_pose.png",
                        TEMPLATE_DIR / f"{template_id}_depth_raw.png",
                        TEMPLATE_DIR / f"{template_id}_lineart.png",
                        TEMPLATE_DIR / f"{template_id}_depth_clean.png",
                        TEMPLATE_DIR / f"{template_id}_mask.png",
                        TEMPLATE_DIR / f"{template_id}_pose_simplified.png",
                    ]
                    if all(p.exists() for p in all_files):
                        tmpl["status"] = "ready"
                    break

            return manifest

        success = await atomic_manifest_update(_do_update)
        if success:
            logger.info(f"[TemplatePose] manifest 已更新 | template_id={template_id}")
        else:
            logger.error(f"[TemplatePose] manifest 更新失败 | template_id={template_id}")
