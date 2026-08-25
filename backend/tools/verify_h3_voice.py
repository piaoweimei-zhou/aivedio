#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
H3 人声验证（方案 X：H3 直出人声）

提交一个最小 batch：concept（角色图）→ video（minimax_h3 I2VA + tts_texts 台词），
验证口播文案被注入 H3 prompt 后，H3 音频 DiT 直接合成人声。

用法：
    python tools/verify_h3_voice.py [--host http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Any, Dict, List

import httpx

STEP_TERMINAL = {"completed", "failed", "skipped"}
BATCH_TERMINAL = {"completed", "failed", "partial", "cancelled"}

TOPIC = "一只会做饭的橘猫在厨房里做番茄炒蛋"
NARRATION = "欢迎来到我的厨房，今天教你做番茄炒蛋。先把鸡蛋打散，热锅倒油，再把蛋液倒进去翻炒，最后撒上葱花。"


def build_steps() -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []

    s_concept = "s1_concept_character1"
    steps.append({
        "step_id": s_concept,
        "stage_id": "concept",
        "name": "概念图-角色",
        "provider_id": "comfyui",
        "params": {
            "prompt": f"{TOPIC} 的主要角色设计，精致卡通风格",
            "negative_prompt": "low quality, blurry, deformed, ugly",
            "content_type": "character",
            "width": 768,
            "height": 1024,
        },
        "input_asset_ids": [],
        "input_from_steps": [],
    })

    s_video = "s2_video"
    steps.append({
        "step_id": s_video,
        "stage_id": "video",
        "name": "视频生成（H3 I2VA + 人声）",
        "provider_id": "minimax_h3",
        "params": {
            "prompt": TOPIC,
            "duration": 8,
            "aspect_ratio": "9:16",
            "resolution": "720p",
            "frame_rate": 24,
            "width": 480,
            "height": 864,
            "segment_seconds": 4,
            "segment_prompts": [TOPIC, "橘猫把番茄炒蛋装盘"],
            "tts_enabled": True,
            "tts_texts": [NARRATION, "一盘香喷喷的番茄炒蛋就做好了。"],
            "tts_mode": "voice_design",
            "tts_volume": 1.0,
        },
        "input_asset_ids": [],
        "input_from_steps": [s_concept],
    })

    return steps


async def run(host: str) -> int:
    async with httpx.AsyncClient(timeout=30) as client:
        payload = {
            "name": "H3人声验证",
            "steps": build_steps(),
        }
        r = await client.post(f"{host}/api/director/batches", json=payload)
        r.raise_for_status()
        body = r.json()
        batch_id = (body.get("batch") or {}).get("batch_id") or body.get("id") or body.get("batch_id")
        print(f"batch={batch_id} steps={len(build_steps())}")
        if not batch_id:
            print(f"创建失败: {json.dumps(body, ensure_ascii=False)[:500]}")
            return 1

        sr = await client.post(f"{host}/api/director/batches/{batch_id}/start", timeout=30)
        print(f"start status={sr.status_code}")

        deadline = time.time() + 30 * 60
        while time.time() < deadline:
            await asyncio.sleep(15)
            g = await client.get(f"{host}/api/director/batches/{batch_id}")
            data = g.json()
            status = data.get("status") or data.get("batch", {}).get("status") or ""
            steps = data.get("steps") or data.get("batch", {}).get("steps") or []
            done = sum(1 for s in steps if s.get("status") in STEP_TERMINAL)
            print(f"  status={status} steps_done={done}/{len(steps)}")
            if status in BATCH_TERMINAL:
                for s in steps:
                    print(
                        f"  [{s.get('step_id')}] {s.get('stage_id')} -> "
                        f"{s.get('status')} | err={s.get('error') or ''} | "
                        f"asset={s.get('output_asset_id') or ''}"
                    )
                ok = status == "completed"
                if ok:
                    last = [s for s in steps if s.get("status") == "completed"][-1]
                    print(f"\n成片 asset_id={last.get('output_asset_id')}")
                return 0 if ok else 1
        print("超时")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    return asyncio.run(run(args.host))


if __name__ == "__main__":
    sys.exit(main())
