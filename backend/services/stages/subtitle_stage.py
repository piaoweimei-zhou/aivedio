"""
字幕烧录阶段

竖版短视频大字幕：关键词高亮 + 描边，纯 ffmpeg 烧录，零模型成本。

输入：video 资产
参数：
  - subtitle_texts: [{text, start, end}] 显式时间轴（如来自 TTS 时间戳）
  - text: 单段文案（无时间戳时按语速自动估算分布）
  - keywords: 需要高亮的关键词列表
  - font_name / font_size / font_color / highlight_color / outline / margin_v
"""

import logging
import os
import re
import uuid
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.stages import ffmpeg_utils
from services.providers.provider_utils import output_path_for, output_url_for

logger = logging.getLogger(__name__)

# ASS 颜色格式：&HAABBGGRR
_DEFAULT_FONT_COLOR = "FFFFFF"  # 白
_DEFAULT_HIGHLIGHT_COLOR = "00FFFF"  # 黄
_DEFAULT_OUTLINE_COLOR = "141414"  # 深色描边

_CJK_MIN = 0x4E00
_CJK_MAX = 0x9FFF


class SubtitleStage(StagePlugin):
    """字幕烧录阶段"""

    stage_def = StageDef(
        stage_id="subtitle",
        name="字幕烧录",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="竖版大字幕烧录（关键词高亮描边，纯 ffmpeg）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc, _ = self._get_services()

        err = self._require_input(input_assets, 1)
        if err:
            return self._error_result(err)

        video = input_assets[0]
        if not video.urls:
            return self._error_result("输入视频无 URL")

        subtitle_texts = params.get("subtitle_texts") or []
        text = params.get("text", "")
        keywords = params.get("keywords") or []

        if not subtitle_texts and text:
            subtitle_texts = [{"text": t} for t in self._split_lines(text)]

        if not subtitle_texts:
            return self._error_result("请提供 subtitle_texts 或 text 参数")

        try:
            await ffmpeg_utils.check_ffmpeg()
            local_video = await ffmpeg_utils.resolve_local_video(video.urls[0])
            width, height = await ffmpeg_utils.get_video_size(local_video)
            duration = await ffmpeg_utils.get_video_duration(local_video)

            timeline = self._build_timeline(subtitle_texts, duration)
            ass_content = self._build_ass(timeline, width, height, params, keywords)
            ass_file = output_path_for(f"sub_{uuid.uuid4().hex[:8]}.ass", "temp")
            with open(ass_file, "w", encoding="utf-8") as f:
                f.write(ass_content)

            output_file = output_path_for(f"sub_{uuid.uuid4().hex[:8]}.mp4", "output")
            # 用相对路径避免 Windows 冒号/反斜杠被滤镜解析器误读
            rel_ass = os.path.relpath(ass_file).replace("\\", "/")
            await ffmpeg_utils.run_ffmpeg(
                [
                    "-y",
                    "-i",
                    local_video,
                    "-vf",
                    f"ass={rel_ass}",
                    "-c:a",
                    "copy",
                    output_file,
                ]
            )

            out_url = output_url_for(os.path.basename(output_file), "output")
            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="video",
                name=params.get("name", "字幕视频"),
                urls=[out_url],
                input_assets=input_assets,
                extra_metadata={
                    "source_asset_ids": [video.asset_id],
                    "subtitle_count": len(timeline),
                    "keywords": keywords,
                    "video_url": out_url,
                },
            )
            return AssetProduceResult(asset=new_asset, success=True)

        except Exception as e:
            logger.error(f"[SubtitleStage] 字幕烧录失败: {e}")
            return self._error_result(str(e))

    # ---- 纯逻辑（可单测）----

    def _split_lines(self, text: str) -> List[str]:
        """把整段文案切成字幕行（按换行/标点，单行 ≤ 18 字）"""
        lines: List[str] = []
        for para in text.replace("\r", "").split("\n"):
            para = para.strip()
            if not para:
                continue
            for seg in re.split(r"(?<=[。！？；])", para):
                seg = seg.strip()
                if not seg:
                    continue
                if len(seg) > 18:
                    # 先按低优先级标点切
                    sub_segs = re.split(r"(?<=[，、：])", seg)
                    for sub in sub_segs:
                        sub = sub.strip()
                        if not sub:
                            continue
                        if len(sub) > 18:
                            # 无标点可切：按字符数硬切
                            for i in range(0, len(sub), 18):
                                lines.append(sub[i : i + 18])
                        else:
                            lines.append(sub)
                else:
                    lines.append(seg)
        return lines

    def _estimate_duration(self, text: str) -> float:
        """估算朗读时长：中文 0.28s/字，其他 0.10s/字符，含 0.7s 缓冲"""
        cjk = sum(1 for c in text if _CJK_MIN <= ord(c) <= _CJK_MAX)
        other = max(len(text) - cjk, 0)
        return cjk * 0.28 + other * 0.10 + 0.7

    def _build_timeline(
        self, subtitle_texts: List[Dict[str, Any]], video_duration: float
    ) -> List[Dict[str, Any]]:
        """构建时间轴：有显式时间戳直接用，否则按语速估算并压缩到视频时长内"""
        if any("start" in t and "end" in t for t in subtitle_texts):
            return subtitle_texts
        durations = [self._estimate_duration(t.get("text", "")) for t in subtitle_texts]
        total = sum(durations)
        usable = max(video_duration * 0.9, 1.0)
        if total > usable:
            scale = usable / total
            durations = [d * scale for d in durations]
        timeline: List[Dict[str, Any]] = []
        cursor = 0.5
        for item, dur in zip(subtitle_texts, durations):
            timeline.append(
                {
                    "text": item.get("text", ""),
                    "start": round(cursor, 2),
                    "end": round(cursor + dur, 2),
                }
            )
            cursor += dur
        return timeline

    def _build_ass(
        self,
        timeline: List[Dict[str, Any]],
        width: int,
        height: int,
        params: Dict[str, Any],
        keywords: List[str],
    ) -> str:
        """生成 ASS 字幕内容（竖版大字、关键词高亮描边）"""
        font_name = params.get("font_name", "Microsoft YaHei")
        font_size = int(params.get("font_size", 0) or max(48, int(width * 0.07)))
        font_color = str(params.get("font_color", _DEFAULT_FONT_COLOR)).lstrip("#").upper()
        highlight_color = (
            str(params.get("highlight_color", _DEFAULT_HIGHLIGHT_COLOR)).lstrip("#").upper()
        )  # noqa: E501
        outline = int(params.get("outline", max(3, width // 200)))
        margin_v_raw = params.get("margin_v", 0.12)
        try:
            margin_v = float(margin_v_raw)
        except (TypeError, ValueError):
            margin_v = 0.12
        if 0 < margin_v <= 1:
            margin_v = int(height * margin_v)
        else:
            margin_v = int(margin_v)

        header = (
            "[Script Info]\n"
            "ScriptType: v4.00+\n"
            f"PlayResX: {width}\n"
            f"PlayResY: {height}\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"  # noqa: E501
            f"Style: Default,{font_name},{font_size},&H00{font_color},&H000000FF,&H00{_DEFAULT_OUTLINE_COLOR},&H80000000,-1,0,0,0,100,100,0,0,1,{outline},2,2,{int(width*0.04)},{int(width*0.04)},{margin_v},1\n"  # noqa: E501
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )
        events = []
        for item in timeline:
            start = self._ass_time(item["start"])
            end = self._ass_time(item["end"])
            text = self._highlight(item.get("text", ""), keywords, font_color, highlight_color)
            events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")
        return header + "\n".join(events) + "\n"

    def _highlight(
        self, text: str, keywords: List[str], font_color: str, highlight_color: str
    ) -> str:
        """关键词用颜色覆盖高亮（ASS \\c 标签）"""
        text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        for kw in keywords:
            kw_esc = kw.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            if not kw_esc:
                continue
            text = text.replace(
                kw_esc, f"{{\\c&H{highlight_color}&}}{kw_esc}{{\\c&H{font_color}&}}"
            )  # noqa: E501
        return text

    def _ass_time(self, seconds: float) -> str:
        """秒 → ASS 时间格式 H:MM:SS.cc"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        cs = int(round((seconds - int(seconds)) * 100))
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
