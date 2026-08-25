"""
模板清场+蒙版生成阶段

Phase 2 工业化流程核心：从参考构图图自动生成清场深度图和蒙版
- SAM2 自动识别人物区域
- Inpaint 自动清场深度图（去除人物，保留场景深度层次）
- 自动生成蒙版（3像素高斯模糊 + 1像素收缩）

输入：参考构图图 + 原始深度图
输出：depth_clean.png + mask_final.png
"""

import logging
import os
import shutil
import time
from typing import Any, Dict, List

import cv2
import numpy as np
from PIL import Image, ImageFilter

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.template_utils import TEMPLATE_DIR, validate_template_id, safe_filename_prefix

logger = logging.getLogger(__name__)


class TemplateCleanStage(StagePlugin):
    """模板清场+蒙版生成阶段 — SAM2清场深度图 + 自动生成蒙版"""

    stage_def = StageDef(
        stage_id="template_clean",
        name="模板清场+蒙版",
        input_types=["concept", "storyboard", "depth", "template_production"],
        input_content_types=[],
        output_type="template_production",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="SAM2自动识别人物 → 清场深度图 + 生成蒙版，一步完成",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        # 参数解析
        template_id = params.get("template_id", "")
        template_name = params.get("template_name", "")

        if not template_id:
            return self._error_result("缺少 template_id 参数（如 T01_双人正面对话）")

        if not validate_template_id(template_id):
            return self._error_result(
                f"template_id 格式不合法: {template_id}（不允许包含路径分隔符）"
            )

        # 幂等性：重新清场时，先清除上次生成的资产记录
        asset_svc, _ = self._get_services()
        # 先收集要删除的资产 ID，再逐一删除，避免遍历中删除导致跳过
        ids_to_delete = [
            a.asset_id
            for a in asset_svc.list_assets()
            if (
                a.metadata
                and a.metadata.get("template_id") == template_id
                and a.metadata.get("extraction_type") == "template_clean"
            )
        ]
        for aid in ids_to_delete:
            try:
                await asset_svc.delete(aid)
                logger.info(f"[TemplateClean] 清除旧资产: id={aid}")
            except Exception as del_err:
                logger.warning(f"[TemplateClean] 清除旧资产失败: {del_err}")

        # 需要至少2个输入资产：参考构图图 + 原始深度图
        if len(input_assets) < 1:
            return self._error_result("至少需要1个输入资产（参考构图图）")

        # 查找参考构图图和原始深度图
        ref_asset = None
        depth_asset = None

        for asset in input_assets:
            if asset.asset_type in ("concept", "storyboard", "reference") and not ref_asset:
                ref_asset = asset
            elif asset.asset_type == "depth" and not depth_asset:
                depth_asset = asset

        # 如果只传了一个资产，尝试从同项目查找深度图
        if ref_asset and not depth_asset:
            asset_svc, _ = self._get_services()
            for a in asset_svc.list_assets():
                if (
                    a.asset_type == "depth"
                    and a.metadata
                    and a.metadata.get("template_id") == template_id
                    and a.metadata.get("extraction_type") == "template_batch_extract"
                ):
                    depth_asset = a
                    logger.info(
                        f"[TemplateClean] 自动关联深度图 | id={a.asset_id} " f"name={a.name}"
                    )
                    break

        if not ref_asset:
            ref_asset = input_assets[0]

        ref_url = next((u for u in (ref_asset.urls or []) if u), "")
        if not ref_url:
            return self._error_result(f"参考构图图 {ref_asset.asset_id} 无有效 URL")

        # 深度图URL（可选，如果没有则只用参考图）
        depth_url = ""
        if depth_asset:
            depth_url = next((u for u in (depth_asset.urls or []) if u), "")

        if not depth_url:
            logger.warning(
                f"[TemplateClean] 未找到原始深度图 | template_id={template_id} | "
                f"清场工作流将只使用参考构图图，Inpaint效果可能受限"
            )

        logger.info(
            f"[TemplateClean] 开始 | template_id={template_id} | "
            f"ref={ref_asset.asset_id} depth={'有' if depth_url else '无'}"
        )

        start_time = time.time()
        created_assets: List[AssetRef] = []

        try:
            from services.providers.comfyui_provider import ComfyUIProvider

            provider = ComfyUIProvider()

            # 构建参考图列表
            reference_images = [{"url": ref_url, "role": "reference", "type": "reference"}]
            if depth_url:
                reference_images.append({"url": depth_url, "role": "depth", "type": "depth"})

            # 调用模板清场+蒙版工作流
            result = await provider.generate_image(
                prompt="模板清场+蒙版生成",
                reference_images=reference_images,
                template="template_clean",
                filename_prefix=safe_filename_prefix(template_id),
            )

            if not result or not result.filenames:
                return self._error_result("模板清场+蒙版生成未返回结果")

            filenames = result.filenames or []
            all_urls = result.images or []

            # 诊断：打印所有输出文件名，便于排查 mask_final 缺失问题
            logger.info(
                f"[TemplateClean] ComfyUI 输出 | "
                f"filenames={filenames} | urls_count={len(all_urls)}"
            )
            for i, fn in enumerate(filenames):
                url = all_urls[i] if i < len(all_urls) else "N/A"
                logger.info(f"[TemplateClean] 输出文件[{i}] | {fn} → {url}")

            # 简化工作流只输出 mask_raw，直接用固定前缀匹配
            mask_url = None
            for i, fn in enumerate(filenames):
                url = all_urls[i] if i < len(all_urls) else ""
                if not url:
                    continue
                fname_lower = fn.lower()
                if "mask_raw" in fname_lower:
                    mask_url = url
                    break

            if not mask_url:
                return self._error_result("ComfyUI 未输出蒙版文件")

            # 注册 mask 资产
            mask_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="mask",
                name=f"{template_name or template_id} 蒙版",
                urls=[mask_url],
                input_assets=[ref_asset],
                extra_metadata={
                    "source_asset_id": ref_asset.asset_id,
                    "extraction_type": "template_clean",
                    "template_id": template_id,
                },
                content_type="template_production",
            )
            if mask_asset:
                created_assets.append(mask_asset)
                logger.info(
                    f"[TemplateClean] 创建资产 | type=mask "
                    f"name={mask_asset.name} id={mask_asset.asset_id}"
                )

            # 复制到模板目录并重命名 + 蒙版后处理
            self._copy_to_template_dir(filenames, template_id)

            # depth_clean 由后端 OpenCV inpaint 生成（从 depth_raw + mask 内插）
            depth_clean_path = str(TEMPLATE_DIR / f"{template_id}_depth_clean.png")
            self._postprocess_depth_clean(depth_clean_path, depth_clean_path, "", template_id)
            if os.path.exists(depth_clean_path):
                # 同时也复制到 ComfyUI output 目录，方便通过 /api/comfyui/image 访问
                from services.workflow_builder import _COMFYUI_OUTPUT_DIR

                depth_clean_filename = f"{safe_filename_prefix(template_id)}_depth_clean.png"
                comfyui_dst = os.path.join(_COMFYUI_OUTPUT_DIR, depth_clean_filename)
                shutil.copy2(depth_clean_path, comfyui_dst)
                depth_clean_url = f"/api/comfyui/image?filename={depth_clean_filename}"
                depth_asset = await self._register_asset_direct(
                    asset_svc,
                    asset_type="depth_clean",
                    name=f"{template_name or template_id} 清场深度图",
                    urls=[depth_clean_url],
                    input_assets=[ref_asset],
                    extra_metadata={
                        "source_asset_id": ref_asset.asset_id,
                        "extraction_type": "template_clean",
                        "template_id": template_id,
                        "method": "opencv_inpaint_ns",
                    },
                    content_type="template_production",
                )
                if depth_asset:
                    created_assets.append(depth_asset)
                    logger.info(
                        f"[TemplateClean] 创建资产 | type=depth_clean "
                        f"name={depth_asset.name} id={depth_asset.asset_id}"
                    )
            else:
                logger.warning("[TemplateClean] depth_clean 未生成，跳过资产注册")

            # 更新 manifest
            await self._update_manifest(template_id=template_id)

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                f"[TemplateClean] 完成 | template_id={template_id} | "
                f"创建 {len(created_assets)} 个资产 | {elapsed_ms}ms"
            )

            # 返回第一个创建的资产（通常是 depth_clean），而非输入资产
            return AssetProduceResult(
                asset=created_assets[0],
                success=True,
                elapsed_ms=elapsed_ms,
            )

        except Exception as e:
            logger.error(f"[TemplateClean] 失败: {e}", exc_info=True)
            return self._error_result(str(e))

    def _copy_to_template_dir(self, filenames: List[str], template_id: str):
        """将清场深度图和蒙版复制到模板目录并按规范重命名

        depth_clean 后处理：Qwen Image Edit 输出可能带色彩或纹理，
        真正的深度清场图应是灰度渐变图，因此需要：
        1. 转灰度
        2. 用原始深度图的非人物区域像素替换（保持原始深度值不变）
        3. 对 Inpaint 区域做高斯平滑（消除接缝和纹理）
        """
        from services.workflow_builder import _COMFYUI_OUTPUT_DIR
        from services.template_utils import (
            ensure_template_dir,
            remove_old_files,
            match_and_copy_files,
        )

        output_dir = _COMFYUI_OUTPUT_DIR
        if not output_dir or not os.path.isdir(output_dir):
            logger.warning(f"[TemplateClean] ComfyUI output 目录不存在: {output_dir}")
            return

        ensure_template_dir()

        # 幂等性：清除上次生成的下游文件
        remove_old_files(
            template_id,
            ["depth_clean.png", "mask.png"],
            tag="TemplateClean",
        )

        # 复制 mask_raw（depth_clean 由后处理生成，不从 ComfyUI 直接复制）
        rename_map = {"mask_raw": f"{template_id}_mask_raw.png"}
        copied = match_and_copy_files(
            filenames=filenames,
            output_dir=output_dir,
            rename_map=rename_map,
            tag="TemplateClean",
        )

        # 后处理：
        # 1. mask_raw → mask：收缩1px + 羽化3px（原 ComfyUI GrowMask/FeatherMask 已移至后端）
        # 2. depth_clean：灰度化 + 原始深度图混合 + 边缘平滑
        # 注意：mask 后处理必须在 depth_clean 之前，因为 depth_clean 后处理需要读取 mask
        if "mask_raw" in copied:
            self._postprocess_mask(copied["mask_raw"], template_id)
        # depth_clean 的后处理在 _postprocess_depth_clean 中完成
        # 需要从 ComfyUI 输出中找 depth_clean 原始文件
        for fn in filenames:
            fname_lower = fn.lower()
            if "depth_clean" in fname_lower:
                src_path = os.path.join(output_dir, fn)
                if os.path.exists(src_path):
                    self._postprocess_depth_clean(src_path, src_path, output_dir, template_id)
                break

    def _postprocess_mask(self, mask_raw_path: str, template_id: str):
        """蒙版后处理：收缩1px + 羽化3px

        原 ComfyUI 工作流中的 GrowMask(-1) + FeatherMask(3) 已移至后端 PIL，
        避免 ComfyUI 插件兼容性问题（FeatherMask 属于 Impact Pack，可能未安装）。

        输入：mask_raw（SAM2 原始分割蒙版，白色=人物）
        输出：{template_id}_mask.png（收缩+羽化后的蒙版）
        """
        try:
            mask_img = Image.open(mask_raw_path).convert("L")
            mask_arr = np.array(mask_img, dtype=np.float32)

            logger.info(
                f"[TemplateClean] 蒙版后处理输入 | path={mask_raw_path} "
                f"size={mask_img.size} white_pixels={int((mask_arr > 127).sum())}"
            )

            # 1. 收缩 1px（腐蚀）：去掉人物边缘可能残留的深度噪声
            # MinFilter(size=3) 等效于 1px 腐蚀
            eroded = mask_img.filter(ImageFilter.MinFilter(size=3))
            eroded_arr = np.array(eroded, dtype=np.float32)

            # 2. 羽化 3px：高斯模糊让蒙版边缘平滑过渡，避免 Inpaint 后硬边
            feathered = eroded.filter(ImageFilter.GaussianBlur(radius=3))
            feathered_arr = np.array(feathered, dtype=np.float32)

            # 3. 保存为最终蒙版
            mask_output_path = str(TEMPLATE_DIR / f"{template_id}_mask.png")
            feathered.save(mask_output_path)

            logger.info(
                f"[TemplateClean] 蒙版后处理完成 | {mask_output_path} "
                f"size={feathered.size} "
                f"white_pixels(raw)={int((mask_arr > 127).sum())} → "
                f"white_pixels(eroded)={int((eroded_arr > 127).sum())} → "
                f"white_pixels(feathered)={int((feathered_arr > 127).sum())}"
            )

        except Exception as e:
            logger.error(f"[TemplateClean] 蒙版后处理失败: {e}", exc_info=True)

    def _postprocess_depth_clean(
        self,
        input_path: str,
        output_path: str,
        comfyui_output_dir: str,
        template_id: str,
    ):
        """深度清场图后处理：用 OpenCV 深度值内插取代 Qwen Inpaint

        Qwen Image Edit 是通用图像编辑模型，不理解深度图的灰度渐变语义，
        填充的人像区域是黑色空洞/彩色纹理，不是期望的深度渐变图。

        正确做法：直接用 OpenCV inpaint 从原始深度图的周围像素内插，
        填充人像区域，得到自然的深度渐变。
        """
        try:
            depth_raw_path = str(TEMPLATE_DIR / f"{template_id}_depth_raw.png")
            mask_path = str(TEMPLATE_DIR / f"{template_id}_mask.png")

            logger.info(
                f"[TemplateClean] 深度图清场 | depth_raw={depth_raw_path} "
                f"exists={os.path.exists(depth_raw_path)} | "
                f"mask={mask_path} exists={os.path.exists(mask_path)}"
            )

            if not os.path.exists(depth_raw_path):
                logger.error("[TemplateClean] 原始深度图不存在，跳过")
                return

            if not os.path.exists(mask_path):
                logger.warning("[TemplateClean] 蒙版不存在，使用未清场的原始深度图")
                # 直接复制原始深度图作为输出
                shutil.copy2(depth_raw_path, output_path)
                return

            # 用 PIL 读取（支持中文路径），然后转 numpy 给 OpenCV 处理
            img_depth = Image.open(depth_raw_path).convert("L")
            img_mask = Image.open(mask_path).convert("L")

            # 统一尺寸
            if img_mask.size != img_depth.size:
                img_mask = img_mask.resize(img_depth.size, Image.NEAREST)

            depth_raw = np.array(img_depth, dtype=np.uint8)
            mask = np.array(img_mask, dtype=np.uint8)

            # 二值化蒙版
            _, mask_binary = cv2.threshold(mask, 30, 255, cv2.THRESH_BINARY)

            person_pixels = int((mask_binary > 0).sum())
            total_pixels = mask_binary.size
            logger.info(
                f"[TemplateClean] 深度图清场 | size={depth_raw.shape} "
                f"person_pixels={person_pixels}/{total_pixels} "
                f"({100*person_pixels//total_pixels}%)"
            )

            # OpenCV 深度值内插（仅内存计算，不涉及文件IO，避免中文路径问题）
            inpaint_radius = 3
            result = cv2.inpaint(depth_raw, mask_binary, inpaint_radius, cv2.INPAINT_NS)

            # 人物区域深度梯度模拟：填充后的区域是平滑平面，模拟轻微立体感
            # 从蒙版中心到边缘递减，最大深度差 10 个灰度值
            ys, xs = np.where(mask_binary > 0)
            if len(ys) > 100:  # 足够大的区域才做梯度
                center_y, center_x = ys.mean(), xs.mean()
                distances = np.sqrt((xs - center_x) ** 2 + (ys - center_y) ** 2)
                max_dist = distances.max()
                if max_dist > 0:
                    gradient = np.zeros_like(result, dtype=np.float32)
                    gradient[ys, xs] = (1 - distances / max_dist) * 10
                    result = np.clip(result.astype(np.float32) + gradient, 0, 255).astype(np.uint8)
                    logger.info(
                        f"[TemplateClean] 深度梯度模拟 | center=({center_x:.0f},{center_y:.0f}) "
                        f"max_dist={max_dist:.0f}px"
                    )

            # 用 PIL 保存（支持中文路径）
            Image.fromarray(result, mode="L").save(output_path)

            if os.path.exists(output_path):
                out_size = os.path.getsize(output_path)
                logger.info(
                    f"[TemplateClean] 深度图清场完成 | {output_path} "
                    f"{out_size} bytes | method=INPAINT_NS radius={inpaint_radius}"
                )
            else:
                logger.error(f"[TemplateClean] 输出保存失败: {output_path}")

        except Exception as e:
            logger.error(f"[TemplateClean] 深度图清场失败: {e}", exc_info=True)

    async def _update_manifest(self, template_id: str):
        """更新 manifest 中的清场深度图和蒙版状态（原子写入，异步互斥锁保护）"""
        from services.template_utils import atomic_manifest_update, TEMPLATE_DIR

        def _do_update(manifest):
            templates = manifest.get("templates", [])

            depth_clean_exists = (TEMPLATE_DIR / f"{template_id}_depth_clean.png").exists()
            mask_exists = (TEMPLATE_DIR / f"{template_id}_mask.png").exists()

            for tmpl in templates:
                if tmpl.get("id") == template_id:
                    if "files" not in tmpl:
                        tmpl["files"] = {}
                    for key in ["depth_clean", "mask"]:
                        tmpl["files"].pop(key, None)
                    if depth_clean_exists:
                        tmpl["files"]["depth_clean"] = f"{template_id}_depth_clean.png"
                    if mask_exists:
                        tmpl["files"]["mask"] = f"{template_id}_mask.png"
                    if depth_clean_exists or mask_exists:
                        tmpl["status"] = "cleaned"
                    break

            manifest["templates"] = templates
            return manifest

        success = await atomic_manifest_update(_do_update)
        if success:
            logger.info(f"[TemplateClean] manifest 已更新 | template_id={template_id}")
        else:
            logger.error(f"[TemplateClean] manifest 更新失败 | template_id={template_id}")
