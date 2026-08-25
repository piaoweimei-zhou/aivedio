"""
ComfyUI 服务 — 分镜批量生成/中间产物 Mixin（从 comfyui_storyboard.py 拆分，P2 治理）

被 ComfyUIStoryboardMixin 继承（MRO），含 batch 生成与断点续跑。
"""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.comfyui_helpers import (
    ComfyUIGenResult,
    StoryboardStepResult,
    _collect_all_reference_urls,
)

logger = logging.getLogger(__name__)


class ComfyUIStoryboardBatchMixin:
    async def batch_generate_storyboard(
        self,
        project_id: str,
        shots: List[Dict[str, Any]],
        reference_images: Dict[str, str],
        reference_items: List[Dict[str, Any]],
        character_count: int = 1,
        preset_name: Optional[str] = None,
        seed: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
        **kwargs,
    ) -> List[ComfyUIGenResult]:
        """批量生成多个分镜帧

        ⭐ V5.0: Fish 融合 1 步直出，先统一预分析参考图，再逐帧生成。

        Args:
            project_id: 项目ID
            shots: 分镜列表，每个元素 {"name": str, "prompt": str, "character_ids": [...], ...}
            reference_images: 共享参考图字典
            reference_items: 参考图条目列表
            character_count: 角色数量
            preset_name: 预设名称（保留接口兼容，V5.0 不使用）
            seed: 随机种子
            progress_callback: 全局进度回调 (msg, pct) → None

        Returns:
            每帧的生成结果列表
        """
        total_shots = len(shots)
        results: List[ComfyUIGenResult] = []

        logger.info(
            f"[StoryboardBatch] 批量生成开始 | shots={total_shots}"
            f" | chars={character_count}, preset={preset_name}"
        )

        # ═══════════════════════════════════════════════════════════════
        # ⭐ Phase 0: 统一预分析所有参考图（一次 Vision，N 帧复用）
        # ═══════════════════════════════════════════════════════════════
        # 合并共享参考图 + 各分镜独立参考图（去重）
        unique_refs = _collect_all_reference_urls(
            reference_items=reference_items,
            shots=shots,
        )

        if progress_callback:
            try:
                progress_callback(
                    f"🔍 批量预分析: {len(unique_refs)} 张参考图 (缓存优先)...",
                    0,
                )
            except Exception:
                pass

        # 统一预分析（自动缓存 + 崩溃恢复）
        # 注意：此调用会停止 ComfyUI（如运行中），分析后不自动重启
        # storyboard_generation_v2 会在 Phase 2 自动启动 ComfyUI
        await self._pre_analyze_references(
            unique_refs,
            project_id=project_id,
            progress_callback=(
                lambda msg, pct: (
                    progress_callback(f"🔍 {msg}", max(0, min(5, int(pct * 0.05))))
                    if progress_callback
                    else None
                )
            ),
        )

        # 将分析结果同步回原始 reference_items（供 storyboard_generation_v2 复用）
        # unique_refs 和 reference_items 可能指向不同 dict 对象，需要同步 visual_desc
        _sync_map: Dict[str, str] = {}
        for ref in unique_refs:
            url = ref.get("image_url") or ref.get("url", "") or ""
            vd = ref.get("visual_desc", "")
            if url and vd:
                _sync_map[url] = vd

        for ref in reference_items:
            url = ref.get("image_url") or ref.get("url", "") or ""
            if url and url in _sync_map and not ref.get("visual_desc"):
                ref["visual_desc"] = _sync_map[url]

        cached_count = sum(1 for r in unique_refs if r.get("visual_desc"))
        if progress_callback:
            try:
                progress_callback(
                    f"✅ 参考图预分析完成 ({cached_count}/{len(unique_refs)})，开始批量生成...",
                    5,
                )
            except Exception:
                pass

        logger.info(
            f"[StoryboardBatch] 预分析完成 | analyzed={cached_count}/{len(unique_refs)}"
            f" | cache_synced={len(_sync_map)}"
        )

        # ═══════════════════════════════════════════════════════════════
        # Phase 1-N: 逐帧生成（复用预分析的 visual_desc）
        # ═══════════════════════════════════════════════════════════════
        for shot_idx, shot in enumerate(shots):
            shot_name = shot.get("name", f"分镜{shot_idx+1}")
            shot_prompt = shot.get("prompt", "")

            if progress_callback:
                batch_pct = int((shot_idx / total_shots) * 100)
                progress_callback(
                    f"🎬 批量生成 ({shot_idx+1}/{total_shots}): {shot_name}", batch_pct
                )

            # ⭐ 每帧生成前标记活跃状态
            self._mark_generation_active()

            shot_kwargs = {
                "project_id": project_id,
                "prompt_text": shot_prompt,
                "reference_images": reference_images,
                "reference_items": reference_items,
                "character_count": character_count,
                "seed": seed + shot_idx if seed else None,
            }
            # ⭐ V6.0: 传递模板参数
            if shot.get("template"):
                shot_kwargs["template"] = shot["template"]
            if shot.get("per_frame_prompts"):
                shot_kwargs["per_frame_prompts"] = shot["per_frame_prompts"]
            if shot.get("pose_reference_image"):
                shot_kwargs["pose_reference_image"] = shot["pose_reference_image"]

            result = await self.storyboard_generation_v2(
                **shot_kwargs,
                progress_callback=(
                    (
                        lambda msg, pct: progress_callback(
                            f"({shot_idx+1}/{total_shots}) {msg}",
                            int((shot_idx * 100 + pct) / total_shots),
                        )
                    )
                    if progress_callback
                    else None
                ),
                **kwargs,
            )
            results.append(result)
            logger.info(f"[StoryboardBatch] {shot_name} 完成 | file={result.filename}")

        if progress_callback:
            progress_callback(f"✅ 批量生成完成 ({total_shots} 帧)", 100)

        logger.info(f"[StoryboardBatch] 批量生成完成 | total_shots={total_shots}")
        return results

    def _get_intermediates_dir(self, project_id: str, trace_id: str) -> Path:
        """获取中间结果保存目录（数据统一收敛到 backend/data，勿写项目根 data）"""
        intermediates_dir = (
            Path(__file__).parent.parent
            / "data"
            / "storyboard_intermediates"
            / project_id[-8:]
            / trace_id
        )
        intermediates_dir.mkdir(parents=True, exist_ok=True)
        return intermediates_dir

    async def _save_step_intermediate(
        self,
        project_id: str,
        trace_id: str,
        step_index: int,
        step_name: str,
        image_filename: str,
        metadata: Dict[str, Any],
    ) -> str:
        """持久化单步中间结果到磁盘

        Args:
            project_id: 项目ID
            trace_id: 本次生成追踪ID
            step_index: 步骤序号
            step_name: 步骤显示名
            image_filename: ComfyUI 输出的图片文件名
            metadata: 步骤元数据 (denoise, cfg, elapsed_ms 等)

        Returns:
            保存的中间文件路径
        """
        intermediates_dir = self._get_intermediates_dir(project_id, trace_id)

        # 保存元数据 JSON
        meta_path = intermediates_dir / f"step{step_index:02d}_{step_name}.json"
        meta_data = {
            "step_index": step_index,
            "step_name": step_name,
            "image_filename": image_filename,
            "timestamp": time.time(),
            **metadata,
        }
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta_data, f, ensure_ascii=False, indent=2)

        # 拷贝图片文件（如果存在）
        comfyui_input = (
            Path(self.config.output_dir)
            if hasattr(self.config, "output_dir")
            else Path("comfyui/output")
        )
        src_path = (
            comfyui_input / image_filename
            if not Path(image_filename).is_absolute()
            else Path(image_filename)
        )
        dst_path = intermediates_dir / f"step{step_index:02d}_{step_name}.png"
        if src_path.exists():
            shutil.copy2(src_path, dst_path)
            logger.debug(f"[Intermediates] 已保存: {dst_path}")
        else:
            logger.warning(f"[Intermediates] 源文件不存在: {src_path}")

        return str(dst_path)

    async def _resume_from_checkpoint(
        self,
        project_id: str,
        trace_id: str,
        total_steps: int,
    ) -> Tuple[Optional[str], List[StoryboardStepResult], int]:
        """从断点恢复生成流程

        检查中间目录中已完成的步骤，返回最后完成的图片和结果列表。

        Args:
            project_id: 项目ID
            trace_id: 本次生成追踪ID
            total_steps: 总步骤数

        Returns:
            (last_image: Optional[str], completed_results: List[StoryboardStepResult], resume_from: int)  # noqa: E501
            resume_from = 0 表示从头开始（无检查点）
        """
        intermediates_dir = self._get_intermediates_dir(project_id, trace_id)
        if not intermediates_dir.exists():
            return None, [], 0

        # 扫描已完成的步骤
        completed_steps: List[StoryboardStepResult] = []
        last_image = None
        max_completed = 0

        for step_idx in range(1, total_steps + 1):
            meta_files = list(intermediates_dir.glob(f"step{step_idx:02d}_*.json"))
            if meta_files:
                with open(meta_files[0], "r", encoding="utf-8") as f:
                    meta = json.load(f)
                completed_steps.append(
                    StoryboardStepResult(
                        step_index=meta["step_index"],
                        step_name=meta["step_name"],
                        filename=meta["image_filename"],
                        elapsed_ms=meta.get("elapsed_ms", 0),
                    )
                )
                last_image = meta["image_filename"]
                max_completed = max(max_completed, step_idx)

        if max_completed > 0:
            logger.info(
                f"[Resume] [{trace_id}] 发现检查点: {max_completed}/{total_steps} 步已完成"
                f" | last_image={last_image}"
            )

        return last_image, completed_steps, max_completed
