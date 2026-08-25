"""
存量资产迁移脚本 — 把 data/generated 根目录的扁平文件整理到语义分层目录

用法：
    python tools/migrate_assets.py --dry-run          # 预览（默认）
    python tools/migrate_assets.py --apply            # 实际移动文件
    python tools/migrate_assets.py --apply --copy     # 复制而非移动（保留源文件）
    python tools/migrate_assets.py --generated-dir <path>

整理规则：按文件名前缀推断阶段，归入 global/{stage}/，重命名为语义名。
存量文件无项目归属信息，统一归入 global/。
"""

import argparse
import logging
import os
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.asset_organizer import organize_asset_files  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_assets")

# 文件名前缀 → (stage_id, content_keyword)
_PREFIX_RULES: List[Tuple[str, str, str]] = [
    ("nknown_storyboard_depth_", "storyboard", "depth"),
    ("nknown_storyboard_lineart_", "storyboard", "lineart"),
    ("nknown_storyboard_pose_", "storyboard", "pose"),
    ("nknown_storyboard_", "storyboard", ""),
    ("nknown_refine_", "refine", ""),
    ("panorama_final_", "pano", ""),
    ("minimax_h3_", "video", ""),
    ("mmh3_", "video", ""),
    ("h3_voice_check", "video", ""),
    ("multi_person_", "concept", "multi_person"),
    ("prop_", "concept", "prop"),
    ("MSR_verify_", "test", ""),
]


def infer_stage(fname: str) -> Tuple[str, str]:
    """根据文件名前缀推断 (stage_id, content_keyword)"""
    for prefix, stage, content in _PREFIX_RULES:
        if fname.startswith(prefix):
            return stage, content
    # 通用 ComfyUI 输出
    if fname.startswith("ComfyUI_"):
        if fname.lower().endswith((".flac", ".wav", ".m4a", ".mp3")):
            return "tts", ""
        return "concept", ""
    return "unknown", ""


def scan_flat_files(generated_dir: str) -> Dict[Tuple[str, str], List[str]]:
    """扫描根目录文件（不含子目录），按 (stage, content) 分组"""
    groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for fname in os.listdir(generated_dir):
        fpath = os.path.join(generated_dir, fname)
        if not os.path.isfile(fpath):
            continue
        stage, content = infer_stage(fname)
        groups[(stage, content)].append(fname)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description="存量资产迁移到语义分层目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览不移动（默认）")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认移动）")
    parser.add_argument("--copy", action="store_true", help="复制而非移动")
    parser.add_argument("--update-registry", action="store_true", help="迁移后同步更新资产注册表 URL")
    parser.add_argument(
        "--generated-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "generated"),
        help="data/generated 目录",
    )
    args = parser.parse_args()

    generated_dir = os.path.abspath(args.generated_dir)
    if not os.path.isdir(generated_dir):
        logger.error(f"目录不存在: {generated_dir}")
        return 1

    groups = scan_flat_files(generated_dir)
    total = sum(len(v) for v in groups.values())
    if total == 0:
        logger.info("根目录没有待整理文件")
        return 0

    logger.info(f"发现 {total} 个根目录文件，按 {len(groups)} 组整理")
    mode = "预览" if args.dry_run else ("复制" if args.copy else "移动")
    logger.info(f"执行模式: {mode}")

    for (stage, content), files in sorted(groups.items()):
        project = "global"
        logger.info(f"\n[{project}/{stage}] content={content or '-'} | {len(files)} 个文件")
        if args.dry_run:
            for f in sorted(files):
                logger.info(f"  -> {f}")
            continue
        organized, skipped = organize_asset_files(
            files,
            project_id=project,
            stage_id=stage,
            content_keyword=content,
            generated_dir=generated_dir,
            move=not args.copy,
        )
        logger.info(f"  整理 {len(organized)} 个，跳过 {len(skipped)} 个")
        for s in skipped:
            logger.warning(f"  跳过: {s}")

    if args.dry_run:
        logger.info("\n以上为预览结果，加 --apply 实际执行（默认移动，--copy 可复制）")
        return 0

    # 迁移后同步更新资产注册表，避免旧 URL 失效
    if args.update_registry:
        try:
            from tools.repair_asset_registry import main as repair_main
            logger.info("\n同步更新资产注册表 URL...")
            repair_main(["--apply", "--generated-dir", generated_dir])
        except Exception as e:
            logger.warning(f"注册表同步失败（可稍后手动运行 repair_asset_registry.py）: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
