"""
模板制作共享工具

提供3个Stage共用的常量和工具函数：
- 模板目录路径
- manifest 原子读写（防竞态）
- 文件复制+校验
- 资产注册
"""

import asyncio
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 模板目录（3个Stage共用）
# 从 backend/services/template_utils.py → workflow/templates
TEMPLATE_DIR = Path(__file__).parent.parent.parent / "workflows" / "templates"
MANIFEST_PATH = TEMPLATE_DIR / "templates_manifest.json"

# 进程内异步互斥锁：防止多个协程并发 read-modify-write 导致 TOCTOU 竞态
# 使用 asyncio.Lock 而非 threading.Lock，避免阻塞事件循环
_manifest_mutex = asyncio.Lock()


def validate_template_id(template_id: str) -> bool:
    """校验 template_id 是否安全（防止路径遍历攻击）

    合法的 template_id 格式：T01_双人正面对话
    - 只允许字母、数字、下划线、中文
    - 不允许包含 .. / \\ 等路径分隔符
    """
    if not template_id:
        return False
    # 禁止路径遍历字符
    forbidden = ["..", "/", "\\", ":", ";", "|", "<", ">", "?", "*", '"']
    for ch in forbidden:
        if ch in template_id:
            return False
    # 长度限制
    if len(template_id) > 100:
        return False
    return True


def safe_filename_prefix(template_id: str) -> str:
    """将 template_id 转换为 ComfyUI 安全的 filename_prefix

    ComfyUI 的 SaveImage 节点在 Windows 上对中文文件名处理可能异常，
    导致输出文件名乱码，后续的文件匹配和复制流程断裂。

    策略：提取编号部分（如 T01），中文部分替换为简短拼音缩写。
    例如：
      T01_双人正面对话 → T01_drzm
      T02_单人depth特写 → T02_dr_depth

    注意：此函数仅用于 ComfyUI filename_prefix，
    资产注册和 manifest 中仍使用原始 template_id。
    """
    import re
    # 提取编号部分（如 T01, T02）
    m = re.match(r'(T\d+)', template_id)
    prefix = m.group(1) if m else "TPL"

    # 将非 ASCII 字符替换为下划线，然后压缩连续下划线
    ascii_part = re.sub(r'[^\w]', '_', template_id, flags=re.ASCII)
    ascii_part = re.sub(r'_+', '_', ascii_part).strip('_')

    # 如果 ASCII 部分只剩编号（如 T01），直接用
    # 否则用编号 + 简短后缀
    if ascii_part == prefix or not ascii_part:
        return prefix
    # 取编号 + 下划线后的第一个英文段（如 depth）
    parts = ascii_part.split('_')
    segments = [prefix]
    for p in parts[1:]:
        if p and re.match(r'^[a-zA-Z]', p):
            segments.append(p)
    return '_'.join(segments) if len(segments) > 1 else prefix


def read_manifest() -> Optional[Dict[str, Any]]:
    """原子读取 manifest.json

    如果文件不存在，返回空 manifest（含 templates 列表）。
    如果文件损坏（JSON 解析失败），自动备份损坏文件并返回空 manifest。
    """
    if not MANIFEST_PATH.exists():
        logger.warning(f"[TemplateUtils] manifest 文件不存在: {MANIFEST_PATH}")
        return {"templates": [], "version": "1.0"}
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("[TemplateUtils] manifest 格式异常（非 dict），重置为空")
                return {"templates": [], "version": "1.0"}
            if "templates" not in data:
                data["templates"] = []
            return data
    except json.JSONDecodeError as e:
        logger.error(f"[TemplateUtils] manifest JSON 损坏: {e}，备份并重置")
        # 备份损坏的文件，防止数据丢失
        try:
            import time as _time
            backup_path = str(MANIFEST_PATH) + f".corrupted.{int(_time.time())}"
            os.replace(str(MANIFEST_PATH), backup_path)
            logger.info(f"[TemplateUtils] 损坏文件已备份到: {backup_path}")
        except OSError:
            pass
        return {"templates": [], "version": "1.0"}
    except (IOError, OSError) as e:
        logger.error(f"[TemplateUtils] 读取 manifest 失败: {e}")
        return None


