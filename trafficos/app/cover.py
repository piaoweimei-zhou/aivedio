"""封面合成器（③ 包装层，B5）：背景 + 标题大字 + 角标 → 封面图。

用 Pillow 本地合成（快、批量、可控），不依赖 ComfyUI 重生成。
背景：可选 bg_url（下载+压暗）或深色渐变；标题：白色大字自动换行；角标：按风格映射。
"""
from __future__ import annotations

import io
import logging
import os
import time
import urllib.request
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

COVER_DIR_NAME = "covers"

# 封面尺寸（抖音竖版 9:16）
DEFAULT_SIZE = (1080, 1440)

# 角标颜色（按维度/风格氛围）
_BADGE_STYLES = {
    "干货": (41, 128, 185),       # 蓝
    "亲测可用": (231, 76, 60),    # 红
    "高能": (243, 156, 18),       # 橙
    "资源": (46, 204, 113),       # 绿
    "推荐": (155, 89, 182),       # 紫
}

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",   # 微软雅黑 Bold（Windows）
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def badge_from_style(cover_style: str) -> str:
    """把 cover_style 文本映射为角标词。"""
    style = cover_style or ""
    if any(k in style for k in ("干货", "清单", "编号")):
        return "干货"
    if any(k in style for k in ("亲测", "对比", "露出", "行动", "演示")):
        return "亲测可用"
    if any(k in style for k in ("悬念", "反差", "高能")):
        return "高能"
    if any(k in style for k in ("资源", "网盘", "免费")):
        return "资源"
    return "推荐"


def _make_background(size: Tuple[int, int], bg_url: Optional[str]) -> Image.Image:
    """背景：bg_url 下载压暗，否则深色渐变。"""
    img = Image.new("RGB", size, (18, 22, 40))
    if bg_url:
        try:
            with urllib.request.urlopen(bg_url, timeout=8) as resp:
                data = resp.read()
            bg = Image.open(io.BytesIO(data)).convert("RGB").resize(size, Image.LANCZOS)
            # 压暗 + 轻微高斯模糊，保证文字可读
            bg = bg.filter(ImageFilter.GaussianBlur(4))
            overlay = Image.new("RGB", size, (0, 0, 0))
            img = Image.blend(bg, overlay, alpha=0.45)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TrafficOS] 背景图加载失败，用渐变: %s", exc)
    else:
        # 垂直渐变：深蓝 → 黑
        top = (26, 32, 66)
        bottom = (8, 10, 20)
        for y in range(size[1]):
            t = y / size[1]
            color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
            draw = ImageDraw.Draw(img)
            draw.line([(0, y), (size[0], y)], fill=color)
    return img


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list:
    """按像素宽度自动换行。"""
    lines = []
    cur = ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_width:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _draw_badge(draw: ImageDraw.ImageDraw, badge: str, size: Tuple[int, int]) -> None:
    """顶部左侧角标。"""
    font = _load_font(48)
    color = _BADGE_STYLES.get(badge, (155, 89, 182))
    pad_x = 40
    tw = draw.textlength(badge, font=font)
    x0, y0 = 40, 60
    x1 = int(x0 + tw + pad_x * 2)
    y1 = int(y0 + 90)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=color)
    draw.text((x0 + pad_x, y0 + 18), badge, font=font, fill=(255, 255, 255))


def _draw_title(draw: ImageDraw.ImageDraw, title: str, size: Tuple[int, int]) -> None:
    """标题：白色大字，居中，自动换行。"""
    w, h = size
    font = _load_font(88)
    max_w = int(w * 0.86)
    lines = _wrap_text(draw, title, font, max_w)
    # 行数多则缩小
    while len(lines) > 4 and font.size > 44:
        font = _load_font(font.size - 8)
        lines = _wrap_text(draw, title, font, max_w)
    line_h = int(font.size * 1.4)
    total_h = line_h * len(lines)
    y = int(h * 0.42 - total_h / 2)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = (w - tw) / 2
        # 阴影提升可读性
        draw.text((x + 4, y + 4), ln, font=font, fill=(0, 0, 0))
        draw.text((x, y), ln, font=font, fill=(255, 255, 255))
        y += line_h


def render_cover(
    title: str,
    cover_style: str,
    bg_url: Optional[str] = None,
    output_dir: Optional[str] = None,
    size: Tuple[int, int] = DEFAULT_SIZE,
) -> Dict[str, object]:
    """合成封面。

    Returns:
        {"cover_id", "path", "url", "size", "badge", "title"}
    """
    data_dir = os.environ.get("TRAFFICOS_DATA_DIR", "")
    if not output_dir:
        base = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )
        output_dir = os.path.join(base, COVER_DIR_NAME)
    os.makedirs(output_dir, exist_ok=True)

    img = _make_background(size, bg_url)
    draw = ImageDraw.Draw(img)
    badge = badge_from_style(cover_style)
    if badge:
        _draw_badge(draw, badge, size)
    _draw_title(draw, title, size)

    cover_id = f"cover_{int(time.time() * 1000)}"
    filename = f"{cover_id}.jpg"
    path = os.path.join(output_dir, filename)
    img.save(path, "JPEG", quality=88)
    logger.info("[TrafficOS] 封面已合成: %s", path)
    return {
        "cover_id": cover_id,
        "path": path,
        "url": f"/api/traffic/cover/files/{filename}",
        "size": list(size),
        "badge": badge,
        "title": title,
    }
