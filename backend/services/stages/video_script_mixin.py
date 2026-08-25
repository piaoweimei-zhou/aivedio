"""视频脚本批量生成 Mixin（script 资产逐幕生成 + 分镜帧收集）"""

import logging
from typing import Any, Dict, List

from services.asset_service import AssetProduceResult, AssetRef

logger = logging.getLogger(__name__)


class VideoScriptMixin:
    """视频脚本批量生成 Mixin（script 资产逐幕生成 + 分镜帧收集）"""

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
