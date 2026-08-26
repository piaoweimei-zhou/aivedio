"""发布包生成器（④ 发布层，B6 半自动路径）：视频 + 封面 + 标题 + 文案 + 清单。

半自动发布：生成标准"发布包"，用户手动发到抖音；权限下来后同包可切全自动。
发布包结构：
    {data}/publish_packages/{package_id}/
        video.mp4          # 拷贝的成片
        cover.jpg          # 合成封面
        title.txt          # 标题（建议首行）
        caption.txt        # 完整文案（标题+钩子+话题）
        manifest.json      # 机器可读清单（供自动发布/ROI 回传使用）
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import Dict, List, Optional

from app.cover import render_cover
from app.models import Dimension, Monetizer
from app.packaging import generate_packaging

logger = logging.getLogger(__name__)

PKG_DIR_NAME = "publish_packages"

# 维度 → 默认话题
_DIM_HASHTAGS: Dict[str, List[str]] = {
    Dimension.PURE_CONTENT: ["#涨知识", "#今日分享", "#有趣"],
    Dimension.KNOWLEDGE: ["#知识分享", "#干货", "#学习方法"],
    Dimension.SOFT_AD: ["#效率工具", "#神器推荐", "#亲测好用"],
}
_MONETIZER_HASHTAGS: Dict[str, List[str]] = {
    Monetizer.TOOL: ["#去水印工具", "#实用工具"],
    Monetizer.COURSE: ["#副业", "#技能提升"],
    Monetizer.RESOURCE: ["#免费资源", "#资源分享"],
    Monetizer.ADSHARE: ["#副业变现", "#广告分成"],
}

# 平台白名单（P1c 多平台）
PLATFORMS = ("douyin", "kuaishou", "bilibili", "xiaohongshu")
DEFAULT_PLATFORM = "douyin"

# 各平台默认话题（差异化）
_PLATFORM_HASHTAGS: Dict[str, List[str]] = {
    "douyin": ["#抖音热门", "#上热门"],
    "kuaishou": ["#快手热榜", "#老铁"],
    "bilibili": ["#B站", "#干货"],
    "xiaohongshu": ["#小红书", "#种草", "#好物分享"],
}

# 各平台发布注意（写入 manifest.platform_notes，指导手动发布）
_PLATFORM_NOTES: Dict[str, str] = {
    "douyin": "竖版 9:16 优先；标题 ≤55 字；建议挂锚点/合集（需企业资质）",
    "kuaishou": "竖版 9:16 优先；标题 ≤55 字；可加 #老铁 拉互动",
    "bilibili": "横版 16:9 优先；标题 ≤40 字；建议加分区标签与话题",
    "xiaohongshu": "竖版 3:4/9:16 均可；标题 ≤20 字 + 正文种草风；emoji 辅助排版",
}


def _platform_hashtags(platform: str, tags: List[str]) -> List[str]:
    """平台话题 = 平台默认 + 内容话题，去重后保留前 8。"""
    merged = list(_PLATFORM_HASHTAGS.get(platform, []))
    for t in tags:
        if t not in merged:
            merged.append(t)
    return merged[:8]


def _pkg_root(data_dir: str) -> str:
    base = data_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    return os.path.join(base, PKG_DIR_NAME)


def build_publish_package(
    title: str,
    video_path: str,
    dimension: Optional[Dimension] = None,
    monetizer: Optional[Monetizer] = None,
    cover_style: str = "",
    account_id: str = "",
    topic_id: str = "",
    content_id: str = "",
    platform: str = DEFAULT_PLATFORM,
    data_dir: Optional[str] = None,
) -> Dict[str, object]:
    """生成发布包。

    Args:
        title: 内容标题
        video_path: 成片视频路径（必须存在）
        dimension/monetizer: 决定话题与包装模板
        cover_style: 封面风格（可空，用 packaging 兜底）
        account_id/topic_id/content_id: 归因信息（写入 manifest）
        platform: 目标平台（douyin/kuaishou/bilibili/xiaohongshu），决定话题与发布注意

    Returns:
        {"package_id", "title", "caption", "cover_path", "video_path",
         "manifest_path", "files": [...], "size_bytes", "platform"}
    """
    if platform not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform} (支持: {PLATFORMS})")

    video_path = os.path.abspath(video_path)
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"video not found: {video_path}")

    # 包装：标题候选 + 钩子 + 封面风格
    pkg = generate_packaging(title, dimension or Dimension.PURE_CONTENT,
                             monetizer or Monetizer.ADSHARE)
    main_title = pkg["titles"][0] if pkg["titles"] else title
    hook = pkg["hooks"][0] if pkg.get("hooks") else ""
    style = cover_style or pkg["cover_style"]

    # 封面
    data_dir = data_dir or os.environ.get("TRAFFICOS_DATA_DIR", "")
    pkg_root = _pkg_root(data_dir)
    package_id = f"pkg_{int(time.time() * 1000)}"
    pkg_dir = os.path.join(pkg_root, package_id)
    os.makedirs(pkg_dir, exist_ok=True)

    cover_res = render_cover(main_title, style, output_dir=pkg_dir)
    cover_path = os.path.join(pkg_dir, "cover.jpg")
    if os.path.abspath(cover_res["path"]) != os.path.abspath(cover_path):
        shutil.copy2(cover_res["path"], cover_path)
        os.remove(cover_res["path"])

    # 视频拷贝
    ext = os.path.splitext(video_path)[1] or ".mp4"
    pkg_video = os.path.join(pkg_dir, f"video{ext}")
    shutil.copy2(video_path, pkg_video)

    # 标题 + 文案（小红书追加种草引导）
    hashtags = _hashtags(platform, dimension, monetizer)
    caption = main_title
    if hook:
        caption = f"{main_title}\n\n{hook}"
    if platform == "xiaohongshu" and main_title:
        caption = f"{main_title}｜实测分享\n\n{hook}" if hook else f"{main_title}｜实测分享"
    if hashtags:
        caption = f"{caption}\n\n{' '.join(hashtags)}"
    _write_utf8(os.path.join(pkg_dir, "title.txt"), main_title)
    _write_utf8(os.path.join(pkg_dir, "caption.txt"), caption)

    # 清单
    manifest = {
        "package_id": package_id,
        "title": main_title,
        "caption": caption,
        "hashtags": hashtags,
        "dimension": dimension.value if dimension else None,
        "monetizer": monetizer.value if monetizer else None,
        "account_id": account_id,
        "topic_id": topic_id,
        "content_id": content_id,
        "platform": platform,
        "platform_notes": _PLATFORM_NOTES.get(platform, ""),
        "video": os.path.basename(pkg_video),
        "cover": "cover.jpg",
        "video_source": video_path,
        "mode": "semi_auto",
        "created_at": time.time(),
    }
    manifest_path = os.path.join(pkg_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    size = sum(os.path.getsize(os.path.join(pkg_dir, fn))
               for fn in os.listdir(pkg_dir) if os.path.isfile(os.path.join(pkg_dir, fn)))
    logger.info("[TrafficOS] 发布包已生成(%s): %s", platform, pkg_dir)
    return {
        "package_id": package_id,
        "title": main_title,
        "caption": caption,
        "cover_path": cover_path,
        "video_path": pkg_video,
        "manifest_path": manifest_path,
        "files": sorted(os.listdir(pkg_dir)),
        "size_bytes": size,
        "platform": platform,
    }


def _hashtags(
    platform: str,
    dimension: Optional[Dimension],
    monetizer: Optional[Monetizer],
) -> List[str]:
    """话题 = 平台默认 + 维度 + 变现，去重后保留前 8。"""
    tags = list(_DIM_HASHTAGS.get(dimension, []))
    tags += list(_MONETIZER_HASHTAGS.get(monetizer, []))
    return _platform_hashtags(platform, tags)


def _write_utf8(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
