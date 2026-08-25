"""
视频生成阶段

从图片生成视频（图生视频）。
支持即梦 CLI、RunningHub、火山引擎。

Script 感知：当输入包含 script 资产时，结合 storyboard 帧（含 sibling），
批量生成所有幕的视频片段，每幕注入对应 TTS 台词，返回第一个，
其余通过 metadata.sibling_asset_ids 传递给 edit stage 拼接。
"""

import asyncio
import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class VideoStage(StagePlugin):
    """视频生成阶段"""

    stage_def = StageDef(
        stage_id="video",
        name="视频生成",
        input_types=["storyboard", "concept", "script", "multi_view"],
        input_content_types=[],  # Script 模式下不强制 content_type
        output_type="video",
        default_provider="minimax_h3",
        supported_providers=["comfyui", "jimeng", "runninghub", "volcengine", "minimax_h3"],
        description="从图片生成视频（图生视频，默认 MiniMax H3 本地链路，支持 i2v/t2v 与云端 provider，支持 script 批量生成）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}

        err = self._require_input(input_assets)
        if err:
            return self._error_result(err)

        provider_id = self._resolve_provider(provider_id)
        asset_svc, provider_svc = self._get_services()

        # ── Script 感知：如果输入包含 script 资产，批量生成所有幕的视频 ──
        from services.stages.script_utils import find_script_asset

        script_asset = find_script_asset(input_assets)
        if script_asset:
            return await self._execute_from_script(
                script_asset,
                input_assets,
                provider_id,
                params,
                asset_svc,
                provider_svc,
            )

        # ── 原有逻辑：单段视频生成 ──
        # MiniMax H3 有参考图时走 I2VA（图生视频）；无图时保持纯文本→视频
        has_image = bool(input_assets and input_assets[0].urls)
        is_text_source = provider_id == "minimax_h3" and not has_image
        if not is_text_source:
            err = self._require_urls(input_assets[0])
            if err:
                return self._error_result(err)

        source = input_assets[0]

        prompt = params.get(
            "prompt",
            source.name if is_text_source else f"Animate this scene: {source.name}",
        )
        model = params.get("model", "")
        aspect_ratio = params.get("aspect_ratio", "16:9")
        resolution = params.get("resolution", "480p")

        # ⭐ 修复 B1：使用统一时间控制工具，消除 duration/frame_count/segment_seconds 三重含义
        from services.video_time import resolve_video_duration, resolve_segment_seconds

        raw_duration = params.get("duration")
        raw_frame_count = params.get("frame_count")
        raw_fps = params.get("fps") or params.get("frame_rate")
        duration, frame_count, fps = resolve_video_duration(
            duration=float(raw_duration) if raw_duration else None,
            frame_count=int(raw_frame_count) if raw_frame_count else None,
            fps=int(raw_fps) if raw_fps else None,
        )
        segment_seconds = resolve_segment_seconds(
            int(params.get("segment_seconds")) if params.get("segment_seconds") else None
        )

        # ⭐ 逐镜路径（_generate_h3_segmented）需要 width/height 作为独立局部变量
        width = int(params["width"]) if params.get("width") else None
        height = int(params["height"]) if params.get("height") else None
        extra_kwargs = {
            "width": width,
            "height": height,
            "frame_count": frame_count,
            "seed": int(params["seed"]) if params.get("seed") else None,
            "fps": fps,
            "segment_seconds": segment_seconds,
        }
        # 清理 None 值
        extra_kwargs = {k: v for k, v in extra_kwargs.items() if v is not None}

        reference_image_files = params.get("reference_image_files", [])
        reference_images = {}
        if reference_image_files and len(input_assets) >= len(reference_image_files):
            for i, orig_file in enumerate(reference_image_files):
                if input_assets[i].urls:
                    reference_images[orig_file] = input_assets[i].urls[0]

        segment_prompts = params.get("segment_prompts", [])
        force_segmented = bool(params.get("segmented_oneclick", False))

        tts_audios = list(params.get("tts_audios", []) or [])
        tts_texts = params.get("tts_texts", []) or []
        tts_mode = params.get("tts_mode", "voice_design")
        tts_voice_desc = params.get("tts_voice_desc", "")
        tts_ref_audio = params.get("tts_ref_audio", "")
        tts_mix_mode = params.get("tts_mix_mode", "replace")
        tts_volume = float(params.get("tts_volume", 1.0))
        bgm_volume = float(params.get("bgm_volume", 0.2))
        bgm_url = params.get("bgm_url", "")

        tts_enabled = params.get("tts_enabled", False)
        # ⭐ H3 原生支持人声（音频由 prompt 驱动），无需 ComfyUI Qwen3TTS 旁支
        if (
            tts_enabled
            and tts_texts
            and len(tts_audios) < len(tts_texts)
            and provider_id != "minimax_h3"
        ):  # noqa: E501
            try:
                from services.comfyui_service import get_comfyui_service

                comfyui_svc = get_comfyui_service()
                logger.info(
                    f"[VideoStage] 生成 TTS 配音 | texts={len(tts_texts)}段 | mode={tts_mode}"
                )
                for i, text in enumerate(tts_texts):
                    if not text or not text.strip():
                        tts_audios.append("")
                        continue
                    if i < len(tts_audios) and tts_audios[i]:
                        continue
                    tts_result = await comfyui_svc.generate_tts_audio(
                        text=text,
                        mode=tts_mode,
                        voice_description=tts_voice_desc,
                        ref_audio_url=tts_ref_audio,
                        asset_tag=f"tts_seg{i+1}",
                    )
                    while len(tts_audios) < i:
                        tts_audios.append("")
                    if len(tts_audios) == i:
                        tts_audios.append(tts_result.image_url)
                    else:
                        tts_audios[i] = tts_result.image_url
                    logger.info(
                        f"[VideoStage] TTS 段{i+1} 生成完成 | url={tts_result.image_url[:60]}"
                    )
            except Exception as tts_e:
                logger.warning(f"[VideoStage] TTS 生成失败，将无配音 | error={tts_e}")
                tts_audios = []

        log_extra = f" | 多角色参考图={list(reference_images.keys())}" if reference_images else ""
        log_extra += f" | 分段故事={len(segment_prompts)}段" if segment_prompts else ""
        log_extra += f" | 自定义参数={list(extra_kwargs.keys())}" if extra_kwargs else ""
        log_extra += f" | TTS={len(tts_audios)}段/{tts_mix_mode}" if tts_audios else ""
        log_extra += f" | BGM={'是' if bgm_url else '否'}" if bgm_url else ""
        logger.info(
            f"[VideoStage] 视频 | provider={provider_id} | asset={source.asset_id} "
            f"| duration={duration}s{log_extra}"
        )

        try:
            # ⭐ 逐镜生成+拼接：minimax_h3 多镜头路径改逐个生成、拼对齐，
            #   避免"整段旁白拼进一个 prompt 导致音画节奏乱 / 时长不一致"。
            if (
                provider_id == "minimax_h3"
                and (segment_prompts or force_segmented)
                and len(tts_texts or segment_prompts) > 1
            ):
                result = await self._generate_h3_segmented(
                    provider_svc=provider_svc,
                    prompt=prompt,
                    segment_prompts=segment_prompts,
                    tts_texts=tts_texts,
                    images=source.urls,
                    width=width,
                    height=height,
                    duration=duration,
                    segment_seconds=segment_seconds,
                    segment_durations=params.get("segment_durations") or [],
                    seed=int(params["seed"]) if params.get("seed") else None,
                    model=model,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                )
            else:
                result = await provider_svc.generate_video(
                    provider_id=provider_id,
                    prompt=prompt,
                    images=source.urls,
                    model=model,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    reference_images=reference_images,
                    segment_prompts=segment_prompts,
                    tts_audios=tts_audios,
                    tts_texts=tts_texts,
                    tts_mode=tts_mix_mode,
                    tts_volume=tts_volume,
                    bgm_url=bgm_url,
                    bgm_volume=bgm_volume,
                    **extra_kwargs,
                )

            new_asset = await self._register_asset(
                asset_svc,
                result,
                asset_type="video",
                name=f"{source.name} 视频",
                parent_id=source.asset_id,
                extra_metadata={
                    "source_asset_id": source.asset_id,
                    "prompt": prompt,
                    "duration": duration,
                    "aspect_ratio": aspect_ratio,
                    "resolution": resolution,
                    "video_url": result.video_url,
                    "reference_images": reference_images,
                    "segment_prompts": segment_prompts,
                    "tts_audios": tts_audios,
                    "tts_mix_mode": tts_mix_mode,
                    "tts_mode": tts_mode,
                    "tts_enabled": bool(tts_enabled),
                    "tts_texts": tts_texts,
                    "bgm_url": bgm_url,
                    **extra_kwargs,
                },
                content_type=source.content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[VideoStage] 视频生成失败: {e}")
            return self._error_result(str(e))

    async def _generate_h3_segmented(
        self,
        provider_svc,
        prompt: str,
        segment_prompts: List[str],
        tts_texts: List[str],
        images: List[str],
        width,
        height,
        duration: float,
        segment_seconds: int,
        seed,
        model: str = "",
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        segment_durations: List[float] = None,
    ) -> Any:
        """minimax_h3 逐镜生成 + 对齐拼接（成片音画时长一致的核心修复）

        每镜一个独立 H3 生成（各自带音轨、含头部爆音清理），再用 ffmpeg 拼接。
        · 消除"整段旁白拼进一个 prompt"导致的音画节奏乱
        · 拼接时以实际帧长为准、逐镜尾部对齐，杜绝音长>画长
        · segment_durations[i] 可覆盖固定 segment_seconds：按台词长度动态分配镜头时长，
          避免长台词被压进固定时长导致语速突快
        """
        import time as _time

        start = _time.time()

        seg_count = len(segment_prompts) or len(tts_texts)
        if seg_count <= 1:
            seg_count = max(1, int(round(duration / max(segment_seconds, 1))))
        seg_count = max(1, seg_count)

        logger.info(
            f"[VideoStage] H3 逐镜生成 | prompt={prompt[:30]}... | segments={seg_count} "
            f"| seg_seconds={segment_seconds or 'auto'} | seed={seed}"
        )

        # 每镜生成一个 H3 视频（画面 prompt 驱动，人声由独立 TTS 提供）
        from services.comfyui_service import get_comfyui_service

        comfyui_svc = get_comfyui_service()
        seg_results = []
        for i in range(seg_count):
            seg_prompt = (
                segment_prompts[i]
                if i < len(segment_prompts) and segment_prompts[i]
                else f"{prompt} 镜头{i+1}"
            )  # noqa: E501
            seg_text = ""
            if i < len(tts_texts) and tts_texts[i]:
                seg_text = tts_texts[i]
            # ⭐ P3：镜头时长优先取 segment_durations[i]（按台词长度动态分配），
            #   否则回退固定 segment_seconds
            if segment_durations and i < len(segment_durations) and segment_durations[i]:
                seg_dur = max(float(segment_durations[i]), 4.0)
            else:
                seg_dur = max(float(segment_seconds or 4.0), 4.0)
            # ⭐ P2：画面 prompt 含动作语义（baseline 已注入），不再拼台词进 prompt，
            #   避免 H3 生成不可靠的伪人声；人声统一由独立 TTS 提供
            try:
                r = await provider_svc.generate_video(
                    provider_id="minimax_h3",
                    prompt=seg_prompt,
                    images=images,
                    model=model,
                    duration=seg_dur,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    reference_images={},
                    segment_prompts=[],
                    tts_audios=[],
                    tts_texts=[],  # ⭐ 不依赖 H3 prompt 注入人声
                    tts_mode="voice_design",
                    tts_volume=1.0,
                    bgm_url="",
                    bgm_volume=0,
                    width=width,
                    height=height,
                    seed=(seed + i) if seed is not None else None,
                    fps=24,
                    frame_count=None,
                )
                # ⭐ 人声修复：独立 TTS 生成该镜旁白，混入 H3 视频（保留环境音+人声）
                if seg_text:
                    try:
                        tts_url = await self._gen_tts_segment(comfyui_svc, seg_text, i)
                        if tts_url:
                            mixed_url = await self._mix_tts_into_segment(r.video_url, tts_url)
                            if mixed_url:
                                r.video_url = mixed_url
                                r.image_url = mixed_url
                    except Exception as tts_e:
                        logger.warning(
                            f"[VideoStage] 镜{i+1} TTS 混音失败，保留 H3 原音频 | err={tts_e}"
                        )
                seg_results.append(r)
                logger.info(
                    f"[VideoStage] H3 镜{i+1}/{seg_count} 完成 | url={getattr(r, 'video_url', '')[:60]}"  # noqa: E501
                )  # noqa: E501
            except Exception as e:
                logger.error(f"[VideoStage] H3 镜{i+1} 生成失败 | err={e}")
                seg_results.append(None)

        valid = [r for r in seg_results if r and getattr(r, "video_url", "")]
        if not valid:
            raise RuntimeError("逐镜生成全部失败，无可用镜头")

        if len(valid) == 1:
            result = valid[0]
            result.elapsed_ms = int((_time.time() - start) * 1000)
            return result

        # 拼接：复用逐镜音频清理 + ffmpeg concat
        merged_url = await self._concat_segments_with_clean(
            [r.video_url for r in valid],
            seed=seed,
        )

        # 构造拼接后的 ProviderResult
        total_duration = (
            sum(float(r.duration or 0) for r in valid if getattr(r, "duration", None)) or duration
        )
        _fake = type(
            "FakeResult",
            (),
            {
                "provider_id": "minimax_h3",
                "video_url": merged_url,
                "image_url": merged_url,
                "images": [merged_url],
                "filenames": [merged_url],
                "seed": seed or 0,
                "elapsed_ms": int((_time.time() - start) * 1000),
                "prompt": prompt,
                "prompt_id": "",
                "duration": total_duration,
            },
        )()
        return _fake

    async def _gen_tts_segment(self, comfyui_svc, text: str, idx: int) -> str:
        """生成单镜独立 TTS 人声音频，返回音频 URL（失败返回空串）"""
        try:
            tts_result = await comfyui_svc.generate_tts_audio(
                text=text,
                mode="voice_design",
                voice_description="成年女性，温柔亲切，语速适中",
                asset_tag=f"h3seg{idx+1}",
            )
            url = getattr(tts_result, "image_url", "") or ""
            if url:
                logger.info(f"[VideoStage] 镜{idx+1} TTS 人声生成完成 | url={url[:60]}")
            return url
        except Exception as e:
            logger.warning(f"[VideoStage] 镜{idx+1} TTS 生成失败 | err={e}")
            return ""

    async def _download_media(self, url: str, ext: str) -> str:
        """下载媒体 URL 到本地临时文件（保留扩展名），失败返回空串"""
        import os
        import uuid
        import httpx
        from services.providers.provider_utils import output_path_for

        if url.startswith(("http://", "https://")):
            temp_path = output_path_for(f"tmp_{uuid.uuid4().hex[:8]}.{ext}", "temp")
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(20.0, connect=20.0, read=300.0)
                ) as client:  # noqa: E501
                    resp = await client.get(url)
                    resp.raise_for_status()
                    with open(temp_path, "wb") as f:
                        f.write(resp.content)
                if os.path.exists(temp_path) and os.path.getsize(temp_path) > 0:
                    return temp_path
            except Exception as e:
                logger.warning(f"[VideoStage] 下载失败 url={url[:60]} | err={e}")
            return ""
        return url

    async def _mix_tts_into_segment(self, seg_url: str, tts_url: str) -> str:
        """把 TTS 人声叠加到 H3 镜头视频（保留环境音 + 人声，音画对齐）"""
        import os
        import uuid
        from services.stages.ffmpeg_utils import _ffmpeg_bin
        from services.providers.provider_utils import output_path_for, output_url_for

        ffmpeg = _ffmpeg_bin()
        seg_local = await self._download_media(seg_url, "mp4")
        tts_local = await self._download_media(tts_url, "flac")
        if not seg_local or not tts_local:
            return ""
        out_path = output_path_for(f"seg_tts_{uuid.uuid4().hex[:8]}.mp4", "output")
        # 环境音降为背景(0.45) + TTS 人声(1.0) 叠加，amix 以视频原音频时长为准
        args = [
            ffmpeg,
            "-y",
            "-i",
            seg_local,
            "-i",
            tts_local,
            "-filter_complex",
            "[0:a]volume=0.45[env];"
            "[1:a]volume=1.0,apad[tts];"
            "[env][tts]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]",
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-ac",
            "2",
            out_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.warning(
                f"[VideoStage] TTS 混音失败: {stderr.decode('utf-8', errors='replace')[:300]}"
            )  # noqa: E501
            return ""
        logger.info(f"[VideoStage] 镜 TTS 混音完成 | out={os.path.basename(out_path)}")
        return output_url_for(os.path.basename(out_path), "output")

    async def _concat_segments_with_clean(self, video_urls: List[str], seed=None) -> str:
        """逐镜音频清理 + ffmpeg 拼接（保证音画时长一致）"""
        import os
        import uuid

        from services.stages.ffmpeg_utils import _ffmpeg_bin, _ffprobe_bin, resolve_local_video
        from services.providers.provider_utils import output_path_for, output_url_for

        ffmpeg = _ffmpeg_bin()
        ffprobe = _ffprobe_bin()

        # 下载 + 清理每个镜头音频头部（爆音/静音），并统一时长与采样率
        cleaned = []
        for i, url in enumerate(video_urls):
            local = await resolve_local_video(url)
            cleaned_local = await self._clean_clip_audio_align(local, ffmpeg, ffprobe)
            if cleaned_local:
                cleaned.append(cleaned_local)

        if len(cleaned) == 1:
            return video_urls[0]

        concat_file = output_path_for(f"concat_{uuid.uuid4().hex[:8]}.txt", "output")
        with open(concat_file, "w", encoding="utf-8") as f:
            for path in cleaned:
                f.write(f"file '{path.replace(chr(39), chr(39)*2)}'\n")

        output_file = output_path_for(f"seg_concat_{uuid.uuid4().hex[:8]}.mp4", "output")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file,
            "-c",
            "copy",
            output_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"[VideoStage] concat copy 失败，改用重编码: {stderr.decode('utf-8', errors='replace')[:300]}"  # noqa: E501
            )  # noqa: E501
            return await self._concat_reencode(cleaned, output_file)
        try:
            os.remove(concat_file)
        except Exception:
            pass
        return output_url_for(os.path.basename(output_file), "output")

    async def _clean_clip_audio_align(self, local, ffmpeg, ffprobe):
        """清理单个镜头音频头部爆音，并将音频裁剪/补齐到与视频严格等长"""
        import os
        import uuid

        from services.providers.provider_utils import (
            output_path_for,
        )

        if not local or not os.path.exists(local):
            return None

        # 探测视频流真实时长（以视频为准，避免被加长的音频撑大 format.duration）
        dur_d = float("nan")
        try:
            import json as _json

            proc = await asyncio.create_subprocess_exec(
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=duration",
                "-of",
                "json",
                local,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            data = _json.loads(out.decode("utf-8", errors="replace"))
            streams = data.get("streams") or []
            if streams and streams[0].get("duration"):
                dur_d = float(streams[0]["duration"])
            else:
                # 流无 duration 字段时回退 format
                proc2 = await asyncio.create_subprocess_exec(
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    local,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                out2, _ = await proc2.communicate()
                data2 = _json.loads(out2.decode("utf-8", errors="replace"))
                dur_d = float(data2["format"]["duration"])
        except Exception:
            dur_d = float("nan")

        # 复用 export 的爆音检测思路（简化版）
        head_trim = 0.0
        try:
            import re as _re

            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-hide_banner",
                "-i",
                local,
                "-af",
                "silencedetect=noise=-45dB:d=0.05",
                "-f",
                "null",
                "-",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await proc.communicate()
            out_txt = err.decode("utf-8", errors="replace")
            m_s = _re.search(r"silence_start:\s*(-?[\d.]+)", out_txt)
            if m_s:
                s = float(m_s.group(1))
                m_e = _re.search(r"silence_end:\s*([\d.]+)", out_txt[m_s.end() :])
                if m_e:
                    e = float(m_e.group(1))
                    if s <= 0.35 and e >= 0.03 and e <= 0.6:
                        head_trim = min(e + 0.03, 0.28)
        except Exception:
            head_trim = 0.0

        out_path = output_path_for(f"seg_clean_{uuid.uuid4().hex[:8]}.mp4", "output")
        # 视频与音频同步裁剪开头（-ss 在输入前→同步），再以 -t 限到视频时长，风格 audio 对齐
        args = [ffmpeg, "-y"]
        if head_trim > 0:
            args += ["-ss", str(head_trim)]
        args += ["-i", local]
        vid_dur = dur_d - head_trim if not (dur_d != dur_d) else None
        audio_filter = "aresample=48000,alimiter=limit=0.85:level=false"
        if vid_dur and vid_dur > 0:
            audio_filter = f"atrim=0:{vid_dur},asetpts=PTS-STARTPTS,apad,atrim=0:{vid_dur},{audio_filter}"  # noqa: E501
        args += ["-vf", "setpts=PTS-STARTPTS"]
        args += ["-af", audio_filter]
        if vid_dur and vid_dur > 0:
            args += ["-t", str(vid_dur)]
        args += ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-ac", "2"]
        args += [out_path]

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                f"[VideoStage] 镜头音频清理失败: {stderr.decode('utf-8', errors='replace')[:300]}"
            )  # noqa: E501
            return None
        os.path.exists(out_path) and os.path.getsize(out_path) > 0 and out_path

        return out_path if os.path.exists(out_path) and os.path.getsize(out_path) > 0 else None

    async def _concat_reencode(self, cleaned_paths: List[str], output_file: str) -> str:
        """拼接失败时用 filter_complex 重编码拼接（各镜统一重编码，保证可拼）"""
        from services.stages.ffmpeg_utils import _ffmpeg_bin
        from services.providers.provider_utils import output_url_for
        import os

        ffmpeg = _ffmpeg_bin()
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except Exception:
                pass
        fc = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(len(cleaned_paths)))
        fc += f"concat=n={len(cleaned_paths)}:v=1:a=1[v][a]"
        args = [ffmpeg, "-y"]
        for p in cleaned_paths:
            args += ["-i", p]
        args += [
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-ac",
            "2",
            output_file,
        ]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"逐镜重编码拼接失败: {stderr.decode('utf-8', errors='replace')[:400]}"
            )
        return output_url_for(os.path.basename(output_file), "output")

    async def _execute_from_script(
        self,
        script_asset: AssetRef,
        input_assets: List[AssetRef],
        provider_id: str,
        params: Dict[str, Any],
        asset_svc,
        provider_svc,
    ) -> AssetProduceResult:
        """从 script 资产批量生成所有幕的视频片段

        策略：
        - 读取剧本 JSON，提取 acts 列表
        - 收集 storyboard 帧（主 + sibling_asset_ids）作为图生视频输入
        - 对每幕用 scene/narration 作为 prompt，注入对应 tts_texts
        - 批量生成视频，全部注册
        - 返回第一个，metadata.sibling_asset_ids 记录其余
        """
        import time

        start = time.time()
        from services.stages.script_utils import load_script_json, extract_acts

        script = await load_script_json(script_asset)
        if not script:
            return self._error_result(f"无法读取剧本 JSON: {script_asset.asset_id}")

        acts = extract_acts(script)
        if not acts:
            return self._error_result("剧本中无 acts，无法生成视频")

        # 收集 storyboard 帧（主资产 + sibling_asset_ids）
        storyboard_frames = self._collect_storyboard_frames(input_assets, asset_svc)
        if not storyboard_frames:
            return self._error_result("无可用的 storyboard 帧作为图生视频输入")

        model = params.get("model", "")
        aspect_ratio = params.get("aspect_ratio", "16:9")
        resolution = params.get("resolution", "480p")

        # ⭐ 修复 P0：_execute_from_script 此前未调用 resolve_video_duration
        # 导致 frame_count/fps/segment_seconds 三个变量未定义，剧本批量生成必抛 NameError
        from services.video_time import resolve_video_duration, resolve_segment_seconds

        raw_duration = params.get("duration")
        raw_frame_count = params.get("frame_count")
        raw_fps = params.get("fps") or params.get("frame_rate")
        duration, frame_count, fps = resolve_video_duration(
            duration=float(raw_duration) if raw_duration else None,
            frame_count=int(raw_frame_count) if raw_frame_count else None,
            fps=int(raw_fps) if raw_fps else None,
        )
        segment_seconds = resolve_segment_seconds(
            int(params.get("segment_seconds")) if params.get("segment_seconds") else None
        )

        tts_mode = params.get("tts_mode", "voice_design")
        tts_voice_desc = params.get("tts_voice_desc", "")
        tts_ref_audio = params.get("tts_ref_audio", "")
        tts_mix_mode = params.get("tts_mix_mode", "replace")
        tts_volume = float(params.get("tts_volume", 1.0))
        bgm_volume = float(params.get("bgm_volume", 0.2))
        tts_enabled = params.get("tts_enabled", False)

        # 多角色音色映射：从 script.characters 构建，支持用户显式覆盖
        multi_voice_map: Dict[str, str] = {}
        if tts_enabled and tts_mode == "voice_design":
            from services.stages.script_utils import extract_characters
            from services.stages.tts_utils import build_voice_map

            characters = extract_characters(script)
            user_voice_map = params.get("voice_map", {}) or {}
            multi_voice_map = build_voice_map(characters, user_voice_map)
            logger.info(
                f"[VideoStage] 多角色音色启用 | voices={len(multi_voice_map)} | "
                f"characters={[c['name'] for c in characters]}"
            )

        # ⭐ 统一时间控制参数注入（与单路径 _execute 一致）
        extra_kwargs = {
            "width": int(params["width"]) if params.get("width") else None,
            "height": int(params["height"]) if params.get("height") else None,
            "frame_count": frame_count,
            "seed": int(params["seed"]) if params.get("seed") else None,
            "fps": fps,
            "segment_seconds": segment_seconds,
        }
        # 清理 None 值
        extra_kwargs = {k: v for k, v in extra_kwargs.items() if v is not None}

        logger.info(
            f"[VideoStage] Script 感知 | script={script_asset.asset_id} | "
            f"acts={len(acts)} | storyboard_frames={len(storyboard_frames)} | tts_enabled={tts_enabled}"  # noqa: E501
        )

        created_assets: List[AssetRef] = []
        errors: List[str] = []

        for i, act in enumerate(acts):
            scene_desc = (act.get("scene") or "").strip()
            narration = (act.get("narration") or "").strip()
            prompt = scene_desc or narration or f"第{act.get('act', i+1)}幕视频"
            if narration and narration != prompt:
                prompt = f"{prompt}。{narration}"

            # 对应的 storyboard 帧（如果有的话）
            frame_url = (
                storyboard_frames[i].urls[0]
                if i < len(storyboard_frames) and storyboard_frames[i].urls
                else None
            )  # noqa: E501
            if not frame_url and storyboard_frames:
                # 回退用第一帧
                frame_url = storyboard_frames[0].urls[0] if storyboard_frames[0].urls else None
            if not frame_url:
                errors.append(f"幕{i+1}: 无可用 storyboard 帧")
                continue

            # 本幕 TTS 文本
            act_tts_texts = act.get("tts_texts") or []
            act_tts_audios: List[str] = []

            # 如果启用 TTS 且有台词，生成 TTS 音频（H3 原生支持人声，跳过 Qwen3TTS 旁支）
            if tts_enabled and act_tts_texts and provider_id != "minimax_h3":
                try:
                    from services.comfyui_service import get_comfyui_service

                    comfyui_svc = get_comfyui_service()
                    for j, text in enumerate(act_tts_texts):
                        if not text or not text.strip():
                            act_tts_audios.append("")
                            continue
                        # 多角色音色：优先从 voice_map 检测说话人
                        actual_voice_desc = tts_voice_desc
                        actual_text = text
                        if multi_voice_map:
                            from services.stages.tts_utils import (
                                get_voice_for_text,
                                strip_speaker_prefix,
                            )

                            actual_voice_desc = get_voice_for_text(
                                text, multi_voice_map, tts_voice_desc
                            )
                            actual_text = strip_speaker_prefix(text)
                        tts_result = await comfyui_svc.generate_tts_audio(
                            text=actual_text,
                            mode=tts_mode,
                            voice_description=actual_voice_desc,
                            ref_audio_url=tts_ref_audio,
                            asset_tag=f"tts_act{i+1}_seg{j+1}",
                        )
                        act_tts_audios.append(tts_result.image_url)
                        logger.info(
                            f"[VideoStage] 幕{i+1} TTS 段{j+1} 完成 | "
                            f"voice={actual_voice_desc[:20]} | url={tts_result.image_url[:60]}"
                        )
                except Exception as tts_e:
                    logger.warning(f"[VideoStage] 幕{i+1} TTS 失败 | err={tts_e}")
                    act_tts_audios = []

            # 本幕时长
            # ⭐ 修复 P1 #6：duration 被剧本覆盖后，frame_count/fps 未同步更新
            # 导致 duration 和 frame_count 不一致（duration=5s 但 frame_count=120 帧可能不匹配）
            act_duration = float(act.get("duration_seconds", 5.0))
            # 用本幕 duration 重新解析 frame_count（保持与 fps 一致）
            from services.video_time import resolve_video_duration as _resolve_dur

            act_dur_resolved, act_frame_count, act_fps = _resolve_dur(
                duration=act_duration, frame_count=None, fps=fps
            )
            # 覆盖 extra_kwargs 中的时间参数，确保本幕 duration/frame_count/fps 三者一致
            extra_kwargs["frame_count"] = act_frame_count
            extra_kwargs["fps"] = act_fps
            extra_kwargs["segment_seconds"] = segment_seconds
            duration = act_dur_resolved

            try:
                result = await provider_svc.generate_video(
                    provider_id=provider_id,
                    prompt=prompt,
                    images=[frame_url],
                    model=model,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    reference_images={},
                    segment_prompts=[],
                    tts_audios=act_tts_audios,
                    tts_texts=act_tts_texts,
                    tts_mode=tts_mix_mode,
                    tts_volume=tts_volume,
                    bgm_url="",  # BGM 在 edit 阶段统一加
                    bgm_volume=bgm_volume,
                    **extra_kwargs,
                )
                new_asset = await self._register_asset(
                    asset_svc,
                    result,
                    asset_type="video",
                    name=f"第{act.get('act', i+1)}幕视频",
                    parent_id=script_asset.asset_id,
                    extra_metadata={
                        "prompt": prompt,
                        "duration": duration,
                        "aspect_ratio": aspect_ratio,
                        "resolution": resolution,
                        "video_url": result.video_url,
                        "script_asset_id": script_asset.asset_id,
                        "act_index": i,
                        "act_number": act.get("act", i + 1),
                        "scene": scene_desc,
                        "tts_audios": act_tts_audios,
                        "tts_enabled": bool(tts_enabled),
                        "tts_texts": act_tts_texts,
                        "tts_mode": tts_mode,
                        "source_storyboard_asset_id": (
                            storyboard_frames[i].asset_id if i < len(storyboard_frames) else ""
                        ),  # noqa: E501
                    },
                    content_type="",
                )
                created_assets.append(new_asset)
                logger.info(
                    f"[VideoStage] 幕{i+1}/{len(acts)} 完成 | id={new_asset.asset_id} | duration={duration}s"  # noqa: E501
                )  # noqa: E501
            except Exception as e:
                logger.error(f"[VideoStage] 幕{i+1} 生成失败 | err={e}")
                errors.append(f"幕{i+1}: {e}")

        if not created_assets:
            return self._error_result(f"所有视频生成失败 | errors={errors}")

        primary = created_assets[0]
        sibling_ids = [a.asset_id for a in created_assets[1:]]
        sibling_meta = {
            "sibling_asset_ids": sibling_ids,
            "script_asset_id": script_asset.asset_id,
            "total_clips": len(created_assets),
        }
        if errors:
            sibling_meta["batch_errors"] = errors
        await asset_svc.update(primary.asset_id, metadata=sibling_meta)
        primary.metadata.update(sibling_meta)

        elapsed = int((time.time() - start) * 1000)
        logger.info(
            f"[VideoStage] Script 批量完成 | primary={primary.asset_id} | "
            f"siblings={len(sibling_ids)} | errors={len(errors)} | elapsed={elapsed}ms"
        )

        return AssetProduceResult(asset=primary, success=True, elapsed_ms=elapsed)

    def _collect_storyboard_frames(
        self,
        input_assets: List[AssetRef],
        asset_svc,
    ) -> List[AssetRef]:
        """收集 storyboard 帧（主资产 + sibling_asset_ids 中的兄弟帧）

        按 act_index 排序，确保和 script acts 顺序对齐
        """
        frames: List[AssetRef] = []
        seen_ids = set()
        for asset in input_assets:
            if asset.asset_type == "script":
                continue
            if asset.asset_type != "storyboard":
                continue
            if asset.asset_id in seen_ids:
                continue
            seen_ids.add(asset.asset_id)
            frames.append(asset)
            # 展开 sibling_asset_ids
            for sid in asset.metadata.get("sibling_asset_ids", []):
                if sid in seen_ids:
                    continue
                sibling = asset_svc.get(sid) if hasattr(asset_svc, "get") else None
                if sibling:
                    seen_ids.add(sid)
                    frames.append(sibling)

        # 按 act_index 排序
        def _act_index(a: AssetRef) -> int:
            try:
                return int(a.metadata.get("act_index", 999))
            except Exception:
                return 999

        frames.sort(key=_act_index)
        return frames
