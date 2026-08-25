# -*- coding: utf-8 -*-
"""comfyui_video.py mixin 拆分（P2 大文件治理 3/5）

generate_long_video + generate_minimax_h3 → comfyui_video_long.py
主类 ComfyUIVideoMixin 保留 generate_video，继承 LongMixin。
"""
import ast
import io

SRC = "services/comfyui_video.py"
LONG_DST = "services/comfyui_video_long.py"

LONG_METHODS = {"generate_long_video", "generate_minimax_h3"}

src = io.open(SRC, encoding="utf-8").read()
lines = src.splitlines(True)
tree = ast.parse(src)

methods = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "ComfyUIVideoMixin":
        for m in node.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append((m.name, m.lineno, m.end_lineno))
print("方法清单:", len(methods))


def extract(names):
    blocks = []
    for n, a, b in methods:
        if n in names:
            start = a - 1
            if start > 0 and lines[start - 1].strip() == "":
                start -= 1
            blocks.append("".join(lines[start:b]))
    return "\n".join(blocks)


LONG_HEADER = '''"""
ComfyUI 服务 — 长视频/Minimax H3 生成 Mixin（从 comfyui_video.py 拆分，P2 治理）

被 ComfyUIVideoMixin 继承（MRO），generate_long_video 调用主类 generate_video。
"""

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult, logger

logger = logging.getLogger(__name__)


class ComfyUIVideoLongMixin:
'''

long_full = LONG_HEADER + extract(LONG_METHODS) + "\n"
io.open(LONG_DST, "w", encoding="utf-8", newline="").write(long_full)

core_names = {n for n, _, _ in methods} - LONG_METHODS
core_block = extract(core_names)

MAIN_HEADER = '''"""
ComfyUI 服务 — 视频生成 Mixin 主类

LTX 视频生成。P2 治理：长视频/Minimax 方法拆至 comfyui_video_long.py。
"""

import json
import logging
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import aiohttp

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult, _mem_log, logger

from services.comfyui_video_long import ComfyUIVideoLongMixin

logger = logging.getLogger(__name__)


class ComfyUIVideoMixin(ComfyUIVideoLongMixin):
'''

main_full = MAIN_HEADER + core_block + "\n"
io.open(SRC, "w", encoding="utf-8", newline="").write(main_full)
print(f"{LONG_DST}: {len(long_full.splitlines())} 行 | {SRC}: {len(main_full.splitlines())} 行")
print("CORE 保留:", sorted(core_names))
