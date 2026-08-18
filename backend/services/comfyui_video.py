"""
ComfyUI 服务 — 视频生成 Mixin

LTX 视频生成与长视频生成。
"""

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

import aiohttp

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult, _mem_log, logger

logger = logging.getLogger(__name__)


class ComfyUIVideoMixin:
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
        _mem_log("视频生成开始", f"workflow={workflow_file} ref={reference_image[:50] if reference_image else 'none'}")

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
                        seg_prompts = [s.get("prompt", "").strip() for s in segments if s.get("prompt", "").strip()]
                        if seg_prompts:
                            inputs["local_prompts"] = " | ".join(seg_prompts)
                            logger.info(f"[ComfyUI] 从 timeline_data 提取 local_prompts | node={node_id} | segments={len(seg_prompts)}")
                    except (json.JSONDecodeError, AttributeError) as e:
                        logger.warning(f"[ComfyUI] timeline_data 解析失败 | node={node_id} | error={e}")

            # 安全检查：如果 local_prompts 仍为空，报错避免 ComfyUI 崩溃
            final_local = str(inputs.get("local_prompts", "")).strip()
            if not final_local:
                logger.error(f"[ComfyUI] local_prompts 为空！node={node_id} class={node_data.get('class_type')}")
            else:
                seg_count = len([p for p in final_local.split("|") if p.strip()])
                logger.info(f"[ComfyUI] 视频提示词 | node={node_id} | global={str(inputs.get('global_prompt',''))[:60]}... | local_segs={seg_count}")

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
                    seg_lengths = seg_lengths[:seg_count] if len(seg_lengths) > seg_count else seg_lengths + [avg_len] * (seg_count - len(seg_lengths))
                inputs["segment_lengths"] = ",".join(str(l) for l in seg_lengths)
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
                logger.info(f"[ComfyUI] LTXDirector同步 | seg_lengths={inputs['segment_lengths']} | total={total_frames}f | {width}x{height}")

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
                                audio_items.append({
                                    "narration": seg["narration"],
                                    "seg_index": min(i, len(existing_timeline_segs) - 1),
                                })
                    elif narration:
                        # 单段旁白：放到第一段
                        audio_items.append({"narration": narration, "seg_index": 0})
                    
                    # 生成TTS音频并注入
                    if audio_items:
                        import uuid
                        from services.comfyui.config import COMFYUI_INPUT_DIR
                        comfyui_input = Path(COMFYUI_INPUT_DIR) if COMFYUI_INPUT_DIR else (Path(COMFYUI_DIR) / "input" if COMFYUI_DIR else None)
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
                                        seg_info = existing_timeline_segs[seg_idx] if seg_idx < len(existing_timeline_segs) else {}
                                        seg_start = seg_info.get("start", 0)
                                        seg_length = seg_info.get("length", 48)

                                        audio_segments.append({
                                            "id": uuid.uuid4().hex[:16],
                                            "type": "audio",
                                            "start": seg_start,
                                            "length": seg_length,
                                            "trimStart": 0,
                                            "audioDurationFrames": seg_length,
                                            "audioFile": audio_file,
                                            "fileName": audio_file,
                                            "waveformPeaks": waveform_peaks,
                                        })
                                        logger.info(f"[ComfyUI] TTS音频注入 | text={item['narration'][:30]}... | file={audio_file}")
                                except Exception as e:
                                    logger.warning(f"[ComfyUI] TTS生成失败: {e}")
                        
                        # 写回timeline_data
                        tdata["audioSegments"] = audio_segments
                        director_inputs["timeline_data"] = json.dumps(tdata, ensure_ascii=False)
                        director_inputs["use_custom_audio"] = True
                        logger.info(f"[ComfyUI] timeline_data音频注入完成 | audio_segs={len(audio_segments)}")
                        
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
                if MSR_NODE_WIDTH in workflow and workflow[MSR_NODE_WIDTH].get("class_type") == "INTConstant":
                    workflow[MSR_NODE_WIDTH]["inputs"]["value"] = width
                    logger.info(f"[ComfyUI] 宽度覆盖 | 节点{MSR_NODE_WIDTH} → {width}")
            if height is not None and height != 720:
                if MSR_NODE_HEIGHT in workflow and workflow[MSR_NODE_HEIGHT].get("class_type") == "INTConstant":
                    workflow[MSR_NODE_HEIGHT]["inputs"]["value"] = height
                    logger.info(f"[ComfyUI] 高度覆盖 | 节点{MSR_NODE_HEIGHT} → {height}")
            if frame_count is not None and frame_count != 97:
                if MSR_NODE_TOTAL_LENGTH in workflow and workflow[MSR_NODE_TOTAL_LENGTH].get("class_type") == "INTConstant":
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
            (nid, n) for nid, n in workflow.items()
            if n.get("class_type") == "LoadImage"
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
    async def generate_long_video(
        self,
        prompt: str = "",
        reference_image: str = "",
        reference_images: Optional[Dict[str, str]] = None,
        segment_prompts: Optional[List[str]] = None,
        workflow_file: str = "LTX-2.3_MSR_sample_workflow_V2.json",
        segment_count: int = 4,
        segment_seconds: int = 15,
        frame_rate: int = 24,
        width: int = 1280,
        height: int = 720,
        project_id: Optional[str] = None,
        asset_tag: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        # TTS 配音相关参数
        tts_audios: Optional[List[str]] = None,
        tts_mode: str = "replace",
        tts_volume: float = 1.0,
        bgm_url: str = "",
        bgm_volume: float = 0.2,
        **kwargs,
    ) -> ComfyUIGenResult:
        """
        分段生成 + 拼接长视频（推荐方案）

        - 串行生成 N 个 segment_seconds 秒片段
        - 每段可注入不同的参考图（reference_images）和故事情节（segment_prompts）
        - 用 ffmpeg concat 拼接成最终长视频

        Args:
            reference_images: 多角色参考图字典，键为工作流原文件名
                示例：{"2.jpg": "主角URL", "1.jpg": "配角URL", "bg.png": "场景URL"}
            segment_prompts: 每段的 local_prompts（故事情节），长度应等于 segment_count
                示例：["女人走来", "两人对视", "开始对话", "并肩离去"]
            tts_audios: 每段对应的 TTS 音频 URL 列表（可选）
                长度应等于 segment_count；为空则不混入 TTS
            tts_mode: 'replace' TTS替代原音频 | 'overlay' TTS叠加原音频（仅当原视频有音频时生效）
            tts_volume: TTS 音量 0.0-1.0
            bgm_url: 背景音乐 URL（可选），将整段混入最终视频
            bgm_volume: BGM 音量 0.0-1.0
        """
        import asyncio
        import subprocess
        import random as _random
        from pathlib import Path

        start = time.time()
        total_frames = segment_seconds * frame_rate

        # 如果没有提供 segment_prompts，使用工作流原值（每段相同故事）
        use_segment_prompts = bool(segment_prompts) and len(segment_prompts) >= segment_count

        logger.info(
            f"[ComfyUI] 长视频分段生成 | segments={segment_count} "
            f"× {segment_seconds}s = {segment_count * segment_seconds}s | "
            f"独立故事情节={'是' if use_segment_prompts else '否(工作流原值)'} | "
            f"多角色参考图={'是' if reference_images else '否'}"
        )

        segment_urls: List[str] = []
        segment_filenames: List[str] = []
        segment_seeds: List[int] = []

        # 1. 串行生成每个片段
        for i in range(segment_count):
            seg_start = time.time()
            seg_seed = _random.randint(0, 2**48 - 1)
            segment_seeds.append(seg_seed)

            # 该段的故事情节（local_prompts）
            seg_local_prompts = ""
            if use_segment_prompts:
                seg_local_prompts = segment_prompts[i]
                logger.info(
                    f"[ComfyUI] 生成片段 {i+1}/{segment_count} | seed={seg_seed} | "
                    f"故事: {seg_local_prompts[:50]}..."
                )
            else:
                logger.info(
                    f"[ComfyUI] 生成片段 {i+1}/{segment_count} | seed={seg_seed} | "
                    f"故事: 工作流原值"
                )

            # 进度回调：整体进度 = 已完成片段/总片段 + 当前片段进度
            def seg_progress(frac: float, _cb_i: int = i):
                if progress_callback:
                    overall = int((_cb_i + frac) / segment_count * 100)
                    try:
                        progress_callback(f"片段 {_cb_i+1}/{segment_count}", overall)
                    except Exception:
                        pass

            # 构建该段的 kwargs：注入 reference_images 和该段 local_prompts
            seg_kwargs = dict(kwargs)
            if reference_images:
                seg_kwargs["reference_images"] = reference_images
            if seg_local_prompts:
                seg_kwargs["local_prompts"] = seg_local_prompts

            seg_result = await self.generate_video(
                prompt=prompt,
                reference_image=reference_image,
                workflow_file=workflow_file,
                width=width,
                height=height,
                frame_count=total_frames,
                frame_rate=frame_rate,
                seed=seg_seed,
                project_id=project_id,
                asset_tag=f"{asset_tag}_seg{i+1}" if asset_tag else f"seg{i+1}",
                progress_callback=seg_progress,
                **seg_kwargs,
            )

            if not seg_result.image_url:
                logger.error(f"[ComfyUI] 片段 {i+1} 生成失败")
                raise RuntimeError(f"片段 {i+1}/{segment_count} 生成失败")

            segment_urls.append(seg_result.image_url)
            segment_filenames.append(seg_result.filename)
            seg_elapsed = int(time.time() - seg_start)
            logger.info(
                f"[ComfyUI] 片段 {i+1} 完成 | file={seg_result.filename} "
                f"耗时={seg_elapsed}s"
            )

        # 2. 用 ffmpeg 拼接
        logger.info(f"[ComfyUI] 开始拼接 {segment_count} 个片段")

        # 准备 concat 列表文件
        tmp_dir = Path(tempfile.gettempdir()) / "director_long_video"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        list_file = tmp_dir / f"concat_{int(time.time())}.txt"

        # 下载每个片段到本地（ffmpeg concat 需要本地文件）
        local_segment_files: List[str] = []
        session = self._get_http_session()
        try:
            for i, url in enumerate(segment_urls):
                # 从 URL 提取 ComfyUI 文件名
                if "?filename=" in url:
                    fname = url.split("?filename=")[-1].split("&")[0]
                elif "/view?filename=" in url:
                    fname = url.split("/view?filename=")[-1].split("&")[0]
                else:
                    fname = f"seg_{i}.mp4"

                # 从 ComfyUI /view 下载
                view_url = f"{self.config.base_url}/view?filename={fname}&type=output"
                local_path = tmp_dir / f"seg_{i:02d}_{fname}"
                async with session.get(view_url) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"下载片段{i+1}失败: HTTP {resp.status}")
                    data = await resp.read()
                    local_path.write_bytes(data)
                local_segment_files.append(str(local_path))
                logger.info(f"[ComfyUI] 已下载片段 {i+1} | {local_path.name}")

            # 写入 concat 列表（使用绝对路径，转义反斜杠）
            with open(list_file, "w", encoding="utf-8") as f:
                for local_path in local_segment_files:
                    # Windows 路径反斜杠转义
                    escaped = local_path.replace("\\", "/")
                    f.write(f"file '{escaped}'\n")

            # 拼接输出
            final_filename = f"longvideo_{int(time.time())}.mp4"
            output_dir = Path(COMFYUI_DIR) / "output" if COMFYUI_DIR else tmp_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / final_filename

            # 检测每个片段的音频流
            def _has_audio_stream(file_path: str) -> bool:
                try:
                    probe = subprocess.run(
                        ["ffmpeg", "-i", file_path, "-hide_banner"],
                        capture_output=True, text=True, timeout=30,
                        encoding="utf-8", errors="replace",
                    )
                    return "Audio:" in (proc.stderr if False else probe.stderr)
                except Exception:
                    return False

            def _probe_audio_codec(file_path: str) -> str:
                """返回音频编码名（如 aac/opus），无音频返回空串"""
                try:
                    probe = subprocess.run(
                        ["ffprobe", "-v", "error", "-select_streams", "a:0",
                         "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                         file_path],
                        capture_output=True, text=True, timeout=30,
                        encoding="utf-8", errors="replace",
                    )
                    return (probe.stdout or "").strip()
                except Exception:
                    return ""

            seg_has_audio = any(_probe_audio_codec(f) for f in local_segment_files)
            logger.info(f"[ComfyUI] 片段音频检测 | 任一片段含音频={seg_has_audio}")

            # 优先尝试 concat copy（视频流不变，音频流也 copy）
            # 若任一片段无音频或 copy 失败，则使用重编码 + 强制音频规范化
            use_reencode = not seg_has_audio
            if not use_reencode:
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(list_file),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(output_path),
                ]
                logger.info(f"[ComfyUI] ffmpeg 拼接(copy) | cmd={' '.join(cmd[:8])}...")
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=300,
                    encoding="utf-8", errors="replace",
                )
                if proc.returncode != 0:
                    logger.warning(f"[ComfyUI] copy 拼接失败，回退重编码 | stderr={proc.stderr[-300:]}")
                    use_reencode = True

            if use_reencode:
                # 重编码：视频 libx264，音频 aac；若输入无音频则注入静音轨道
                logger.info("[ComfyUI] 使用重编码拼接（保证音频流）")
                if seg_has_audio:
                    cmd_reencode = [
                        "ffmpeg", "-y",
                        "-f", "concat",
                        "-safe", "0",
                        "-i", str(list_file),
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k",
                        "-movflags", "+faststart",
                        str(output_path),
                    ]
                else:
                    # 输入无音频：用第一个片段做视频源 + 合成静音音频轨道
                    # 时长对齐视频流；后续可由用户叠加 BGM/TTS
                    cmd_reencode = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0",
                        "-i", str(list_file),
                        "-f", "lavfi", "-t", "0", "-i", "anullsrc=r=44100:cl=stereo",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k",
                        "-shortest",
                        "-movflags", "+faststart",
                        str(output_path),
                    ]
                logger.info(f"[ComfyUI] ffmpeg 拼接(reencode) | cmd={' '.join(cmd_reencode[:8])}...")
                proc = subprocess.run(
                    cmd_reencode, capture_output=True, text=True, timeout=900,
                    encoding="utf-8", errors="replace",
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg 拼接失败: {proc.stderr[-500:]}")

            # 验证输出文件确实有音频流
            out_audio = _probe_audio_codec(str(output_path))
            logger.info(f"[ComfyUI] 拼接完成 | output={output_path} | size={output_path.stat().st_size//1024}KB | audio={out_audio or '无'}")

            # ===== TTS 配音混音 =====
            # 如果提供了 TTS 音频，按段对齐并混入视频
            use_tts = bool(tts_audios) and len(tts_audios) >= segment_count
            if use_tts:
                try:
                    import aiohttp as _aiohttp_tts
                    import urllib.request as _urlreq

                    logger.info(
                        f"[ComfyUI-TTS] 开始混音 | tts_mode={tts_mode} | "
                        f"tts_volume={tts_volume} | bgm={'是' if bgm_url else '否'}"
                    )

                    # 1. 下载每段 TTS 音频到临时目录
                    tts_local_files: List[str] = []
                    tts_session = self._get_http_session()
                    for i, tts_url in enumerate(tts_audios[:segment_count]):
                        if not tts_url:
                            tts_local_files.append("")
                            continue
                        tts_filename = f"tts_seg{i+1}_{int(time.time())}.flac"
                        tts_path = tmp_dir / tts_filename
                        try:
                            async with tts_session.get(
                                tts_url, timeout=_aiohttp_tts.ClientTimeout(total=60)
                            ) as tts_resp:
                                if tts_resp.status != 200:
                                    logger.warning(f"[ComfyUI-TTS] 段{i+1}下载失败 status={tts_resp.status}")
                                    tts_local_files.append("")
                                    continue
                                tts_data = await tts_resp.read()
                            tts_path.write_bytes(tts_data)
                            tts_local_files.append(str(tts_path))
                            logger.info(f"[ComfyUI-TTS] 段{i+1}已下载 | size={len(tts_data)//1024}KB")
                        except Exception as tts_e:
                            logger.warning(f"[ComfyUI-TTS] 段{i+1}下载异常: {tts_e}")
                            tts_local_files.append("")

                    # 2. 把每段 TTS 音频按段时长对齐，合并成一个完整音轨
                    merged_audio_path = tmp_dir / f"tts_merged_{int(time.time())}.flac"
                    seg_duration = segment_seconds  # 每段视频时长（秒）

                    # 用 ffmpeg 把每段 TTS 拼到对应时间点（不足补静音，超长截断）
                    filter_parts = []
                    inputs = []
                    valid_tts_count = 0
                    for i, tts_local in enumerate(tts_local_files):
                        if not tts_local:
                            # 该段无 TTS，用对应时长的静音
                            inputs += ["-f", "lavfi", "-t", str(seg_duration), "-i", "anullsrc=r=44100:cl=stereo"]
                        else:
                            inputs += ["-i", tts_local]
                        # 对该段做：apad 补齐到 seg_duration，然后 atrim 截断
                        # 如果是静音源，已经正好 seg_duration
                        filter_parts.append(f"[{i}:a]atrim=0:{seg_duration},asetpts=PTS-STARTPTS,apad=whole_dur={seg_duration},atrim=0:{seg_duration},asetpts=PTS-STARTPTS[a{i}]")
                        valid_tts_count += 1

                    # 合并所有段
                    concat_filter = "".join(f"[a{i}]" for i in range(valid_tts_count))
                    filter_complex = ";".join(filter_parts) + f";{concat_filter}concat=n={valid_tts_count}:v=0:a=1[out]"

                    cmd_merge = [
                        "ffmpeg", "-y",
                        *inputs,
                        "-filter_complex", filter_complex,
                        "-map", "[out]",
                        "-c:a", "flac",
                        str(merged_audio_path),
                    ]
                    logger.info(f"[ComfyUI-TTS] 合并 TTS 段 | cmd={' '.join(cmd_merge[:6])}...")
                    proc_merge = subprocess.run(
                        cmd_merge, capture_output=True, text=True, timeout=300,
                        encoding="utf-8", errors="replace",
                    )
                    if proc_merge.returncode != 0:
                        logger.warning(f"[ComfyUI-TTS] 合并失败，跳过 TTS | stderr={proc_merge.stderr[-300:]}")
                        use_tts = False
                    else:
                        logger.info(f"[ComfyUI-TTS] TTS 合并完成 | file={merged_audio_path.name}")

                    # 3. 把合并后的 TTS 音轨混入最终视频
                    if use_tts and merged_audio_path.exists():
                        mixed_path = output_dir / f"longvideo_tts_{int(time.time())}.mp4"
                        if tts_mode == "replace" or not out_audio:
                            # 替换原音频：直接用 TTS 作为最终音轨
                            audio_filter = f"[1:a]volume={tts_volume}[tts]"
                            map_args = ["-map", "0:v", "-map", "[tts]"]
                        else:
                            # 叠加原音频：原音频 + TTS 混音
                            audio_filter = (
                                f"[0:a]volume=1.0[orig];"
                                f"[1:a]volume={tts_volume}[tts];"
                                f"[orig][tts]amix=inputs=2:duration=first:dropout_transition=0[mix]"
                            )
                            map_args = ["-map", "0:v", "-map", "[mix]"]

                        # 如果有 BGM，再叠加一层
                        bgm_inputs = []
                        bgm_filter = ""
                        if bgm_url:
                            try:
                                bgm_local = tmp_dir / f"bgm_{int(time.time())}.flac"
                                async with tts_session.get(
                                    bgm_url, timeout=_aiohttp_tts.ClientTimeout(total=60)
                                ) as bgm_resp:
                                    if bgm_resp.status == 200:
                                        bgm_data = await bgm_resp.read()
                                        bgm_local.write_bytes(bgm_data)
                                        bgm_inputs = ["-i", str(bgm_local)]
                                        # 把 BGM 循环到视频时长，再与前述 mix 混音
                                        if "mix" in audio_filter:
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[mix][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                                        else:
                                            # replace 模式 + BGM
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[tts][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                            except Exception as bgm_e:
                                logger.warning(f"[ComfyUI-TTS] BGM 下载失败: {bgm_e}")

                        cmd_mix = [
                            "ffmpeg", "-y",
                            "-i", str(output_path),
                            "-i", str(merged_audio_path),
                            *bgm_inputs,
                            "-filter_complex", audio_filter + bgm_filter,
                            *map_args,
                            "-c:v", "copy",
                            "-c:a", "aac", "-b:a", "192k",
                            "-shortest",
                            "-movflags", "+faststart",
                            str(mixed_path),
                        ]
                        logger.info(f"[ComfyUI-TTS] 混音 | mode={tts_mode} | bgm={'是' if bgm_url else '否'}")
                        proc_mix = subprocess.run(
                            cmd_mix, capture_output=True, text=True, timeout=600,
                            encoding="utf-8", errors="replace",
                        )
                        if proc_mix.returncode == 0 and mixed_path.exists():
                            # 替换输出文件
                            try:
                                output_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            output_path = mixed_path
                            final_filename = mixed_path.name
                            logger.info(f"[ComfyUI-TTS] 混音完成 | file={final_filename} | size={mixed_path.stat().st_size//1024}KB")
                        else:
                            logger.warning(f"[ComfyUI-TTS] 混音失败，保留原视频 | stderr={proc_mix.stderr[-300:]}")

                        # 清理 TTS 临时文件
                        try:
                            merged_audio_path.unlink(missing_ok=True)
                            for f in tts_local_files:
                                if f:
                                    Path(f).unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception as tts_outer_e:
                    logger.warning(f"[ComfyUI-TTS] TTS 混音整体失败，保留原视频 | error={tts_outer_e}")

            # 清理临时文件
            try:
                list_file.unlink(missing_ok=True)
                for f in local_segment_files:
                    Path(f).unlink(missing_ok=True)
            except Exception:
                pass

            # 上传最终视频到 ComfyUI output（如果不在）
            final_url = f"{self.config.base_url}/view?filename={final_filename}"
            elapsed_ms = int((time.time() - start) * 1000)

            logger.info(
                f"[ComfyUI] 长视频生成完成 | 总时长={segment_count * segment_seconds}s "
                f"| 耗时={elapsed_ms//1000}s | seeds={segment_seeds}"
            )

            return ComfyUIGenResult(
                image_url=final_url,
                filename=final_filename,
                images=[final_url],
                filenames=[final_filename],
                prompt_id="",
                elapsed_ms=elapsed_ms,
                seed=segment_seeds[0],
                prompt=prompt,
            )
        finally:
            pass
