"""
火山引擎 (VolcEngine) 供应商

从 Infinite-Canvas generate_volcengine_provider_image() 提取。
火山引擎方舟平台，支持图+视频生成。
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    bearer_auth,
    extract_image_from_response,
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
    capabilities = ["image", "video", "text"]

    def is_available(self) -> bool:
        return bool(os.getenv("ARK_API_KEY"))

    def _get_api_key(self) -> str:
        return os.getenv("ARK_API_KEY", "").strip()

    def _get_base_url(self) -> str:
        return os.getenv("VOLCENGINE_BASE_URL", "https://ark.cn-beijing.volces.com").rstrip("/")

    def _get_image_model(self) -> str:
        # 免费额度模型：doubao-seedream-4-5 / doubao-seedream-4-0 / doubao-seedream-5-0
        return os.getenv("VOLCENGINE_IMAGE_MODEL", "doubao-seedream-4-5").strip()

    def _get_video_model(self) -> str:
        # 免费额度模型：doubao-seedance-1-0-pro-fast / doubao-seedance-1-0-pro / doubao-seedance-1-0-lite-i2v  # noqa: E501
        # 注意：doubao-seedance-1-5-pro 已下线，勿再使用
        return os.getenv("VOLCENGINE_VIDEO_MODEL", "doubao-seedance-1-0-pro-fast").strip()

    def _get_text_model(self) -> str:
        # 免费额度模型：doubao-seed-2-0-pro / doubao-seed-1-6 / doubao-1-5-pro-32k
        return os.getenv("VOLCENGINE_TEXT_MODEL", "doubao-seed-2-0-pro").strip()

    def _normalize_size(self, size: str, model: str = "") -> str:
        """规范化尺寸为火山引擎支持的格式（Seedream 4.x 需总像素 ≥ 3686400）"""
        w, h = parse_size(size)
        if not w or not h:
            return "2048x2048"
        ratio = w / h
        # Seedream 4.x 2K 推荐尺寸（官方文档，均满足最小像素要求）
        supported = [
            (2048, 2048, 1.0),  # 1:1
            (2304, 1728, 4 / 3),  # 4:3
            (1728, 2304, 3 / 4),  # 3:4
            (2848, 1600, 16 / 9),  # 16:9
            (1600, 2848, 9 / 16),  # 9:16
            (2496, 1664, 3 / 2),  # 3:2
            (1664, 2496, 2 / 3),  # 2:3
            (3136, 1344, 21 / 9),  # 21:9
        ]
        best = min(supported, key=lambda s: abs(s[2] - ratio))
        return f"{best[0]}x{best[1]}"

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs,
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
            "model": model or self._get_image_model(),
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
        **kwargs,
    ) -> ProviderResult:
        start = time.time()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 ARK_API_KEY")

        base_url = self._get_base_url()
        endpoint = f"{base_url}/api/v3/contents/generations/tasks"
        headers = {
            "Accept": "application/json",
            "Authorization": bearer_auth(api_key),
            "Content-Type": "application/json",
        }

        body = {
            "model": model or self._get_video_model(),
            "content": [{"type": "text", "text": prompt}],
        }
        if duration:
            body["duration"] = int(duration)
        if aspect_ratio:
            body["ratio"] = aspect_ratio
        if resolution:
            body["resolution"] = resolution
        if seed is not None:
            body["seed"] = int(seed)

        # 参考图（图生视频-首帧）：本地路径转 base64，远程 URL 原样
        if images:
            for url in images[:1]:
                data_url = reference_to_data_url({"url": url}, max_size=1536)
                if data_url:
                    body["content"].append({"type": "image_url", "image_url": {"url": data_url}})

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

            # 轮询（Seedance 查询端点：/contents/generations/tasks/{id}）
            query_url = f"{base_url}/api/v3/contents/generations/tasks/{task_id}"
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(5)
                query_res = await client.get(query_url, headers=headers)
                query_res.raise_for_status()
                result = query_res.json()
                status = str(result.get("status") or "").lower()
                if status in {"succeeded", "complete", "completed"}:
                    video_url = ""
                    content = result.get("content") or {}
                    if isinstance(content, dict):
                        video_url = content.get("video_url", "")
                    if not video_url:
                        for item in result.get("data", []):
                            if isinstance(item, dict) and item.get("url"):
                                video_url = item["url"]
                                break
                    if video_url:
                        local_url = await save_video_to_output(video_url, prefix="volc_vid_")
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
                if status in {"failed", "error", "expired"}:
                    raise ValueError(f"火山引擎视频任务失败: {result}")

            raise TimeoutError("火山引擎视频任务超时")

    async def generate_text(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> ProviderResult:
        """文本生成 — 方舟 OpenAI 兼容 /chat/completions（豆包系列）"""
        start = time.time()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 ARK_API_KEY")

        base_url = self._get_base_url()
        used_model = model or self._get_text_model()
        headers = {
            "Accept": "application/json",
            "Authorization": bearer_auth(api_key),
            "Content-Type": "application/json",
        }

        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": used_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            body["response_format"] = response_format

        chat_url = f"{base_url}/api/v3/chat/completions"
        logger.info(
            f"[VolcEngine] 文本生成 | model={used_model} | messages={len(messages)} "
            f"| temp={temperature} | json_mode={'是' if response_format else '否'}"
        )

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            response = await client.post(chat_url, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()

        elapsed = int((time.time() - start) * 1000)
        text = ""
        try:
            text = raw["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            logger.warning(f"[VolcEngine] 解析文本响应失败 | raw={str(raw)[:300]}")

        return ProviderResult(
            image_url="",
            images=[],
            filenames=[],
            elapsed_ms=elapsed,
            prompt=prompt,
            provider_id="volcengine",
            model=used_model,
            raw=raw,
            task_id=raw.get("id", ""),
            metadata={"text": text, "usage": raw.get("usage", {})},
        )
