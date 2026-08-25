"""
RunningHub 供应商

从 Infinite-Canvas generate_runninghub_provider_image() 提取。
RunningHub 是云端 ComfyUI 工作流平台，支持图+视频生成。
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional

import httpx

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    bearer_auth,
    extract_image_from_response,
    output_file_from_url,
    parse_size,
    save_image_to_output,
    save_video_to_output,
)

logger = logging.getLogger(__name__)

RUNNINGHUB_DEFAULT_BASE_URL = "https://www.runninghub.cn"
VIDEO_POLL_TIMEOUT = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=20.0)


class RunningHubProvider(ProviderPlugin):
    """RunningHub 云端工作流供应商"""

    provider_id = "runninghub"
    provider_name = "RunningHub (云端)"
    capabilities = ["image", "video"]

    def is_available(self) -> bool:
        return bool(os.getenv("RUNNINGHUB_API_KEY"))

    def _get_api_key(self) -> str:
        key = os.getenv("RUNNINGHUB_API_KEY", "").strip()
        return key.removeprefix("Bearer ").strip()

    def _get_wallet_key(self) -> str:
        return os.getenv("RUNNINGHUB_WALLET_API_KEY", "").strip()

    def _headers(self, use_wallet: bool = False) -> Dict[str, str]:
        api_key = (
            self._get_wallet_key() if use_wallet and self._get_wallet_key() else self._get_api_key()
        )  # noqa: E501
        if not api_key:
            raise ValueError("未配置 RunningHub API Key")
        return {
            "Authorization": bearer_auth(api_key),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs,
    ) -> ProviderResult:
        start = time.time()
        headers = self._headers()
        base_url = RUNNINGHUB_DEFAULT_BASE_URL

        # 构建 payload
        body = {"prompt": prompt}
        w, h = parse_size(size)
        if w and h:
            body["width"] = w
            body["height"] = h

        # 参考图上传
        image_urls = []
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20.0, read=300.0, write=180.0, pool=20.0)
        ) as client:  # noqa: E501
            for ref in (reference_images or [])[:10]:
                url = ref.get("url", "")
                if not url:
                    continue
                # 上传参考图到 RunningHub
                try:
                    upload_url = f"{base_url}/task/openapi/upload"
                    local_path = output_file_from_url(url)
                    if local_path and os.path.exists(local_path):
                        with open(local_path, "rb") as f:
                            upload_res = await client.post(
                                upload_url,
                                headers={"Authorization": headers["Authorization"]},
                                files={"file": (os.path.basename(local_path), f)},
                            )
                            if upload_res.status_code == 200:
                                upload_data = upload_res.json()
                                if upload_data.get("data", {}).get("url"):
                                    image_urls.append(upload_data["data"]["url"])
                except Exception as e:
                    logger.warning(f"RunningHub 参考图上传失败: {e}")

            if image_urls:
                body["imageUrls"] = image_urls

            # 提交任务
            endpoint = kwargs.get("endpoint") or model or "comfyui_default"
            task_url = f"{base_url}/task/openapi/create"
            body["nodeId"] = endpoint

            response = await client.post(task_url, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()

            # 尝试直接获取图片
            try:
                img_data = extract_image_from_response(raw)
                if img_data.get("value"):
                    local_url = await save_image_to_output(img_data, prefix="rh_")
                    elapsed = int((time.time() - start) * 1000)
                    return ProviderResult(
                        image_url=local_url,
                        images=[local_url],
                        elapsed_ms=elapsed,
                        prompt=prompt,
                        provider_id="runninghub",
                        model=model,
                        raw=raw,
                    )
            except Exception:
                pass

            # 异步轮询
            task_id = raw.get("taskId") or raw.get("task_id")
            if not task_id:
                raise ValueError(f"RunningHub 未返回 taskId: {raw}")

            query_url = f"{base_url}/task/openapi/query"
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                query_res = await client.post(query_url, headers=headers, json={"taskId": task_id})
                query_res.raise_for_status()
                result = query_res.json()
                status = str(result.get("status") or "").upper()
                if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "3"}:
                    # 提取输出
                    outputs = result.get("data", {}).get("outputs", [])
                    urls = []
                    for out in outputs:
                        if isinstance(out, dict):
                            for url in (out.get("url"), out.get("image_url"), out.get("video_url")):
                                if url and str(url).startswith(("http://", "https://")):
                                    urls.append(url)
                    if urls:
                        local_url = await save_image_to_output(
                            {"type": "url", "value": urls[0]}, prefix="rh_"
                        )  # noqa: E501
                        elapsed = int((time.time() - start) * 1000)
                        return ProviderResult(
                            image_url=local_url,
                            images=[local_url],
                            elapsed_ms=elapsed,
                            prompt=prompt,
                            provider_id="runninghub",
                            model=model,
                            raw=result,
                        )
                    raise ValueError(f"RunningHub 成功但无输出: {result}")
                if status in {"FAILED", "FAIL", "ERROR", "4"}:
                    raise ValueError(f"RunningHub 任务失败: {result}")

            raise TimeoutError("RunningHub 任务超时")

    async def generate_video(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：接受统一参数（RunningHub 通过 duration 控制时长）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs,
    ) -> ProviderResult:
        start = time.time()
        headers = self._headers(use_wallet=True)
        base_url = RUNNINGHUB_DEFAULT_BASE_URL

        body = {
            "prompt": prompt,
            "duration": str(max(1, min(60, int(duration)))),
        }

        async with httpx.AsyncClient(timeout=VIDEO_POLL_TIMEOUT) as client:
            # 上传参考图
            image_urls = []
            for url in (images or [])[:10]:
                local_path = output_file_from_url(url)
                if local_path and os.path.exists(local_path):
                    try:
                        with open(local_path, "rb") as f:
                            upload_res = await client.post(
                                f"{base_url}/task/openapi/upload",
                                headers={"Authorization": headers["Authorization"]},
                                files={"file": (os.path.basename(local_path), f)},
                            )
                            if upload_res.status_code == 200:
                                upload_data = upload_res.json()
                                if upload_data.get("data", {}).get("url"):
                                    image_urls.append(upload_data["data"]["url"])
                    except Exception as e:
                        logger.warning(f"RunningHub 参考图上传失败: {e}")

            if image_urls:
                body["imageUrls"] = image_urls

            endpoint = kwargs.get("endpoint") or model or "comfyui_video"
            body["nodeId"] = endpoint

            # 提交任务
            task_url = f"{base_url}/task/openapi/create"
            response = await client.post(task_url, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()
            task_id = raw.get("taskId") or raw.get("task_id")
            if not task_id:
                raise ValueError(f"RunningHub 未返回 taskId: {raw}")

            # 轮询
            query_url = f"{base_url}/task/openapi/query"
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                await asyncio.sleep(3)
                query_res = await client.post(query_url, headers=headers, json={"taskId": task_id})
                query_res.raise_for_status()
                result = query_res.json()
                status = str(result.get("status") or "").upper()
                if status in {"SUCCESS", "SUCCEEDED", "COMPLETED", "3"}:
                    outputs = result.get("data", {}).get("outputs", [])
                    video_urls = []
                    for out in outputs:
                        if isinstance(out, dict):
                            url = out.get("url") or out.get("video_url", "")
                            if url and str(url).startswith(("http://", "https://")):
                                video_urls.append(url)
                    if video_urls:
                        local_url = await save_video_to_output(video_urls[0], prefix="rh_vid_")
                        elapsed = int((time.time() - start) * 1000)
                        return ProviderResult(
                            video_url=local_url,
                            elapsed_ms=elapsed,
                            prompt=prompt,
                            provider_id="runninghub",
                            model=model,
                            status="succeeded",
                            raw=result,
                        )
                    raise ValueError(f"RunningHub 视频成功但无输出: {result}")
                if status in {"FAILED", "FAIL", "ERROR", "4"}:
                    raise ValueError(f"RunningHub 视频任务失败: {result}")

            raise TimeoutError("RunningHub 视频任务超时")
