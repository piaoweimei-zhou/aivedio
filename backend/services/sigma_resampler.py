"""ManualSigmas 重采样工具

解决 MSR 视频路径 steps 参数无法注入的问题：
ComfyUI 的 ManualSigmas 节点用逗号分隔的 sigma 列表控制步数，
直接覆写步数需要重新生成 sigma 序列。

策略：
- 线性重采样：在原 sigma 范围 [sigma_max, sigma_min] 内均匀生成 N 个点
- 保留端点：首尾 sigma 值不变，中间线性插值
- 兼容原格式：输出逗号分隔的字符串
"""
from typing import List


def resample_sigmas(original_sigmas: str, target_steps: int) -> str:
    """对 ManualSigmas 的 sigma 序列进行线性重采样

    Args:
        original_sigmas: 原始 sigma 字符串（逗号分隔），如 "1.0, 0.973, ..., 0.0"
        target_steps: 目标步数（生成 target_steps+1 个 sigma 点，含端点）

    Returns:
        重采样后的 sigma 字符串（逗号分隔）

    Example:
        >>> resample_sigmas("1.0, 0.5, 0.0", 5)
        "1.0, 0.8, 0.6, 0.4, 0.2, 0.0"
    """
    try:
        sigmas = [float(s.strip()) for s in original_sigmas.split(",") if s.strip()]
    except (ValueError, AttributeError):
        return original_sigmas  # 解析失败，返回原值

    if len(sigmas) < 2 or target_steps < 1:
        return original_sigmas

    sigma_max = sigmas[0]
    sigma_min = sigmas[-1]

    # 线性插值生成 target_steps+1 个点（含两端）
    new_sigmas: List[float] = []
    for i in range(target_steps + 1):
        t = i / target_steps
        new_sigmas.append(sigma_max + t * (sigma_min - sigma_max))

    # 格式化为字符串（保留 6 位小数，去除末尾 0）
    return ", ".join(f"{s:.6f}".rstrip("0").rstrip(".") if "." in f"{s:.6f}" else f"{s:.6f}" for s in new_sigmas)


def get_sigma_steps(sigmas_str: str) -> int:
    """获取 sigma 序列的步数（= 项数 - 1）"""
    try:
        sigmas = [s.strip() for s in sigmas_str.split(",") if s.strip()]
        return max(len(sigmas) - 1, 1)
    except Exception:
        return 0
