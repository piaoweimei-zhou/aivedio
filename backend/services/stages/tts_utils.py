"""
TTS 多角色配音工具

支持：
  - 从 script.characters 解析角色→音色映射
  - 自动按角色名/性别/年龄推断默认音色描述
  - 支持用户在 params.voice_map 中显式指定角色→音色
  - 对每个 tts_text 段标注归属角色（启发式：台词前缀含 "角色名："）
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 默认音色库（按性别/年龄段）
# ============================================================
_DEFAULT_VOICES = {
    # 男声
    "male_young": "年轻男声，25岁，清朗有活力",
    "male_middle": "中年男声，40岁，沉稳磁性",
    "male_old": "老年男声，65岁，沙哑苍劲",
    "male_child": "男童声，8岁，稚嫩活泼",
    # 女声
    "female_young": "年轻女声，25岁，清脆甜美",
    "female_middle": "中年女声，40岁，温柔知性",
    "female_old": "老年女声，65岁，慈祥缓慢",
    "female_child": "女童声，8岁，天真可爱",
    # 旁白
    "narrator_male": "男声旁白，专业播音腔",
    "narrator_female": "女声旁白，专业播音腔",
}

# 默认音色轮转池（角色未指定音色时按顺序分配）
_DEFAULT_VOICE_POOL = [
    "male_young",
    "female_young",
    "male_middle",
    "female_middle",
    "male_old",
    "female_old",
]


def build_voice_map(
    characters: List[Dict[str, str]],
    user_voice_map: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """构建 角色名 → 音色描述 的映射

    Args:
        characters: [{"name": "皇帝", "desc": "...", "role": "..."}]
        user_voice_map: 用户显式指定的 {"皇帝": "老年男声，威严"} 或
                        {"皇帝": "male_old"} （使用预设 key）

    Returns:
        {"皇帝": "老年男声，65岁，沙哑苍劲"}
    """
    user_voice_map = user_voice_map or {}
    voice_map: Dict[str, str] = {}
    pool_idx = 0

    for char in characters:
        name = char.get("name", "").strip()
        if not name:
            continue

        # 1. 用户显式指定优先
        if name in user_voice_map:
            v = user_voice_map[name]
            # 如果是预设 key（如 male_old），替换为完整描述
            if v in _DEFAULT_VOICES:
                voice_map[name] = _DEFAULT_VOICES[v]
            else:
                voice_map[name] = v
            continue

        # 2. 从角色描述推断（启发式关键词匹配）
        desc = (char.get("desc", "") + " " + char.get("role", "")).lower()
        inferred = _infer_voice_from_desc(desc)
        if inferred:
            voice_map[name] = inferred
            continue

        # 3. 默认轮转池
        pool_key = _DEFAULT_VOICE_POOL[pool_idx % len(_DEFAULT_VOICE_POOL)]
        voice_map[name] = _DEFAULT_VOICES[pool_key]
        pool_idx += 1

    # 旁白默认
    if "旁白" not in voice_map and "narrator" not in voice_map:
        voice_map["旁白"] = _DEFAULT_VOICES["narrator_male"]
        voice_map["narrator"] = _DEFAULT_VOICES["narrator_male"]

    logger.info(
        f"[TTS] voice_map 构建完成 | characters={len(voice_map)} | {list(voice_map.keys())}"
    )
    return voice_map


def _infer_voice_from_desc(desc: str) -> Optional[str]:
    """从角色描述关键词推断音色"""
    if not desc:
        return None

    # 性别
    is_female = any(
        k in desc for k in ["女", "母", "娘", "姐", "妹", "婆", "妻", "皇后", "公主", "妃"]
    )
    is_male = any(k in desc for k in ["男", "公", "爹", "哥", "弟", "爷", "夫", "帝", "王", "将"])

    # 年龄
    is_old = any(k in desc for k in ["老", "爷", "奶", "翁", "65", "60", "70", "古稀"])
    is_child = any(k in desc for k in ["童", "小", "孩", "8", "10", "幼", "娃"])
    is_middle = any(k in desc for k in ["中年", "40", "45", "成", "熟"])

    if is_child:
        return _DEFAULT_VOICES["female_child" if is_female else "male_child"]
    if is_old:
        return _DEFAULT_VOICES["female_old" if is_female else "male_old"]
    if is_middle:
        return _DEFAULT_VOICES["female_middle" if is_female else "male_middle"]
    if is_female:
        return _DEFAULT_VOICES["female_young"]
    if is_male:
        return _DEFAULT_VOICES["male_young"]

    return None


def detect_speaker(text: str, voice_map: Dict[str, str]) -> Optional[str]:
    """从台词文本中检测说话角色

    支持格式：
      - "皇帝：你说什么？"
      - "皇帝: 你说什么？"
      - "【皇帝】你说什么？"

    Returns:
        匹配到的角色名，否则 None
    """
    if not text or not voice_map:
        return None

    # 常见前缀格式
    patterns = [
        r"^【([^】]+)】",
        r"^\[([^\]]+)\]",
        r"^《([^》]+)》",
        r"^([^:：]{1,10})[：:]",  # 角色名： 台词
    ]
    for pattern in patterns:
        m = re.match(pattern, text.strip())
        if m:
            speaker = m.group(1).strip()
            # 精确匹配
            if speaker in voice_map:
                return speaker
            # 模糊匹配（包含）
            for name in voice_map.keys():
                if speaker in name or name in speaker:
                    return name
    return None


def get_voice_for_text(
    text: str,
    voice_map: Dict[str, str],
    default_voice: str = "",
) -> str:
    """根据文本获取对应的音色描述

    Args:
        text: 台词文本
        voice_map: 角色名 → 音色描述
        default_voice: 默认音色（未匹配到角色时）

    Returns:
        音色描述字符串
    """
    speaker = detect_speaker(text, voice_map)
    if speaker and speaker in voice_map:
        return voice_map[speaker]
    return default_voice or _DEFAULT_VOICES["narrator_male"]


def strip_speaker_prefix(text: str) -> str:
    """去除台词中的角色名前缀

    "皇帝：你说什么？" → "你说什么？"
    """
    if not text:
        return text
    patterns = [
        r"^【[^】]+】\s*",
        r"^\[[^\]]+\]\s*",
        r"^《[^》]+》\s*",
        r"^[^:：]{1,10}[：:]\s*",
    ]
    result = text.strip()
    for pattern in patterns:
        result = re.sub(pattern, "", result).strip()
    return result


def list_default_voices() -> List[Dict[str, str]]:
    """返回默认音色库（供前端 UI 展示）"""
    return [{"key": k, "desc": v} for k, v in _DEFAULT_VOICES.items()]
