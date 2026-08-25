"""视频音频/混音工具 Mixin（TTS 人声生成、媒体下载、音画混音）"""

import asyncio
import logging

logger = logging.getLogger(__name__)


class VideoAudioMixin:
    """视频音频/混音工具 Mixin（TTS 人声生成、媒体下载、音画混音）"""

    async def _gen_tts_segment(self, comfyui_svc, text: str, idx: int) -> str:
        """生成单镜独立 TTS 人声音频，返回音频 URL（失败返回空串）"""
        try:
            tts_result = await comfyui_svc.generate_tts_audio(
                text=text,
                mode="voice_design",
                voice_description="成年女性，温柔亲切，语速适中",
                asset_tag=f"h3seg{idx+1}",
            )
            url = getattr(tts_result, "image_url", "") or ""
            if url:
                logger.info(f"[VideoStage] 镜{idx+1} TTS 人声生成完成 | url={url[:60]}")
            return url
        except Exception as e:
            logger.warning(f"[VideoStage] 镜{idx+1} TTS 生成失败 | err={e}")
            return ""

    async def _download_media(self, url: str, ext: str) -> str:
        """下载媒体 URL 到本地临时文件（保留扩展名），失败返回空串"""
        import os
        import uuid
        import httpx
        from services.providers.provider_utils import output_path_for

        if url.startswith(("http://", "https://")):
            temp_path = output_path_for(f"tmp_{uuid.uuid4().hex[:8]}.{ext}", "temp")
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(20.0, connect=20.0, read=300.0)
                ) as client:  # noqa: E501
                    resp = await client.get(url)
                    resp.raise_for_status()
                    with open(temp_path, "wb") as f:
                        f.write(resp.content)
                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    return temp_path
            except Exception as e:
                logger.warning(f"[VideoStage] 下载失败 url={url[:60]} | err={e}")
            return ""
        return url

    async def _mix_tts_into_segment(self, seg_url: str, tts_url: str) -> str:
        """把 TTS 人声叠加到 H3 镜头视频（保留环境音 + 人声，音画对齐）"""
        import os
        import uuid
        from services.stages.ffmpeg_utils import _ffmpeg_bin
        from services.providers.provider_utils import output_path_for, output_url_for

        ffmpeg = _ffmpeg_bin()
        seg_local = await self._download_media(seg_url, "mp4")
        tts_local = await self._download_media(tts_url, "flac")
        if not seg_local or not tts_local:
            return ""
        out_path = output_path_for(f"seg_tts_{uuid.uuid4().hex[:8]}.mp4", "output")
        # 环境音降为背景(0.45) + TTS 人声(1.0) 叠加，amix 以视频原音频时长为准
        args = [
            ffmpeg,
            "-y",
            "-i",
            seg_local,
            "-i",
            tts_local,
            "-filter_complex",
            "[0:a]volume=0.45[env];"
            "[1:a]volume=1.0,apad[tts];"
            "[env][tts]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-ac",
            "2",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.warning(
                f"[VideoStage] TTS 混音失败: {stderr.decode('utf-8', errors='replace')[:300]}"
            )  # noqa: E501
            return ""
        logger.info(f"[VideoStage] 镜 TTS 混音完成 | out={os.path.basename(out_path)}")
        return output_url_for(os.path.basename(out_path), "output")
