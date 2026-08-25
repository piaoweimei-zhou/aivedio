"""
资产文件整理器 — 统一 data/generated 目录结构与语义命名

目标：所有生成资产（ComfyUI 出图/出视频 + 后处理导出）统一落到
    data/generated/{project_id}/{stage_id}/{project_id}_{stage_id}[_{content}]_{seq:03d}_{hash6}.{ext}

语义文件名 = 项目 + 阶段 + 内容关键词 + 序号 + 短哈希，便于按项目/阶段浏览、按文件名检索。
"""

import hashlib
import logging
import os
import re
import shutil
import uuid
from typing import List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# 非法文件名字符（Windows 保留字符 + 空白）
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\s]+')
# 语义关键词长度上限
_KEYWORD_MAX = 12
# 无项目归属时的兜底目录名
FALLBACK_PROJECT = "global"


def sanitize_keyword(text: str, fallback: str = "asset") -> str:
    """把任意文本清洗成文件名安全的关键词"""
    if not text:
        return fallback
    cleaned = _INVALID_CHARS.sub("_", str(text).strip()).strip("_")
    if not cleaned or cleaned == "unknown":
        return fallback
    return cleaned[:_KEYWORD_MAX]


def short_hash(seed: str = "") -> str:
    """生成 6 位短哈希（用于防冲突）"""
    if seed:
        return hashlib.md5(seed.encode("utf-8")).hexdigest()[:6]
    return uuid.uuid4().hex[:6]


def build_asset_filename(
    project_id: str,
    stage_id: str,
    content_keyword: str = "",
    seq: int = 1,
    ext: str = ".png",
    hash6: str = "",
) -> str:
    """构建语义文件名：{project}_{stage}[_{content}]_{seq:03d}_{hash6}.{ext}"""
    project = sanitize_keyword(project_id, FALLBACK_PROJECT)
    stage = sanitize_keyword(stage_id, "asset")
    parts = [project, stage]
    content = sanitize_keyword(content_keyword, "") if content_keyword else ""
    if content:
        parts.append(content)
    parts.append(f"{int(seq):03d}")
    parts.append(hash6 or short_hash())
    return "_".join(parts) + ext


def build_asset_rel_path(
    project_id: str,
    stage_id: str,
    content_keyword: str = "",
    seq: int = 1,
    ext: str = ".png",
    hash6: str = "",
) -> str:
    """构建相对路径：{project}/{stage}/{filename}"""
    project = sanitize_keyword(project_id, FALLBACK_PROJECT)
    stage = sanitize_keyword(stage_id, "asset")
    fname = build_asset_filename(project_id, stage_id, content_keyword, seq, ext, hash6)
    return f"{project}/{stage}/{fname}"


def next_seq(generated_dir: str, project_id: str, stage_id: str, content_keyword: str = "") -> int:
    """计算目标目录下一个可用序号（扫描现有文件的最大序号 + 1）"""
    project = sanitize_keyword(project_id, FALLBACK_PROJECT)
    stage = sanitize_keyword(stage_id, "asset")
    target_dir = os.path.join(generated_dir, project, stage)
    max_seq = 0
    if os.path.isdir(target_dir):
        prefix = f"{project}_{stage}"
        content = sanitize_keyword(content_keyword, "") if content_keyword else ""
        if content:
            prefix += f"_{content}"
        for fname in os.listdir(target_dir):
            if not fname.startswith(prefix):
                continue
            # 提取 {seq:03d} 段
            rest = fname[len(prefix):]
            m = re.match(r"_(\d{3})_", rest)
            if m:
                try:
                    max_seq = max(max_seq, int(m.group(1)))
                except ValueError:
                    pass
    return max_seq + 1


def _extract_filename(url: str) -> str:
    """从 URL 提取文件名（支持 /api/comfyui/image?filename=X 与 /output/.../X）"""
    if not url:
        return ""
    if "filename=" in url:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        return params.get("filename", [""])[0]
    return os.path.basename(url.split("?")[0])


