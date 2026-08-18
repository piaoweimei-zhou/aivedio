"""
网感风格注册表 (StyleRegistry)

集中定义短视频「网感风格」预设，作为脚本生成、图像生成、前端风格选择器的
单一数据源。每个风格包含：
- script_guidance: 注入剧本生成 system prompt 的风格指引
- visual_prompt:   注入图像生成提示词的视觉风格关键词
- params:          关联的生成参数（steps/cfg/尺寸 等）

风格选择流程：
- 前端调用 GET /api/director/styles 获取风格列表
- 用户选择风格后，style_id 随阶段参数传递
- script/concept/storyboard 阶段通过 get_style() 查询并注入
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 网感风格定义
# ============================================================

_STYLES: Dict[str, Dict[str, Any]] = {
    "wanggan_vivid": {
        "style_id": "wanggan_vivid",
        "name": "高饱和网感",
        "category": "wanggan",
        "description": "短视频爆款风格：高饱和、高对比、画面冲击力强，节奏明快情绪饱满",
        "script_guidance": (
            "整体风格：高饱和网感爆款。节奏明快、情绪饱满、钩子前置（前3秒抓住注意力）；"
            "多用夸张修辞、网络热词、口语化表达；每幕都有情绪爆点，结尾钩子强转化。"
        ),
        "visual_prompt": (
            "high saturation, vibrant colors, high contrast, bright and punchy, "
            "short-video viral aesthetic, strong visual impact, crisp details"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1080, "height": 1920},
        "tags": ["网感", "爆款", "高饱和"],
    },
    "cinematic": {
        "style_id": "cinematic",
        "name": "电影感",
        "category": "wanggan",
        "description": "电影级质感：浅景深、胶片颗粒、暗部层次丰富，叙事节奏沉稳",
        "script_guidance": (
            "整体风格：电影感叙事。节奏沉稳有张力，镜头语言丰富，注重留白与情绪铺垫；"
            "对白精炼，场景描写有画面感，结尾钩子自然不突兀。"
        ),
        "visual_prompt": (
            "cinematic lighting, shallow depth of field, film grain, anamorphic widescreen, "
            "rich shadow detail, moody atmosphere, movie still quality"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1920, "height": 1080},
        "tags": ["电影感", "质感", "沉稳"],
    },
    "healing": {
        "style_id": "healing",
        "name": "治愈系",
        "category": "wanggan",
        "description": "温柔治愈：柔和光线、温暖色调、奶油质感，走心共鸣慢节奏",
        "script_guidance": (
            "整体风格：治愈系。温柔慢节奏，走心共鸣，情感细腻；"
            "多用生活化细节和暖心台词，避免尖锐冲突，结尾温暖有力量。"
        ),
        "visual_prompt": (
            "soft warm lighting, cozy atmosphere, creamy pastel tones, gentle and clean, "
            "healing vibe, dreamy soft focus, warm color grading"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1080, "height": 1350},
        "tags": ["治愈", "温暖", "慢节奏"],
    },
    "cyberpunk": {
        "style_id": "cyberpunk",
        "name": "赛博朋克",
        "category": "wanggan",
        "description": "未来科技感：霓虹灯、冷色调+洋红青绿对比、科技光效，反差反转",
        "script_guidance": (
            "整体风格：赛博朋克。未来感、科技感强，设定新颖；"
            "剧情有反差反转，节奏紧凑，结尾钩子带科技悬念。"
        ),
        "visual_prompt": (
            "cyberpunk, neon lights, futuristic city, magenta and cyan contrast, "
            "cold color palette, glowing tech effects, rain reflections, high-tech atmosphere"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1080, "height": 1920},
        "tags": ["赛博朋克", "未来", "科技"],
    },
    "retro_film": {
        "style_id": "retro_film",
        "name": "复古胶片",
        "category": "wanggan",
        "description": "怀旧年代感：复古胶片质感、颗粒感、暖黄色调，经典叙事",
        "script_guidance": (
            "整体风格：复古胶片。怀旧叙事，年代感强，可融入经典桥段；"
            "对白有时代气息，节奏舒缓，结尾带情怀升华。"
        ),
        "visual_prompt": (
            "retro film aesthetic, film grain, vintage warm yellow tones, "
            "old movie style, nostalgic atmosphere, analog photography look"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1080, "height": 1350},
        "tags": ["复古", "胶片", "怀旧"],
    },
    "fresh_japanese": {
        "style_id": "fresh_japanese",
        "name": "清新日系",
        "category": "wanggan",
        "description": "日系清新：柔和低饱和、自然光、留白构图，干净通透日常感",
        "script_guidance": (
            "整体风格：清新日系。轻快治愈，日常感强，画面感干净；"
            "台词自然不刻意，节奏轻松，结尾温暖收尾。"
        ),
        "visual_prompt": (
            "fresh Japanese style, soft low saturation, natural light, "
            "clean and airy, minimalist composition with negative space, "
            "bright and transparent, daily life aesthetic"
        ),
        "params": {"steps": 25, "cfg": 2.0, "width": 1080, "height": 1350},
        "tags": ["日系", "清新", "自然"],
    },
}

# 默认风格（未选择时使用）
_DEFAULT_STYLE_ID = "wanggan_vivid"


def list_styles() -> List[Dict[str, Any]]:
    """返回全部风格（含默认标记）"""
    return [
        {**style, "is_default": sid == _DEFAULT_STYLE_ID}
        for sid, style in _STYLES.items()
    ]


def get_style(style_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """按 style_id 查询风格，不存在返回 None"""
    if not style_id:
        return None
    return _STYLES.get(style_id)


def get_style_or_default(style_id: Optional[str]) -> Dict[str, Any]:
    """查询风格，未指定或不存在时回退到默认风格"""
    style = get_style(style_id)
    if style:
        return style
    return _STYLES[_DEFAULT_STYLE_ID]


def get_script_guidance(style_id: Optional[str]) -> str:
    """获取风格的脚本指引文本（用于注入剧本 system prompt）"""
    return get_style_or_default(style_id).get("script_guidance", "")


def get_visual_prompt(style_id: Optional[str]) -> str:
    """获取风格的视觉提示词（用于追加到图像生成 prompt）"""
    return get_style_or_default(style_id).get("visual_prompt", "")


def get_style_params(style_id: Optional[str]) -> Dict[str, Any]:
    """获取风格的生成参数（steps/cfg/尺寸 等）"""
    return dict(get_style_or_default(style_id).get("params", {}))
