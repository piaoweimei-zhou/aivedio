"""
视频生成阶段

从图片生成视频（图生视频）。
支持即梦 CLI、RunningHub、火山引擎。

Script 感知：当输入包含 script 资产时，结合 storyboard 帧（含 sibling），
批量生成所有幕的视频片段，每幕注入对应 TTS 台词，返回第一个，
其余通过 metadata.sibling_asset_ids 传递给 edit stage 拼接。
"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin
from services.stages.video_audio_mixin import VideoAudioMixin  # noqa: E402
from services.stages.video_concat_mixin import VideoConcatMixin  # noqa: E402
from services.stages.video_script_mixin import VideoScriptMixin  # noqa: E402

logger = logging.getLogger(__name__)


class VideoStage(VideoScriptMixin, VideoAudioMixin, VideoConcatMixin, StagePlugin):
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
