# -*- coding: utf-8 -*-
"""workflow_core.py 大文件拆分：qwen 函数组 → workflow_core_qwen.py"""
import io

SRC = "services/workflow_core.py"
DST = "services/workflow_core_qwen.py"

src = io.open(SRC, encoding="utf-8").read()
lines = src.splitlines(True)

# qwen 组行区间（1-indexed 含边界，来自 AST 分析）
qwen_ranges = [(36, 267), (270, 400), (403, 425), (426, 511), (514, 578), (893, 915)]
qwen_src = "".join("".join(lines[a - 1:b]) for a, b in qwen_ranges)
# build_comfyui_workflow L579-890
comfy_src = "".join(lines[578:890])

qwen_header = '''"""
ComfyUI 工作流构建器 — Qwen 编辑/三视图/精修/标准化/结构化提示词转换

从 workflow_core.py 拆分（P2 大文件治理），API 与拆前完全一致，
由 workflow_core 统一 re-export。
"""

from services.workflow_helpers import (
    _REFINE_LORA_STRENGTH,
    _REFINE_SCALE_LENGTH,
    _resolve_comfyui_image,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

'''

comfy_header = '''"""
ComfyUI 工作流构建器 — 主流程工作流构建（build_comfyui_workflow）

从 workflow_core.py 拆分（P2 大文件治理），Qwen 相关构建函数移至
services/workflow_core_qwen.py，此处 re-export 保持 API 兼容。
"""

from services.workflow_helpers import (
    ADDITIONAL_LORAS,
    BASE_WORKFLOW,
    CINEMATIC_WORKFLOW,
    PROP_WORKFLOW,
    YAOGUANG_DEFAULT_NEGATIVE,
    _detect_age_in_prompt,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

import copy
import logging
import random
import time
from typing import Dict, Any, Optional, Tuple, List

logger = logging.getLogger(__name__)

'''

qwen_full = qwen_header + qwen_src
comfy_full = (comfy_header + comfy_src +
              "\n\n# ---- 以下由 workflow_core_qwen 提供（API 兼容 re-export） ----\n"
              "from services.workflow_core_qwen import (\n"
              "    _build_fallback_workflow,\n"
              "    build_qwen_workflow,\n"
              "    build_refinement_workflow,\n"
              "    build_scene_multiangle_workflow,\n"
              "    build_standardization_workflow,\n"
              "    structured_prompt_to_comfyui_prompt,\n"
              ")\n")

io.open(DST, "w", encoding="utf-8", newline="").write(qwen_full)
io.open(SRC, "w", encoding="utf-8", newline="").write(comfy_full)
print(f"{DST}: {len(qwen_full.splitlines())} 行")
print(f"{SRC}: {len(comfy_full.splitlines())} 行")
