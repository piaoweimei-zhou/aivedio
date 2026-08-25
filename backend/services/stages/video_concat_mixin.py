"""视频拼接工具 Mixin（逐镜音频清理对齐、重编码、ffmpeg 拼接）"""

import asyncio
import logging
from typing import List

logger = logging.getLogger(__name__)


class VideoConcatMixin:
    """视频拼接工具 Mixin（逐镜音频清理对齐、重编码、ffmpeg 拼接）"""

    async def _concat_segments_with_clean(self, video_urls: List[str], seed=None) -> str:
        """逐镜音频清理 + ffmpeg 拼接（保证音画时长一致）"""
        import os
        import uuid

        from services.stages.ffmpeg_utils import _ffmpeg_bin, _ffprobe_bin, resolve_local_video
        from services.providers.provider_utils import output_path_for, output_url_for

        ffmpeg = _ffmpeg_bin()
        ffprobe = _ffprobe_bin()

        # 下载 + 清理每个镜头音频头部（爆音/静音），并统一时长与采样率
        cleaned = []
        for i, url in enumerate(video_urls):
            local = await resolve_local_video(url)
            cleaned_local = await self._clean_clip_audio_align(local, ffmpeg, ffprobe)
            if cleaned_local:
                cleaned.append(cleaned_local)

        if len(cleaned) == 1:
            return video_urls[0]

        concat_file = output_path_for(f"concat_{uuid.uuid4().hex[:8]}.txt", "output")
        with open(concat_file, "w", encoding="utf-8") as f:
            for path in cleaned:
                f.write(f"file '{path.replace(chr(39), chr(39)*2)}'\n")

        output_file = output_path_for(f"seg_concat_{uuid.uuid4().hex[:8]}.mp4", "output")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c",
            "copy",
            output_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"[VideoStage] concat copy 失败，改用重编码: {stderr.decode('utf-8', errors='replace')[:300]}"  # noqa: E501
            )  # noqa: E501
            return await self._concat_reencode(cleaned, output_file)
        try:
            os.remove(concat_file)
        except Exception:
            pass
        return output_url_for(os.path.basename(output_file), "output")

    async def _clean_clip_audio_align(self, local, ffmpeg, ffprobe):
        """清理单个镜头音频头部爆音，并将音频裁剪/补齐到与视频严格等长"""
        import os
        import uuid

        from services.providers.provider_utils import (
            output_path_for,
        )

        if not local or not os.path.exists(local):
            return None

        # 探测视频流真实时长（以视频为准，避免被加长的音频撑大 format.duration）
        dur_d = float("nan")
        try:
            import json as _json

            proc = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                local,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            data = _json.loads(out.decode("utf-8", errors="replace"))
            streams = data.get("streams") or []
            if streams and streams[0].get("duration"):
                dur_d = float(streams[0]["duration"])
            else:
                # 流无 duration 字段时回退 format
                proc2 = await asyncio.create_subprocess_exec(
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    local,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out2, _ = await proc2.communicate()
                data2 = _json.loads(out2.decode("utf-8", errors="replace"))
                dur_d = float(data2["format"]["duration"])
        except Exception:
            dur_d = float("nan")

        # 复用 export 的爆音检测思路（简化版）
        head_trim = 0.0
        try:
            import re as _re

            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-hide_banner",
                "-i",
                local,
                "-af",
                "silencedetect=noise=-45dB:d=0.05",
                "-f",
                "null",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            out_txt = err.decode("utf-8", errors="replace")
            m_s = _re.search(r"silence_start:\s*(-?[\d.]+)", out_txt)
            if m_s:
                s = float(m_s.group(1))
                m_e = _re.search(r"silence_end:\s*([\d.]+)", out_txt[m_s.end() :])
                if m_e:
                    e = float(m_e.group(1))
                    if s <= 0.35 and e >= 0.03 and e <= 0.6:
                        head_trim = min(e + 0.03, 0.28)
        except Exception:
            head_trim = 0.0

        out_path = output_path_for(f"seg_clean_{uuid.uuid4().hex[:8]}.mp4", "output")
        # 视频与音频同步裁剪开头（-ss 在输入前→同步），再以 -t 限到视频时长，风格 audio 对齐
        args = [ffmpeg, "-y"]
        if head_trim > 0:
            args += ["-ss", str(head_trim)]
        args += ["-i", local]
        vid_dur = dur_d - head_trim if not (dur_d != dur_d) else None
        audio_filter = "aresample=48000,alimiter=limit=0.85:level=false"
        if vid_dur and vid_dur > 0:
            audio_filter = f"atrim=0:{vid_dur},asetpts=PTS-STARTPTS,apad,atrim=0:{vid_dur},{audio_filter}"  # noqa: E501
        args += ["-vf", "setpts=PTS-STARTPTS"]
        args += ["-af", audio_filter]
        if vid_dur and vid_dur > 0:
            args += ["-t", str(vid_dur)]
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-ac", "2"]
        args += [out_path]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"[VideoStage] 镜头音频清理失败: {stderr.decode('utf-8', errors='replace')[:300]}"
            )  # noqa: E501
            return None
        os.path.exists(out_path) and os.path.getsize(out_path) > 0 and out_path

        return out_path if os.path.exists(out_path) and os.path.getsize(out_path) > 0 else None

    async def _concat_reencode(self, cleaned_paths: List[str], output_file: str) -> str:
        """拼接失败时用 filter_complex 重编码拼接（各镜统一重编码，保证可拼）"""
        from services.stages.ffmpeg_utils import _ffmpeg_bin
        from services.providers.provider_utils import output_url_for
        import os

        ffmpeg = _ffmpeg_bin()
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except Exception:
                pass
        fc = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(cleaned_paths)))
        fc += f"concat=n={len(cleaned_paths)}:v=1:a=1[v][a]"
        args = [ffmpeg, "-y"]
        for p in cleaned_paths:
            args += ["-i", p]
        args += [
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-ac",
            "2",
            output_file,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"逐镜重编码拼接失败: {stderr.decode('utf-8', errors='replace')[:400]}"
            )
        return output_url_for(os.path.basename(output_file), "output")
