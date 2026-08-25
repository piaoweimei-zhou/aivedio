# -*- coding: utf-8 -*-
"""按文件输出剩余全部违规"""

import os
import subprocess

BACKEND = r"D:\1\2\director\backend"
VENV = os.path.join(BACKEND, ".venv-test", "Scripts", "python.exe")
TARGETS = ["api", "services", "core", "main.py", "tools", "scripts", "tests"]
SKIP_DIRS = (".venv-test", "__pycache__")

by_file = {}
for t in TARGETS:
    p = os.path.join(BACKEND, t)
    if os.path.isfile(p):
        files = [p]
    else:
        files = []
        for root, dirs, fs in os.walk(p):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fn in fs:
                if fn.endswith(".py"):
                    files.append(os.path.join(root, fn))
    for fp in files:
        r = subprocess.run(
            [VENV, "-m", "flake8", fp, "--max-line-length=100"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if r.stdout.strip():
            by_file.setdefault(os.path.relpath(fp, BACKEND), []).extend(r.stdout.splitlines())

for fp in sorted(by_file):
    print(f"\n### {fp} ({len(by_file[fp])})")
    for ln in by_file[fp]:
        parts = ln.split(":")
        print(f"  L{parts[-3]}:{parts[-2]} {parts[-1].strip()}")
