"""
分屏合成阶段

将多个视频/图片素材按布局合成单个视频：
  - horizontal: 左右分屏（手动 vs 工具 对比型）
  - vertical:   上下分屏
  - grid:       网格分屏（2x2 / 3x3 等）
  - split_compare: 特殊左右分屏 + 中间画 × 和 ✓ 标识

支持：
  - 视频与视频合成（必须等时长，或自动拉伸到最长时长）
  - 图片与图片合成（输出为单图）
  - 混合：图片转视频后与视频合成

输出：video 或 image 资产
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.stage_service import StageDef, StagePlugin
from services.stages.ffmpeg_utils import _ffmpeg_bin

logger = logging.getLogger(__name__)


class ComposeStage(StagePlugin):
    """分屏合成阶段"""

    stage_def = StageDef(
        stage_id="compose",
        name="分屏合成",
        input_types=["video", "image"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="多素材分屏合成（左右/上下/网格/对比模式，支持视频和图片）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        start = time.time()
        asset_svc = get_asset_service()

        err = self._require_input(input_assets, min_count=2)
        if err:
            return self._error_result(err)

        layout = params.get("layout", "horizontal")  # horizontal/vertical/grid/split_compare
        columns = int(params.get("columns", 2))  # grid 列数
        gap = int(params.get("gap", 0))  # 间隔像素
        labels = params.get("labels", []) or []  # 每路素材标签
        output_name = params.get("name", "分屏合成")
        target_size = params.get("size", "")  # "1920x1080" 空则用第一路尺寸
        target_duration = float(params.get("duration", 0))  # 0=最长素材时长
        bg_color = params.get("bg_color", "black")

        # 提取所有输入 URL（视频优先，图片次之）
        urls: List[str] = []
        asset_types: List[str] = []  # 标记每路是 video/image
        for asset in input_assets:
            for url in asset.urls:
                if url:
                    urls.append(url)
                    # 优先看 metadata.video_url，否则看 asset_type
                    if asset.metadata.get("video_url") or asset.asset_type == "video":
                        asset_types.append("video")
                    else:
                        asset_types.append("image")
                    break  # 每个资产只取第一个 URL

        if len(urls) < 2:
            return self._error_result(f"分屏合成至少需要 2 个有效素材 URL，实际 {len(urls)}")

        logger.info(
            f"[ComposeStage] layout={layout} | urls={len(urls)} | "
            f"types={asset_types} | labels={labels}"
        )

        try:
            from services.providers.provider_utils import (
                output_path_for, output_url_for, output_file_from_url
            )

            ffmpeg = _ffmpeg_bin()
            await self._check_ffmpeg(ffmpeg)

            # ── 下载远程 URL 到本地（ffmpeg 需要本地文件） ────────
            local_files = await self._download_urls(urls)
            if len(local_files) < 2:
                return self._error_result("下载素材失败，至少需要 2 个本地文件")

            # ── 判断输出是视频还是图片 ──────────────────────────
            has_video = any(t == "video" for t in asset_types[:len(local_files)])
            output_type = "video" if has_video else "image"
            ext = ".mp4" if output_type == "video" else ".png"

            output_file = output_path_for(
                f"compose_{uuid.uuid4().hex[:8]}_{int(time.time())}{ext}",
                "output",
            )

            # ── 构建 ffmpeg filter_complex ──────────────────────
            filter_complex = await self._build_filter(
                local_files=local_files,
                asset_types=asset_types,
                layout=layout,
                columns=columns,
                gap=gap,
                labels=labels,
                target_size=target_size,
                target_duration=target_duration,
                bg_color=bg_color,
                output_type=output_type,
            )

            # ── 执行 ffmpeg ────────────────────────────────────
            args: List[str] = [ffmpeg, "-y"]
            for f in local_files:
                # -i 每个输入文件
                if f.startswith("http"):
                    args += ["-i", f]
                else:
                    args += ["-i", f]

            if output_type == "video":
                args += [
                    "-filter_complex", filter_complex,
                    "-map", "[out]",
                    "-c:v", "libx264",
                    "-preset", "medium",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    output_file,
                ]
            else:
                args += [
                    "-filter_complex", filter_complex,
                    "-map", "[out]",
                    "-frames:v", "1",
                    output_file,
                ]

            logger.info(f"[ComposeStage] ffmpeg 命令: {' '.join(args[:8])}... (total {len(args)} args)")

            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")[:800]
                raise RuntimeError(f"ffmpeg 合成失败 (code={proc.returncode}): {err_msg}")

            if not os.path.exists(output_file):
                raise RuntimeError(f"ffmpeg 输出文件未生成: {output_file}")

            result_url = output_url_for(os.path.basename(output_file), "output")
            elapsed = int((time.time() - start) * 1000)

            source_ids = [a.asset_id for a in input_assets[:len(local_files)]]
            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type=output_type,
                name=output_name,
                urls=[result_url],
                input_assets=input_assets,
                extra_metadata={
                    "mode": "compose",
                    "layout": layout,
                    "columns": columns,
                    "gap": gap,
                    "labels": labels,
                    "source_asset_ids": source_ids,
                    "video_url": result_url if output_type == "video" else "",
                    "source_types": asset_types[:len(local_files)],
                    "elapsed_ms": elapsed,
                },
                content_type="",
            )

            logger.info(
                f"[ComposeStage] 合成完成 | id={new_asset.asset_id} | "
                f"type={output_type} | elapsed={elapsed}ms"
            )
            return AssetProduceResult(
                asset=new_asset,
                success=True,
                elapsed_ms=elapsed,
            )

        except Exception as e:
            logger.error(f"[ComposeStage] 合成失败: {e}")
            return self._error_result(str(e))

    # ──────────────────────────────────────────────────────────
    # filter_complex 构建
    # ──────────────────────────────────────────────────────────
    async def _build_filter(
        self,
        local_files: List[str],
        asset_types: List[str],
        layout: str,
        columns: int,
        gap: int,
        labels: List[str],
        target_size: str,
        target_duration: float,
        bg_color: str,
        output_type: str,
    ) -> str:
        """构建 ffmpeg filter_complex 字符串

        核心思路：
        1. 每路素材先 scale 到统一尺寸（target_size 或自动推导）
        2. 视频素材统一时长（pad/loop）
        3. 按 layout 用 hstack/vstack/grid 合成
        4. 应用 labels 文字（drawtext）
        """
        n = len(local_files)

        # ── 推导统一尺寸 ──────────────────────────────────
        # 横向分屏：每路 = 总宽/N - gap*(N-1)/N，高度=总高
        # 竖向分屏：每路宽=总宽，每路高=总高/N - gap*(N-1)/N
        if target_size:
            tw, th = self._parse_size(target_size)
        else:
            # 默认 1920x1080
            tw, th = 1920, 1080

        parts: List[str] = []
        # ── 每路素材预处理：统一尺寸 + 统一时长 + 加标签 ────
        for i in range(n):
            # 图片转视频循环（如果整体输出是视频且该路是图片）
            if output_type == "video" and asset_types[i] == "image":
                # loop image to video, 5s default
                parts.append(
                    f"[{i}:v]loop=loop=750:size=1,trim=duration={target_duration or 5}"
                )
                # loop 后重标号
                last = parts[-1].split("[")[0]
                parts[-1] = f"[{i}:v]loop=loop=750:size=1,trim=duration={target_duration or 5},setsar=1[v{i}]"
            else:
                parts.append(f"[{i}:v]setsar=1[v{i}]")

        # ── 计算 tile 尺寸 ────────────────────────────────
        if layout == "horizontal":
            # 横向：每路宽=(tw-gap*(n-1))/n，高=th
            tile_w = max(100, (tw - gap * (n - 1)) // n)
            tile_h = th
            for i in range(n):
                parts.append(f"[v{i}]scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[s{i}]")
            # 加标签
            for i in range(n):
                label = labels[i] if i < len(labels) else ""
                if label:
                    parts.append(f"[s{i}]drawtext=text='{self._escape(label)}':x=10:y=10:fontcolor=white:fontsize=36:box=1:boxcolor=black@0.5[t{i}]")
                else:
                    parts.append(f"[s{i}]null[t{i}]")
            # hstack
            inputs = "".join(f"[t{i}]" for i in range(n))
            if gap > 0:
                parts.append(f"{inputs}hstack=inputs={n}[stacked]")
                # 在每路之间加间隔（用 pad 模拟）
                # 简化版：直接 hstack，gap=0 时使用
            else:
                parts.append(f"{inputs}hstack=inputs={n}[stacked]")
            # 拉伸到目标尺寸
            parts.append(f"[stacked]scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[out]")

        elif layout == "vertical":
            # 竖向：每路宽=tw，高=(th-gap*(n-1))/n
            tile_w = tw
            tile_h = max(100, (th - gap * (n - 1)) // n)
            for i in range(n):
                parts.append(f"[v{i}]scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[s{i}]")
            for i in range(n):
                label = labels[i] if i < len(labels) else ""
                if label:
                    parts.append(f"[s{i}]drawtext=text='{self._escape(label)}':x=10:y=10:fontcolor=white:fontsize=36:box=1:boxcolor=black@0.5[t{i}]")
                else:
                    parts.append(f"[s{i}]null[t{i}]")
            inputs = "".join(f"[t{i}]" for i in range(n))
            parts.append(f"{inputs}vstack=inputs={n}[stacked]")
            parts.append(f"[stacked]scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[out]")

        elif layout == "split_compare":
            # 对比模式：左右 2 路 + 中间 ×/✓
            if n != 2:
                raise ValueError(f"split_compare 仅支持 2 路素材，实际 {n}")
            tile_w = (tw - gap) // 2
            tile_h = th
            for i in range(2):
                label = labels[i] if i < len(labels) else ""
                # 左侧用红色"×"，右侧用绿色"✓"
                mark = "×" if i == 0 else "✓"
                mark_color = "red" if i == 0 else "green"
                # 缩放
                parts.append(f"[v{i}]scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[s{i}]")
                # 中央画大字 × 或 ✓
                parts.append(
                    f"[s{i}]drawtext=text='{mark}':x=(w-text_w)/2:y=(h-text_h)/2:fontcolor={mark_color}@0.8:fontsize=200:box=1:boxcolor=black@0.3[mk{i}]"
                )
                # 左上角小标签
                if label:
                    parts.append(f"[mk{i}]drawtext=text='{self._escape(label)}':x=20:y=20:fontcolor=white:fontsize=48:box=1:boxcolor=black@0.6[t{i}]")
                else:
                    parts.append(f"[mk{i}]null[t{i}]")
            parts.append(f"[t0][t1]hstack=inputs=2[out]")

        elif layout == "grid":
            # 网格：每路 = (tw-gap*(cols-1))/cols × (th-gap*(rows-1))/rows
            rows = (n + columns - 1) // columns
            tile_w = max(100, (tw - gap * (columns - 1)) // columns)
            tile_h = max(100, (th - gap * (rows - 1)) // rows)
            for i in range(n):
                parts.append(f"[v{i}]scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[s{i}]")
                label = labels[i] if i < len(labels) else ""
                if label:
                    parts.append(f"[s{i}]drawtext=text='{self._escape(label)}':x=10:y=10:fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5[t{i}]")
                else:
                    parts.append(f"[s{i}]null[t{i}]")
            # 按 row 拼接，再 vstack
            row_outputs = []
            for r in range(rows):
                row_indices = list(range(r * columns, min((r + 1) * columns, n)))
                if not row_indices:
                    continue
                inputs = "".join(f"[t{i}]" for i in row_indices)
                row_name = f"row{r}"
                row_outputs.append(row_name)
                parts.append(f"{inputs}hstack=inputs={len(row_indices)}[{row_name}]")
            if len(row_outputs) == 1:
                parts.append(f"[{row_outputs[0]}]scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[out]")
            else:
                inputs = "".join(f"[{r}]" for r in row_outputs)
                parts.append(f"{inputs}vstack=inputs={len(row_outputs)}[stacked]")
                parts.append(f"[stacked]scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2:color={bg_color}[out]")

        else:
            raise ValueError(f"不支持的 layout: {layout}")

        return ";".join(parts)

    # ──────────────────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────────────────
    def _parse_size(self, size: str) -> Tuple[int, int]:
        """解析 "1920x1080" → (1920, 1080)"""
        try:
            w, h = size.lower().split("x")
            return int(w), int(h)
        except Exception:
            return 1920, 1080

    def _escape(self, text: str) -> str:
        """转义 drawtext 文本中的特殊字符"""
        if not text:
            return ""
        # ffmpeg drawtext 转义规则：' : \ %
        text = text.replace("\\", "\\\\")
        text = text.replace(":", "\\:")
        text = text.replace("'", "\\'")
        text = text.replace("%", "\\%")
        return text

    async def _download_urls(self, urls: List[str]) -> List[str]:
        """下载远程 URL 到本地（http 开头的才下载，本地路径直接返回）"""
        from services.providers.provider_utils import output_file_from_url, output_path_for
        import httpx

        local_files = []
        for url in urls:
            if url.startswith(("http://", "https://")):
                # 下载到 temp 目录
                temp_path = output_path_for(f"temp_{uuid.uuid4().hex[:8]}.mp4", "temp")
                try:
                    async with httpx.AsyncClient(
                        timeout=httpx.Timeout(connect=20.0, read=300.0)
                    ) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        with open(temp_path, "wb") as f:
                            f.write(resp.content)
                    local_files.append(temp_path)
                except Exception as e:
                    logger.warning(f"[ComposeStage] 下载失败 {url}: {e}")
            else:
                # 本地路径
                local = output_file_from_url(url)
                if local and os.path.exists(local):
                    local_files.append(local)
                else:
                    logger.warning(f"[ComposeStage] 本地文件不存在: {url}")
        return local_files

    async def _check_ffmpeg(self, ffmpeg: str):
        """检查 ffmpeg 是否可用"""
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg 返回非零状态")
        except FileNotFoundError:
            raise RuntimeError(
                f"ffmpeg 未安装或路径无效: {ffmpeg}，请安装 ffmpeg 或设置 FFMPEG_PATH 环境变量"
            )
