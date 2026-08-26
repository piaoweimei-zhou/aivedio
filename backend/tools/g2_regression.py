#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G2 一键成片端到端回归（P2）

两种模式：
    smoke（默认）—— 契约冒烟：TestClient 起 app，验证回归构造的 steps 可被
        后端接受并生成 DAG（concept→angle→video→subtitle→hook→export）。
        零外部依赖，每次执行必须通过（可进 CI）。
    full —— 真实一键成片回归：驱动真实批量接口完整执行，聚合成功率/延迟。
        前置条件：后端已起(127.0.0.1:8000) + ComfyUI(127.0.0.1:8188) + provider key。
        复用 tools/baseline_oneclick.py 的测量能力。

用法：
    python tools/g2_regression.py                 # 契约冒烟
    python tools/g2_regression.py --full --runs 3 # 真实回归 3 轮
"""

from __future__ import annotations

import argparse
import os
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# director 真实读取的 provider key 集（G2 preconditions 精确化：
# 只认这组后端实际消费的密钥，避免"任意 KEY 环境变量"误判已配置）
_REAL_PROVIDER_KEYS = (
    "ARK_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "MODELSCOPE_API_KEY",
    "RUNNINGHUB_API_KEY",
    "COMFYUI_API_KEY",
)


def _check_preconditions() -> list:
    """真实回归前置条件：后端 / ComfyUI / 真实 provider key"""
    import urllib.request

    from dotenv import load_dotenv

    # 加载 backend/.env（若存在），保证 provider key 进入环境
    _env_file = os.path.join(BACKEND, ".env")
    if os.path.exists(_env_file):
        load_dotenv(_env_file)

    missing = []
    for name, url in [
        ("后端 127.0.0.1:8000", "http://127.0.0.1:8000/health"),
        ("ComfyUI 127.0.0.1:8188", "http://127.0.0.1:8188/system_stats"),
    ]:
        try:
            urllib.request.urlopen(url, timeout=3).close()
        except Exception:
            missing.append(name)
    # 精确检查：后端真实读取的 provider key 至少存在一个（默认 provider=comfyui
    # 本地生成不需外部 key，但文案/字幕/LLM 步骤仍依赖其一）
    keys = [k for k in _REAL_PROVIDER_KEYS if os.environ.get(k, "").strip()]
    if not keys:
        missing.append(
            "provider key（backend/.env 未见任一真实 provider key: "
            + "/".join(_REAL_PROVIDER_KEYS)
            + "）"
        )
    return missing


def smoke():
    """契约冒烟：steps 构造 → 后端接受 → DAG 生成"""
    import logging

    # Windows 下 RotatingFileHandler 滚动偶发文件锁，吞掉 logging 内部异常避免污染 stderr
    logging.raiseExceptions = False
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    from fastapi.testclient import TestClient
    from tools.baseline_oneclick import build_oneclick_steps

    import main

    steps = build_oneclick_steps(provider="comfyui", topic="一只会做饭的橘猫在厨房里做番茄炒蛋")
    assert len(steps) >= 6, f"steps 应含全链路(≥6), 实际 {len(steps)}"
    stage_ids = [s["stage_id"] for s in steps]
    print(f"[smoke] 构造 steps: {len(steps)} 个 → {stage_ids}")

    with TestClient(main.app) as client:
        # 创建 batch
        r = client.post(
            "/api/director/batches",
            json={
                "name": f"G2契约冒烟-{os.getpid()}",
                "steps": steps,
                "stop_on_failure": True,
            },
        )
        assert r.status_code == 200, f"创建 batch 失败: {r.status_code} {r.text[:200]}"
        batch_id = r.json()["batch"]["batch_id"]
        print(f"[smoke] 创建 batch: {batch_id}")

        # DAG 生成
        r2 = client.get(f"/api/director/batches/{batch_id}/dag")
        assert r2.status_code == 200, f"DAG 获取失败: {r2.status_code}"
        dag = r2.json().get("dag", {})
        assert dag, "DAG 为空"
        print(f"[smoke] DAG 生成: {len(dag.get('nodes', []))} 节点")

        # 清理
        try:
            client.delete(f"/api/director/batches/{batch_id}")
            print("[smoke] 已清理测试 batch")
        except Exception:
            pass
    print("[smoke] PASS：steps 契约 + DAG 生成均通过")
    return 0


def full(runs: int, host: str):
    """真实一键成片回归 + G2 门禁判定。

    返回码（门禁语义）：
        0 = 全链路 100% 通过（full_chain_success=True）
        1 = 回归跑完但全链路未 100%（门禁拦截）
        2 = 前置条件缺失（后端/ComfyUI/provider key）
    """
    missing = _check_preconditions()
    if missing:
        print("G2 真实回归前置条件缺失：")
        for m in missing:
            print(f"  - {m}")
        print("请先启动后端 + ComfyUI 并配置 provider key。")
        return 2
    sys.path.insert(0, BACKEND)
    from tools.baseline_oneclick import main_async, parse_args  # noqa: F401
    import asyncio

    sys.argv = ["g2_regression", "--runs", str(runs), "--host", host]
    args = parse_args()
    agg = asyncio.run(main_async(args))
    # G2 门禁判定：一键成片全链路必须 100% 通过，否则拦截发布
    if not agg or not agg.get("full_chain_success"):
        print(
            f"[G2] FAIL: 一键成片全链路未 100% 通过 "
            f"(runs={agg.get('run_count') if agg else 0}, "
            f"success_rate={agg.get('full_chain_success_rate') if agg else 0.0})"
        )
        return 1
    print(
        f"[G2] PASS: 一键成片全链路 100% 通过 "
        f"(runs={agg.get('run_count')}, success_rate={agg.get('full_chain_success_rate')})"
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description="G2 一键成片回归")
    ap.add_argument("--full", action="store_true", help="真实回归（需环境就绪）")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--host", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    if args.full:
        return full(args.runs, args.host)
    return smoke()


if __name__ == "__main__":
    sys.exit(main())
