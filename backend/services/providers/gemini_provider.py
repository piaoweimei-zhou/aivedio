"""
Gemini 供应商

从 Infinite-Canvas generate_gemini_provider_image() 提取。
Google Gemini 多模态模型，支持图片生成。
"""

import logging
import os
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import httpx

from services.provider_service import ProviderPlugin, ProviderResult
from services.providers.provider_utils import (
    extract_image_from_response,
    parse_size,
    reference_to_data_url,
    save_image_to_output,
)

logger = logging.getLogger(__name__)

GEMINI_REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=1800.0, write=120.0, pool=20.0)


class GeminiProvider(ProviderPlugin):
    """Gemini 供应商"""

    provider_id = "gemini"
    provider_name = "Gemini"
    capabilities = ["image"]

    def is_available(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def _get_api_key(self) -> str:
        return os.getenv("GEMINI_API_KEY", "").strip()

    def _get_base_url(self) -> str:
        return os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")

    def _model_name(self, model: str) -> str:
        name = (model or "gemini-2.0-flash-exp").strip()
        return name[len("models/") :] if name.startswith("models/") else name

    def _endpoint_url(self, model: str) -> str:
        base = self._get_base_url()
        model_name = urllib.parse.quote(self._model_name(model), safe="")
        return f"{base}/v1beta/models/{model_name}:generateContent"

    def _image_config(self, size: str) -> Dict[str, Any]:
        w, h = parse_size(size)
        if not w or not h:
            return {"aspectRatio": "1:1", "imageSize": "2K"}
        # 推断比例
        from math import gcd

        g = gcd(w, h)
        ratio = f"{w // g}:{h // g}"
        # 推断分辨率
        max_dim = max(w, h)
        if max_dim >= 2048:
            res = "4K"
        elif max_dim >= 1536:
            res = "2K"
        else:
            res = "1K"
        return {"aspectRatio": ratio, "imageSize": res}

    def _reference_part(self, ref: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """将参考图转为 Gemini 格式"""
        value = reference_to_data_url(ref, max_size=1536)
        if not value:
            return None
        if isinstance(value, str) and value.startswith("data:image/") and ";base64," in value:
            header, encoded = value.split(";base64,", 1)
            mime_type = header.replace("data:", "", 1) or "image/png"
            return {"inlineData": {"mimeType": mime_type, "data": encoded}}
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return {"fileData": {"mimeType": "image/png", "fileUri": value}}
        return None

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
            raise ValueError("未配置 GEMINI_API_KEY")

        endpoint = self._endpoint_url(model)
        headers = {
            "Accept": "application/json",
            "x-goog-api-key": api_key,
            "Content-Type": "application/json",
        }

        parts = [{"text": prompt.strip()}]
        for ref in (reference_images or [])[:16]:
            part = self._reference_part(ref)
            if part:
                parts.append(part)

        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": self._image_config(size),
            },
        }

        async with httpx.AsyncClient(timeout=GEMINI_REQUEST_TIMEOUT) as client:
            response = await client.post(endpoint, headers=headers, json=body)
            response.raise_for_status()
            raw = response.json()
            img_data = extract_image_from_response(raw)
            local_url = await save_image_to_output(img_data, prefix="gem_")

            elapsed = int((time.time() - start) * 1000)
            return ProviderResult(
                image_url=local_url,
                images=[local_url],
                elapsed_ms=elapsed,
                prompt=prompt,
                provider_id="gemini",
                model=model,
                raw=raw,
            )