def write_manifest(manifest: Dict[str, Any]) -> bool:
    """原子写入 manifest.json（write-to-temp + rename，防竞态和半写）

    Windows 原子性策略：使用 os.replace() 替代 os.rename()，
    os.replace 在所有平台都是原子操作（覆盖目标文件）。

    Returns:
        True if success, False if failure
    """
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)

    fd = None
    tmp_path = ""
    try:
        # 写入临时文件
        fd, tmp_path = tempfile.mkstemp(
            dir=str(TEMPLATE_DIR),
            prefix=".manifest_tmp_",
            suffix=".json",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        fd = None  # fd 已由 os.fdopen 接管并关闭

        # 原子替换（os.replace 在所有平台原子覆盖目标文件）
        os.replace(tmp_path, str(MANIFEST_PATH))
        tmp_path = ""  # 标记已成功，无需清理
        return True
    except Exception as e:
        logger.error(f"[TemplateUtils] 写入 manifest 失败: {e}")
        # 关闭泄漏的 fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        # 清理临时文件
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


async def update_manifest_entry(template_id: str, updates: Dict[str, Any]) -> bool:
    """更新 manifest 中指定 template_id 的条目

    Args:
        template_id: 模板编号（如 T01_双人正面对话）
        updates: 要更新的字段（如 {"status": "ready", "files": {...}}）

    Returns:
        True if success, False if failure
    """
    async with _manifest_mutex:
        manifest = read_manifest()
        if manifest is None:
            return False

        templates = manifest.get("templates", [])

        for tmpl in templates:
            if tmpl.get("id") == template_id:
                # 合并更新
                for key, value in updates.items():
                    if key == "files" and isinstance(value, dict):
                        # files 字段合并而非替换
                        if "files" not in tmpl:
                            tmpl["files"] = {}
                        tmpl["files"].update(value)
                    else:
                        tmpl[key] = value
                break
        else:
            # template_id 不存在，不追加（应由 batch_extract 创建）
            logger.warning(
                f"[TemplateUtils] manifest 中未找到 template_id={template_id}，跳过更新"
            )
            return False

        manifest["templates"] = templates
        return write_manifest(manifest)


async def atomic_manifest_update(update_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> bool:
    """原子更新 manifest：在异步互斥锁保护下执行 read-modify-write

    用法：
        def my_update(manifest):
            manifest["templates"][0]["status"] = "ready"
            return manifest  # 返回修改后的 manifest

        success = await atomic_manifest_update(my_update)

    Args:
        update_fn: 接收 manifest dict，返回修改后的 manifest dict

    Returns:
        True if success, False if failure
    """
    async with _manifest_mutex:
        manifest = read_manifest()
        if manifest is None:
            return False

        try:
            updated = update_fn(manifest)
            if updated is None:
                return False
            return write_manifest(updated)
        except Exception as e:
            logger.error(f"[TemplateUtils] atomic_manifest_update 失败: {e}")
            return False


# ---------------------------------------------------------------------------
# 文件复制公共函数
# ---------------------------------------------------------------------------


def ensure_template_dir() -> Path:
    """确保模板目录存在，返回 TEMPLATE_DIR"""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    return TEMPLATE_DIR


def remove_old_files(template_id: str, suffixes: List[str], tag: str = "TemplateUtils") -> None:
    """幂等性：删除上一次生成的模板文件，防止残留旧文件

    Args:
        template_id: 模板编号
        suffixes: 文件后缀列表，如 ["pose.png", "depth_raw.png"]
        tag: 日志标签
    """
    for suffix in suffixes:
        old_path = TEMPLATE_DIR / f"{template_id}_{suffix}"
        if old_path.exists():
            try:
                os.remove(str(old_path))
                logger.info(f"[{tag}] 清除旧文件: {template_id}_{suffix}")
            except OSError:
                pass


def match_and_copy_files(
    filenames: List[str],
    output_dir: str,
    rename_map: Dict[str, str],
    tag: str = "TemplateUtils",
    skip_keywords: Optional[List[str]] = None,
    first_only: bool = False,
) -> Dict[str, str]:
    """从 ComfyUI 输出目录匹配并复制文件到模板目录

    统一的文件匹配+复制+校验逻辑，消除三个 Stage 中的重复代码。

    匹配规则：对每个文件名，用分隔符匹配 prefix（避免 template_id 含关键词时误判）：
        - f"_{prefix}_" in fname_lower
        - f"_{prefix}." in fname_lower
        - fname_lower.startswith(f"{prefix}_")
        - fname_lower.startswith(f"{prefix}.")

    Args:
        filenames: ComfyUI 输出的文件名列表
        output_dir: ComfyUI 输出目录
        rename_map: {prefix: target_filename}，如 {"pose": "T01_pose.png"}
        tag: 日志标签
        skip_keywords: 跳过包含这些关键词的文件（如 ["depth_clean"] 用于 batch_extract）
        first_only: True=每个 prefix 只复制第一个匹配文件，False=复制所有匹配

    Returns:
        {prefix: dst_path_str} 成功复制的文件字典
    """
    ensure_template_dir()
    skip_keywords = skip_keywords or []
    copied: Dict[str, str] = {}

    for fn in filenames:
        fname_lower = fn.lower()

        # 跳过包含特定关键词的文件
        if any(kw in fname_lower for kw in skip_keywords):
            continue

        for prefix, target_name in rename_map.items():
            # first_only 模式下，该 prefix 已复制则跳过
            if first_only and prefix in copied:
                continue

            # 分隔符匹配
            if not (
                f"_{prefix}_" in fname_lower
                or f"_{prefix}." in fname_lower
                or fname_lower.startswith(f"{prefix}_")
                or fname_lower.startswith(f"{prefix}.")
            ):
                continue

            src_path = os.path.join(output_dir, fn)
            dst_path = TEMPLATE_DIR / target_name

            if not os.path.exists(src_path):
                logger.warning(f"[{tag}] 源文件不存在: {src_path}")
                break  # 匹配到 prefix 但源文件不存在，跳出

            src_size = os.path.getsize(src_path)
            if src_size == 0:
                logger.warning(f"[{tag}] 源文件为空，跳过: {fn}")
                break

            shutil.copy2(src_path, str(dst_path))

            # 校验复制完整性
            if not os.path.exists(dst_path) or os.path.getsize(str(dst_path)) != src_size:
                logger.error(f"[{tag}] 文件复制不完整: {fn} → {target_name}")
                break

            copied[prefix] = str(dst_path)
            logger.info(f"[{tag}] 复制模板文件 | {fn} → {target_name}")
            break  # 一个文件只匹配一个 prefix

    # 诊断日志
    for prefix, target_name in rename_map.items():
        if prefix in copied:
            logger.info(f"[{tag}] 文件复制状态 | {prefix} → {target_name} ✓")
        else:
            logger.warning(
                f"[{tag}] 文件复制状态 | {prefix} → {target_name} ✗ 缺失！"
                f" ComfyUI 可能未输出此文件"
            )

    return copied


# ---------------------------------------------------------------------------
# 资产类型识别（按文件名前缀匹配）
# ---------------------------------------------------------------------------

# ComfyUI 输出文件名 → (asset_type, label) 的标准映射
# 用于 extract_all / template_batch_extract 等 Stage 识别输出文件类型
DEFAULT_TYPE_MAP: Dict[str, Tuple[str, str]] = {
    "lineart": ("lineart", "线稿"),
    "depth_raw": ("depth", "深度图"),
    "depth": ("depth", "深度图"),
    "pose": ("pose", "姿态"),
}


def match_asset_type_by_filename(
    filename: str,
    type_map: Optional[Dict[str, Tuple[str, str]]] = None,
    skip_keywords: Optional[List[str]] = None,
) -> Optional[Tuple[str, str]]:
    """按文件名前缀匹配资产类型

    消除 extract_all_stage 和 template_batch_extract_stage 中重复的
    "分隔符匹配 + 跳过关键词" 逻辑。

    匹配规则（与 match_and_copy_files 一致，避免子串误判）：
        - f"_{prefix}_" in fname_lower
        - f"_{prefix}." in fname_lower
        - fname_lower.startswith(f"{prefix}_")
        - fname_lower.startswith(f"{prefix}.")

    Args:
        filename: ComfyUI 输出文件名
        type_map: {prefix: (asset_type, label)} 映射，默认使用 DEFAULT_TYPE_MAP
        skip_keywords: 命中后跳过的关键词（如 ["depth_clean"]）

    Returns:
        (asset_type, label) 或 None（未匹配）
    """
    type_map = type_map or DEFAULT_TYPE_MAP
    skip_keywords = skip_keywords or []
    fname_lower = filename.lower()

    if any(kw in fname_lower for kw in skip_keywords):
        return None

    for prefix, (asset_type, label) in type_map.items():
        if (f"_{prefix}_" in fname_lower
            or f"_{prefix}." in fname_lower
            or fname_lower.startswith(f"{prefix}_")
            or fname_lower.startswith(f"{prefix}.")):
            return (asset_type, label)
    return None

