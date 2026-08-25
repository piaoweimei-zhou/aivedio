"""
ComfyUI 服务 — 视频生成 Mixin 主类

LTX 视频生成。P2 治理：长视频/Minimax 方法拆至 comfyui_video_long.py。
"""

import json
import logging
import time
from typing import Callable, Dict, Optional

import aiohttp

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult, _mem_log

from services.comfyui_video_long import ComfyUIVideoLongMixin

logger = logging.getLogger(__name__)


class ComfyUIVideoMixin(ComfyUIVideoLongMixin):
    async def generate_video(
        self,
        prompt: str = "",
        reference_image: str = "",
        workflow_file: str = "LTX2.3导演2.json",
        width: int = 1280,
        height: int = 720,
        frame_count: int = 97,
        frame_rate: int = 24,
        seed: Optional[int] = None,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        **kwargs,
    ) -> ComfyUIGenResult:
        """
        视频生成 — 调用 LTX-2.3 导演工作流（支持timeline分段+TTS音频）

        Args:
            prompt: 视频提示词（global_prompt + local_prompts）
            reference_image: 参考图 URL（首帧引导图）
            workflow_file: ComfyUI 工作流 JSON 文件名（位于 workflows/ 目录）
            width/height: 视频分辨率
            frame_count: 生成帧数（97帧 ≈ 4秒 @24fps）
            frame_rate: 帧率
            seed: 随机种子
            kwargs:
                narration: 旁白文本（自动TTS生成音频并注入）
                narration_voice: TTS语音名称（默认zh-CN-XiaoxiaoNeural）
                segments: 剧本分镜列表 [{prompt, narration, duration_sec}, ...]
        """
        import random
        from pathlib import Path
        from services.workflow_builder import find_node_by_class_type

        start = time.time()
        _mem_log(
            "视频生成开始",
            f"workflow={workflow_file} ref={reference_image[:50] if reference_image else 'none'}",
        )

        # 1. 加载工作流
        workflow_dir = Path(__file__).parent.parent.parent / "workflows"
        workflow_path = workflow_dir / workflow_file

        if not workflow_path.exists():
            # 尝试项目根 workflows 目录（集中常量）
            from services.comfyui.config import WORKFLOWS_DIR

            workflow_path = Path(WORKFLOWS_DIR) / workflow_file
        if not workflow_path.exists():
            raise FileNotFoundError(f"视频工作流不存在: {workflow_file}")

        with open(workflow_path, "r", encoding="utf-8") as f:
            workflow = json.load(f)

        logger.info(f"[ComfyUI] 加载视频工作流 | file={workflow_file} | nodes={len(workflow)}")

        # 2. 设置随机种子
        actual_seed = seed if seed is not None else random.randint(0, 2**48 - 1)
        noise_nodes = find_node_by_class_type(workflow, "RandomNoise")
        if noise_nodes:
            workflow[noise_nodes[0][0]]["inputs"]["noise_seed"] = actual_seed
            logger.info(f"[ComfyUI] 视频种子 | seed={actual_seed}")

        # 3. 提示词处理：支持 PromptRelayEncode / LTXDirector 两种节点
        # - PromptRelayEncode: 标准节点，local_prompts 用 | 分隔
        # - LTXDirector: WhatDreamsCost 自定义节点，内部也调用 _encode_relay，
        #   同样需要 local_prompts 用 | 分隔
        #   当 local_prompts 为空时，自动从 timeline_data.segments[*].prompt 提取
        # 注意：LTXDirectorGuide 是 guide 图像应用节点，不需要 local_prompts
        local_prompts_override = kwargs.get("local_prompts", "")
        global_prompt_override = kwargs.get("global_prompt", "")

        # 查找所有需要 local_prompts 的节点类型
        _PROMPT_NODE_TYPES = ("PromptRelayEncode", "LTXDirector")
        prompt_nodes = []
        for ntype in _PROMPT_NODE_TYPES:
            prompt_nodes.extend(find_node_by_class_type(workflow, ntype))

        for node_id, node_data in prompt_nodes:
            inputs = node_data.get("inputs", {})
            # 覆盖 global_prompt（仅当显式传入）
            if global_prompt_override:
                inputs["global_prompt"] = global_prompt_override
            # 覆盖 local_prompts（仅当显式传入非空值）
            if local_prompts_override:
                inputs["local_prompts"] = local_prompts_override

            # 安全检查：如果 local_prompts 为空，尝试从 timeline_data 提取
            current_local = str(inputs.get("local_prompts", "")).strip()
            if not current_local:
                timeline_str = inputs.get("timeline_data", "")
                if timeline_str:
                    try:
                        tdata = json.loads(timeline_str)
                        segments = tdata.get("segments", [])
                        seg_prompts = [
                            s.get("prompt", "").strip()
                            for s in segments
                            if s.get("prompt", "").strip()
                        ]
                        if seg_prompts:
                            inputs["local_prompts"] = " | ".join(seg_prompts)
                            logger.info(
                                f"[ComfyUI] 从 timeline_data 提取 local_prompts | node={node_id} | segments={len(seg_prompts)}"  # noqa: E501
                            )
                    except (json.JSONDecodeError, AttributeError) as e:
                        logger.warning(
                            f"[ComfyUI] timeline_data 解析失败 | node={node_id} | error={e}"
                        )

            # 安全检查：如果 local_prompts 仍为空，报错避免 ComfyUI 崩溃
            final_local = str(inputs.get("local_prompts", "")).strip()
            if not final_local:
                logger.error(
                    f"[ComfyUI] local_prompts 为空！node={node_id} class={node_data.get('class_type')}"
                )
            else:
                seg_count = len([p for p in final_local.split("|") if p.strip()])
                logger.info(
                    f"[ComfyUI] 视频提示词 | node={node_id} | global={str(inputs.get('global_prompt', ''))[:60]}... | local_segs={seg_count}"  # noqa: E501
                )

            # LTXDirector特有：segment_lengths 和 guide_strength 必须与 local_prompts 数量一致
            if node_data.get("class_type") == "LTXDirector" and final_local:
                seg_count = len([p for p in final_local.split("|") if p.strip()])
                # 从timeline_data.segments提取每段长度
                timeline_str = inputs.get("timeline_data", "")
                seg_lengths = []
                if timeline_str:
                    try:
                        tdata = json.loads(timeline_str)
                        seg_lengths = [s.get("length", 48) for s in tdata.get("segments", [])]
                    except (json.JSONDecodeError, AttributeError):
                        pass
                # 如果timeline段数与prompt段数不匹配，用默认长度补齐
                if len(seg_lengths) != seg_count:
                    avg_len = sum(seg_lengths) // len(seg_lengths) if seg_lengths else 48
                    seg_lengths = (
                        seg_lengths[:seg_count]
                        if len(seg_lengths) > seg_count
                        else seg_lengths + [avg_len] * (seg_count - len(seg_lengths))
                    )
                inputs["segment_lengths"] = ",".join(str(seg) for seg in seg_lengths)
                inputs["guide_strength"] = ",".join(["1.00"] * seg_count)
                # 同步duration_frames和duration_seconds
                total_frames = sum(seg_lengths)
                inputs["duration_frames"] = total_frames
                inputs["duration_seconds"] = round(total_frames / frame_rate, 3)
                # 分辨率（必须是32的倍数）
                w = (width // 32) * 32
                h = (height // 32) * 32
                inputs["custom_width"] = w
                inputs["custom_height"] = h
                # 注意：不修改epsilon/img_compression等LTXDirector参数
                # 蒸馏模型参数是专门优化的，随意修改会降低质量
                logger.info(
                    f"[ComfyUI] LTXDirector同步 | seg_lengths={inputs['segment_lengths']} | total={total_frames}f | {width}x{height}"  # noqa: E501
                )

        # 3.4 质量参数：蒸馏模型(cfg=1, steps=8)是优化值，不修改
        # 只有非蒸馏模型（如MSR工作流）才需要调整cfg和steps

        # 3.5 TTS音频注入（LTXDirector支持audioSegments）
        narration = kwargs.get("narration", "")
        narration_voice = kwargs.get("narration_voice", "zh-CN-XiaoxiaoNeural")
        segments_script = kwargs.get("segments", [])  # [{prompt, narration, duration_sec}, ...]

        # 查找LTXDirector节点
        director_nodes = find_node_by_class_type(workflow, "LTXDirector")
        if director_nodes and (narration or segments_script):
            director_nid, director_ndata = director_nodes[0]
            director_inputs = director_ndata.get("inputs", {})
            timeline_str = director_inputs.get("timeline_data", "")
            if timeline_str:
                try:
                    tdata = json.loads(timeline_str)
                    audio_segments = tdata.get("audioSegments", [])
                    existing_timeline_segs = tdata.get("segments", [])

                    # 确定要生成的音频
                    audio_items = []
                    if segments_script:
                        # 从剧本分镜生成
                        for i, seg in enumerate(segments_script):
                            if seg.get("narration"):
                                audio_items.append(
                                    {
                                        "narration": seg["narration"],
                                        "seg_index": min(i, len(existing_timeline_segs) - 1),
                                    }
                                )
                    elif narration:
                        # 单段旁白：放到第一段
                        audio_items.append({"narration": narration, "seg_index": 0})

                    # 生成TTS音频并注入
                    if audio_items:
                        import uuid
                        from services.comfyui.config import COMFYUI_INPUT_DIR

                        comfyui_input = (
                            Path(COMFYUI_INPUT_DIR)
                            if COMFYUI_INPUT_DIR
                            else (Path(COMFYUI_DIR) / "input" if COMFYUI_DIR else None)
                        )
                        if comfyui_input is None:
                            logger.warning("[TTS] ComfyUI input 目录不可用，跳过音频注入")
                        else:
                            for item in audio_items:
                                try:
                                    tts_result = await self._generate_tts_flac(
                                        text=item["narration"],
                                        voice=narration_voice,
                                        output_dir=comfyui_input,
                                    )
                                    if tts_result:
                                        audio_file, waveform_peaks = tts_result
                                        seg_idx = item["seg_index"]
                                        seg_info = (
                                            existing_timeline_segs[seg_idx]
                                            if seg_idx < len(existing_timeline_segs)
                                            else {}
                                        )
                                        seg_start = seg_info.get("start", 0)
                                        seg_length = seg_info.get("length", 48)

                                        audio_segments.append(
                                            {
                                                "id": uuid.uuid4().hex[:16],
                                                "type": "audio",
                                                "start": seg_start,
                                                "length": seg_length,
                                                "trimStart": 0,
                                                "audioDurationFrames": seg_length,
                                                "audioFile": audio_file,
                                                "fileName": audio_file,
                                                "waveformPeaks": waveform_peaks,
                                            }
                                        )
                                        logger.info(
                                            f"[ComfyUI] TTS音频注入 | text={item['narration'][:30]}... | file={audio_file}"  # noqa: E501
                                        )
                                except Exception as e:
                                    logger.warning(f"[ComfyUI] TTS生成失败: {e}")

                        # 写回timeline_data
                        tdata["audioSegments"] = audio_segments
                        director_inputs["timeline_data"] = json.dumps(tdata, ensure_ascii=False)
                        director_inputs["use_custom_audio"] = True
                        logger.info(
                            f"[ComfyUI] timeline_data音频注入完成 | audio_segs={len(audio_segments)}"
                        )

                except (json.JSONDecodeError, AttributeError) as e:
                    logger.warning(f"[ComfyUI] TTS注入timeline_data失败: {e}")

        # 4. 分辨率和帧数：LTXDirector工作流已在上面的同步逻辑中处理
        # 对于非LTXDirector工作流（如旧版MSR），仍使用INTConstant覆盖
        if not director_nodes:
            # ⭐ 修复 Deep Issue 2：改用 node_id 白名单（值匹配脆弱，易误覆盖）
            # 实测所有 INTConstant 的 _meta.title 都是 "INT Constant"，title 匹配无效
            # MSR 工作流已知节点：43=width 44=height 50=total_length
            MSR_NODE_WIDTH = "43"
            MSR_NODE_HEIGHT = "44"
            MSR_NODE_TOTAL_LENGTH = "50"
            if width is not None and width != 1280:
                if (
                    MSR_NODE_WIDTH in workflow
                    and workflow[MSR_NODE_WIDTH].get("class_type") == "INTConstant"
                ):
                    workflow[MSR_NODE_WIDTH]["inputs"]["value"] = width
                    logger.info(f"[ComfyUI] 宽度覆盖 | 节点{MSR_NODE_WIDTH} → {width}")
            if height is not None and height != 720:
                if (
                    MSR_NODE_HEIGHT in workflow
                    and workflow[MSR_NODE_HEIGHT].get("class_type") == "INTConstant"
                ):
                    workflow[MSR_NODE_HEIGHT]["inputs"]["value"] = height
                    logger.info(f"[ComfyUI] 高度覆盖 | 节点{MSR_NODE_HEIGHT} → {height}")
            if frame_count is not None and frame_count != 97:
                if (
                    MSR_NODE_TOTAL_LENGTH in workflow
                    and workflow[MSR_NODE_TOTAL_LENGTH].get("class_type") == "INTConstant"
                ):
                    workflow[MSR_NODE_TOTAL_LENGTH]["inputs"]["value"] = frame_count
                    logger.info(f"[ComfyUI] 帧数覆盖 | 节点{MSR_NODE_TOTAL_LENGTH} → {frame_count}")

        # 5. 设置帧率（保持原值 24fps）
        if frame_rate != 24:
            ltxv_cond_nodes = find_node_by_class_type(workflow, "LTXVConditioning")
            if ltxv_cond_nodes:
                workflow[ltxv_cond_nodes[0][0]]["inputs"]["frame_rate"] = frame_rate
            create_video_nodes = find_node_by_class_type(workflow, "CreateVideo")
            if create_video_nodes:
                workflow[create_video_nodes[0][0]]["inputs"]["fps"] = frame_rate

        # 6. 设置参考图（LoadImage 节点）
        # 统一处理：先替换缺失文件，再注入参考图
        reference_images = kwargs.get("reference_images", {}) or {}
        comfyui_input = Path(COMFYUI_DIR) / "input" if COMFYUI_DIR else None

        load_image_nodes = [
            (nid, n) for nid, n in workflow.items() if n.get("class_type") == "LoadImage"
        ]

        if load_image_nodes and comfyui_input:
            # 6a. 先检查并替换所有缺失的 LoadImage 文件
            placeholder_name = ""
            missing_files = []
            for nid, node in load_image_nodes:
                orig_file = node["inputs"].get("image", "")
                if orig_file and not (comfyui_input / orig_file).exists():
                    missing_files.append((nid, orig_file))

            if missing_files:
                # 创建占位图（如果还没有）
                placeholder_name = "_director_placeholder.png"
                placeholder_path = comfyui_input / placeholder_name
                if not placeholder_path.exists():
                    try:
                        from PIL import Image

                        img = Image.new("RGB", (64, 64), (200, 200, 200))
                        img.save(str(placeholder_path))
                        logger.info(f"[ComfyUI] 创建占位图: {placeholder_path}")
                    except Exception as e:
                        logger.warning(f"[ComfyUI] 占位图创建失败: {e}")
                        placeholder_name = "blank64.png"  # fallback

                for nid, orig_file in missing_files:
                    workflow[nid]["inputs"]["image"] = placeholder_name
                    logger.info(f"[ComfyUI] 缺失文件替换 | {orig_file} → {placeholder_name}")

            # 6b. 注入参考图（覆盖占位图或已有文件）
            if reference_images or reference_image:
                ref_cache: Dict[str, str] = {}

                async def _resolve_ref(url: str) -> str:
                    if url in ref_cache:
                        return ref_cache[url]
                    fname = ""
                    if "?filename=" in url:
                        comfyui_fname = url.split("?filename=")[-1].split("&")[0]
                        try:
                            fname = await self._copy_output_to_input(comfyui_fname)
                        except Exception as e:
                            logger.warning(f"[ComfyUI] 参考图复制失败: {e}")
                    else:
                        # 如果是 ComfyUI input 目录中已有的文件名，直接返回
                        if comfyui_input and (comfyui_input / url).exists():
                            fname = url
                        else:
                            try:
                                fname = await self._download_to_input(url)
                            except Exception as e:
                                logger.warning(f"[ComfyUI] 参考图下载失败: {e}")
                    ref_cache[url] = fname
                    return fname

                replaced = []
                for nid, node in load_image_nodes:
                    orig_file = node["inputs"].get("image", "")
                    # 模式a：多角色注入 - 按原文件名匹配
                    if orig_file in reference_images:
                        input_fname = await _resolve_ref(reference_images[orig_file])
                        if input_fname:
                            workflow[nid]["inputs"]["image"] = input_fname
                            replaced.append(f"{nid}({orig_file}→{input_fname})")
                        continue
                    # 模式b：单图占位 - 替换所有缺失文件或占位图
                    if reference_image:
                        current_file = node["inputs"].get("image", "")
                        # 替换条件：原文件缺失，或当前是占位图
                        is_missing = current_file in (placeholder_name, "blank64.png")
                        orig_missing = orig_file and not (comfyui_input / orig_file).exists()
                        if is_missing or orig_missing:
                            resolved = await _resolve_ref(reference_image)
                            if resolved:
                                workflow[nid]["inputs"]["image"] = resolved
                                replaced.append(f"{nid}({current_file}→{resolved})")

                if replaced:
                    logger.info(f"[ComfyUI] 视频参考图注入 | {replaced}")

        # 7. 提交工作流
        prompt_id = await self._queue_prompt_with_retry(workflow)
        logger.info(f"[ComfyUI] 视频工作流已提交 | prompt_id={prompt_id}")

        # 8. 等待完成（视频生成耗时较长，task_type=video 在 _wait_for_completion 中有对应超时）
        filenames = await self._wait_for_completion(
            prompt_id=prompt_id,
            task_type="video",
            progress_callback=progress_callback,
        )

        elapsed_ms = int((time.time() - start) * 1000)

        # 9. 视频文件在 /history 的 outputs 中，SaveVideo 节点输出在 "gifs" 或 "videos" 字段
        # _wait_for_prompt 返回的是 images 字段，视频需要单独查 history
        video_url = ""
        video_filename = filenames[0] if filenames else ""

        # 尝试从 history 获取视频文件
        try:
            session = self._get_http_session()
            async with session.get(
                f"{self.config.base_url}/history/{prompt_id}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    history = data.get(prompt_id, {})
                    outputs = history.get("outputs", {})
                    for node_id, node_output in outputs.items():
                        # SaveVideo 节点输出在 "gifs" 字段
                        gifs = node_output.get("gifs", [])
                        for g in gifs:
                            video_filename = g.get("filename", video_filename)
                            break
                        # 也检查 videos 字段
                        videos = node_output.get("videos", [])
                        for v in videos:
                            video_filename = v.get("filename", video_filename)
                            break
        except Exception as e:
            logger.warning(f"[ComfyUI] 获取视频文件名失败: {e}")

        if video_filename:
            video_url = f"/api/comfyui/image?filename={video_filename}"
            # 如果是视频格式，可能需要不同的端点
            if video_filename.endswith((".mp4", ".webm", ".avi", ".mov")):
                video_url = f"{self.config.base_url}/view?filename={video_filename}"

        logger.info(
            f"[ComfyUI] 视频生成完成 | file={video_filename} | "
            f"elapsed={elapsed_ms}ms | url={video_url[:80]}"
        )

        return ComfyUIGenResult(
            image_url=video_url,
            filename=video_filename,
            images=[video_url] if video_url else [],
            filenames=[video_filename] if video_filename else [],
            prompt_id=prompt_id,
            elapsed_ms=elapsed_ms,
            seed=actual_seed,
            prompt=prompt,
        )
