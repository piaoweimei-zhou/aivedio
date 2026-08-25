"""
ModelScope 供应商

从 Infinite-Canvas generate_modelscope_provider_image() + generate_angle_cloud() 提取。
ModelScope 是阿里达摩院的 AI 模型平台，支持图片+三视图生成。
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

import httpx

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    extract_image_from_response,
    parse_size,
    reference_to_data_url,
    save_image_to_output,
)

logger = logging.getLogger(__name__)

MODELSCOPE_API_ROOT = "https://dashscope.aliyuncs.com/compatible-mode"
AI_REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=300.0, write=120.0, pool=20.0)
IMAGE_POLL_INTERVAL = 2  # 秒


class ModelScopeProvider(ProviderPlugin):
    """ModelScope 供应商"""

    provider_id = "modelscope"
    provider_name = "ModelScope"
    capabilities = ["image"]

    def is_available(self) -> bool:
        return bool(os.getenv("MODELSCOPE_API_KEY"))

    def _get_api_key(self) -> str:
        key = os.getenv("MODELSCOPE_API_KEY", "").strip()
        return key.removeprefix("Bearer ").strip()

    def _api_root(self) -> str:
        return os.getenv("MODELSCOPE_BASE_URL", MODELSCOPE_API_ROOT).rstrip("/")

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
            raise ValueError("未配置 MODELSCOPE_API_KEY")

        api_root = self._api_root()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-ModelScope-Async-Mode": "true",
        }

        w, h = parse_size(size)

        # 构建参考图
        refs = []
        for ref in (reference_images or [])[:4]:
            if not ref.get("url"):
                continue
            data_url = reference_to_data_url(ref, max_size=1536)
            if data_url:
                refs.append(data_url)

        payload = {
            "model": model or "Tongyi-MAI/Z-Image-Turbo",
            "prompt": prompt.strip(),
        }
        if w and h:
            payload["width"] = w
            payload["height"] = h
            payload["size"] = f"{w}x{h}"
        if refs:
            payload["image_url"] = refs

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            # 提交任务
            submit_res = await client.post(
                f"{api_root}/v1/images/generations", headers=headers, json=payload
            )  # noqa: E501
            submit_res.raise_for_status()
            raw = submit_res.json()
            task_id = raw.get("task_id")

            # 如果没有 task_id，尝试直接提取结果
            if not task_id:
                try:
                    img_data = extract_image_from_response(raw)
                    if img_data.get("value"):
                        local_url = await save_image_to_output(img_data, prefix="ms_")
                        elapsed = int((time.time() - start) * 1000)
                        return ProviderResult(
                            image_url=local_url,
                            images=[local_url],
                            elapsed_ms=elapsed,
                            prompt=prompt,
                            provider_id="modelscope",
                            model=model,
                            raw=raw,
                        )
                except Exception:
                    pass
                raise ValueError(f"ModelScope 未返回 task_id: {raw}")

            # 异步轮询
            deadline = time.monotonic() + 300
            last_payload = raw
            while time.monotonic() < deadline:
                await asyncio.sleep(IMAGE_POLL_INTERVAL)
                result = await client.get(
                    f"{api_root}/v1/tasks/{task_id}",
                    headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                )
                result.raise_for_status()
                data = result.json()
                last_payload = data
                status = str(data.get("task_status") or data.get("status") or "").upper()

                if status == "SUCCEED":
                    images = data.get("output_images") or []
                    if not images:
                        # 尝试从 data 字段提取
                        img_data = extract_image_from_response(data)
                        if img_data.get("value"):
                            images = [img_data["value"]]
                    if images:
                        local_urls = []
                        for url in images:
                            local_url = await save_image_to_output(
                                {"type": "url", "value": url}, prefix="ms_"
                            )  # noqa: E501
                            local_urls.append(local_url)
                        elapsed = int((time.time() - start) * 1000)
                        return ProviderResult(
                            image_url=local_urls[0],
                            images=local_urls,
                            elapsed_ms=elapsed,
                            prompt=prompt,
                            provider_id="modelscope",
                            model=model,
                            raw=data,
                        )
                    raise ValueError(f"ModelScope 成功但无图片: {data}")

                if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "TIMEOUT"}:
                    detail = data.get("error_info") or data.get("message") or str(data)
                    raise ValueError(f"ModelScope 任务失败: {detail}")

            raise TimeoutError(f"ModelScope 任务超时: {last_payload}")

    async def generate_angle(
        self,
        prompt: str,
        image_urls: List[str],
        model: str = "Qwen/Qwen-Image-Edit-2511",
        resolution: str = "",
        **kwargs,
    ) -> ProviderResult:
        """
        三视图生成（ModelScope 专用）

        Args:
            prompt: 提示词
            image_urls: 输入图片 URL 列表
            model: 模型名称
            resolution: 分辨率
        """
        start = time.time()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 MODELSCOPE_API_KEY")

        api_root = self._api_root()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-ModelScope-Async-Mode": "true",
        }

        payload = {
            "model": model,
            "prompt": prompt.strip(),
            "image_url": [reference_to_data_url({"url": url}, max_size=1536) for url in image_urls],
        }
        if resolution:
            payload["size"] = resolution

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            submit_res = await client.post(
                f"{api_root}/v1/images/generations", headers=headers, json=payload
            )  # noqa: E501
            submit_res.raise_for_status()
            raw = submit_res.json()
            task_id = raw.get("task_id")

            if not task_id:
                img_data = extract_image_from_response(raw)
                local_url = await save_image_to_output(img_data, prefix="ms_angle_")
                elapsed = int((time.time() - start) * 1000)
                return ProviderResult(
                    image_url=local_url,
                    images=[local_url],
                    elapsed_ms=elapsed,
                    prompt=prompt,
                    provider_id="modelscope",
                    model=model,
                )

            # 轮询
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                await asyncio.sleep(2)
                result = await client.get(
                    f"{api_root}/v1/tasks/{task_id}",
                    headers={**headers, "X-ModelScope-Task-Type": "image_generation"},
                )
                result.raise_for_status()
                data = result.json()
                status = str(data.get("task_status") or "").upper()

                if status == "SUCCEED":
                    images = data.get("output_images") or []
                    local_urls = []
                    for url in images:
                        local_url = await save_image_to_output(
                            {"type": "url", "value": url}, prefix="ms_angle_"
                        )  # noqa: E501
                        local_urls.append(local_url)
                    elapsed = int((time.time() - start) * 1000)
                    return ProviderResult(
                        image_url=local_urls[0] if local_urls else "",
                        images=local_urls,
                        elapsed_ms=elapsed,
                        prompt=prompt,
                        provider_id="modelscope",
                        model=model,
                    )

                if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "TIMEOUT"}:
                    raise ValueError(f"ModelScope 三视图任务失败: {data}")

            raise TimeoutError("ModelScope 三视图任务超时")
