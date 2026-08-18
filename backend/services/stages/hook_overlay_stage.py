"""
结尾钩子引导框阶段

短视频结尾固定引导框（"评论区扣1领工具"模板化），ffmpeg overlay。
无自定义图片时用 PIL 生成引导框 PNG（圆角矩形 + 高亮文案）。

输入：video 资产
参数：
  - hook_text: 主文案（默认 "评论区扣1领工具"）
  - sub_text: 副文案（可选）
  - hook_image: 自定义引导框图片 URL（可选）
  - duration: 叠加时长秒（默认 3）
  - position: bottom / top / center
  - margin: 距边缘距离
"""

import logging
import os
import uuid
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.stages import ffmpeg_utils
from services.providers.provider_utils import output_file_from_url, output_path_for, output_url_for

logger = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑粗体
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/simsun.ttc",   # 宋体
]


class HookOverlayStage(StagePlugin):
    """结尾钩子引导框阶段"""

    stage_def = StageDef(
        stage_id="hook_overlay",
        name="结尾钩子引导框",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="结尾固定引导框（评论区扣1领工具模板化），ffmpeg overlay",
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

        hook_text = params.get("hook_text", "评论区扣1领工具")
        sub_text = params.get("sub_text", "")
        hook_image = params.get("hook_image", "")
        duration = float(params.get("duration", 3))
        position = params.get("position", "bottom")
        margin = int(params.get("margin", 0))

        try:
            await ffmpeg_utils.check_ffmpeg()
            local_video = await ffmpeg_utils.resolve_local_video(video.urls[0])
            width, height = await ffmpeg_utils.get_video_size(local_video)
            total_duration = await ffmpeg_utils.get_video_duration(local_video)

            if hook_image:
                overlay_file = await self._resolve_overlay_image(hook_image, width)
            else:
                overlay_file = self._generate_hook_image(width, hook_text, sub_text)

            start = max(total_duration - duration, 0)
            y_expr = self._position_y(position, height, margin)
            output_file = output_path_for(f"hook_{uuid.uuid4().hex[:8]}.mp4", "output")
            await ffmpeg_utils.run_ffmpeg([
                "-y", "-i", local_video, "-i", overlay_file,
                "-filter_complex",
                f"[0:v][1:v]overlay=x=(W-w)/2:y={y_expr}:enable='gte(t,{start:.2f})'",
                "-c:a", "copy",
                output_file,
            ])

            out_url = output_url_for(os.path.basename(output_file), "output")
            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="video",
                name=params.get("name", "钩子引导视频"),
                urls=[out_url],
                input_assets=input_assets,
                extra_metadata={
                    "source_asset_ids": [video.asset_id],
                    "hook_text": hook_text,
                    "overlay_start": round(start, 2),
                    "overlay_duration": duration,
                    "video_url": out_url,
                },
            )
            return AssetProduceResult(asset=new_asset, success=True)

        except Exception as e:
            logger.error(f"[HookOverlayStage] 钩子叠加失败: {e}")
            return self._error_result(str(e))

    # ---- 纯逻辑（可单测）----

    def _position_y(self, position: str, height: int, margin: int) -> str:
        """计算 overlay 的 y 表达式"""
        if position == "top":
            return str(margin)
        if position == "center":
            return "(H-h)/2"
        return f"H-h-{margin}"

    async def _resolve_overlay_image(self, url: str, video_width: int) -> str:
        """解析自定义钩子图片为本地路径（远程则下载）"""
        if url.startswith(("http://", "https://")):
            import httpx
            temp_path = output_path_for(f"hook_{uuid.uuid4().hex[:8]}.png", "temp")
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=120.0)) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(temp_path, "wb") as f:
                    f.write(resp.content)
            return temp_path
        local = output_file_from_url(url)
        if local and os.path.exists(local):
            return local
        raise RuntimeError(f"钩子图片不存在: {url}")

    def _generate_hook_image(self, video_width: int, hook_text: str, sub_text: str) -> str:
        """用 PIL 生成引导框 PNG（圆角矩形 + 黄色主文案 + 白色副文案）"""
        from PIL import Image, ImageDraw, ImageFont

        width = int(video_width * 0.9)
        height = int(width * 0.30)
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        radius = int(height * 0.16)
        draw.rounded_rectangle(
            [0, 0, width - 1, height - 1],
            radius=radius,
            fill=(18, 18, 28, 225),
            outline=(255, 190, 0, 255),
            width=max(3, width // 200),
        )

        font_path = self._find_font()
        main_font = ImageFont.truetype(font_path, int(height * 0.40))
        sub_font = ImageFont.truetype(font_path, int(height * 0.22)) if sub_text else None

        main_color = (255, 205, 40, 255)
        bbox = draw.textbbox((0, 0), hook_text, font=main_font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        total_h = th + (int(height * 0.30) if sub_font else 0)
        y = (height - total_h) / 2 - bbox[1]
        x = (width - tw) / 2 - bbox[0]
        draw.text((x, y), hook_text, font=main_font, fill=main_color)

        if sub_font and sub_text:
            sub_color = (255, 255, 255, 255)
            bbox2 = draw.textbbox((0, 0), sub_text, font=sub_font)
            tw2, th2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
            y2 = y + th + int(height * 0.06)
            x2 = (width - tw2) / 2 - bbox2[0]
            draw.text((x2, y2), sub_text, font=sub_font, fill=sub_color)

        out_path = output_path_for(f"hook_{uuid.uuid4().hex[:8]}.png", "temp")
        img.save(out_path, "PNG")
        return out_path

    def _find_font(self) -> str:
        for p in _FONT_CANDIDATES:
            if os.path.exists(p):
                return p
        return None
