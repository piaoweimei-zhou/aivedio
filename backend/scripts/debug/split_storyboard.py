# -*- coding: utf-8 -*-
"""comfyui_storyboard.py mixin 拆分（P2 大文件治理 4/5）

batch_generate_storyboard + _get_intermediates_dir + _save_step_intermediate
+ _resume_from_checkpoint → comfyui_storyboard_batch.py
主类保留 generate_storyboard + storyboard_generation_v2，继承 BatchMixin。
"""
import ast
import io

SRC = "services/comfyui_storyboard.py"
BATCH_DST = "services/comfyui_storyboard_batch.py"

BATCH_METHODS = {"batch_generate_storyboard", "_get_intermediates_dir",
                 "_save_step_intermediate", "_resume_from_checkpoint"}

src = io.open(SRC, encoding="utf-8").read()
lines = src.splitlines(True)
tree = ast.parse(src)

methods = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "ComfyUIStoryboardMixin":
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


BATCH_HEADER = '''"""
ComfyUI 服务 — 分镜批量生成/中间产物 Mixin（从 comfyui_storyboard.py 拆分，P2 治理）

被 ComfyUIStoryboardMixin 继承（MRO），含 batch 生成与断点续跑。
"""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.comfyui_helpers import (
    ComfyUIGenResult,
    StoryboardStepResult,
    _collect_all_reference_urls,
    logger,
)

logger = logging.getLogger(__name__)


class ComfyUIStoryboardBatchMixin:
'''

batch_full = BATCH_HEADER + extract(BATCH_METHODS) + "\n"
io.open(BATCH_DST, "w", encoding="utf-8", newline="").write(batch_full)

core_names = {n for n, _, _ in methods} - BATCH_METHODS
core_block = extract(core_names)

MAIN_HEADER = '''"""
ComfyUI 服务 — 分镜生成 Mixin 主类

P2 治理：批量生成/中间产物方法拆至 comfyui_storyboard_batch.py。
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.comfyui_helpers import (
    ComfyUIGenResult,
    StoryboardStepResult,
    _collect_all_reference_urls,
    _crop_turnaround_to_front_view,
    _get_ram_pct_safe,
    _get_step_progress_range,
    _mem_log,
    _update_workflow_input,
    logger,
)

from services.comfyui_storyboard_batch import ComfyUIStoryboardBatchMixin

logger = logging.getLogger(__name__)


class ComfyUIStoryboardMixin(ComfyUIStoryboardBatchMixin):
'''

main_full = MAIN_HEADER + core_block + "\n"
io.open(SRC, "w", encoding="utf-8", newline="").write(main_full)
print(f"{BATCH_DST}: {len(batch_full.splitlines())} 行 | {SRC}: {len(main_full.splitlines())} 行")
print("CORE 保留:", sorted(core_names))
