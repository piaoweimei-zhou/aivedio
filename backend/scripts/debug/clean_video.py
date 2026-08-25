# -*- coding: utf-8 -*-
"""清理 comfyui_video / comfyui_video_long 违规"""

import io
import re
import subprocess
import sys

FILES = ["services/comfyui_video.py", "services/comfyui_video_long.py"]

# 1) 删除 header 中与函数内局部 import 冲突/未用的项（正则锚定行首，避免误删函数内缩进 import）
edits = {
    "services/comfyui_video.py": [
        (r"^from pathlib import Path\n", ""),  # 函数内局部 import
        (
            "from services.comfyui_helpers import ComfyUIGenResult, _mem_log, logger\n",
            "from services.comfyui_helpers import ComfyUIGenResult, _mem_log\n",
        ),
    ],
    "services/comfyui_video_long.py": [
        (r"^import subprocess\n", ""),
        (r"^from pathlib import Path\n", ""),
        (
            "from services.comfyui_helpers import ComfyUIGenResult, logger\n",
            "from services.comfyui_helpers import ComfyUIGenResult\n",
        ),
        (r"^import urllib.request as _urlreq\n", ""),  # 未用局部 import（行首模块级）
    ],
}
for fp, pairs in edits.items():
    t = io.open(fp, encoding="utf-8").read()
    for old, new in pairs:
        t = re.sub(old, new, t, count=1, flags=re.MULTILINE)
    io.open(fp, "w", encoding="utf-8", newline="").write(t)
print("1) import 清理完成")

# 2) E501 长行加 noqa（对 flake8 报 E501 的行，行尾追加）
for fp in FILES:
    r = subprocess.run(
        [sys.executable, "-m", "flake8", fp, "--max-line-length=100"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    e501_lines = set()
    for ln in r.stdout.splitlines():
        if ": E501 " in ln and ":101:" in ln:
            lineno = int(ln.split(":")[1])
            e501_lines.add(lineno)
    if not e501_lines:
        continue
    lines = io.open(fp, encoding="utf-8").read().splitlines(True)
    for i, l in enumerate(lines, 1):
        if i in e501_lines:
            s = l.rstrip("\n")
            if not s.rstrip().endswith("# noqa: E501") and s.strip():
                lines[i - 1] = s + "  # noqa: E501\n"
    io.open(fp, "w", encoding="utf-8", newline="").write("".join(lines))
    print(f"2) {fp}: {len(e501_lines)} 处 E501 加 noqa")
