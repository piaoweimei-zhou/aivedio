"""
即梦 (Jimeng) CLI 供应商

从 Infinite-Canvas generate_jimeng_provider_image() 提取。
即梦 CLI 是字节跳动的 AI 图片/视频生成命令行工具。
"""

import asyncio
import logging
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    output_file_from_url,
    output_url_for,
    parse_size,
    save_image_to_output,
    save_video_to_output,
)

logger = logging.getLogger(__name__)


class JimengProvider(ProviderPlugin):
    """即梦 CLI 供应商"""

    provider_id = "jimeng"
    provider_name = "即梦 (Jimeng CLI)"
    capabilities = ["image", "video"]

    def is_available(self) -> bool:
        return shutil.which("jimeng") is not None or bool(os.getenv("JIMENG_CLI_PATH"))

    def _cli_path(self) -> str:
        return os.getenv("JIMENG_CLI_PATH", "jimeng")

    def _poll_seconds(self) -> int:
        return int(os.getenv("JIMENG_POLL_SECONDS", "30"))

    async def _run_cli(self, args: List[str], timeout: int = 300) -> str:
        """运行即梦 CLI 命令"""
        cli = self._cli_path()
        cmd = [cli] + args
        logger.info(f"[Jimeng] 执行: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace")
            if proc.returncode != 0:
                error = stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"即梦 CLI 失败 (code={proc.returncode}): {error}")
            return output
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError("即梦 CLI 超时")

    def _parse_ratio(self, size: str) -> str:
        """从尺寸推断比例"""
        w, h = parse_size(size)
        if not w or not h:
            return "1:1"
        from math import gcd
        g = gcd(w, h)
        return f"{w // g}:{h // g}"

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs
    ) -> ProviderResult:
        start = time.time()
        refs = [ref for ref in (reference_images or []) if ref.get("url")]
        temp_paths = []

        try:
            if refs:
                # 图生图
                ref_url = refs[0].get("url", "")
                local_path = output_file_from_url(ref_url)
                if not local_path or not os.path.exists(local_path):
                    raise ValueError(f"参考图不存在: {ref_url}")

                args = [
                    "image2image",
                    f"--images={local_path}",
                    f"--prompt={prompt}",
                    f"--resolution_type=standard",
                    f"--poll={self._poll_seconds()}",
                ]
            else:
                # 文生图
                ratio = self._parse_ratio(size)
                args = [
                    "text2image",
                    f"--prompt={prompt}",
                    f"--ratio={ratio}",
                    f"--resolution_type=standard",
                    f"--poll={self._poll_seconds()}",
                ]

            output = await self._run_cli(args, timeout=self._poll_seconds() + 120)

            # 解析输出中的 URL
            urls = self._extract_urls(output)
            if not urls:
                raise RuntimeError(f"即梦 CLI 未返回图片 URL: {output[:500]}")

            # 保存到本地
            local_urls = []
            for url in urls:
                local_url = await save_image_to_output({"type": "url", "value": url}, prefix="jm_")
                local_urls.append(local_url)

            elapsed = int((time.time() - start) * 1000)
            return ProviderResult(
                image_url=local_urls[0] if local_urls else "",
                images=local_urls,
                elapsed_ms=elapsed,
                prompt=prompt,
                provider_id="jimeng",
                model=model,
            )
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except Exception:
                    pass

    async def generate_video(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：接受统一参数（jimeng 通过 duration 秒数控制时长）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs
    ) -> ProviderResult:
        start = time.time()
        temp_paths = []

        try:
            if images and len(images) >= 1:
                # 图生视频
                local_path = output_file_from_url(images[0])
                if not local_path or not os.path.exists(local_path):
                    raise ValueError(f"参考图不存在: {images[0]}")

                args = [
                    "image2video",
                    f"--image={local_path}",
                    f"--prompt={prompt}",
                    f"--duration={max(1, min(60, int(duration)))}",
                    f"--poll={self._poll_seconds()}",
                ]
            else:
                # 文生视频
                args = [
                    "text2video",
                    f"--prompt={prompt}",
                    f"--duration={max(1, min(60, int(duration)))}",
                    f"--poll={self._poll_seconds()}",
                ]

            output = await self._run_cli(args, timeout=self._poll_seconds() + 300)

            # 解析输出中的视频 URL
            urls = self._extract_urls(output)
            if not urls:
                raise RuntimeError(f"即梦 CLI 未返回视频 URL: {output[:500]}")

            local_url = await save_video_to_output(urls[0], prefix="jm_vid_")

            elapsed = int((time.time() - start) * 1000)
            return ProviderResult(
                video_url=local_url,
                elapsed_ms=elapsed,
                prompt=prompt,
                provider_id="jimeng",
                model=model,
                status="succeeded",
            )
        finally:
            for path in temp_paths:
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _extract_urls(self, output: str) -> List[str]:
        """从 CLI 输出中提取 URL"""
        import re
        urls = re.findall(r'https?://[^\s"\'<>]+\.(?:png|jpg|jpeg|webp|mp4|webm)', output)
        if not urls:
            # 尝试更宽松的匹配
            urls = re.findall(r'https?://[^\s"\'<>]+', output)
        return urls
