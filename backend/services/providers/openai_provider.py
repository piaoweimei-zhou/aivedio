"""
OpenAI 兼容协议供应商

支持所有 OpenAI /v1/images/generations、/v1/images/edits、/v1/chat/completions 协议的供应商。
从 Infinite-Canvas generate_ai_image() 提取核心逻辑。

文本生成：通过 /v1/chat/completions 接口，兼容 DeepSeek、通义千问、Kimi 等 OpenAI 协议。
"""

import json
import logging
import os
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
)

logger = logging.getLogger(__name__)

# 默认超时（文本生成通常较快，但 DeepSeek 思考模型可能较慢，保留 300s）
AI_REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=300.0, write=120.0, pool=20.0)

# 默认文本模型（可通过 OPENAI_TEXT_MODEL 环境变量覆盖，例如 "deepseek-chat"）
_DEFAULT_TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "deepseek-chat")


class OpenAICompatProvider(ProviderPlugin):
    """OpenAI 兼容协议供应商"""

    provider_id = "openai_compat"
    provider_name = "OpenAI 兼容"
    capabilities = ["image", "text"]

    def is_available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY") or os.getenv("COMFYUI_API_KEY"))

    def _get_base_url(self) -> str:
        return os.getenv("OPENAI_BASE_URL", os.getenv("COMFYUI_BASE_URL", "https://api.openai.com")).rstrip("/")

    def _get_api_key(self) -> str:
        return os.getenv("OPENAI_API_KEY") or os.getenv("COMFYUI_API_KEY") or ""

    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs
    ) -> ProviderResult:
        start = time.time()
        base_url = self._get_base_url()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 API Key (OPENAI_API_KEY 或 COMFYUI_API_KEY)")

        headers = {
            "Accept": "application/json",
            "Authorization": bearer_auth(api_key),
            "Content-Type": "application/json",
        }

        refs = [ref for ref in (reference_images or []) if ref.get("url")]
        image_refs = [ref for ref in refs if str(ref.get("role", "")).lower() != "mask"]
        mask_refs = [ref for ref in refs if str(ref.get("role", "")).lower() == "mask"]

        gen_url = f"{base_url}/v1/images/generations"
        edit_url = f"{base_url}/v1/images/edits"

        async with httpx.AsyncClient(timeout=AI_REQUEST_TIMEOUT) as client:
            # 文生图
            if not image_refs:
                body = {"model": model or "gpt-image-1", "prompt": prompt, "size": size}
                response = await client.post(gen_url, headers=headers, json=body)
                response.raise_for_status()
                raw = response.json()
                img_data = extract_image_from_response(raw)
                local_url = await save_image_to_output(img_data, prefix="oai_")
                elapsed = int((time.time() - start) * 1000)
                return ProviderResult(
                    image_url=local_url,
                    images=[local_url],
                    elapsed_ms=elapsed,
                    prompt=prompt,
                    provider_id="openai_compat",
                    model=model,
                    raw=raw,
                )

            # 图生图 — 使用 /v1/images/edits (multipart)
            files = []
            opened = []
            try:
                for ref in image_refs[:4]:
                    path = output_file_from_url(ref.get("url", ""))
                    if not path or not os.path.exists(path):
                        continue
                    fh = open(path, "rb")
                    opened.append(fh)
                    ext = os.path.splitext(path)[1].lower()
                    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
                    files.append(("image", (os.path.basename(path), fh, mime)))

                if mask_refs:
                    mask_path = output_file_from_url(mask_refs[0].get("url", ""))
                    if mask_path and os.path.exists(mask_path):
                        fh = open(mask_path, "rb")
                        opened.append(fh)
                        files.append(("mask", (os.path.basename(mask_path), fh, "image/png")))

                data = {"model": model or "gpt-image-1", "prompt": prompt, "size": size}
                response = await client.post(edit_url, headers={"Authorization": bearer_auth(api_key)}, data=data, files=files)

                # 如果 edits 失败，回退到 generations + image_urls
                if response.status_code >= 400:
                    # 尝试 JSON 模式
                    body = {"model": model or "gpt-image-1", "prompt": prompt, "size": size}
                    image_urls = [reference_to_data_url(ref, max_size=1536) for ref in image_refs[:16]]
                    if image_urls:
                        body["image_urls"] = image_urls
                    response = await client.post(gen_url, headers=headers, json=body)
                    response.raise_for_status()

                raw = response.json()
                img_data = extract_image_from_response(raw)
                local_url = await save_image_to_output(img_data, prefix="oai_")
                elapsed = int((time.time() - start) * 1000)
                return ProviderResult(
                    image_url=local_url,
                    images=[local_url],
                    elapsed_ms=elapsed,
                    prompt=prompt,
                    provider_id="openai_compat",
                    model=model,
                    raw=raw,
                )
            finally:
                for fh in opened:
                    try:
                        fh.close()
                    except Exception:
                        pass

    async def generate_text(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs
    ) -> ProviderResult:
        """统一文本生成接口 — 调用 /v1/chat/completions

        兼容 DeepSeek、通义千问、Kimi、智谱等 OpenAI 协议服务。

        Args:
            prompt: 用户提示词
            system: 系统提示词（角色设定）
            model: 模型名（默认走 OPENAI_TEXT_MODEL 环境变量 / "deepseek-chat"）
            temperature: 0-2，控制创造性
            max_tokens: 最大输出 token
            response_format: {"type": "json_object"} 启用 JSON 模式
            history: 多轮对话历史 [{"role": "user/assistant", "content": "..."}]

        Returns:
            ProviderResult，文本内容在 raw.choices[0].message.content
            （同时通过 ProviderResult.metadata["text"] 暴露，方便上层使用）
        """
        start = time.time()
        base_url = self._get_base_url()
        api_key = self._get_api_key()
        if not api_key:
            raise ValueError("未配置 API Key (OPENAI_API_KEY 或 COMFYUI_API_KEY)")

        used_model = model or _DEFAULT_TEXT_MODEL
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

        chat_url = f"{base_url}/v1/chat/completions"
        logger.info(
            f"[OpenAICompat] 文本生成 | model={used_model} | messages={len(messages)} "
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
            logger.warning(f"[OpenAICompat] 解析文本响应失败 | raw={str(raw)[:300]}")

        return ProviderResult(
            image_url="",
            images=[],
            filenames=[],
            elapsed_ms=elapsed,
            prompt=prompt,
            provider_id="openai_compat",
            model=used_model,
            raw=raw,
            task_id=raw.get("id", ""),
            metadata={"text": text, "usage": raw.get("usage", {})},
        )
