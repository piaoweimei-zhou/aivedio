"""
视频剪辑阶段

视频剪辑、拼接、转场。
使用 ltx-director-timeline + ffmpeg 本地处理。

Script 感知：当输入的 video 资产携带 sibling_asset_ids（来自 script 批量生成）
时，自动展开所有兄弟视频片段，按 act_index 排序后拼接成完整成片。
"""

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.stage_service import StageDef, StagePlugin, collect_content_type

logger = logging.getLogger(__name__)


class EditStage(StagePlugin):
    """视频剪辑阶段"""

    stage_def = StageDef(
        stage_id="edit",
        name="视频剪辑",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="视频剪辑、拼接、转场（支持 script 批量片段自动拼接）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        asset_svc = get_asset_service()

        if not input_assets:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="视频剪辑需要至少一个视频资产",
            )

        # ── 展开 sibling_asset_ids：收集所有视频片段 ──
        video_clips = self._collect_video_clips(input_assets, asset_svc)

        # 收集所有视频 URL（按 clip 顺序）
        video_urls: List[str] = []
        for clip in video_clips:
            for url in clip.urls:
                if url and url not in video_urls:
                    video_urls.append(url)
            meta_url = clip.metadata.get("video_url", "")
            if meta_url and meta_url not in video_urls:
                video_urls.append(meta_url)

        if not video_urls:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="无有效视频 URL",
            )

        mode = params.get("mode", "concat")  # concat / trim / transition / beat
        output_name = params.get("name", "剪辑视频")

        # 自动判断：如果有多个片段且 mode=concat，自动拼接
        has_siblings = any(
            a.metadata.get("sibling_asset_ids")
            or a.metadata.get("script_asset_id")
            for a in input_assets
        )
        if has_siblings and mode == "concat" and len(video_urls) > 1:
            logger.info(
                f"[EditStage] Script 感知拼接 | clips={len(video_clips)} | "
                f"urls={len(video_urls)} | siblings_detected={has_siblings}"
            )

        logger.info(f"[EditStage] 剪辑 | mode={mode} | videos={len(video_urls)}")

        try:
            if mode == "concat" and len(video_urls) > 1:
                result_url = await self._concat_videos(video_urls)
            elif mode == "beat":
                result_url = await self._beat_sync_videos(video_urls, params)
            elif mode == "trim":
                start = float(params.get("start", 0))
                end = float(params.get("end", 0))
                result_url = await self._trim_video(video_urls[0], start, end)
            else:
                # 单视频直接返回
                result_url = video_urls[0]

            source_ids = [c.asset_id for c in video_clips]
            content_type = collect_content_type(input_assets)

            new_asset = await self._register_asset_direct(
                asset_svc,
                asset_type="video",
                name=output_name,
                urls=[result_url] if result_url else [],
                input_assets=input_assets,
                extra_metadata={
                    "source_asset_ids": source_ids,
                    "mode": mode,
                    "video_url": result_url,
                    "clip_count": len(video_clips),
                    "script_aware": has_siblings,
                },
                content_type=content_type,
            )

            return AssetProduceResult(
                asset=new_asset,
                success=True,
            )

        except Exception as e:
            logger.error(f"[EditStage] 剪辑失败: {e}")
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=str(e),
            )

    def _collect_video_clips(
        self, input_assets: List[AssetRef], asset_svc,
    ) -> List[AssetRef]:
        """收集所有视频片段（input 主资产 + sibling_asset_ids 展开）

        按 act_index 排序，确保和 script acts 顺序对齐
        """
        clips: List[AssetRef] = []
        seen_ids = set()
        for asset in input_assets:
            if asset.asset_type != "video":
                continue
            if asset.asset_id in seen_ids:
                continue
            seen_ids.add(asset.asset_id)
            clips.append(asset)
            # 展开 sibling_asset_ids
            for sid in asset.metadata.get("sibling_asset_ids", []):
                if sid in seen_ids:
                    continue
                sibling = asset_svc.get(sid) if hasattr(asset_svc, "get") else None
                if sibling:
                    seen_ids.add(sid)
                    clips.append(sibling)

        # 按 act_index 排序（有 act_index 的排前面，无的排后面）
        def _act_index(a: AssetRef) -> int:
            try:
                return int(a.metadata.get("act_index", 999))
            except Exception:
                return 999
        clips.sort(key=_act_index)
        return clips

    async def _concat_videos(self, video_urls: List[str]) -> str:
        """使用 ffmpeg 拼接视频"""
        import tempfile
        import uuid

        # 检查 ffmpeg
        ffmpeg = os.getenv("FFMPEG_PATH", "ffmpeg")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError("ffmpeg 不可用")
        except FileNotFoundError:
            raise RuntimeError("ffmpeg 未安装，请安装 ffmpeg 或设置 FFMPEG_PATH")

        # 下载远程视频到临时文件
        from services.providers.provider_utils import output_path_for, output_url_for
        temp_files = []
        for url in video_urls:
            if url.startswith(("http://", "https://")):
                import httpx
                async with httpx.AsyncClient(timeout=httpx.Timeout(connect=20.0, read=300.0)) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    temp_path = output_path_for(f"temp_{uuid.uuid4().hex[:8]}.mp4", "temp")
                    with open(temp_path, "wb") as f:
                        f.write(resp.content)
                    temp_files.append(temp_path)
            else:
                # 本地路径
                from services.providers.provider_utils import output_file_from_url
                local = output_file_from_url(url)
                if local and os.path.exists(local):
                    temp_files.append(local)

        if not temp_files:
            raise RuntimeError("无有效视频文件")

        if len(temp_files) == 1:
            return video_urls[0]

        # 创建 concat 文件列表
        concat_file = output_path_for(f"concat_{uuid.uuid4().hex[:8]}.txt", "temp")
        with open(concat_file, "w", encoding="utf-8") as f:
            for path in temp_files:
                f.write(f"file '{path}'\n")

        output_file = output_path_for(f"edit_{uuid.uuid4().hex[:8]}.mp4", "output")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-c", "copy", output_file,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat 失败: {stderr.decode('utf-8', errors='replace')[:500]}")

        # 清理临时文件
        try:
            os.remove(concat_file)
        except Exception:
            pass

        return output_url_for(os.path.basename(output_file), "output")

    async def _trim_video(self, video_url: str, start: float, end: float) -> str:
        """使用 ffmpeg 裁剪视频"""
        import uuid

        ffmpeg = os.getenv("FFMPEG_PATH", "ffmpeg")
        from services.providers.provider_utils import output_file_from_url, output_path_for, output_url_for

        local = output_file_from_url(video_url)
        if not local or not os.path.exists(local):
            raise RuntimeError(f"视频文件不存在: {video_url}")

        output_file = output_path_for(f"trim_{uuid.uuid4().hex[:8]}.mp4", "output")
        args = [ffmpeg, "-y", "-i", local]
        if start > 0:
            args.extend(["-ss", str(start)])
        if end > 0:
            args.extend(["-to", str(end)])
        args.extend(["-c", "copy", output_file])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg trim 失败: {stderr.decode('utf-8', errors='replace')[:500]}")

        return output_url_for(os.path.basename(output_file), "output")

    # ── 卡点剪辑（beat-sync）──

    @staticmethod
    def _compute_beat_plan(bpm: float, beats_per_cut: int, target_duration: float):
        """计算卡点切段计划

        每段时长 = beats_per_cut 个节拍；段数按目标时长（扣除转场重叠）取整并封顶 60；
        返回 (seg_dur, n_segments, transition_dur, total_duration)
        """
        beat_interval = 60.0 / bpm
        seg_dur = beat_interval * beats_per_cut
        transition_dur = min(0.3, seg_dur * 0.4)
        if seg_dur > transition_dur:
            n_segments = max(
                1, int(round((target_duration - transition_dur) / (seg_dur - transition_dur)))
            )
        else:
            n_segments = max(1, int(round(target_duration / seg_dur)))
        n_segments = min(n_segments, 60)
        total_duration = n_segments * seg_dur - (n_segments - 1) * transition_dur
        return seg_dur, n_segments, transition_dur, total_duration

    async def _beat_sync_videos(
        self, video_urls: List[str], params: Dict[str, Any]
    ) -> str:
        """卡点剪辑：按 BGM 节拍切段 + xfade 转场 + 合成 BGM 混音"""
        import uuid

        from services.stages.ffmpeg_utils import check_ffmpeg, resolve_local_video, run_ffmpeg
        from services.providers.provider_utils import output_path_for, output_url_for

        bpm = float(params.get("bpm", 120))
        beats_per_cut = int(params.get("beats_per_cut", 2))
        transition = params.get("transition", "fade")
        sfx = params.get("sfx", "whoosh")
        target_duration = float(params.get("target_duration", 30))

        await check_ffmpeg()

        seg_dur, n_segments, transition_dur, total_duration = self._compute_beat_plan(
            bpm, beats_per_cut, target_duration
        )
        logger.info(
            f"[EditStage] 卡点剪辑 | bpm={bpm} | beats_per_cut={beats_per_cut} | "
            f"seg_dur={seg_dur:.3f}s | segments={n_segments} | transition={transition} | "
            f"sfx={sfx} | total={total_duration:.2f}s"
        )

        # 解析本地视频（循环填充片段）
        local_paths = []
        for url in video_urls:
            local_paths.append(await resolve_local_video(url))
        if not local_paths:
            raise RuntimeError("无有效视频文件")
        seg_sources = [local_paths[i % len(local_paths)] for i in range(n_segments)]

        # xfade 转场链（视频）
        video_path = await self._xfade_chain(
            seg_sources, transition, seg_dur, transition_dur
        )

        # 合成 BGM 音轨
        bgm_path = output_path_for(f"beat_bgm_{uuid.uuid4().hex[:8]}.wav", "temp")
        self._synthesize_beat_track(bpm, total_duration, sfx, bgm_path)

        # 混音（视频 + BGM）
        final_path = output_path_for(f"beat_{uuid.uuid4().hex[:8]}.mp4", "output")
        await run_ffmpeg([
            "-y", "-i", video_path, "-i", bgm_path,
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            final_path,
        ])

        # 清理临时文件
        for p in [video_path, bgm_path]:
            try:
                os.remove(p)
            except OSError:
                pass

        return output_url_for(os.path.basename(final_path), "output")

    async def _xfade_chain(
        self, seg_sources: List[str], transition: str,
        seg_dur: float, transition_dur: float,
    ) -> str:
        """用 xfade 把多段视频串成转场链（每段裁剪到 seg_dur）"""
        import uuid

        from services.stages.ffmpeg_utils import run_ffmpeg
        from services.providers.provider_utils import output_path_for

        inputs = []
        for p in seg_sources:
            # -stream_loop -1：短片段自动循环补齐到 seg_dur
            inputs += ["-stream_loop", "-1", "-i", p]

        filters = []
        for i in range(len(seg_sources)):
            filters.append(
                f"[{i}:v]trim=duration={seg_dur:.3f},setpts=PTS-STARTPTS[v{i}]"
            )

        prev = "v0"
        for i in range(1, len(seg_sources)):
            out = f"vx{i}"
            offset = i * (seg_dur - transition_dur)
            filters.append(
                f"[{prev}][v{i}]xfade=transition={transition}:"
                f"duration={transition_dur:.3f}:offset={offset:.3f}[{out}]"
            )
            prev = out

        filters.append(f"[{prev}]format=yuv420p[vout]")

        output_file = output_path_for(f"beat_video_{uuid.uuid4().hex[:8]}.mp4", "temp")
        await run_ffmpeg([
            "-y"] + inputs + [
            "-filter_complex", ";".join(filters),
            "-map", "[vout]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            output_file,
        ])
        return output_file

    @staticmethod
    def _synthesize_beat_track(
        bpm: float, duration: float, sfx: str, output_path: str,
        sample_rate: int = 44100,
    ) -> None:
        """用 numpy 合成卡点 BGM 音轨（kick + hihat + bass + 转场音效）"""
        import numpy as np
        import wave

        beat = 60.0 / bpm
        n_beats = max(1, int(duration / beat))
        total = int(duration * sample_rate)
        track = np.zeros(total, dtype=np.float64)

        def _add(sig: np.ndarray, at_sec: float) -> None:
            at = int(at_sec * sample_rate)
            if at < 0:
                # 负偏移（如 whoosh 提前 0.05s）：裁掉起始前被截掉的部分
                sig = sig[-at:]
                at = 0
            if at >= total:
                return
            end = min(at + len(sig), total)
            track[at:end] += sig[: end - at]

        for i in range(n_beats):
            t0 = i * beat

            # kick：音头 120Hz 下滑到 40Hz + 指数衰减
            kl = int(0.12 * sample_rate)
            t = np.arange(kl) / sample_rate
            freq = 120.0 * np.exp(-t * 30) + 40.0
            phase = 2 * np.pi * np.cumsum(freq) / sample_rate
            _add(np.sin(phase) * np.exp(-t * 22), t0)

            # hihat：每半拍噪声短促音
            hl = int(0.05 * sample_rate)
            ht = np.arange(hl) / sample_rate
            _add(np.random.randn(hl) * np.exp(-ht * 60) * 0.3, t0 + beat / 2)

            # bass：每 4 拍换音（低音进行）
            if i % 4 == 0:
                bl = int(beat * 3.5 * sample_rate)
                bt = np.arange(bl) / sample_rate
                bass_freq = [55.0, 55.0, 65.41, 49.0][(i // 4) % 4]
                _add(np.sin(2 * np.pi * bass_freq * bt) * np.exp(-bt * 1.2) * 0.4, t0)

            # 转场音效
            if sfx == "whoosh":
                wl = int(0.25 * sample_rate)
                wt = np.arange(wl) / sample_rate
                sweep = np.sin(2 * np.pi * (200 + 2000 * wt) * wt) * np.exp(-wt * 8) * 0.25
                _add(sweep, t0 - 0.05)
            elif sfx == "hit":
                hl2 = int(0.08 * sample_rate)
                ht2 = np.arange(hl2) / sample_rate
                _add(np.sin(2 * np.pi * 800 * ht2) * np.exp(-ht2 * 40) * 0.3, t0)
            elif sfx == "soft":
                sl = int(0.3 * sample_rate)
                st = np.arange(sl) / sample_rate
                _add(np.sin(2 * np.pi * 300 * st) * np.exp(-st * 10) * 0.15, t0)

        # 归一化并写入 WAV
        peak = float(np.max(np.abs(track))) or 1.0
        pcm = (track / peak * 0.9 * 32767).astype(np.int16)
        with wave.open(output_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm.tobytes())
