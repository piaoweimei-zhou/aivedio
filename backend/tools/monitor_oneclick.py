#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""监控一键成片批次，等待逐镜 video / export 完成，校验成片音画时长对齐"""
import asyncio
import json
import subprocess
import sys
import time

import httpx

# Windows 控制台 stdout 默认编码可能无法输出中文，强制 UTF-8 避免 OSError: Invalid argument
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOST = "http://127.0.0.1:8000"
BATCH_ID = sys.argv[1] if len(sys.argv) > 1 else "batch_932c9a4558c5"
INTERVAL = 10  # 秒


def ffprobe(path):
    """返回 (has_audio, video_duration, audio_duration, audio_start)"""
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, text=True,
        )
        data = json.loads(p.stdout)
        v_dur = a_dur = None
        a_start = None
        has_audio = False
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                v_dur = float(s.get("duration") or data.get("format", {}).get("duration") or 0)
            elif s.get("codec_type") == "audio":
                has_audio = True
                a_dur = float(s.get("duration") or 0)
                a_start = float(s.get("start_time") or 0)
        return has_audio, v_dur, a_dur, a_start
    except Exception as e:
        return None


async def main():
    async with httpx.AsyncClient(base_url=HOST, timeout=20) as client:
        while True:
            try:
                r = await client.get(f"/api/director/batches/{BATCH_ID}")
                batch = r.json()["batch"]
            except Exception as e:
                print(f"[monitor] 查询失败: {e}", flush=True)
                await asyncio.sleep(INTERVAL)
                continue

            steps = {s["step_id"]: s for s in batch["steps"]}
            video = steps.get("s4_video", {})
            export = steps.get("s7_export", {})
            status = batch.get("status")
            now = time.strftime("%H:%M:%S")

            vs = video.get("status")
            es = export.get("status")
            print(f"[{now}] batch={status} | video={vs} | export={es} "
                  f"| video_err={'Y' if video.get('error') else ''} "
                  f"| export_asset={export.get('output_asset_id') or ''}", flush=True)
            # 找成片产物（export 输出）
            if es == "completed" or status in ("completed", "partial", "failed", "cancelled"):
                # 尝试从 export 资产拿视频路径
                asset_id = export.get("output_asset_id")
                if asset_id:
                    try:
                        ar = await client.get(f"/api/director/assets/{asset_id}")
                        asset = ar.json().get("asset", {})
                        path = asset.get("file_path") or asset.get("path") or asset.get("url", "")
                        print(f"[monitor] 成片资产 id={asset_id} path={path}", flush=True)
                        has_a, vd, ad, ast = ffprobe(path)
                        print(f"[monitor] 音画校验 video_dur={vd:.3f}s audio_dur={ad if ad is not None else 'N/A'}s "
                              f"audio_start={ast if ast is not None else 'N/A'} has_audio={has_a} | diff={abs((ad or vd)-vd):.3f}s", flush=True)
                    except Exception as e:
                        print(f"[monitor] 取资产失败: {e}", flush=True)
                print("[monitor] 批次已到终态，退出。", flush=True)
                break

            if status in ("running", "created") and vs == "completed":
                print("[monitor] video 已完成，等待后续阶段...", flush=True)
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())