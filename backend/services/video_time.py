"""视频时间控制统一工具

解决 B1 问题：duration / frame_count / segment_seconds / fps 多字段语义混乱。

统一规则：
- frame_count（帧数）：视频总帧数，优先级最高
- duration（秒）：视频时长，若 frame_count 已提供则由 frame_count/fps 推导
- fps（帧率）：默认 24
- segment_seconds（分段时长）：仅用于长视频分段，默认 15 秒

转换公式：
- frame_count = duration × fps
- duration = frame_count / fps
"""

from typing import Optional, Tuple

DEFAULT_FPS = 24
DEFAULT_SEGMENT_SECONDS = 15
MIN_FRAME_COUNT = 25  # ComfyUI LTX 最小帧数要求


def resolve_video_duration(
    duration: Optional[float] = None,
    frame_count: Optional[int] = None,
    fps: Optional[int] = None,
) -> Tuple[float, int, int]:
    """解析视频时长参数，返回 (duration_seconds, frame_count, fps)

    优先级：
    1. frame_count 显式提供 → 用 frame_count/fps 推导 duration
    2. duration 显式提供 → 用 duration×fps 推导 frame_count
    3. 都未提供 → 默认 duration=5.0s

    Args:
        duration: 视频时长（秒）
        frame_count: 视频总帧数
        fps: 帧率（默认 24）

    Returns:
        (duration_seconds, frame_count, fps)
    """
    effective_fps = int(fps) if fps and fps > 0 else DEFAULT_FPS

    if frame_count is not None and frame_count > 0:
        # frame_count 优先
        effective_frames = max(int(frame_count), MIN_FRAME_COUNT)
        effective_duration = effective_frames / effective_fps
    elif duration is not None and duration > 0:
        # duration 次之
        effective_duration = float(duration)
        effective_frames = max(int(effective_duration * effective_fps), MIN_FRAME_COUNT)
    else:
        # 默认值
        effective_duration = 5.0
        effective_frames = max(int(effective_duration * effective_fps), MIN_FRAME_COUNT)

    return effective_duration, effective_frames, effective_fps


def resolve_segment_seconds(segment_seconds: Optional[int] = None) -> int:
    """解析长视频分段时长

    Args:
        segment_seconds: 每段时长（秒），None 或 ≤0 时使用默认值 15

    Returns:
        有效的分段时长
    """
    if segment_seconds and segment_seconds > 0:
        return int(segment_seconds)
    return DEFAULT_SEGMENT_SECONDS
