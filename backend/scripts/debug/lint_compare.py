# -*- coding: utf-8 -*-
"""精确对比：只统计我改的文件的 lint 净变化（路径标准化）"""
import re
import collections

CHANGED = {
    "services/paths.py", "services/providers/provider_utils.py",
    "services/comfyui_helpers.py", "services/canvas_service.py",
    "services/structured_logging.py", "services/asset_service.py",
    "services/gen_task_manager.py", "services/preset_service.py",
    "services/project_service.py", "services/prompt_service.py",
    "services/workflow_template_service.py", "services/batch_task_service.py",
    "services/stages/qc_stage.py", "services/asset_organizer.py",
    "api/canvas_api.py", "api/director_asset_api.py",
    "api/infinite_canvas_api.py", "main.py",
}


def norm_path(p):
    p = p.strip().lstrip("\ufeff").strip()
    if p.startswith("."):
        p = p.lstrip(".")
    p = p.lstrip("\\/")
    return p.replace("\\", "/").lower()


def parse(fp):
    out = collections.Counter()
    with open(fp, encoding="utf-8") as f:
        for ln in f:
            m = re.match(r"^(?:\.\\)?(.+?):\d+:\d+:\s*([A-Z]\d+)", ln)
            if m:
                fname, code = norm_path(m.group(1)), m.group(2)
                out[(fname, code)] += 1
    return out


base = parse("docs/工程化/lint_baseline.txt")
cur = parse("docs/工程化/lint_p1.txt")

print(f"{'文件':<42}{'码':<6}{'基线':<6}{'当前':<6}差值")
print("-" * 70)
total_delta = 0
for fname in sorted(CHANGED):
    for code in sorted({c for (f, c), _ in base.items() if f == fname} |
                       {c for (f, c), _ in cur.items() if f == fname}):
        bv = base.get((fname, code), 0)
        cv = cur.get((fname, code), 0)
        if bv != cv:
            print(f"{fname:<42}{code:<6}{bv:<6}{cv:<6}{cv-bv:+d}")
            total_delta += cv - bv

print("-" * 70)
print(f"我改的 18 个文件净变化: {total_delta:+d} 条")

# 特别检查：F 系列与 E402 新增（可能我引入）
print()
print("=== 我改文件的新增 F 系列 / E402（需人工核验）===")
for (fname, code), v in sorted(cur.items()):
    if fname in CHANGED and (code.startswith("F") or code == "E402"):
        bv = base.get((fname, code), 0)
        if v > bv:
            print(f"  +{v-bv}  {fname}:{code}")
