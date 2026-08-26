"""包装生成器（③ 包装层，B4）：标题/钩子/封面风格。

按 维度 × 变现目标 三轴生成（规划 v1.4 §6.4）。
- 优先使用用户配置模板（PackagingTemplate 集合，可 CRUD）
- 无配置时用内置默认模板库兜底
- 模板以 {topic} 占位，填充选题标题（自动截断/提炼）
"""
from __future__ import annotations

from typing import Dict, List

from app.models import Dimension, Monetizer, PackagingTemplate
from app.storage import get_collection

# ==================== 内置默认模板库 ====================

# 结构: (dimension, monetizer) -> {titles[], hooks[], cover_style}
_DEFAULT_TEMPLATES: Dict[tuple, Dict[str, List[str]]] = {
    # --- 纯内容：拉流量/涨粉 ---
    (Dimension.PURE_CONTENT, Monetizer.ADSHARE): {
        "titles": [
            "{topic}，看完我直接愣住",
            "3 分钟看懂 {topic}，第 3 秒开始高能",
            "{topic}的真实画面，和你想的不一样",
        ],
        "hooks": ["别划走，这个你绝对没见过", "看完第 3 秒，你也会沉默"],
        "cover_style": "大字悬念 + 高反差对比图",
    },
    (Dimension.PURE_CONTENT, Monetizer.RESOURCE): {
        "titles": ["{topic}合集，转存就送", "{topic}全套打包，评论区领"],
        "hooks": ["这套 {topic}，我整理了一周"],
        "cover_style": "资源堆叠展示 + 箭头指向评论区",
    },
    (Dimension.PURE_CONTENT, Monetizer.NETDISK): {
        "titles": ["{topic}资源网盘版，手慢无", "网盘里存了 {topic}，懂的都懂"],
        "hooks": ["资源已放网盘，评论区自取"],
        "cover_style": "网盘图标 + 紧迫感倒计时",
    },
    # --- 知识讲解：建信任/立人设 ---
    (Dimension.KNOWLEDGE, Monetizer.COURSE): {
        "titles": [
            "零基础学会 {topic}，别再走弯路",
            "{topic} 避坑指南（收藏级）",
            "90% 的人都搞错了 {topic}",
        ],
        "hooks": ["这 3 个坑，90% 新手都踩过", "今天一次讲清 {topic}"],
        "cover_style": "干货标题大字 + 编号清单列表",
    },
    (Dimension.KNOWLEDGE, Monetizer.RESOURCE): {
        "titles": ["{topic} 全套资料，转存就送", "这份 {topic} 资料，够你用一年"],
        "hooks": ["资料整理不易，点个关注持续更新"],
        "cover_style": "资料封面 + '免费领取'标签",
    },
    (Dimension.KNOWLEDGE, Monetizer.TOOL): {
        "titles": ["{topic} 用什么工具？这 3 个够用", "{topic} 效率工具实测对比"],
        "hooks": ["工具选对，效率翻倍"],
        "cover_style": "工具对比图 + 打分标注",
    },
    # --- 产品软广：直接转化 ---
    (Dimension.SOFT_AD, Monetizer.TOOL): {
        "titles": [
            "3 秒解决 {topic}，亲测可用",
            "还在手动 {topic}？这个工具直接解放你",
            "{topic} 神器实测，效果立竿见影",
        ],
        "hooks": ["以前要半小时，现在 3 秒", "真实演示，不吹不黑"],
        "cover_style": "前后对比图 + 工具名大字 + '亲测可用'角标",
    },
    (Dimension.SOFT_AD, Monetizer.SAAS): {
        "titles": ["企业都在用的 {topic} 方案", "{topic} 系统化，效率翻倍的秘密"],
        "hooks": ["别再手工操作了，上系统", "这套方案我们跑了半年"],
        "cover_style": "商务感排版 + 数据提升对比",
    },
    (Dimension.SOFT_AD, Monetizer.COURSE): {
        "titles": ["靠 {topic} 变现，学员真实案例", "{topic} 变现课，从 0 到 1"],
        "hooks": ["学员真实反馈，不是割韭菜", "这条路我走通了，教你避坑"],
        "cover_style": "学员成果截图 + 课程名大字",
    },
    (Dimension.SOFT_AD, Monetizer.XIANYU): {
        "titles": ["{topic} 闲鱼实操，日入小目标", "闲鱼卖 {topic}，新手也能上手"],
        "hooks": ["实操截图，真实收益"],
        "cover_style": "收益截图 + 实操步骤",
    },
}

# 兜底（任意维度×变现未配置时）
_GENERIC_TITLES = ["{topic}，这个思路值得一看", "关于 {topic}，一次讲清楚"]
_GENERIC_HOOKS = ["先别划走，看完再决定", "这条值得收藏"]
_GENERIC_COVER = "简洁大字标题 + 主视觉图"

_COVER_STYLES: Dict[str, str] = {
    Dimension.PURE_CONTENT: "高冲击悬念风格",
    Dimension.KNOWLEDGE: "干货清单风格",
    Dimension.SOFT_AD: "产品露出+行动号召风格",
}


def _shorten_title(title: str, max_len: int = 12) -> str:
    """标题过长时截断（保留完整词感，按字符截断）。"""
    title = title.strip()
    if len(title) <= max_len:
        return title
    return title[: max_len - 1].rstrip("，。！？、 ") + "…"


def _resolve_templates(
    dimension: Dimension,
    monetizer: Monetizer,
) -> Dict[str, object]:
    """解析模板：用户配置优先，内置默认兜底。返回带 source 标记。"""
    col = get_collection("packaging")
    for rec in col.list():
        if rec.get("dimension") == dimension.value and rec.get("monetizer") == monetizer.value:
            t = PackagingTemplate(**rec)
            return {
                "titles": t.title_templates or _GENERIC_TITLES,
                "hooks": t.hook_templates or _GENERIC_HOOKS,
                "cover_style": t.cover_style or _COVER_STYLES.get(dimension, _GENERIC_COVER),
                "source": "custom",
            }
    found = _DEFAULT_TEMPLATES.get((dimension, monetizer))
    if found:
        return {**found, "source": "default"}
    return {
        "titles": _GENERIC_TITLES,
        "hooks": _GENERIC_HOOKS,
        "cover_style": _COVER_STYLES.get(dimension, _GENERIC_COVER),
        "source": "generic",
    }


def generate_packaging(
    topic_title: str,
    dimension: Dimension,
    monetizer: Monetizer,
    max_titles: int = 5,
) -> Dict[str, object]:
    """生成包装（标题候选 + 钩子 + 封面风格）。

    Returns:
        {"titles": [...], "hooks": [...], "cover_style": str, "applied_templates": str}
    """
    tpl = _resolve_templates(dimension, monetizer)
    topic = _shorten_title(topic_title)
    titles = [t.replace("{topic}", topic) for t in tpl["titles"]][:max_titles]
    hooks = [h.replace("{topic}", topic) for h in tpl.get("hooks", [])]
    return {
        "titles": titles,
        "hooks": hooks,
        "cover_style": tpl["cover_style"],
        "applied_templates": tpl["source"],
    }


def list_templates() -> List[PackagingTemplate]:
    return [PackagingTemplate(**r) for r in get_collection("packaging").list()]
