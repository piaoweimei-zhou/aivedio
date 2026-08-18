"""
视频生成阶段

从图片生成视频（图生视频）。
支持即梦 CLI、RunningHub、火山引擎。

Script 感知：当输入包含 script 资产时，结合 storyboard 帧（含 sibling），
批量生成所有幕的视频片段，每幕注入对应 TTS 台词，返回第一个，
其余通过 metadata.sibling_asset_ids 传递给 edit stage 拼接。
"""

import logging
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult
from services.stage_service import StageDef, StagePlugin

logger = logging.getLogger(__name__)


class VideoStage(StagePlugin):
    """视频生成阶段"""

    stage_def = StageDef(
        stage_id="video",
        name="视频生成",
        input_types=["storyboard", "concept", "script"],
        input_content_types=[],  # Script 模式下不强制 content_type
        output_type="video",
        default_provider="comfyui",
        supported_providers=["comfyui", "jimeng", "runninghub", "volcengine"],
        description="从图片生成视频（图生视频，支持本地 LTX-2.3 和云端 provider，支持 script 批量生成）",
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
                script_asset, input_assets, provider_id, params,
                asset_svc, provider_svc,
            )

        # ── 原有逻辑：单段视频生成 ──
        err = self._require_urls(input_assets[0])
        if err:
            return self._error_result(err)

        source = input_assets[0]

        prompt = params.get("prompt", f"Animate this scene: {source.name}")
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

        reference_image_files = params.get("reference_image_files", [])
        reference_images = {}
        if reference_image_files and len(input_assets) >= len(reference_image_files):
            for i, orig_file in enumerate(reference_image_files):
                if input_assets[i].urls:
                    reference_images[orig_file] = input_assets[i].urls[0]

        segment_prompts = params.get("segment_prompts", [])

        tts_audios = list(params.get("tts_audios", []) or [])
        tts_texts = params.get("tts_texts", []) or []
        tts_mode = params.get("tts_mode", "voice_design")
        tts_voice_desc = params.get("tts_voice_desc", "")
        tts_ref_audio = params.get("tts_ref_audio", "")
        tts_mix_mode = params.get("tts_mix_mode", "replace")
        tts_volume = float(params.get("tts_volume", 1.0))
        bgm_url = params.get("bgm_url", "")
        bgm_volume = float(params.get("bgm_volume", 0.2))

        tts_enabled = params.get("tts_enabled", False)
        if tts_enabled and tts_texts and len(tts_audios) < len(tts_texts):
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
                    logger.info(f"[VideoStage] TTS 段{i+1} 生成完成 | url={tts_result.image_url[:60]}")
            except Exception as tts_e:
                logger.warning(f"[VideoStage] TTS 生成失败，将无配音 | error={tts_e}")
                tts_audios = []

        log_extra = (
            f" | 多角色参考图={list(reference_images.keys())}" if reference_images else ""
        )
        log_extra += f" | 分段故事={len(segment_prompts)}段" if segment_prompts else ""
        log_extra += f" | 自定义参数={list(extra_kwargs.keys())}" if extra_kwargs else ""
        log_extra += f" | TTS={len(tts_audios)}段/{tts_mix_mode}" if tts_audios else ""
        log_extra += f" | BGM={'是' if bgm_url else '否'}" if bgm_url else ""
        logger.info(
            f"[VideoStage] 视频 | provider={provider_id} | asset={source.asset_id} "
            f"| duration={duration}s{log_extra}"
        )

        try:
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
                tts_mode=tts_mix_mode,
                tts_volume=tts_volume,
                bgm_url=bgm_url,
                bgm_volume=bgm_volume,
                **extra_kwargs,
            )

            new_asset = await self._register_asset(
                asset_svc, result,
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
                    "bgm_url": bgm_url,
                    **extra_kwargs,
                },
                content_type=source.content_type,
            )

            return AssetProduceResult(asset=new_asset, success=True, elapsed_ms=result.elapsed_ms)

        except Exception as e:
            logger.error(f"[VideoStage] 视频生成失败: {e}")
            return self._error_result(str(e))

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
        bgm_url = params.get("bgm_url", "")
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
            f"acts={len(acts)} | storyboard_frames={len(storyboard_frames)} | tts_enabled={tts_enabled}"
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
            frame_url = storyboard_frames[i].urls[0] if i < len(storyboard_frames) and storyboard_frames[i].urls else None
            if not frame_url and storyboard_frames:
                # 回退用第一帧
                frame_url = storyboard_frames[0].urls[0] if storyboard_frames[0].urls else None
            if not frame_url:
                errors.append(f"幕{i+1}: 无可用 storyboard 帧")
                continue

            # 本幕 TTS 文本
            act_tts_texts = act.get("tts_texts") or []
            act_tts_audios: List[str] = []

            # 如果启用 TTS 且有台词，生成 TTS 音频
            if tts_enabled and act_tts_texts:
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
                                get_voice_for_text, strip_speaker_prefix,
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
                    tts_mode=tts_mix_mode,
                    tts_volume=tts_volume,
                    bgm_url="",  # BGM 在 edit 阶段统一加
                    bgm_volume=bgm_volume,
                    **extra_kwargs,
                )
                new_asset = await self._register_asset(
                    asset_svc, result,
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
                        "source_storyboard_asset_id": storyboard_frames[i].asset_id if i < len(storyboard_frames) else "",
                    },
                    content_type="",
                )
                created_assets.append(new_asset)
                logger.info(f"[VideoStage] 幕{i+1}/{len(acts)} 完成 | id={new_asset.asset_id} | duration={duration}s")
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
        self, input_assets: List[AssetRef], asset_svc,
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
