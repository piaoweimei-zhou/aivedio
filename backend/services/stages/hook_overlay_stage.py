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
    # Linux/CI 常见字体
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
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

        try:
            await ffmpeg_utils.check_ffmpeg()
            local_video = await ffmpeg_utils.resolve_local_video(video.urls[0])
            width, height = await ffmpeg_utils.get_video_size(local_video)
            total_duration = await ffmpeg_utils.get_video_duration(local_video)

            # 底部安全边距：默认按视频高度 10% 留白，避免贴底被平台底部 UI（点赞/评论/操作栏）遮挡；
            # 用户显式传入 margin 时优先使用用户值
            margin = self._resolve_margin(params, height)

            if hook_image:
                overlay_file = await self._resolve_overlay_image(hook_image, width)
            else:
                overlay_file = self._generate_hook_image(width, hook_text, sub_text)

            start = max(total_duration - duration, 0)
            y_expr = self._position_y(position, height, margin)
            # 动画：弹跳入场（可选，默认开启）——overlay_xy 内已含 enable 时间窗
            animate = bool(params.get("animate", True))
            overlay_xy = self._build_overlay_xy(y_expr, start, animate)
            static_xy = f"x=(W-w)/2:y={y_expr}"
            final_xy = overlay_xy if animate else f"{static_xy}:enable=gte(t\\,{start:.2f})"
            output_file = output_path_for(f"hook_{uuid.uuid4().hex[:8]}.mp4", "output")
            await ffmpeg_utils.run_ffmpeg([
                "-y", "-i", local_video, "-i", overlay_file,
                "-filter_complex",
                f"[0:v][1:v]overlay={final_xy}",
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
                    "margin": margin,
                    "video_url": out_url,
                },
            )
            return AssetProduceResult(asset=new_asset, success=True)

        except Exception as e:
            logger.error(f"[HookOverlayStage] 钩子叠加失败: {e}")
            return self._error_result(str(e))

    # ---- 纯逻辑（可单测）----

    def _resolve_margin(self, params: Dict[str, Any], height: int) -> int:
        """计算 overlay 的底部安全边距
        
        默认按视频高度 10% 留白，确保引导框不贴底、避开平台底部 UI 遮挡区；
        用户显式传入 margin 时优先使用用户值。
        """
        if params.get("margin") is None:
            return max(int(height * 0.10), 1)
        return int(params.get("margin"))

    def _position_y(self, position: str, height: int, margin: int) -> str:
        """计算 overlay 的 y 表达式"""
        if position == "top":
            return str(margin)
        if position == "center":
            return "(H-h)/2"
        return f"H-h-{margin}"

    def _build_overlay_xy(self, base_y: str, start: float, animate: bool) -> str:
        """构造 overlay 的 x/y 表达式
        
        - 静态：x 居中 + y 固定到最终位
        - 动画（弹跳入场）：x 居中 + y 从最终位下方弹入，阻尼振荡约 1s 稳定。
          以 (t-start) 为时钟：首拍 sin 为正 → y 增大(下移 90px) → 指数衰减振荡回最终位。
          不叠加 if() 兜底：overlay 未到 enable 时间窗前不会 eval 坐标表达式，无提前求值风险；
          叠 if() 反会让 filter 解析器在含逗号表达式 + enable 时误判报错。
        """
        x = "(W-w)/2"
        if not animate:
            return f"x={x}:y={base_y}"
        bounce = f"{base_y}+" \
                 f"(90*exp(-1.6*(t-{start}))*sin((t-{start})*13))"
        return f"x={x}:y={bounce}:enable=gte(t\\,{start:.2f})"

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
        if font_path:
            main_font = ImageFont.truetype(font_path, int(height * 0.40))
            sub_font = ImageFont.truetype(font_path, int(height * 0.22)) if sub_text else None
        else:
            # 无可用字体（如精简 CI 环境）时回退默认字体
            main_font = ImageFont.load_default()
            sub_font = ImageFont.load_default() if sub_text else None

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