def _resolve_source(filename: str, generated_dir: str, comfyui_output_dir: str = "") -> Optional[str]:
    """在持久化目录（含子目录）与 ComfyUI output 中定位源文件"""
    if not filename:
        return None
    safe = os.path.basename(filename)
    # 持久化目录：先扁平，再递归子目录
    if generated_dir:
        flat = os.path.join(generated_dir, safe)
        if os.path.isfile(flat):
            return flat
        for root, _dirs, files in os.walk(generated_dir):
            if safe in files:
                return os.path.join(root, safe)
    if comfyui_output_dir:
        cand = os.path.join(comfyui_output_dir, safe)
        if os.path.isfile(cand):
            return cand
    return None


def organize_asset_files(
    urls_or_filenames: List[str],
    project_id: str,
    stage_id: str,
    content_keyword: str = "",
    generated_dir: str = "",
    comfyui_output_dir: str = "",
    output_dir: str = "",
    move: bool = False,
) -> Tuple[List[str], List[str]]:
    """把生成资产整理到 data/generated/{project}/{stage}/ 语义目录

    Args:
        urls_or_filenames: 源 URL 或文件名列表（ComfyUI 输出名 / /output/ 路径）
        project_id: 项目 ID（空则归入 global）
        stage_id: 阶段 ID（concept/video/export...）
        content_keyword: 内容关键词（character/scene/prop 或语义名）
        generated_dir: data/generated 目录
        comfyui_output_dir: ComfyUI output 目录
        output_dir: 后端 output 目录（/output/... 源文件所在）
        move: True 时移动源文件（迁移用），False 复制（运行时整理用）

    Returns:
        (organized_urls, skipped): 整理后的 URL 列表 + 未能整理的源列表
    """
    from services.comfyui_helpers import GENERATED_DIR as _DEF_GEN
    from services.comfyui.config import COMFYUI_DIR as _DEF_COMFYUI

    generated_dir = generated_dir or _DEF_GEN
    comfyui_output_dir = comfyui_output_dir or (
        os.path.join(_DEF_COMFYUI, "output") if _DEF_COMFYUI else ""
    )
    output_dir = output_dir or os.path.join(os.path.dirname(__file__), "..", "output")

    project = sanitize_keyword(project_id, FALLBACK_PROJECT)
    stage = sanitize_keyword(stage_id, "asset")
    content = sanitize_keyword(content_keyword, "") if content_keyword else ""
    target_dir = os.path.join(generated_dir, project, stage)
    os.makedirs(target_dir, exist_ok=True)

    organized: List[str] = []
    skipped: List[str] = []
    seq = next_seq(generated_dir, project_id, stage_id, content_keyword)

    for item in urls_or_filenames:
        if not item:
            continue
        fname = _extract_filename(item)
        if not fname:
            skipped.append(item)
            continue
        ext = os.path.splitext(fname)[1].lower() or ".png"

        # 定位源文件
        src = _resolve_source(fname, generated_dir, comfyui_output_dir)
        if not src and item.startswith("/output/"):
            # 后处理产物在 backend/output/{category}/ 下
            rel = item[len("/output/"):]
            cand = os.path.join(output_dir, rel)
            if os.path.isfile(cand):
                src = cand
        if not src:
            skipped.append(item)
            continue

        # 目标语义文件名
        hash6 = short_hash(fname)
        target_name = build_asset_filename(project_id, stage_id, content_keyword, seq, ext, hash6)
        dst = os.path.join(target_dir, target_name)
        try:
            if os.path.abspath(src) != os.path.abspath(dst):
                if move:
                    shutil.move(src, dst)
                else:
                    shutil.copy2(src, dst)
            organized.append(f"/api/comfyui/image?filename={target_name}&subfolder={project}/{stage}")
            seq += 1
        except OSError as e:
            logger.warning(f"[AssetOrganizer] 整理失败 | {fname} -> {dst} | {e}")
            skipped.append(item)

    if organized:
        logger.info(
            f"[AssetOrganizer] 整理 {len(organized)} 个文件 | "
            f"{project}/{stage} | content={content or '-'} | skipped={len(skipped)}"
        )
    return organized, skipped
