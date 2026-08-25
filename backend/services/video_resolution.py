"""视频分辨率统一解析工具

解决 B2 问题：resolution / width / height / aspect_ratio 四重表述混乱。

统一规则：
- width/height（像素）：优先级最高，明确数值
- resolution（档位字符串）："480p"/"720p"/"1080p"，当 width/height 未提供时使用
- aspect_ratio（比例字符串）："16:9"/"9:16"/"1:1"，用于调整宽高方向

转换公式：
- 480p  → 854×480（16:9）
- 720p  → 1280×720（16:9）
- 1080p → 1920×1080（16:9）
"""

from typing import Optional, Tuple

# 分辨率档位 → 基础宽高（16:9 横向）
_RESOLUTION_MAP = {
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "1440p": (2560, 1440),
    "2k": (2560, 1440),
    "4k": (3840, 2160),
}

# 宽高比 → 是否交换宽高
_ASPECT_RATIO_VERTICAL = {"9:16", "9: 16", "portrait", "vertical"}
_ASPECT_RATIO_SQUARE = {"1:1", "square"}


def resolve_video_resolution(
    width: Optional[int] = None,
    height: Optional[int] = None,
    resolution: str = "480p",
    aspect_ratio: str = "16:9",
) -> Tuple[int, int]:
    """解析视频分辨率，返回 (width, height)

    优先级：
    1. width/height 显式提供 → 直接使用（按 aspect_ratio 调整方向）
    2. resolution 档位 → 查表得到基础宽高，再按 aspect_ratio 调整方向
    3. 默认 480p 16:9

    Args:
        width: 视频宽度（像素）
        height: 视频高度（像素）
        resolution: 分辨率档位（"480p"/"720p"/"1080p"）
        aspect_ratio: 宽高比（"16:9"/"9:16"/"1:1"）

    Returns:
        (width, height) 元组
    """
    # 优先使用显式 width/height
    if width and height and width > 0 and height > 0:
        w, h = int(width), int(height)
    else:
        # 从 resolution 档位推导基础宽高
        res_key = (resolution or "480p").lower().strip()
        base = _RESOLUTION_MAP.get(res_key, _RESOLUTION_MAP["480p"])
        w, h = base

    # 根据 aspect_ratio 调整方向
    ar = (aspect_ratio or "16:9").lower().strip()
    if ar in _ASPECT_RATIO_VERTICAL:
        # 竖屏：宽 < 高
        if w > h:
            w, h = h, w
    elif ar in _ASPECT_RATIO_SQUARE:
        # 正方形：取较小值
        side = min(w, h)
        w, h = side, side
    # 横屏（16:9 等）：保持原样

    return w, h


def resolution_to_string(width: int, height: int) -> str:
    """将宽高转换为分辨率档位字符串（用于日志/UI 显示）

    Args:
        width: 视频宽度
        height: 视频高度

    Returns:
        分辨率档位（如 "720p"），未知时返回 "{width}x{height}"
    """
    for res_str, (w, h) in _RESOLUTION_MAP.items():
        if (width, height) == (w, h) or (width, height) == (h, w):
            return res_str
    return f"{width}x{height}"
