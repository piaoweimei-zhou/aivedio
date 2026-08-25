# -*- coding: utf-8 -*-
"""comfyui_lifecycle.py mixin 拆分（P2 大文件治理 2/5）

分组：
  CORE → 主类 ComfyUILifecycleMixin 保留
  MEM  → comfyui_lifecycle_memory.py  ComfyUILifecycleMemoryMixin
  PROC → comfyui_lifecycle_process.py ComfyUILifecycleProcessMixin
"""
import ast
import io

SRC = "services/comfyui_lifecycle.py"
MEM_DST = "services/comfyui_lifecycle_memory.py"
PROC_DST = "services/comfyui_lifecycle_process.py"

MEM_METHODS = {
    "_release_vram_for_comfyui", "_release_vram_for_llama", "_pre_analyze_references",
    "_get_system_memory_usage", "_get_vram_usage", "check_and_release_memory",
    "_quick_release_vram", "_ensure_clean_state",
}
PROC_METHODS = {
    "ensure_running", "_check_alive", "_start_process", "stop", "_stop_process",
    "_kill_process_on_port", "_schedule_idle_shutdown", "_start_health_check",
}

src = io.open(SRC, encoding="utf-8").read()
lines = src.splitlines(True)
tree = ast.parse(src)

# 收集方法行区间
methods = []
for node in tree.body:
    if isinstance(node, ast.ClassDef) and node.name == "ComfyUILifecycleMixin":
        for m in node.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append((m.name, m.lineno, m.end_lineno))
print("方法清单:", len(methods))
for n, a, b in methods:
    print(f"  {n}: L{a}-{b}")


def extract(names):
    """按方法名取源码块（从方法定义行起，向上含前导空行，不含 class 定义行）"""
    blocks = []
    for n, a, b in methods:
        if n in names:
            start = a - 1  # 0-indexed 方法定义行
            if start > 0 and lines[start - 1].strip() == "":
                start -= 1  # 含方法前的空行（保持格式）
            blocks.append("".join(lines[start:b]))
    return "\n".join(blocks)


MEM_HEADER = '''"""
ComfyUI 服务 — 内存/显存协调 Mixin（从 comfyui_lifecycle.py 拆分，P2 治理）

为 ComfyUI / llama.cpp 交替释放显存、系统内存/显存占用检测、参考图预分析。
被 ComfyUILifecycleMixin 继承（MRO），方法用 self.xxx 调用主类其他 mixin。
"""

import asyncio
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from services.comfyui.config import (
    MEMORY_HIGH_THRESHOLD,
    VRAM_HIGH_THRESHOLD,
)
from services.comfyui_helpers import (
    DISABLE_PROCESS_MANAGEMENT,
    _analyze_reference_images,
    _apply_vision_cache,
    _get_ram_pct_safe,
    _load_vision_cache,
    _mem_log,
    _save_vision_cache,
    logger,
)

logger = logging.getLogger(__name__)


class ComfyUILifecycleMemoryMixin:
'''

PROC_HEADER = '''"""
ComfyUI 服务 — 进程管理 Mixin（从 comfyui_lifecycle.py 拆分，P2 治理）

ComfyUI 进程启动/停止/健康检查/空闲自停/端口清理。
被 ComfyUILifecycleMixin 继承（MRO），方法用 self.xxx 调用主类其他 mixin。
"""

import asyncio
import logging
import os
import signal
import subprocess
import sys
import time

from services.comfyui.config import (
    COMFYUI_DIR,
    COMFYUI_PYTHON,
)
from services.comfyui_helpers import (
    COMFYUI_SCRIPT,
    COMFYUI_START_TIMEOUT,
    DISABLE_PROCESS_MANAGEMENT,
    _mem_log,
    logger,
)

logger = logging.getLogger(__name__)


class ComfyUILifecycleProcessMixin:
'''

# 生成子模块
mem_full = MEM_HEADER + extract(MEM_METHODS) + "\n"
proc_full = PROC_HEADER + extract(PROC_METHODS) + "\n"
io.open(MEM_DST, "w", encoding="utf-8", newline="").write(mem_full)
io.open(PROC_DST, "w", encoding="utf-8", newline="").write(proc_full)

# 主文件：保留 CORE 方法 + 继承子 mixin
core_names = {n for n, _, _ in methods} - MEM_METHODS - PROC_METHODS
core_block = extract(core_names)

MAIN_HEADER = '''"""
ComfyUI 服务 — 生命周期 Mixin 主类

进程启动/停止、内存与显存协调、健康检查、空闲自停、输出清理。
P2 治理：内存/进程方法拆分至 comfyui_lifecycle_memory / _process 子模块，
本文件保留核心方法并组合继承。
"""

import asyncio
import logging
import os
import shutil
import time
from typing import Awaitable, Callable, List

import aiohttp

from services.comfyui_helpers import (
    COMFYUI_BASE_URL,
    GENERATED_DIR,
    logger,
)

from services.comfyui_lifecycle_memory import ComfyUILifecycleMemoryMixin
from services.comfyui_lifecycle_process import ComfyUILifecycleProcessMixin

logger = logging.getLogger(__name__)


class ComfyUILifecycleMixin(ComfyUILifecycleMemoryMixin, ComfyUILifecycleProcessMixin):
'''

main_full = MAIN_HEADER + core_block + "\n"
io.open(SRC, "w", encoding="utf-8", newline="").write(main_full)
print(f"{MEM_DST}: {len(mem_full.splitlines())} 行 | "
      f"{PROC_DST}: {len(proc_full.splitlines())} 行 | "
      f"{SRC}: {len(main_full.splitlines())} 行")
print("CORE 保留方法:", sorted(core_names))
