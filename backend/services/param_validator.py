"""阶段参数校验中间件（Phase 4）

解决 E2/E3 问题：
- E2: 工作流模板缺少参数校验合约（类型/范围/必填）
- E3: 前端提交的 params 无类型安全

本模块在 stage_api 入口处对 params 进行校验，拒绝非法参数。
校验规则按 stage_id 分组，与 STAGE_PARAM_SCHEMAS 对应。

使用方式：
    from services.param_validator import validate_stage_params

    # 在 director_stage_api.execute_stage 入口调用
    errors = validate_stage_params(request.stage_id, request.params)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
"""

from typing import Any, Dict, List, Optional

# ============================================================
# 参数校验规则定义
# ============================================================

# 字段规格：{type, min, max, default, required, choices}
FieldSpec = Dict[str, Any]

# stage_id → {param_name → FieldSpec}
STAGE_PARAM_SCHEMAS: Dict[str, Dict[str, FieldSpec]] = {
    # 视频生成阶段
    "video": {
        "prompt": {"type": str, "required": True},
        "width": {"type": int, "min": 256, "max": 4096, "default": 1280},
        "height": {"type": int, "min": 256, "max": 4096, "default": 720},
        "duration": {"type": (int, float), "min": 0.1, "max": 60, "default": 5.0},
        "frame_count": {"type": int, "min": 1, "max": 3600, "default": None},
        "fps": {"type": int, "min": 1, "max": 60, "default": 24},
        "cfg": {"type": float, "min": 0.1, "max": 10.0, "default": 3.0},
        "steps": {"type": int, "min": 1, "max": 100, "default": 20},
        "seed": {"type": int, "min": -1, "default": -1},
        "resolution": {
            "type": str,
            "choices": ["480p", "720p", "1080p", "1440p", "2k", "4k"],
            "default": "480p",
        },  # noqa: E501
        "aspect_ratio": {
            "type": str,
            "choices": ["16:9", "9:16", "1:1", "4:3", "3:4"],
            "default": "16:9",
        },  # noqa: E501
    },
    # 概念图阶段（图像生成）
    "concept": {
        "prompt": {"type": str, "required": True},
        "width": {"type": int, "min": 256, "max": 4096, "default": 1080},
        "height": {"type": int, "min": 256, "max": 4096, "default": 1920},
        "steps": {"type": int, "min": 1, "max": 100, "default": 25},
        "cfg": {"type": float, "min": 0.5, "max": 10.0, "default": 2.0},
        "seed": {"type": int, "min": -1, "default": -1},
    },
    # 精修阶段（图像放大/优化）
    "refine": {
        "prompt": {"type": str, "required": False},
        "width": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "height": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "steps": {"type": int, "min": 1, "max": 100, "default": 20},
        "cfg": {"type": float, "min": 0.5, "max": 10.0, "default": 2.0},
        "seed": {"type": int, "min": -1, "default": -1},
        "denoise": {"type": float, "min": 0.0, "max": 1.0, "default": 0.5},
    },
    # 分镜阶段（图像批量生成）
    "storyboard": {
        "prompt": {"type": str, "required": True},
        "width": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "height": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "steps": {"type": int, "min": 1, "max": 100, "default": 25},
        "cfg": {"type": float, "min": 0.5, "max": 10.0, "default": 2.0},
        "seed": {"type": int, "min": -1, "default": -1},
    },
    # 多人分镜阶段
    "multi_person": {
        "prompt": {"type": str, "required": True},
        "width": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "height": {"type": int, "min": 256, "max": 4096, "default": 1344},
        "steps": {"type": int, "min": 1, "max": 100, "default": 25},
        "cfg": {"type": float, "min": 0.5, "max": 10.0, "default": 2.0},
        "seed": {"type": int, "min": -1, "default": -1},
    },
}


def _coerce_type(value: Any, expected_type: Any) -> Any:
    """尝试将值转换为期望类型"""
    if isinstance(expected_type, tuple):
        for t in expected_type:
            try:
                if t is int:
                    return int(value)
                elif t is float:
                    return float(value)
                elif t is str:
                    return str(value)
            except (ValueError, TypeError):
                continue
        return None
    else:
        try:
            if expected_type is int:
                return int(value)
            elif expected_type is float:
                return float(value)
            elif expected_type is str:
                return str(value)
        except (ValueError, TypeError):
            return None
    return value


def validate_stage_params(stage_id: str, params: Dict[str, Any]) -> List[str]:
    """校验阶段参数

    Args:
        stage_id: 阶段 ID（如 video/concept/refine）
        params: 用户提交的参数字典（会被原地修改为转换后的值）

    Returns:
        错误消息列表（空列表表示校验通过）
    """
    schema = STAGE_PARAM_SCHEMAS.get(stage_id)
    if not schema:
        # 未定义 schema 的阶段跳过校验（向后兼容）
        return []

    errors: List[str] = []

    for param_name, spec in schema.items():
        value = params.get(param_name)
        is_required = spec.get("required", False)
        default = spec.get("default")
        expected_type = spec.get("type")

        # 缺失参数处理
        if value is None or value == "":
            if is_required:
                errors.append(f"缺少必填参数: {param_name}")
            elif default is not None:
                params[param_name] = default
            continue

        # 类型转换
        if expected_type:
            coerced = _coerce_type(value, expected_type)
            if coerced is None:
                errors.append(f"参数 {param_name}={value} 无法转换为 {expected_type}")
                continue
            params[param_name] = coerced
            value = coerced

        # 范围校验
        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is not None and isinstance(value, (int, float)) and value < min_val:
            errors.append(f"参数 {param_name}={value} 小于最小值 {min_val}")
        if max_val is not None and isinstance(value, (int, float)) and value > max_val:
            errors.append(f"参数 {param_name}={value} 超过最大值 {max_val}")

        # 选项校验
        choices = spec.get("choices")
        if choices and value not in choices:
            errors.append(f"参数 {param_name}={value} 不在允许选项 {choices} 中")

    return errors


def get_stage_schema(stage_id: str) -> Optional[Dict[str, FieldSpec]]:
    """获取阶段的参数 schema（供前端生成表单）"""
    return STAGE_PARAM_SCHEMAS.get(stage_id)
