"""
成片导出阶段

导出最终成片，支持：
  - 编码/格式转换（mp4/webm/mov/avi/gif）
  - 分辨率/码率/帧率调整
  - 字幕烧录（SRT/ASS）
  - 水印（图片/文字）
  - 封面提取（首帧/指定时间）
  - 音量归一化 / 静音

使用 ffmpeg 本地处理。
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.stage_service import StageDef, StagePlugin
from services.stages.ffmpeg_utils import _ffmpeg_bin, _ffprobe_bin

logger = logging.getLogger(__name__)


class ExportStage(StagePlugin):
    """成片导出阶段"""

    stage_def = StageDef(
        stage_id="export",
        name="成片导出",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="导出最终成片（编码/格式转换/字幕烧录/水印/封面）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc = get_asset_service()

        if not input_assets:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="导出需要至少一个视频资产",
            )

        source = input_assets[0]
        video_url = ""
        for url in source.urls:
            if url:
                video_url = url
                break
        if not video_url:
            video_url = source.metadata.get("video_url", "")

        if not video_url:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="无有效视频 URL",
            )

        # 导出参数
        format_ = params.get("format", "mp4")
        codec = params.get("codec", "libx264")
        resolution = params.get("resolution", "")  # e.g. "1920x1080"
        bitrate = params.get("bitrate", "")  # e.g. "5M"
        fps = params.get("fps", 0)
        output_name = params.get("name", f"成片.{format_}")

        # 增强参数
        subtitle_url = params.get("subtitle_url", "")  # SRT/ASS 字幕文件 URL
        subtitle_force_style = params.get("subtitle_force_style", "")  # ASS 样式覆盖
        watermark_url = params.get("watermark_url", "")  # 图片水印 URL
        watermark_position = params.get("watermark_position", "bottom-right")  # 位置
        watermark_opacity = float(params.get("watermark_opacity", 0.8))
        watermark_scale = float(params.get("watermark_scale", 0.15))
        text_watermark = params.get("text_watermark", "")  # 文字水印
        extract_cover = bool(params.get("extract_cover", False))  # 是否提取封面
        cover_time = float(params.get("cover_time", 1.0))  # 封面提取时间点（秒）
        normalize_audio = bool(params.get("normalize_audio", False))  # 音量归一化
        mute = bool(params.get("mute", False))  # 静音
        trim_start = float(params.get("trim_start", 0))  # 裁剪起点（秒）
        trim_end = float(params.get("trim_end", 0))  # 裁剪终点（秒），0=不裁剪

        logger.info(
            f"[ExportStage] 导出 | format={format_} | codec={codec} | source={source.asset_id} "
            f"| subtitle={'是' if subtitle_url else '否'} | watermark={'是' if watermark_url or text_watermark else '否'} "
            f"| cover={'是' if extract_cover else '否'}"
        )

        try:
            # 1. 主视频导出
            result_url = await self._export_video(
                video_url, format_=format_, codec=codec,
                resolution=resolution, bitrate=bitrate, fps=fps,
                subtitle_url=subtitle_url, subtitle_force_style=subtitle_force_style,
                watermark_url=watermark_url, watermark_position=watermark_position,
                watermark_opacity=watermark_opacity, watermark_scale=watermark_scale,
                text_watermark=text_watermark,
                normalize_audio=normalize_audio, mute=mute,
                trim_start=trim_start, trim_end=trim_end,
            )

            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="video",
                name=output_name,
                urls=[result_url] if result_url else [],
                input_assets=[source],
                extra_metadata={
                    "source_asset_id": source.asset_id,
                    "format": format_,
                    "codec": codec,
                    "resolution": resolution,
                    "bitrate": bitrate,
                    "video_url": result_url,
                    "exported": True,
                    "subtitle_burned": bool(subtitle_url),
                    "watermark_added": bool(watermark_url or text_watermark),
                    "trimmed": bool(trim_start or trim_end),
                },
                content_type=source.content_type,
            )

            # 2. 提取封面（可选）
            cover_url = ""
            if extract_cover:
                try:
                    cover_url = await self._extract_cover(result_url or video_url, cover_time)
                    if cover_url:
                        # 创建封面图片资产
                        cover_asset = await self._register_asset_direct(
                            asset_svc,
                            asset_type="image",
                            name=f"{output_name} - 封面",
                            urls=[cover_url],
                            input_assets=[new_asset] if hasattr(new_asset, 'asset_id') else None,
                            extra_metadata={
                                "source_asset_id": source.asset_id,
                                "video_asset_id": new_asset.asset_id,
                                "cover_time": cover_time,
                                "image_url": cover_url,
                                "is_cover": True,
                            },
                        )
                        # 在视频资产上记录封面资产 ID
                        await asset_svc.update(new_asset.asset_id, metadata={
                            "cover_asset_id": cover_asset.asset_id,
                            "cover_url": cover_url,
                        })
                        logger.info(f"[ExportStage] 封面已提取 | cover_asset={cover_asset.asset_id}")
                except Exception as e:
                    logger.warning(f"[ExportStage] 封面提取失败（不影响主流程）: {e}")

            return AssetProduceResult(
                asset=new_asset,
                success=True,
            )

        except Exception as e:
            logger.error(f"[ExportStage] 导出失败: {e}")
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=str(e),
            )

    async def _export_video(
        self,
        video_url: str,
        format_: str = "mp4",
        codec: str = "libx264",
        resolution: str = "",
        bitrate: str = "",
        fps: int = 0,
        subtitle_url: str = "",
        subtitle_force_style: str = "",
        watermark_url: str = "",
        watermark_position: str = "bottom-right",
        watermark_opacity: float = 0.8,
        watermark_scale: float = 0.15,
        text_watermark: str = "",
        normalize_audio: bool = False,
        mute: bool = False,
        trim_start: float = 0,
        trim_end: float = 0,
    ) -> str:
        """使用 ffmpeg 导出视频（支持字幕烧录/水印/裁剪）"""
        import uuid

        ffmpeg = _ffmpeg_bin()
        ffprobe = _ffprobe_bin()
        from services.providers.provider_utils import (
            output_file_from_url, output_path_for, output_url_for,
        )

        # 检查 ffmpeg
        await self._check_ffmpeg(ffmpeg)

        # 获取本地文件
        local = await self._ensure_local(video_url, "video")

        # 处理水印图片（如需）
        watermark_local = ""
        if watermark_url:
            watermark_local = await self._ensure_local(watermark_url, "watermark")

        # 处理字幕文件（如需）
        subtitle_local = ""
        if subtitle_url:
            subtitle_local = await self._ensure_local(subtitle_url, "subtitle")

        ext = format_ if format_ in ("mp4", "webm", "mov", "avi", "gif") else "mp4"
        output_file = output_path_for(f"export_{uuid.uuid4().hex[:8]}.{ext}", "output")

        # 构建 ffmpeg 命令
        args = [ffmpeg, "-y"]

        # 裁剪（输入参数）
        if trim_start > 0:
            args.extend(["-ss", str(trim_start)])
        args.extend(["-i", local])
        if watermark_local:
            args.extend(["-i", watermark_local])
        if trim_end > 0:
            duration = max(trim_end - trim_start, 0.1)
            args.extend(["-t", str(duration)])

        # 构建 filter_complex
        filters = []
        overlay_inputs = 0  # 额外输入数量（watermark）

        # 分辨率
        if resolution and "x" in resolution:
            filters.append(f"[0:v]scale={resolution}[v0]")

        # 字幕烧录
        if subtitle_local:
            # Windows 路径需要转义
            sub_path_escaped = subtitle_local.replace("\\", "/").replace(":", "\\:")
            sub_filter = f"subtitles='{sub_path_escaped}'"
            if subtitle_force_style:
                sub_filter += f":force_style='{subtitle_force_style}'"
            if filters:
                filters.append(f"[v0]{sub_filter}[v1]")
                video_label = "[v1]"
            else:
                filters.append(f"[0:v]{sub_filter}[v1]")
                video_label = "[v1]"
        else:
            video_label = "[v0]" if filters else "[0:v]"

        # 图片水印
        if watermark_local:
            # 缩放水印
            wm_input_idx = 1
            filters.append(f"[{wm_input_idx}:v]scale=iw*{watermark_scale}:-1[wm]")
            # 计算位置
            pos = self._watermark_position_arg(watermark_position)
            if video_label != "[0:v]":
                # 已有滤镜输出
                final_v_label = "[vfinal]"
                filters.append(f"{video_label}[wm]overlay={pos}{final_v_label}")
            else:
                final_v_label = "[vfinal]"
                filters.append(f"[0:v][wm]overlay={pos}{final_v_label}")
            video_label = final_v_label

        # 文字水印
        if text_watermark:
            # 使用 drawtext
            safe_text = text_watermark.replace(":", "\\:").replace("'", "\\'")
            pos = self._text_watermark_pos(watermark_position)
            drawtext = (
                f"drawtext=text='{safe_text}':fontsize=28:fontcolor=white@{watermark_opacity}:"
                f"box=1:boxcolor=black@0.5:boxborderw=8:{pos}"
            )
            if video_label != "[0:v]":
                new_label = "[vtext]"
                filters.append(f"{video_label}{drawtext}{new_label}")
                video_label = new_label
            else:
                filters.append(f"[0:v]{drawtext}[vtext]")
                video_label = "[vtext]"

        # 应用 filter_complex
        if filters:
            args.extend(["-filter_complex", ";".join(filters)])
            if video_label.startswith("["):
                args.extend(["-map", video_label])
                args.extend(["-map", "0:a?"])
        else:
            # 无滤镜
            pass

        # 视频编码
        if codec and format_ != "gif":
            args.extend(["-c:v", codec])

        # 码率
        if bitrate and format_ != "gif":
            args.extend(["-b:v", bitrate])

        # 帧率
        if fps > 0:
            args.extend(["-r", str(fps)])

        # 音频处理
        if mute:
            args.extend(["-an"])
        elif normalize_audio:
            args.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])

        # GIF 特殊处理
        if format_ == "gif":
            args.extend(["-vf", f"fps={fps or 15},scale={resolution or '480:-1'}:flags=lanczos", "-loop", "0"])

        # faststart（仅 mp4）
        if format_ == "mp4":
            args.extend(["-movflags", "+faststart"])

        args.append(output_file)

        logger.info(f"[ExportStage] ffmpeg 命令 | args={' '.join(args[:6])}...")

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode('utf-8', errors='replace')[-800:]
            raise RuntimeError(f"ffmpeg 导出失败: {err_msg}")

        return output_url_for(os.path.basename(output_file), "output")

    async def _extract_cover(self, video_url: str, time_sec: float) -> str:
        """从视频提取封面图"""
        import uuid
        ffmpeg = _ffmpeg_bin()
        from services.providers.provider_utils import output_path_for, output_url_for

        await self._check_ffmpeg(ffmpeg)
        local = await self._ensure_local(video_url, "video")

        cover_file = output_path_for(f"cover_{uuid.uuid4().hex[:8]}.jpg", "output")
        args = [
            ffmpeg, "-y",
            "-ss", str(time_sec),
            "-i", local,
            "-frames:v", "1",
            "-q:v", "2",
            cover_file,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"封面提取失败: {stderr.decode('utf-8', errors='replace')[-300:]}")
        return output_url_for(os.path.basename(cover_file), "output")

    async def _check_ffmpeg(self, ffmpeg: str):
        """检查 ffmpeg 可用性"""
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg 不可用")
        except FileNotFoundError:
            raise RuntimeError("ffmpeg 未安装，请安装 ffmpeg 或设置 FFMPEG_PATH")

    async def _ensure_local(self, url: str, category: str = "temp") -> str:
        """确保文件在本地，返回本地路径"""
        import os
        from services.providers.provider_utils import output_file_from_url, output_path_for

        if not url:
            raise RuntimeError(f"空 URL ({category})")

        # 本地 URL 路径
        local = output_file_from_url(url)
        if local and os.path.exists(local):
            return local

        # 已是本地路径
        if os.path.exists(url):
            return url

        # HTTP 下载
        if url.startswith(("http://", "https://")):
            import httpx
            import uuid
            ext = ".mp4"
            if category == "watermark":
                ext = ".png"
            elif category == "subtitle":
                ext = ".srt"
            elif "jpg" in url or "png" in url:
                ext = ".png" if "png" in url else ".jpg"
            local_path = output_path_for(f"temp_{category}_{uuid.uuid4().hex[:8]}{ext}", "temp")
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=300.0)) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(resp.content)
            return local_path

        raise RuntimeError(f"文件不可访问: {url}")

    def _watermark_position_arg(self, position: str) -> str:
        """水印 overlay 位置参数"""
        positions = {
            "top-left": "10:10",
            "top-right": "main_w-overlay_w-10:10",
            "bottom-left": "10:main_h-overlay_h-10",
            "bottom-right": "main_w-overlay_w-10:main_h-overlay_h-10",
            "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2",
        }
        return positions.get(position, positions["bottom-right"])

    def _text_watermark_pos(self, position: str) -> str:
        """文字水印位置参数"""
        positions = {
            "top-left": "x=10:y=10",
            "top-right": "x=w-tw-10:y=10",
            "bottom-left": "x=10:y=h-th-10",
            "bottom-right": "x=w-tw-10:y=h-th-10",
            "center": "x=(w-tw)/2:y=(h-th)/2",
        }
        return positions.get(position, positions["bottom-right"])
