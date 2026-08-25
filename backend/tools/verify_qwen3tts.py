#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Qwen3TTS 节点验证（pad_token_id 补丁后）

提交一个最小 batch：tts（voice_design 音色设计），
验证 ComfyUI Qwen3TTS 节点在补丁后能真正合成人声音频。

用法：
    python tools/verify_qwen3tts.py [--host http://127.0.0.1:8000]
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

TEXT = "欢迎来到我的厨房，今天教你做番茄炒蛋。先把鸡蛋打散，热锅倒油，再把蛋液倒进去翻炒，最后撒上葱花。"
VOICE_DESC = "成年女性，温柔亲切，语速适中，普通话标准"


def build_steps(text: str) -> List[Dict[str, Any]]:
    return [{
        "step_id": "s1_tts",
        "stage_id": "tts",
        "name": "TTS 音色设计（Qwen3TTS）",
        "provider_id": "comfyui",
        "params": {
            "text": text,
            "mode": "voice_design",
            "voice_description": VOICE_DESC,
            "language": "Auto",
        },
        "input_asset_ids": [],
        "input_from_steps": [],
    }]


async def run(host: str, text: str) -> int:
    steps = build_steps(text)
    async with httpx.AsyncClient(timeout=30) as client:
        payload = {
            "name": "Qwen3TTS补丁验证",
            "steps": steps,
        }
        r = await client.post(f"{host}/api/director/batches", json=payload)
        r.raise_for_status()
        body = r.json()
        batch_id = (body.get("batch") or {}).get("batch_id") or body.get("id") or body.get("batch_id")
        print(f"batch={batch_id} steps={len(steps)}")
        if not batch_id:
            print(f"创建失败: {json.dumps(body, ensure_ascii=False)[:500]}")
            return 1

        sr = await client.post(f"{host}/api/director/batches/{batch_id}/start", timeout=30)
        print(f"start status={sr.status_code}")

        deadline = time.time() + 20 * 60
        while time.time() < deadline:
            await asyncio.sleep(10)
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
                    print(f"\n音频 asset_id={last.get('output_asset_id')}")
                return 0 if ok else 1
        print("超时")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    ap.add_argument("--text", default=TEXT, help="要合成的文本（默认内置文案）")
    args = ap.parse_args()
    return asyncio.run(run(args.host, args.text))


if __name__ == "__main__":
    sys.exit(main())
