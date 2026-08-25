"""
ComfyUI 服务 — TTS 音频 Mixin

文本转语音生成与音频波形处理。
"""

import aiohttp
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional


from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult, logger


class ComfyUITTSMixin:
    async def generate_tts_audio(
        self,
        text: str,
        mode: str = "voice_design",
        voice_description: str = "",
        ref_audio_url: str = "",
        ref_text: str = "",
        language: str = "Auto",
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> ComfyUIGenResult:
        """
        生成 TTS 音频（基于 Qwen3-TTS 工作流）

        Args:
            text: 要合成的文本（必填）
            mode: 'voice_design' 音色设计 | 'voice_clone' 音色克隆
            voice_description: 音色设计模式的音色描述（如"清脆童声，8岁女童"）
            ref_audio_url: 音色克隆模式的参考音频 URL（必填，克隆模式）
            ref_text: 参考音频对应的文本（可选，空则由 Whisper 自动识别）
            language: 语言，默认 Auto

        Returns:
            ComfyUIGenResult，image_url 字段为音频文件 URL，filename 为音频文件名
        """
        from pathlib import Path

        start = time.time()

        if not text or not text.strip():
            raise ValueError("TTS 文本不能为空")

        # 1. 选择并加载工作流
        workflow_file = (
            "Qwen3+TTS+音色设计.json" if mode == "voice_design" else "Qwen3+TTS+音频克隆.json"
        )
        workflow_dir = Path(__file__).parent.parent.parent / "workflows"
        workflow_path = workflow_dir / workflow_file
        if not workflow_path.exists():
            from services.comfyui.config import WORKFLOWS_DIR

            workflow_path = Path(WORKFLOWS_DIR) / workflow_file
        if not workflow_path.exists():
            raise FileNotFoundError(f"TTS 工作流不存在: {workflow_file}")

        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        logger.info(
            f"[ComfyUI-TTS] 加载工作流 | mode={mode} | file={workflow_file} | "
            f"text_len={len(text)} | tag={asset_tag}"
        )

        # 2. 修改节点参数
        if mode == "voice_design":
            # 节点25: JWString - 要合成的文本
            if "25" in workflow:
                workflow["25"]["inputs"]["text"] = text
            # 节点26: PrimitiveStringMultiline - 音色描述
            if "26" in workflow:
                workflow["26"]["inputs"]["value"] = (
                    voice_description or "成年女性，温柔亲切，语速适中"
                )
            # 节点22: TDQwen3TTSVoiceDesign - 语言
            if "22" in workflow:
                workflow["22"]["inputs"]["language"] = language
        else:
            # voice_clone 模式
            # 节点31: JWString - 要合成的文本
            if "31" in workflow:
                workflow["31"]["inputs"]["text"] = text
            # 节点27: TDQwen3TTSVoiceClone - 语言
            if "27" in workflow:
                workflow["27"]["inputs"]["language"] = language
            # 节点17: LoadAudio - 参考音频
            if not ref_audio_url:
                raise ValueError("音色克隆模式必须提供 ref_audio_url")
            ref_filename = await self._download_audio_to_input(ref_audio_url)
            if "17" in workflow:
                workflow["17"]["inputs"]["audio"] = ref_filename
                workflow["17"]["inputs"][
                    "audioUI"
                ] = f"/api/view?filename={ref_filename}&type=input&subfolder=&rand=0.5"

        # 3. 提交工作流
        try:
            prompt_id = await self._queue_prompt_with_retry(workflow)
        except Exception as e:
            logger.error(f"[ComfyUI-TTS] 提交失败 | error={e}")
            raise

        # 4. 等待完成
        output_filenames = await self._wait_for_completion(
            prompt_id, progress_callback, task_type="tts"
        )

        if not output_filenames:
            raise RuntimeError("TTS 生成失败：无输出文件")

        # 5. 找到音频文件（SaveAudio 输出 .flac 或 .wav）
        audio_filename = None
        for fname in output_filenames:
            lower = fname.lower()
            if lower.endswith((".flac", ".wav", ".mp3", ".ogg", ".m4a")):
                audio_filename = fname
                break
        if not audio_filename:
            audio_filename = output_filenames[0]

        audio_url = f"{self.config.base_url}/view?filename={audio_filename}&type=output"
        subfolder = self._output_subfolders.get(audio_filename, "")
        if subfolder:
            audio_url += f"&subfolder={subfolder}"
        elapsed_ms = int((time.time() - start) * 1000)

        logger.info(f"[ComfyUI-TTS] 生成完成 | file={audio_filename} | 耗时={elapsed_ms//1000}s")

        return ComfyUIGenResult(
            image_url=audio_url,
            filename=audio_filename,
            images=[audio_url],
            filenames=[audio_filename],
            prompt_id=prompt_id,
            elapsed_ms=elapsed_ms,
            seed=0,
            prompt=text,
        )

    async def _download_audio_to_input(self, url: str) -> str:
        """下载 URL 音频到 ComfyUI input 目录，返回文件名"""
        from pathlib import Path

        if not url:
            return ""

        # 如果是 ComfyUI 内部 URL（/view?filename=xxx），直接提取文件名
        if "/view?" in url and "filename=" in url:
            try:
                from urllib.parse import urlparse, parse_qs

                parsed = urlparse(url)
                params = parse_qs(parsed.query)
                fname = params.get("filename", [None])[0]
                if fname:
                    if COMFYUI_DIR:
                        input_dir = Path(COMFYUI_DIR) / "input"
                        if (input_dir / fname).exists():
                            logger.info(f"[ComfyUI-TTS] 参考音频已在 input | file={fname}")
                            return fname
            except Exception:
                pass

        # 外部 URL：下载后上传
        try:
            session = self._get_http_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"下载参考音频失败: HTTP {resp.status}")
                audio_data = await resp.read()

            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            suffix = ".flac"
            lower = url.lower().split("?")[0]
            for ext in [".flac", ".wav", ".mp3", ".ogg", ".m4a"]:
                if lower.endswith(ext):
                    suffix = ext
                    break
            filename = f"tts_ref_{url_hash}{suffix}"

            form = aiohttp.FormData()
            form.add_field("image", audio_data, filename=filename, content_type="audio/flac")
            async with session.post(
                f"{self.config.base_url}/upload/image",
                data=form,
            ) as upload_resp:
                if upload_resp.status == 200:
                    result = await upload_resp.json()
                    uploaded_name = result.get("name", filename)
                    logger.info(
                        f"[ComfyUI-TTS] 参考音频已上传 | file={uploaded_name} | size={len(audio_data)//1024}KB"  # noqa: E501
                    )  # noqa: E501
                    return uploaded_name
                else:
                    err_text = await upload_resp.text()
                    raise RuntimeError(f"上传参考音频失败: {upload_resp.status} {err_text[:200]}")
        except Exception as e:
            logger.error(f"[ComfyUI-TTS] 下载参考音频失败 | url={url} | error={e}")
            raise

    async def _generate_tts_flac(
        self, text: str, voice: str = "zh-CN-XiaoxiaoNeural", output_dir: Path = None
    ) -> Optional[tuple]:  # noqa: E501
        """生成TTS语音并转为FLAC格式，返回 (filename, waveform_peaks) 或 None"""
        import uuid

        if not output_dir:
            from services.comfyui.config import COMFYUI_INPUT_DIR

            output_dir = Path(COMFYUI_INPUT_DIR) if COMFYUI_INPUT_DIR else Path("ComfyUI/input")

        try:
            import edge_tts

            audio_file = f"tts_{uuid.uuid4().hex[:8]}.flac"
            mp3_path = output_dir / f"_tts_temp_{uuid.uuid4().hex[:8]}.mp3"
            flac_path = output_dir / audio_file

            # 生成mp3
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(mp3_path))

            # 转flac
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(mp3_path), "-c:a", "flac", "-f", "flac", str(flac_path)],
                capture_output=True,
                timeout=30,
            )
            mp3_path.unlink(missing_ok=True)

            if not flac_path.exists():
                logger.warning("[ComfyUI] TTS flac转换失败")
                return None

            # 计算波形峰值
            waveform_peaks = self._compute_waveform_peaks(flac_path)

            logger.info(f"[ComfyUI] TTS生成成功 | text={text[:30]}... | file={audio_file}")
            return (audio_file, waveform_peaks)

        except Exception as e:
            logger.warning(f"[ComfyUI] TTS生成异常: {e}")
            return None

    def _compute_waveform_peaks(self, flac_path: Path, num_peaks: int = 256) -> list:
        """计算音频波形峰值"""
        try:
            import numpy as np

            result = subprocess.run(
                ["ffmpeg", "-i", str(flac_path), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                capture_output=True,
                timeout=30,
            )
            if not result.stdout:
                return [0.0] * num_peaks
            samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
            chunk_size = max(len(samples) // num_peaks, 1)
            peaks = []
            for i in range(num_peaks):
                start = i * chunk_size
                end = min(start + chunk_size, len(samples))
                if start < len(samples):
                    peaks.append(float(np.max(np.abs(samples[start:end]))))
                else:
                    peaks.append(0.0)
            return peaks
        except Exception:
            return [0.0] * num_peaks

    async def _copy_output_to_input(self, comfyui_filename: str) -> str:
        """将 ComfyUI output 目录的图片复制到 input 目录（供 LoadImage 使用）"""

        session = self._get_http_session()
        # 1. 从 ComfyUI /view 下载 output 图片
        view_url = f"{self.config.base_url}/view?filename={comfyui_filename}&type=output"
        async with session.get(view_url) as resp:
            if resp.status != 200:
                logger.warning(
                    f"[ComfyUI] 获取 output 图片失败 | status={resp.status} | url={view_url}"
                )
                return ""
            img_data = await resp.read()

        # 2. 上传到 ComfyUI input 目录
        form = aiohttp.FormData()
        form.add_field("image", img_data, filename=comfyui_filename, content_type="image/png")
        async with session.post(
            f"{self.config.base_url}/upload/image",
            data=form,
        ) as upload_resp:
            if upload_resp.status == 200:
                result = await upload_resp.json()
                uploaded_name = result.get("name", comfyui_filename)
                logger.info(
                    f"[ComfyUI] 参考图已上传到 input | src={comfyui_filename} -> input={uploaded_name}"
                )  # noqa: E501
                return uploaded_name
            else:
                logger.warning(f"[ComfyUI] 上传到 input 失败 | status={upload_resp.status}")
                return ""

    async def _download_to_input(self, url: str) -> str:
        """下载 URL 图片到 ComfyUI input 目录"""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        # 从 URL 或 query 参数提取原始文件名
        orig_name = (
            url.split("?filename=")[-1].split("&")[0]
            if "?filename=" in url
            else f"ref_{url_hash}.png"
        )  # noqa: E501
        if not orig_name.endswith((".png", ".jpg", ".jpeg", ".webp")):
            orig_name = f"ref_{url_hash}.png"

        # 通常 ComfyUI input 目录在安装目录下
        # 这里用 API 上传
        try:
            # 如果是后端自身的相对接口（/api/...），拼后端地址而非 ComfyUI 地址
            if url.startswith("/api/"):
                backend_port = os.environ.get("DIRECTOR_PORT", "8000")
                full_url = f"http://127.0.0.1:{backend_port}{url}"
            else:
                full_url = url

            session = self._get_http_session()
            async with session.get(full_url) as resp:
                if resp.status == 200:
                    img_data = await resp.read()
                    # 上传到 ComfyUI input
                    form = aiohttp.FormData()
                    form.add_field("image", img_data, filename=orig_name, content_type="image/png")
                    async with session.post(
                        f"{self.config.base_url}/upload/image",
                        data=form,
                    ) as upload_resp:
                        if upload_resp.status == 200:
                            result = await upload_resp.json()
                            return result.get("name", orig_name)
        except Exception as e:
            logger.warning(f"[ComfyUI] 下载参考图失败: {e}")
        return ""
