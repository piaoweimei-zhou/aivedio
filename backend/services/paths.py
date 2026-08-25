"""运行时路径单一来源（工程化 T7 收敛）

所有运行时目录根在此定义一次。业务代码禁止散落定义目录根，
必须通过 from services.paths import ... 引用对应常量。

对齐原则：
- backend/data    = 持久化数据（generated/uploads/presets/projects/...）
- backend/output  = 生成产物（脚本/图片/视频）
- backend/assets  = 资产注册表（asset_registry.json）
- backend/logs    = 结构化日志

历史遗留说明：
- 原 main.py / comfyui_helpers.py 重复定义 GENERATED_DIR，已收敛到本模块
- 原 provider_utils.OUTPUT_DIR 与 main.py 挂载 /output 共用，已收敛到本模块
"""

import os

# backend/ 根
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 运行时根目录
DATA_DIR = os.path.join(BACKEND_DIR, "data")
OUTPUT_DIR = os.path.join(BACKEND_DIR, "output")
ASSETS_DIR = os.path.join(BACKEND_DIR, "assets")
LOGS_DIR = os.path.join(BACKEND_DIR, "logs")

# data/ 子目录
GENERATED_DIR = os.path.join(DATA_DIR, "generated")       # 持久化生成图/视频（不随 ComfyUI output 清理）
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")           # 上传目录（含 canvas 上传）
PIPELINES_DIR = os.path.join(DATA_DIR, "pipelines")       # 管线数据
LIBRARIES_DIR = os.path.join(DATA_DIR, "libraries")       # 分镜画布素材库
CANVAS_DIR = os.path.join(DATA_DIR, "canvas")             # 画布数据
TASK_STATE_DIR = os.path.join(DATA_DIR, "task_state")     # 生成任务持久化
GEN_TASK_STATE_DIR = os.path.join(DATA_DIR, "gen_task_state")
PRESETS_DIR = os.path.join(DATA_DIR, "presets")           # 任务预设
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")         # 项目数据
PROMPTS_DIR = os.path.join(DATA_DIR, "prompts")           # 提示词
WORKFLOW_TEMPLATES_DIR = os.path.join(DATA_DIR, "workflow_templates")
BATCHES_DIR = os.path.join(DATA_DIR, "batches")           # 批量任务

# generated/ 子目录
QC_DIR = os.path.join(GENERATED_DIR, "qc")                # QC 报告落盘
