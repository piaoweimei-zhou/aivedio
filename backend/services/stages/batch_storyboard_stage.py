"""
CSV批量分镜生成阶段

从CSV分镜脚本逐行读取，批量调用多人分镜/分层渲染工作流。
CSV格式：
  镜头号,模板编号,人物A,人物B,人物C,人物D,区域A提示词,区域B提示词,全局提示词

输入：CSV文件资产 + 项目中所有人物资产（作为查找池）
输出：storyboard_batch 类型资产（批量生成结果）
"""

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin, collect_content_type

logger = logging.getLogger(__name__)

# CSV列映射：列索引 → 工作流参数
CSV_COLUMNS = [
    "shot_id",       # 镜头号
    "template",      # 模板编号（如T01_双人正面对话）
    "char_a",        # 人物A资产名
    "char_b",        # 人物B资产名
    "char_c",        # 人物C资产名（可选，用于3人/分层）
    "char_d",        # 人物D资产名（可选，用于4人/分层）
    "prompt_a",      # 区域A提示词
    "prompt_b",      # 区域B提示词
    "prompt_global", # 全局提示词
    "prompt_id",     # 提示词库 ID（可选，引用提示词中心，自动解析为 prompt_global）
    "prompt_vars",   # 提示词变量 JSON（可选，如 {"character":"小明","action":"走路"}）
]


@dataclass
class BatchResult:
    """批量生成结果容器 — 兼容 _register_asset 的 ProviderResult 接口"""
    urls: List[str]
    elapsed_ms: int
    outputs: List = field(default_factory=list)
    # 兼容 _register_asset 所需的属性
    images: List[str] = field(default_factory=list)
    image_url: str = ""
    provider_id: str = "comfyui"
    seed: int = 0


