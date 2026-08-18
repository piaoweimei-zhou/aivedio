"""
火山引擎 (VolcEngine) 供应商

从 Infinite-Canvas generate_volcengine_provider_image() 提取。
火山引擎方舟平台，支持图+视频生成。
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    bearer_auth,
    extract_image_from_response,
    output_file_from_url,
    parse_size,
    reference_to_data_url,
    save_image_to_output,
    save_video_to_output,
)

logger = logging.getLogger(__name__)

AI_REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=300.0, write=120.0, pool=20.0)
VIDEO_POLL_TIMEOUT = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=20.0)


class VolcEngineProvider(ProviderPlugin):
    """火山引擎方舟供应商"""

    provider_id = "volcengine"
    provider_name = "火山引擎 (方舟)"
    capabilities = ["image", "video"]

    def is_available(self) -> bool:
        return bool(os.getenv("ARK_API_KEY"))

    def _get_api_key(self) -> str:
        return os.getenv("ARK_API_KEY", "").strip()

    def _get_base_url(self) -> str:
        return os.getenv("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com").rstrip("/")

    def _normalize_size(self, size: str, model: str = "") -> str:
        """规范化尺寸为火山引擎支持的格式"""
        w, h = parse_size(size)
        if not w or not h:
            return "1024x1024"
        # 火山引擎支持特定尺寸
        supported = [
            (512, 512), (768, 768), (1024, 1024),
            (768, 1024), (1024, 768),
            (768, 1365), (1365, 768),
            (720, 1280), (1280, 720),
        ]
        # 找最接近的
        best = min(supported, key=lambda s: abs(s[0] - w) + abs(s[1] - h))
        return f"{best[0]}x{best[1]}"

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs
    ) -> ProviderResult:
        start = time.time()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 ARK_API_KEY")

        base_url = self._get_base_url()
        endpoint = f"{base_url}/api/v3/images/generations"
        headers = {
            "Accept": "application/json",
            "Authorization": bearer_auth(api_key),
            "Content-Type": "application/json",
        }

        normalized_size = self._normalize_size(size, model)
        body = {
            "model": model or "doubao-seedream-3-0-t2i-250415",
            "prompt": prompt,
            "size": normalized_size,
            "response_format": "url",
        }

        # 参考图
        images = []
        for ref in (reference_images or [])[:10]:
            data_url = reference_to_data_url(ref, max_size=1536)
            if data_url:
                images.append(data_url)
        if images:
            body["image"] = images

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()
            img_data = extract_image_from_response(raw)
            local_url = await save_image_to_output(img_data, prefix="volc_")

            elapsed = int((time.time() - start) * 1000)
            return ProviderResult(
                image_url=local_url,
                images=[local_url],
                elapsed_ms=elapsed,
                prompt=prompt,
                provider_id="volcengine",
                model=model,
                raw=raw,
            )

    async def generate_video(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：接受统一参数（volcengine 当前通过 resolution 字符串控制分辨率）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs
    ) -> ProviderResult:
        start = time.time()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 ARK_API_KEY")

        base_url = self._get_base_url()
        endpoint = f"{base_url}/api/v3/contents/generations"
        headers = {
            "Accept": "application/json",
            "Authorization": bearer_auth(api_key),
            "Content-Type": "application/json",
        }

        body = {
            "model": model or "doubao-seaweed",
            "content": [{"type": "text", "text": prompt}],
        }

        # 参考图
        if images:
            for url in images[:1]:
                body["content"].append({"type": "image_url", "image_url": {"url": url}})

        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()
            task_id = raw.get("id")

            if not task_id:
                # 尝试直接获取结果
                video_urls = []
                for item in raw.get("data", []):
                    if isinstance(item, dict):
                        url = item.get("url", "")
                        if url:
                            video_urls.append(url)
                if video_urls:
                    local_url = await save_video_to_output(video_urls[0], prefix="volc_vid_")
                    elapsed = int((time.time() - start) * 1000)
                    return ProviderResult(
                        video_url=local_url,
                        elapsed_ms=elapsed,
                        prompt=prompt,
                        provider_id="volcengine",
                        model=model,
                        status="succeeded",
                    )
                raise ValueError(f"火山引擎视频未返回任务 ID: {raw}")

            # 轮询
            query_url = f"{base_url}/api/v3/contents/generations/{task_id}"
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(5)
                query_res = await client.get(query_url, headers=headers)
                query_res.raise_for_status()
                result = query_res.json()
                status = str(result.get("status") or "").lower()
                if status in {"succeeded", "complete", "completed"}:
                    video_urls = []
                    for item in result.get("data", []):
                        if isinstance(item, dict):
                            url = item.get("url", "")
                            if url:
                                video_urls.append(url)
                    if video_urls:
                        local_url = await save_video_to_output(video_urls[0], prefix="volc_vid_")
                        elapsed = int((time.time() - start) * 1000)
                        return ProviderResult(
                            video_url=local_url,
                            elapsed_ms=elapsed,
                            prompt=prompt,
                            provider_id="volcengine",
                            model=model,
                            status="succeeded",
                        )
                    raise ValueError(f"火山引擎视频成功但无输出: {result}")
                if status in {"failed", "error"}:
                    raise ValueError(f"火山引擎视频任务失败: {result}")

            raise TimeoutError("火山引擎视频任务超时")
