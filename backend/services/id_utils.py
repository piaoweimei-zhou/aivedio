"""统一实体 ID 生成规范

规范：
- 所有实体 ID 使用 hex[:12]（12 位十六进制）
- 前缀格式：{entity_short_name}_{hex}
- 前缀用于从 ID 快速识别实体类型

历史遗留（不改动，避免破坏现有数据引用）：
- asset_id: 无前缀（保持）
- project_id: proj_ + hex[:10]（保持）
- canvas_id: canvas_ + hex[:8]（保持）
- template_id: wf_ + hex[:12]（保持）

新实体应使用本模块的 generate_id() 函数。
"""

import uuid
from typing import Optional


def generate_id(prefix: Optional[str] = None, length: int = 12) -> str:
    """生成标准化实体 ID

    Args:
        prefix: 实体前缀（如 "task", "batch"）。None 则无前缀（如 asset_id）。
        length: hex 长度，默认 12

    Returns:
        标准化 ID，如 "task_a1b2c3d4e5f6"
    """
    hex_part = uuid.uuid4().hex[:length]
    if prefix:
        return f"{prefix}_{hex_part}"
    return hex_part


# 便捷函数（常用实体）
def gen_task_id() -> str:
    return generate_id("task")


def gen_batch_id() -> str:
    return generate_id("batch")


def gen_preset_id() -> str:
    return generate_id("preset")


def gen_prompt_id() -> str:
    return generate_id("prompt")


def gen_asset_id() -> str:
    return generate_id(None)  # asset_id 保持无前缀（兼容历史数据）
