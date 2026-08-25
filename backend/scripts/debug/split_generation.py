# -*- coding: utf-8 -*-
"""comfyui_generation.py mixin 拆分（P2 大文件治理 5/5）

refine_image/_prepare_fullbody_reference/_build_full_body_expansion_prompt/
standardize_views → comfyui_generation_vision.py
主类保留 16 个基础+核心方法，继承 VisionMixin。
"""
import ast
import io

SRC = "services/comfyui_generation.py"
VISION_DST = "services/comfyui_generation_vision.py"

VISION_METHODS = {"refine_image", "_prepare_fullbody_reference",
                  "_build_full_body_expansion_prompt", "standardize_views"}

src = io.open(SRC, encoding="utf-8").read()
lines = src.splitlines(True)
tree = ast.parse(src)

methods = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "ComfyUIGenerationMixin":
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


VISION_HEADER = '''"""
ComfyUI 服务 — 图像精修/标准化 Mixin（从 comfyui_generation.py 拆分，P2 治理）

被 ComfyUIGenerationMixin 继承（MRO），精修与三视图标准化，
依赖主类的队列提交/完成等待方法。
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui_helpers import (
    ComfyUIGenResult,
    MAX_POLL_TIME,
    POLL_INTERVAL,
    TASK_TIMEOUTS,
    _extract_clip_text,
    _mem_log,
    logger,
)
from services.workflow_builder import (
    build_comfyui_workflow,
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)

logger = logging.getLogger(__name__)


class ComfyUIGenerationVisionMixin:
'''

vision_full = VISION_HEADER + extract(VISION_METHODS) + "\n"
io.open(VISION_DST, "w", encoding="utf-8", newline="").write(vision_full)

core_names = {n for n, _, _ in methods} - VISION_METHODS
core_block = extract(core_names)

MAIN_HEADER = '''"""
ComfyUI 服务 — 图像生成 Mixin 主类

文生图/图生图核心与队列管理。P2 治理：
精修/标准化方法拆至 comfyui_generation_vision.py。
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui_helpers import (
    ComfyUIGenResult,
    MAX_POLL_TIME,
    POLL_INTERVAL,
    TASK_TIMEOUTS,
    _extract_clip_text,
    _mem_log,
    logger,
)
from services.workflow_builder import (
    build_comfyui_workflow,
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)
from services.qwen_workflow import YAOGUANG_DEFAULT_NEGATIVE

from services.comfyui_generation_vision import ComfyUIGenerationVisionMixin

logger = logging.getLogger(__name__)


class ComfyUIGenerationMixin(ComfyUIGenerationVisionMixin):
'''

main_full = MAIN_HEADER + core_block + "\n"
io.open(SRC, "w", encoding="utf-8", newline="").write(main_full)
print(f"{VISION_DST}: {len(vision_full.splitlines())} 行 | {SRC}: {len(main_full.splitlines())} 行")
print("CORE 保留:", sorted(core_names))
