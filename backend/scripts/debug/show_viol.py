# -*- coding: utf-8 -*-
"""看文件 F821/F541 具体行"""

import os
import subprocess
import sys

BACKEND = r"D:\1\2\director\backend"
VENV = os.path.join(BACKEND, ".venv-test", "Scripts", "python.exe")

fp = sys.argv[1]
r = subprocess.run(
    [VENV, "-m", "flake8", fp, "--max-line-length=100"],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="ignore",
)
lines = r.stdout.splitlines()
f821 = [line for line in lines if "F821" in line]
f541 = [line for line in lines if "F541" in line]
print(f"=== {fp}: 总违规 {len(lines)}, F821 {len(f821)}, F541 {len(f541)} ===")
for item in f821[:8]:
    print("  " + item)
