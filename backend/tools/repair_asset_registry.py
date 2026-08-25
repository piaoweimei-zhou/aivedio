"""
资产注册表修复脚本 — 迁移后同步更新 asset_registry.json 中的失效 URL

背景：存量迁移把文件移动+重命名为语义名（如 prop_xxx → global_concept_prop_001_xxx.png），
注册表中旧 URL（filename=旧名）指向的文件已不存在。本脚本通过短哈希反推定位新文件并更新 URL。

用法：
    python tools/repair_asset_registry.py --dry-run   # 预览（默认）
    python tools/repair_asset_registry.py --apply     # 实际更新注册表
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("repair_asset_registry")

REGISTRY = os.path.join(os.path.dirname(__file__), "..", "assets", "asset_registry.json")
GENERATED = os.path.join(os.path.dirname(__file__), "..", "data", "generated")


def find_file_recursive(fname: str) -> str:
    """在 generated 目录递归查找文件，返回相对路径（含子目录）或空串"""
    for root, _dirs, files in os.walk(GENERATED):
        if fname in files:
            return os.path.relpath(os.path.join(root, fname), GENERATED).replace("\\", "/")
    return ""


def find_by_hash(hash6: str, ext: str) -> str:
    """按短哈希 + 扩展名在 generated 目录查找新文件，返回相对路径或空串"""
    for root, _dirs, files in os.walk(GENERATED):
        for f in files:
            if f.endswith(ext) and f"_{hash6}{ext}" in f:
                return os.path.relpath(os.path.join(root, f), GENERATED).replace("\\", "/")
    return ""


def repair_url(url: str) -> tuple:
    """修复单个 URL，返回 (新URL, 状态)。状态: ok/skip/fixed/missing"""
    if "filename=" not in url:
        return url, "skip"
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    fname = params.get("filename", [""])[0]
    if not fname:
        return url, "skip"
    # 已带 subfolder 的新格式：检查文件是否真实存在
    sub = params.get("subfolder", [""])[0]
    if sub:
        rel = os.path.join(sub, fname).replace("\\", "/")
        if os.path.isfile(os.path.join(GENERATED, rel)):
            return url, "ok"
        # 新格式但文件缺失 → 尝试 hash 反推
    # 检查旧文件名是否仍存在（递归）
    found = find_file_recursive(fname)
    if found:
        return f"/api/comfyui/image?filename={fname}&subfolder={os.path.dirname(found)}", "ok"
    # 旧文件名不存在 → hash 反推
    ext = os.path.splitext(fname)[1].lower()
    hash6 = hashlib.md5(fname.encode("utf-8")).hexdigest()[:6]
    new_rel = find_by_hash(hash6, ext)
    if new_rel:
        new_name = os.path.basename(new_rel)
        new_sub = os.path.dirname(new_rel)
        return f"/api/comfyui/image?filename={new_name}&subfolder={new_sub}", "fixed"
    return url, "missing"


def main(argv: list = None) -> int:
    global GENERATED
    parser = argparse.ArgumentParser(description="修复迁移后资产注册表中的失效 URL")
    parser.add_argument("--dry-run", action="store_true", help="只预览不写回（默认）")
    parser.add_argument("--apply", action="store_true", help="实际写回注册表")
    parser.add_argument("--registry", default=REGISTRY)
    parser.add_argument("--generated-dir", default=GENERATED)
    args = parser.parse_args(argv)

    GENERATED = os.path.abspath(args.generated_dir)

    if not os.path.isfile(args.registry):
        logger.error(f"注册表不存在: {args.registry}")
        return 1
    if not os.path.isdir(GENERATED):
        logger.error(f"generated 目录不存在: {GENERATED}")
        return 1

    with open(args.registry, "r", encoding="utf-8") as f:
        data = json.load(f)

    assets = data.get("assets", data) if isinstance(data, dict) else data
    items = list(assets.values()) if isinstance(assets, dict) else assets

    stats = {"ok": 0, "skip": 0, "fixed": 0, "missing": 0}
    fixed_urls = []

    for a in items:
        urls = a.get("urls") or []
        changed = False
        for i, u in enumerate(urls):
            new_u, status = repair_url(u)
            stats[status] += 1
            if status == "fixed":
                fixed_urls.append((u, new_u))
                urls[i] = new_u
                changed = True
            elif status == "missing":
                logger.warning(f"  无法修复: {u}")
        if changed:
            a["urls"] = urls

    logger.info(f"统计: {stats}")
    if args.dry_run:
        logger.info(f"预览修复 {len(fixed_urls)} 条 URL（加 --apply 写回）:")
        for old, new in fixed_urls[:20]:
            logger.info(f"  {old}\n    -> {new}")
        if len(fixed_urls) > 20:
            logger.info(f"  ... 等共 {len(fixed_urls)} 条")
    else:
        with open(args.registry, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"已写回注册表，修复 {len(fixed_urls)} 条 URL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