class BatchStoryboardStage(StagePlugin):
    """CSV批量分镜生成阶段"""

    stage_def = StageDef(
        stage_id="batch_storyboard",
        name="CSV批量分镜",
        input_types=["csv", "concept", "multi_view", "storyboard", "storyboard_multi"],
        input_content_types=[],
        output_type="storyboard_batch",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从CSV分镜脚本批量生成多人分镜，支持断点续传",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        # 解析CSV数据（CSV批量模式不需要输入资产，数据来自 params）
        csv_data = params.get("csv_data", "")
        csv_rows = params.get("csv_rows", [])

        if not csv_rows and csv_data:
            csv_rows = self._parse_csv(csv_data)

        if not csv_rows:
            return self._error_result("CSV数据为空，请提供有效的分镜脚本")

        # 获取起始行（断点续传）
        start_row = params.get("start_row", 0)
        total_rows = len(csv_rows)

        logger.info(
            f"[BatchStoryboardStage] CSV批量生成 | total={total_rows} | "
            f"start={start_row} | provider={provider_id}"
        )

        # ⭐ 构建人物资产名 → {asset_id, urls} 映射
        # 从 input_assets 中构建，也通过 asset_svc 获取项目中所有可用资产
        asset_name_map: Dict[str, Dict[str, Any]] = {}
        for asset in input_assets:
            if asset.name:
                asset_name_map[asset.name] = {
                    "asset_id": asset.asset_id,
                    "urls": asset.urls or [],
                    "asset_type": asset.asset_type,
                }

        # 补充：通过 asset_svc 获取所有资产作为查找池
        # ⭐ 注意：list_assets 不接受 project_id，直接获取全部资产
        try:
            all_assets = asset_svc.list_assets()
            for a in all_assets:
                if a.name and a.name not in asset_name_map:
                    asset_name_map[a.name] = {
                        "asset_id": a.asset_id,
                        "urls": a.urls or [],
                        "asset_type": a.asset_type,
                    }
        except Exception as e:
            logger.warning(f"[BatchStoryboardStage] 获取资产列表失败: {e}，仅使用输入资产")

        # 逐行执行
        results = []
        errors = []
        import time
        start_time = time.time()

        for i in range(start_row, total_rows):
            row = csv_rows[i]
            shot_id = row.get("shot_id", str(i + 1))
            template = row.get("template", "multi_person")
            prompt_global = row.get("prompt_global", "")
            prompt_a = row.get("prompt_a", "")
            prompt_b = row.get("prompt_b", "")

            # ⭐ 提示词中心集成：若 CSV 行含 prompt_id，从提示词库解析
            # 用法：CSV 新增 prompt_id 和 prompt_vars 列
            #   prompt_id: 引用提示词库的 ID
            #   prompt_vars: 变量 JSON，如 {"character":"小明","action":"走路"}
            # 解析结果注入 prompt_global（若未显式提供）
            prompt_id_in_row = row.get("prompt_id", "")
            if prompt_id_in_row and not prompt_global:
                try:
                    from services.prompt_service import get_prompt_service
                    prompt_svc = get_prompt_service()
                    vars_json = row.get("prompt_vars", "")
                    row_vars = {}
                    if vars_json:
                        try:
                            row_vars = json.loads(vars_json) if isinstance(vars_json, str) else vars_json
                        except Exception:
                            logger.warning(f"[BatchStoryboardStage] 镜头{shot_id}: prompt_vars JSON 解析失败")
                    result = prompt_svc.resolve(prompt_id_in_row, row_vars)
                    if result:
                        prompt_global = result[0]
                        logger.info(f"[BatchStoryboardStage] 镜头{shot_id}: prompt_id 解析成功")
                except Exception as e:
                    logger.warning(f"[BatchStoryboardStage] 镜头{shot_id}: prompt_id 解析失败: {e}")

            # ⭐ 解析人物资产，构建 reference_images 列表
            reference_images = []
            char_keys = ["char_a", "char_b", "char_c", "char_d"]
            char_type_map = {
                "char_a": "character",
                "char_b": "character2",
                "char_c": "character3",
                "char_d": "character4",
            }

            for key in char_keys:
                name = row.get(key, "")
                if not name:
                    continue
                asset_info = asset_name_map.get(name)
                if not asset_info:
                    logger.warning(f"[BatchStoryboardStage] 镜头{shot_id}: 未找到人物资产 '{name}'")
                    continue
                urls = asset_info.get("urls", [])
                if urls:
                    ref_type = char_type_map[key]
                    reference_images.append({
                        "url": urls[0],
                        "role": ref_type,
                        "type": ref_type,
                        "name": name,
                    })

            if len(reference_images) < 1:
                errors.append({"shot_id": shot_id, "error": "缺少人物资产或图片URL"})
                continue

            # 决定使用哪个工作流模板
            if template.startswith("T09") or template.startswith("T10") or len(reference_images) >= 4:
                wf_template = "layered_render"
            else:
                wf_template = "multi_person"

            try:
                result = await provider_svc.generate_image(
                    provider_id=provider_id,
                    prompt=prompt_global or prompt_a,
                    size=params.get("size", "1024x1024"),
                    model=params.get("model", ""),
                    reference_images=reference_images,
                    template=wf_template,
                    template_name=template,
                    prompt_a=prompt_a,
                    prompt_b=prompt_b,
                )
                results.append({
                    "shot_id": shot_id,
                    "template": template,
                    "success": True,
                    "output": result.images if hasattr(result, "images") else [],
                })
                logger.info(f"[BatchStoryboardStage] 镜头{shot_id} 完成")
            except Exception as e:
                errors.append({"shot_id": shot_id, "error": str(e)})
                logger.error(f"[BatchStoryboardStage] 镜头{shot_id} 失败: {e}")

        elapsed_ms = int((time.time() - start_time) * 1000)
        success_count = len(results)
        fail_count = len(errors)

        logger.info(
            f"[BatchStoryboardStage] 批量生成完成 | total={total_rows} | "
            f"success={success_count} | fail={fail_count} | elapsed={elapsed_ms}ms"
        )

        # 注册批量结果资产
        parent_ids = [a.asset_id for a in input_assets]
        parent_id = parent_ids[0] if len(parent_ids) == 1 else ""

        content_type = collect_content_type(input_assets)

        batch_result = BatchResult(urls=[], elapsed_ms=elapsed_ms)

        new_asset = await self._register_asset(
            asset_svc, batch_result,
            asset_type="storyboard_batch",
            name=params.get("name", f"批量分镜({success_count}/{total_rows})"),
            parent_id=parent_id,
            extra_metadata={
                "source_asset_ids": parent_ids,
                "template": "batch_storyboard",
                "total_rows": total_rows,
                "success_count": success_count,
                "fail_count": fail_count,
                "results": results,
                "errors": errors,
                "elapsed_ms": elapsed_ms,
            },
            content_type=content_type,
        )

        return AssetProduceResult(
            asset=new_asset,
            success=fail_count == 0,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def _parse_csv(csv_text: str) -> List[Dict[str, str]]:
        """解析CSV文本为行字典列表"""
        rows = []
        reader = csv.DictReader(io.StringIO(csv_text))
        for row in reader:
            # 标准化列名（去除空格，支持中英文列名）
            normalized = {}
            for key, value in row.items():
                clean_key = key.strip().lower()
                # 映射中文列名
                cn_map = {
                    "镜头号": "shot_id", "镜头": "shot_id",
                    "模板编号": "template", "模板": "template",
                    "人物a": "char_a", "人物b": "char_b",
                    "人物c": "char_c", "人物d": "char_d",
                    "区域a提示词": "prompt_a", "区域b提示词": "prompt_b",
                    "全局提示词": "prompt_global",
                }
                mapped_key = cn_map.get(clean_key, clean_key)
                normalized[mapped_key] = (value or "").strip()
            rows.append(normalized)
        return rows
