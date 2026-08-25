"""统一任务状态枚举

后端任务系统（GenTask / BatchStep）内部使用 completed，
前端 API 边界映射为 succeeded（保持向后兼容）。

使用方式：
    from services.task_status import TaskStatus, to_frontend_status

    # 内部使用常量
    step.status = TaskStatus.COMPLETED

    # API 返回时映射
    return {"status": to_frontend_status(task.status)}
"""


class TaskStatus:
    """任务状态常量（字符串常量，便于与现有代码兼容）"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"  # 后端内部统一用 completed
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"  # BatchStep 专用：上游失败时自动跳过

    # 前端兼容别名（ProviderResult / 旧 API 使用）
    SUCCEEDED = "succeeded"  # = COMPLETED 的前端别名


# 后端内部状态 → 前端状态映射
_FRONTEND_MAP = {
    TaskStatus.COMPLETED: TaskStatus.SUCCEEDED,
    TaskStatus.PENDING: TaskStatus.PENDING,
    TaskStatus.RUNNING: TaskStatus.RUNNING,
    TaskStatus.FAILED: TaskStatus.FAILED,
    TaskStatus.CANCELLED: TaskStatus.CANCELLED,
    TaskStatus.SKIPPED: TaskStatus.SKIPPED,
    # RunningHub / 第三方平台大写状态兼容映射
    "PENDING": TaskStatus.PENDING,
    "RUNNING": TaskStatus.RUNNING,
    "COMPLETED": TaskStatus.SUCCEEDED,
    "SUCCESS": TaskStatus.SUCCEEDED,
    "SUCCEEDED": TaskStatus.SUCCEEDED,
    "FAILED": TaskStatus.FAILED,
    "FAIL": TaskStatus.FAILED,
    "ERROR": TaskStatus.FAILED,
    "SUBMITTED": TaskStatus.PENDING,
    "NOT_FOUND": TaskStatus.FAILED,
}


def to_frontend_status(status: str) -> str:
    """将后端内部状态映射为前端状态（completed → succeeded）

    自动处理大小写：大写的 PENDING/SUCCESS/FAILED 等也会被映射为小写前端状态。
    """
    if not status:
        return status
    # 直接查映射表（支持大写）
    mapped = _FRONTEND_MAP.get(status)
    if mapped:
        return mapped
    # 兜底：尝试大写后再查
    mapped = _FRONTEND_MAP.get(str(status).upper())
    if mapped:
        return mapped
    return status


def is_terminal(status: str) -> bool:
    """判断是否为终态（不再变化）"""
    return status in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.SKIPPED,
    )  # noqa: E501
