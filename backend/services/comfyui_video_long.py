"""
ComfyUI 服务 — 长视频/Minimax H3 生成 Mixin（从 comfyui_video.py 拆分，P2 治理）

被 ComfyUIVideoMixin 继承（MRO），generate_long_video 调用主类 generate_video。
"""

import logging
import tempfile
import time
from typing import Callable, Dict, List, Optional

from services.comfyui.config import COMFYUI_DIR
from services.comfyui_helpers import ComfyUIGenResult

logger = logging.getLogger(__name__)


class ComfyUIVideoLongMixin:
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
                f"[ComfyUI] 片段 {i+1} 完成 | file={seg_result.filename} " f"耗时={seg_elapsed}s"
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
                        capture_output=True,
                        text=True,
                        timeout=30,
                        encoding="utf-8",
                        errors="replace",
                    )
                    return "Audio:" in (proc.stderr if False else probe.stderr)
                except Exception:
                    return False

            def _probe_audio_codec(file_path: str) -> str:
                """返回音频编码名（如 aac/opus），无音频返回空串"""
                try:
                    probe = subprocess.run(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-select_streams",
                            "a:0",
                            "-show_entries",
                            "stream=codec_name",
                            "-of",
                            "csv=p=0",
                            file_path,
                        ],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        encoding="utf-8",
                        errors="replace",
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
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-c",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ]
                logger.info(f"[ComfyUI] ffmpeg 拼接(copy) | cmd={' '.join(cmd[:8])}...")
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode != 0:
                    logger.warning(
                        f"[ComfyUI] copy 拼接失败，回退重编码 | stderr={proc.stderr[-300:]}"
                    )
                    use_reencode = True

            if use_reencode:
                # 重编码：视频 libx264，音频 aac；若输入无音频则注入静音轨道
                logger.info("[ComfyUI] 使用重编码拼接（保证音频流）")
                if seg_has_audio:
                    cmd_reencode = [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(list_file),
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "20",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ]
                else:
                    # 输入无音频：用第一个片段做视频源 + 合成静音音频轨道
                    # 时长对齐视频流；后续可由用户叠加 BGM/TTS
                    cmd_reencode = [
                        "ffmpeg",
                        "-y",
                        "-f",
                        "concat",
                        "-safe",
                        "0",
                        "-i",
                        str(list_file),
                        "-f",
                        "lavfi",
                        "-t",
                        "0",
                        "-i",
                        "anullsrc=r=44100:cl=stereo",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "20",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-shortest",
                        "-movflags",
                        "+faststart",
                        str(output_path),
                    ]
                logger.info(
                    f"[ComfyUI] ffmpeg 拼接(reencode) | cmd={' '.join(cmd_reencode[:8])}..."
                )
                proc = subprocess.run(
                    cmd_reencode,
                    capture_output=True,
                    text=True,
                    timeout=900,
                    encoding="utf-8",
                    errors="replace",
                )
                if proc.returncode != 0:
                    raise RuntimeError(f"ffmpeg 拼接失败: {proc.stderr[-500:]}")

            # 验证输出文件确实有音频流
            out_audio = _probe_audio_codec(str(output_path))
            logger.info(
                f"[ComfyUI] 拼接完成 | output={output_path} | size={output_path.stat().st_size//1024}KB | audio={out_audio or '无'}"  # noqa: E501
            )

            # ===== TTS 配音混音 =====
            # 如果提供了 TTS 音频，按段对齐并混入视频
            use_tts = bool(tts_audios) and len(tts_audios) >= segment_count
            if use_tts:
                try:
                    import aiohttp as _aiohttp_tts

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
                                    logger.warning(
                                        f"[ComfyUI-TTS] 段{i+1}下载失败 status={tts_resp.status}"
                                    )
                                    tts_local_files.append("")
                                    continue
                                tts_data = await tts_resp.read()
                            tts_path.write_bytes(tts_data)
                            tts_local_files.append(str(tts_path))
                            logger.info(
                                f"[ComfyUI-TTS] 段{i+1}已下载 | size={len(tts_data)//1024}KB"
                            )
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
                            inputs += [
                                "-f",
                                "lavfi",
                                "-t",
                                str(seg_duration),
                                "-i",
                                "anullsrc=r=44100:cl=stereo",
                            ]
                        else:
                            inputs += ["-i", tts_local]
                        # 对该段做：apad 补齐到 seg_duration，然后 atrim 截断
                        # 如果是静音源，已经正好 seg_duration
                        filter_parts.append(
                            f"[{i}:a]atrim=0:{seg_duration},asetpts=PTS-STARTPTS,apad=whole_dur={seg_duration},atrim=0:{seg_duration},asetpts=PTS-STARTPTS[a{i}]"  # noqa: E501
                        )
                        valid_tts_count += 1

                    # 合并所有段
                    concat_filter = "".join(f"[a{i}]" for i in range(valid_tts_count))
                    filter_complex = (
                        ";".join(filter_parts)
                        + f";{concat_filter}concat=n={valid_tts_count}:v=0:a=1[out]"
                    )

                    cmd_merge = [
                        "ffmpeg",
                        "-y",
                        *inputs,
                        "-filter_complex",
                        filter_complex,
                        "-map",
                        "[out]",
                        "-c:a",
                        "flac",
                        str(merged_audio_path),
                    ]
                    logger.info(f"[ComfyUI-TTS] 合并 TTS 段 | cmd={' '.join(cmd_merge[:6])}...")
                    proc_merge = subprocess.run(
                        cmd_merge,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if proc_merge.returncode != 0:
                        logger.warning(
                            f"[ComfyUI-TTS] 合并失败，跳过 TTS | stderr={proc_merge.stderr[-300:]}"
                        )
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
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[mix][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"  # noqa: E501
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                                        else:
                                            # replace 模式 + BGM
                                            bgm_filter = f";[2:a]aloop=loop=-1:size=2e9,volume={bgm_volume}[bgm];[tts][bgm]amix=inputs=2:duration=first:dropout_transition=0[final]"  # noqa: E501
                                            map_args = ["-map", "0:v", "-map", "[final]"]
                            except Exception as bgm_e:
                                logger.warning(f"[ComfyUI-TTS] BGM 下载失败: {bgm_e}")

                        cmd_mix = [
                            "ffmpeg",
                            "-y",
                            "-i",
                            str(output_path),
                            "-i",
                            str(merged_audio_path),
                            *bgm_inputs,
                            "-filter_complex",
                            audio_filter + bgm_filter,
                            *map_args,
                            "-c:v",
                            "copy",
                            "-c:a",
                            "aac",
                            "-b:a",
                            "192k",
                            "-shortest",
                            "-movflags",
                            "+faststart",
                            str(mixed_path),
                        ]
                        logger.info(
                            f"[ComfyUI-TTS] 混音 | mode={tts_mode} | bgm={'是' if bgm_url else '否'}"
                        )
                        proc_mix = subprocess.run(
                            cmd_mix,
                            capture_output=True,
                            text=True,
                            timeout=600,
                            encoding="utf-8",
                            errors="replace",
                        )
                        if proc_mix.returncode == 0 and mixed_path.exists():
                            # 替换输出文件
                            try:
                                output_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            output_path = mixed_path
                            final_filename = mixed_path.name
                            logger.info(
                                f"[ComfyUI-TTS] 混音完成 | file={final_filename} | size={mixed_path.stat().st_size//1024}KB"  # noqa: E501
                            )
                        else:
                            logger.warning(
                                f"[ComfyUI-TTS] 混音失败，保留原视频 | stderr={proc_mix.stderr[-300:]}"
                            )

                        # 清理 TTS 临时文件
                        try:
                            merged_audio_path.unlink(missing_ok=True)
                            for f in tts_local_files:
                                if f:
                                    Path(f).unlink(missing_ok=True)
                        except Exception:
                            pass
                except Exception as tts_outer_e:
                    logger.warning(
                        f"[ComfyUI-TTS] TTS 混音整体失败，保留原视频 | error={tts_outer_e}"
                    )

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

    async def generate_minimax_h3(
        self,
        prompt: str = "",
        width: int = 480,
        height: int = 864,
        duration_seconds: float = 5.0,
        seed: Optional[int] = None,
        audio_mode: str = "native",
        video_steps: int = 8,
        audio_steps: int = 10,
        filename_prefix: str = "minimax_h3",
        reference_image_url: str = "",
        progress_callback: Optional[Callable] = None,
        **kwargs,
    ) -> ComfyUIGenResult:
        """MiniMax H3 视频生成（本地 FL2VA 链路，含同步生成环境音）

        使用 workflow_minimax 构建器动态生成参数化工作流，经 ComfyUI 队列提交。
        输出为带音轨的 mp4（VHS_VideoCombine），从 /history 的 gifs/videos 字段取回。
        reference_image_url 非空时走 I2VA（图生视频），否则 T2VA（纯文本→视频）。
        """
        from services.workflow_minimax import build_minimax_h3_video_workflow

        start = time.time()
        actual_seed = seed if seed is not None else int(time.time() * 1000) % (2**32)
        # MiniMax 分辨率选择器要求 32 对齐，交由 builder 统一处理
        if "frame_rate" in kwargs:
            del kwargs["frame_rate"]

        reference_image = ""
        if reference_image_url:
            reference_image = await self._download_to_input(reference_image_url)
            if not reference_image:
                logger.warning(
                    f"[MiniMaxH3] 参考图下载失败，回退 T2VA | url={reference_image_url[:80]}"
                )

        workflow = build_minimax_h3_video_workflow(
            prompt=prompt,
            width=int(width or 480),
            height=int(height or 864),
            duration_seconds=float(duration_seconds or 5.0),
            seed=actual_seed,
            audio_mode=audio_mode or "native",
            video_steps=int(video_steps or 8),
            audio_steps=int(audio_steps or 10),
            filename_prefix=filename_prefix,
            reference_image=reference_image,
        )
        logger.info(
            f"[MiniMaxH3] 提交 | prompt={prompt[:50]}... | size={workflow['cond']['inputs']['width']}x"  # noqa: E501
            f"{workflow['cond']['inputs']['height']} | seed={actual_seed} | audio={audio_mode}"
            f" | mode={'I2VA' if reference_image else 'T2VA'}"
        )

        prompt_id = await self._queue_prompt_with_retry(workflow)
        logger.info(f"[MiniMaxH3] 已提交 | prompt_id={prompt_id}")

        # 等待完成（视频文件输出在 gifs/videos 字段，video 超时档）
        filenames = await self._wait_for_completion(
            prompt_id=prompt_id,
            task_type="video",
            progress_callback=progress_callback,
            output_fields=("gifs", "videos"),
        )
        video_filename = filenames[0] if filenames else ""

        video_url = ""
        if video_filename:
            video_url = f"{self.config.base_url}/view?filename={video_filename}"

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            f"[MiniMaxH3] 完成 | file={video_filename} | elapsed={elapsed_ms}ms | url={video_url[:80]}"  # noqa: E501
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
