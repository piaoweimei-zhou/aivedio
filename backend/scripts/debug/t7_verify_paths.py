# -*- coding: utf-8 -*-
"""T7 验收：扫描残留目录根定义"""
import io
import os
import re

BACKEND = r"D:\1\2\director\backend"
SKIP = ("\\.venv-test", "\\scripts\\debug\\p1_consolidate_paths.py",
        "t7_verify_paths.py", "verify_mounts.py")

# 残留目录根定义模式（不应再出现，除 paths.py）
ROOT_DEFS = [
    re.compile(r'GENERATED_DIR\s*=\s*os\.path\.join'),
    re.compile(r'OUTPUT_DIR\s*=\s*os\.path\.join'),
    re.compile(r'_PRESET_DIR\s*=\s*"data/'),
    re.compile(r'_PROMPT_DIR\s*=\s*"data/'),
    re.compile(r'_DEFAULT_[A-Z_]*DIR\s*=\s*"data/'),
    re.compile(r'_DEFAULT_PERSIST_DIR\s*=\s*"data/'),
    re.compile(r'os\.path\.join\("data"'),
    re.compile(r'os\.path\.join\(_BASE_DIR,\s*"data"'),
    re.compile(r'Path\(__file__\)\.parent\.parent\s*/\s*[\'"]logs'),
]

issues = []
paths_users = set()
for root, dirs, files in os.walk(BACKEND):
    dirs[:] = [d for d in dirs if d != ".venv-test" and d != "__pycache__"]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(root, fn)
        if any(s in fp for s in SKIP):
            continue
        with io.open(fp, encoding="utf-8") as f:
            try:
                lines = f.readlines()
            except Exception:
                continue
        rel = os.path.relpath(fp, BACKEND)
        if "services\\paths.py" in rel:
            continue
        for i, ln in enumerate(lines, 1):
            for pat in ROOT_DEFS:
                if pat.search(ln):
                    issues.append(f"{rel}:L{i}: {ln.strip()[:80]}")
        if any("from services.paths import" in ln for ln in lines):
            paths_users.add(rel)

print("=" * 60)
if issues:
    print(f"⚠️ 发现 {len(issues)} 处残留目录根定义：")
    for x in issues:
        print("  ❌", x)
else:
    print("✅ 无残留目录根定义——目录根已全部收敛到 services/paths.py")

print("-" * 60)
print(f"已引用 services.paths 的文件（{len(paths_users)} 个）：")
for u in sorted(paths_users):
    print("  ", u)

# 额外：确认双定义消除
print("-" * 60)
gen_def = 0
for root, dirs, files in os.walk(BACKEND):
    dirs[:] = [d for d in dirs if d != ".venv-test" and d != "__pycache__"]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        fp = os.path.join(root, fn)
        if any(s in fp for s in SKIP):
            continue
        if "services\\paths.py" in fp or "services/paths.py" in fp:
            continue
        with io.open(fp, encoding="utf-8") as f:
            txt = f.read()
        gen_def += txt.count("GENERATED_DIR = GENERATED_DIR") + txt.count(
            "GENERATED_DIR = os.path.join")
print(f"GENERATED_DIR 直接定义残留: {gen_def}（应为 0，除 paths.py）")
