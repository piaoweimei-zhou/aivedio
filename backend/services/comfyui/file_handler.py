"""
ComfyUI 文件处理器 — 图片/视频下载与存储

职责：
- 确保参考图在 ComfyUI input 目录中（_ensure_image_in_input_dir）
- 图片缓存管理（磁盘验证，不再内存缓存）
- 从 output / HTTP / uploads / 项目目录 查找图片
- 参考图尺寸标准化
"""

import logging
import os
import shutil
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class ComfyUIFileHandler:
    """ComfyUI 文件处理器

    管理图片在 ComfyUI input/output 目录之间的流转，
    确保参考图在正确位置供 LoadImage 节点加载。
    """

    def __init__(
        self,
        comfyui_dir: str,
        output_dir: str,
        base_url: str,
        http_session_fn=None,
    ):
        """
        Args:
            comfyui_dir: ComfyUI 安装目录
            output_dir: ComfyUI output 目录
            base_url: ComfyUI HTTP 地址
            http_session_fn: 获取 aiohttp session 的回调函数
        """
        self._comfyui_dir = comfyui_dir
        self._output_dir = output_dir
        self._base_url = base_url
        self._http_session_fn = http_session_fn

    @property
    def input_dir(self) -> str:
        return os.path.join(self._comfyui_dir, "input")

    @property
    def output_dir(self) -> str:
        return self._output_dir

    def _get_http_session(self):
        """获取 HTTP session"""
        if self._http_session_fn:
            return self._http_session_fn()
        return None

    # ── 图片缓存 ──────────────────────────────────────────────

    async def cache_image(self, filename: str):
        """验证图片存在于磁盘（V2: 不再内存缓存）

        图片通过 /api/comfyui/image 代理端点从磁盘读取，
        无需在内存中缓存。
        """
        output_path = os.path.join(self._output_dir, filename)
        if os.path.exists(output_path):
            logger.debug(f"[ComfyUI] 图片已在磁盘: {filename}")
        else:
            logger.warning(f"[ComfyUI] 图片不在 output 目录: {filename}")

    def get_cached_image(self, filename: str) -> Optional[bytes]:
        """获取缓存的图片数据（V2: 始终返回 None，已移除内存缓存）"""
        return None

    def clear_image_cache(self):
        """清理图片缓存（V2: 空操作，已移除内存缓存）"""
        logger.info("[ComfyUI] 清理图片缓存: 无内存缓存需要清理 (V2)")

    # ── 参考图管理 ──────────────────────────────────────────────

    async def ensure_image_in_input_dir(
        self, image_url: str, project_id: Optional[str] = None
    ) -> str:
        """确保参考图像存在于 ComfyUI input 目录中

        按优先级尝试：
        1. 从 output 目录复制
        2. 从 ComfyUI HTTP 服务下载
        3. 从 data/uploads/ 目录复制
        4. 从项目 pipeline 目录搜索（模糊匹配）

        Args:
            image_url: 图片 URL 或文件名
            project_id: 项目 ID（用于搜索项目 pipeline 目录）

        Returns:
            ComfyUI input 目录下的纯文件名
        """
        from urllib.parse import urlparse, parse_qs

        _ref_t0 = time.time()

        if not image_url:
            return image_url

        # 解析 URL 提取文件名
        parsed = urlparse(image_url)
        params = parse_qs(parsed.query)

        if "filename" in params:
            filename = params["filename"][0]
        elif parsed.path:
            filename = parsed.path.rsplit("/", 1)[-1]
        else:
            filename = image_url

        fname = os.path.basename(filename)
        input_dir = self.input_dir
        input_path = os.path.join(input_dir, fname)

        # ⭐ 检查 input 目录中的文件是否与 output 目录一致
        # 避免同名旧文件导致超分/精修使用了错误的图片
        output_path = os.path.join(self._output_dir, fname)
        if os.path.exists(input_path) and not os.path.exists(output_path):
            # input 存在但 output 不存在（可能是上传的文件），直接使用
            _ref_elapsed = (time.time() - _ref_t0) * 1000
            logger.info(f"[REFIMG] 已存在(仅input) | file={fname} | elapsed={_ref_elapsed:.0f}ms")
            return fname
        if os.path.exists(input_path) and os.path.exists(output_path):
            # 两者都存在，比较大小判断是否需要更新
            src_sz = os.path.getsize(output_path)
            dst_sz = os.path.getsize(input_path)
            if src_sz == dst_sz:
                _ref_elapsed = (time.time() - _ref_t0) * 1000
                logger.info(f"[REFIMG] 已存在(一致) | file={fname} | elapsed={_ref_elapsed:.0f}ms")
                return fname
            # 大小不同，需要更新
            logger.info(f"[REFIMG] 文件已变更，更新 | file={fname} | old={dst_sz}B new={src_sz}B")
            try:
                shutil.copy2(output_path, input_path)
                _ref_elapsed = (time.time() - _ref_t0) * 1000
                logger.info(f"[REFIMG] 已更新 | file={fname} | elapsed={_ref_elapsed:.0f}ms")
                return fname
            except Exception as e:
                logger.warning(f"[REFIMG] 更新失败: {e}")

        os.makedirs(input_dir, exist_ok=True)

        # 1. 从 output 目录复制
        output_path = os.path.join(self._output_dir, fname)
        if os.path.exists(output_path):
            try:
                shutil.copy2(output_path, input_path)
                _ref_elapsed = (time.time() - _ref_t0) * 1000
                logger.info(f"[REFIMG] output复制 | file={fname} | elapsed={_ref_elapsed:.0f}ms")
                return fname
            except Exception as e:
                logger.warning(f"[ComfyUI] output 复制失败: {e}")

        # 2. HTTP 下载
        try:
            import aiohttp

            dl_url = f"{self._base_url}/view?filename={fname}&type=output"
            session = self._get_http_session()
            if session:
                async with session.get(
                    dl_url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with open(input_path, "wb") as f:
                            f.write(data)
                        data_size = len(data)
                        del data
                        _ref_elapsed = (time.time() - _ref_t0) * 1000
                        logger.info(
                            f"[REFIMG] HTTP下载 | file={fname}"
                            f" | size={data_size}B | elapsed={_ref_elapsed:.0f}ms"
                        )
                        return fname
                    else:
                        logger.warning(
                            f"[REFIMG] HTTP下载失败 | file={fname} | status={resp.status}"
                        )
        except Exception as e:
            logger.warning(f"[REFIMG] HTTP下载异常 | file={fname} | error={e}")

        # 3. 从 data/uploads/ 目录复制
        try:
            uploads_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "data",
                "uploads",
            )
            upload_path = os.path.join(uploads_dir, fname)
            if os.path.isfile(upload_path):
                shutil.copy2(upload_path, input_path)
                logger.info(f"[ComfyUI] 从 data/uploads 复制到 input: {fname}")
                return fname
        except Exception as e:
            logger.debug(f"[ComfyUI] data/uploads 查找失败: {e}")

        # 4. 从项目 pipeline 目录搜索
        if project_id:
            try:
                from services.pipeline_manager import get_pipeline_manager

                mgr = get_pipeline_manager()
                project = mgr.get_project(project_id)
                if project:
                    folder_path = project.get("folder_path", "")
                    if folder_path and os.path.isdir(folder_path):
                        for stage in (
                            "concept",
                            "refinement",
                            "standardize",
                            "storyboard",
                        ):
                            img_dir = os.path.join(folder_path, stage, "images")
                            if not os.path.isdir(img_dir):
                                continue
                            # 精确匹配
                            exact = os.path.join(img_dir, fname)
                            if os.path.isfile(exact):
                                shutil.copy2(exact, input_path)
                                logger.info(
                                    f"[ComfyUI] 从项目目录精确匹配: {fname}"
                                    f" ← {stage}/images/"
                                )
                                return fname
                            # 模糊匹配
                            for f in os.listdir(img_dir):
                                if fname in f or f in fname:
                                    fpath = os.path.join(img_dir, f)
                                    if os.path.isfile(fpath):
                                        shutil.copy2(fpath, input_path)
                                        logger.info(
                                            f"[ComfyUI] 从项目目录模糊匹配: {fname}"
                                            f" ← {stage}/images/{f}"
                                        )
                                        return fname
                    logger.warning(
                        f"[ComfyUI] 项目目录也找不到: {fname} (project={project_id})"
                    )
            except Exception as e:
                logger.warning(f"[ComfyUI] 项目目录搜索失败: {e}")

        # 所有来源都失败
        logger.error(
            f"[ComfyUI] 无法找到图片文件: {fname}"
            f"（output/缓存/HTTP/项目目录 均不可用）"
        )
        return fname  # 仍返回文件名，ComfyUI 会报更明确的错误

    # ── 参考图尺寸标准化 ──────────────────────────────────────

    async def normalize_reference_images(
        self, ref_items: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """检测并统一参考图的尺寸比例，确保融图质量

        如果参考图尺寸不一致，会将较小的图居中放置在最大尺寸的画布上，
        保持原始宽高比，背景填充为白色。
        """
        try:
            from PIL import Image

            if not ref_items or len(ref_items) <= 1:
                return ref_items

            # 下载所有参考图并获取尺寸
            sizes = []
            for item in ref_items:
                img_url = item.get("image_url", "")
                if not img_url:
                    sizes.append(None)
                    continue
                fname = await self.ensure_image_in_input_dir(img_url)
                img_path = os.path.join(self.input_dir, fname)
                if os.path.exists(img_path):
                    try:
                        with Image.open(img_path) as img:
                            sizes.append(img.size)
                    except Exception:
                        sizes.append(None)
                else:
                    sizes.append(None)

            # 检查是否所有尺寸一致
            valid_sizes = [s for s in sizes if s is not None]
            if not valid_sizes or len(set(valid_sizes)) <= 1:
                return ref_items

            # 找到最大尺寸
            max_w = max(s[0] for s in valid_sizes)
            max_h = max(s[1] for s in valid_sizes)

            logger.info(
                f"[ComfyUI] 参考图尺寸不一致，标准化到 {max_w}×{max_h}"
            )

            # 标准化每张图
            result = []
            for i, item in enumerate(ref_items):
                if sizes[i] is None or sizes[i] == (max_w, max_h):
                    result.append(item)
                    continue

                img_url = item.get("image_url", "")
                fname = await self.ensure_image_in_input_dir(img_url)
                img_path = os.path.join(self.input_dir, fname)

                try:
                    with Image.open(img_path) as img:
                        # 创建白色画布
                        canvas = Image.new("RGB", (max_w, max_h), (255, 255, 255))
                        # 居中放置
                        x = (max_w - img.width) // 2
                        y = (max_h - img.height) // 2
                        canvas.paste(img, (x, y))
                        # 保存标准化后的图
                        std_fname = f"std_{fname}"
                        std_path = os.path.join(self.input_dir, std_fname)
                        canvas.save(std_path)
                        # 更新 URL
                        new_item = dict(item)
                        new_item["image_url"] = std_fname
                        result.append(new_item)
                        logger.info(
                            f"[ComfyUI] 参考图标准化: {fname} ({sizes[i]})"
                            f" → {std_fname} ({max_w}×{max_h})"
                        )
                except Exception as e:
                    logger.warning(f"[ComfyUI] 参考图标准化失败: {fname} - {e}")
                    result.append(item)

            return result

        except ImportError:
            logger.warning("[ComfyUI] PIL 未安装，跳过参考图尺寸标准化")
            return ref_items
        except Exception as e:
            logger.warning(f"[ComfyUI] 参考图标准化异常: {e}")
            return ref_items

    # ── 输出文件管理 ──────────────────────────────────────────

    def get_output_path(self, filename: str) -> str:
        """获取输出文件的完整路径"""
        return os.path.join(self._output_dir, filename)

    def output_file_exists(self, filename: str) -> bool:
        """检查输出文件是否存在"""
        return os.path.exists(os.path.join(self._output_dir, filename))

    def copy_to_input(self, filename: str, unique_name: str = "") -> str:
        """将 output 目录的文件复制到 input 目录

        Args:
            filename: output 目录中的文件名
            unique_name: 目标文件名（可选，默认使用原文件名）

        Returns:
            input 目录中的文件名
        """
        src = os.path.join(self._output_dir, filename)
        dst_name = unique_name or filename
        dst = os.path.join(self.input_dir, dst_name)
        os.makedirs(self.input_dir, exist_ok=True)

        if os.path.exists(src):
            shutil.copy2(src, dst)
            return dst_name
        return dst_name

    # ── 输出文件清理 ──────────────────────────────────────────

    def cleanup_old_output_files(
        self,
        max_age_hours: int = 24,
        max_total_gb: float = 10.0,
        dry_run: bool = False,
    ) -> dict:
        """清理 ComfyUI output 目录中的旧文件，防止磁盘写满

        策略：
        1. 删除超过 max_age_hours 的文件
        2. 如果总大小超过 max_total_gb，按修改时间从旧到新删除直到低于阈值

        Args:
            max_age_hours: 文件最大保留时间（小时），默认 24
            max_total_gb: output 目录最大总大小（GB），默认 10GB
            dry_run: 仅统计不删除

        Returns:
            清理统计 {"deleted": int, "freed_mb": float, "remaining_mb": float}
        """
        if not os.path.isdir(self._output_dir):
            return {"deleted": 0, "freed_mb": 0.0, "remaining_mb": 0.0}

        now = time.time()
        max_age_secs = max_age_hours * 3600
        max_total_bytes = max_total_gb * 1024 * 1024 * 1024

        # 收集所有文件信息
        files = []
        for entry in os.scandir(self._output_dir):
            if entry.is_file():
                try:
                    stat = entry.stat()
                    files.append((entry.path, stat.st_mtime, stat.st_size))
                except OSError:
                    pass

        if not files:
            return {"deleted": 0, "freed_mb": 0.0, "remaining_mb": 0.0}

        # 按修改时间排序（旧→新）
        files.sort(key=lambda x: x[1])

        deleted = 0
        freed_bytes = 0

        # 阶段1：删除超龄文件
        for path, mtime, size in list(files):
            if now - mtime > max_age_secs:
                if not dry_run:
                    try:
                        os.remove(path)
                        deleted += 1
                        freed_bytes += size
                    except OSError as e:
                        logger.warning(f"[Cleanup] 删除失败: {path} | {e}")
                else:
                    deleted += 1
                    freed_bytes += size
                files.remove((path, mtime, size))

        # 阶段2：如果总大小超限，从最旧的开始删除
        remaining_bytes = sum(size for _, _, size in files)
        if remaining_bytes > max_total_bytes:
            for path, mtime, size in files:
                if remaining_bytes <= max_total_bytes:
                    break
                if not dry_run:
                    try:
                        os.remove(path)
                        deleted += 1
                        freed_bytes += size
                        remaining_bytes -= size
                    except OSError as e:
                        logger.warning(f"[Cleanup] 删除失败: {path} | {e}")
                else:
                    deleted += 1
                    freed_bytes += size
                    remaining_bytes -= size

        freed_mb = freed_bytes / (1024 * 1024)
        remaining_mb = remaining_bytes / (1024 * 1024)

        if deleted > 0:
            logger.info(
                f"[Cleanup] output 清理完成 | "
                f"deleted={deleted} | freed={freed_mb:.1f}MB | "
                f"remaining={remaining_mb:.1f}MB"
                f"{' (dry-run)' if dry_run else ''}"
            )

        return {
            "deleted": deleted,
            "freed_mb": round(freed_mb, 1),
            "remaining_mb": round(remaining_mb, 1),
        }
