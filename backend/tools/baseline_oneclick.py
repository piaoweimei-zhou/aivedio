#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键成片「真实成功率」基线测量工具（Phase 0 第 0 步）

用途
----
作为**独立 HTTP 客户端**驱动现有「一键成片」批量接口真实运行 N 次，
逐 step 采集 status / elapsed_ms / error / output_asset_id，
聚合出：每环节成功率、全链路成功率、每环节 P50/P95 延迟、错误码分布。

设计原则
--------
1. 零侵入：只调用现有 API（POST /api/director/batches + GET 轮询），不改任何业务代码。
2. 真实基线：steps **不传 max_retries**，沿用默认 0，确保测出的是「现状零重试」成功率，
   这是校准后续重试阈值的前提（见 一键成片基线校验.md）。
3. 防御：单批超时（默认 20min）强制终止并记录 timeout，绝不抛未捕获异常中断整体。

依赖：仅标准库 + httpx（后端已装）。

用法示例
--------
    python tools/baseline_oneclick.py --runs 10 --provider comfyui
    python tools/baseline_oneclick.py --runs 5 --provider volcengine --host http://127.0.0.1:8234
    python tools/baseline_oneclick.py --runs 3 --skip-dry-run --topic "一只会做饭的猫"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# ----------------------------------------------------------------------------
# 配置常量
# ----------------------------------------------------------------------------

DEFAULT_HOST = "http://localhost:8000"
DEFAULT_RUNS = 10
DEFAULT_PROVIDER = "comfyui"
DEFAULT_TIMEOUT = 20 * 60  # 单批硬超时 20 分钟
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data"

# 后端 BatchStep 终态
STEP_TERMINAL = {"completed", "failed", "skipped"}
BATCH_TERMINAL = {"completed", "failed", "partial", "cancelled"}

# 测试主题池（避免每次都相同素材，更接近真实多样性）
TOPIC_POOL = [
    "一只会做饭的橘猫在厨房里做番茄炒蛋",
    "都市白领下班后在天台看城市夜景",
    "古建筑修复师小心翼翼修补一件青花瓷",
    "海边小镇的咖啡店主清晨开门迎客",
    "山村教师给孩子们上最后一堂语文课",
    "消防员在暴雨中救援被困司机",
    "年轻情侣在樱花树下告别",
    "老匠人手工打造一把油纸伞",
    "程序员熬夜调试代码终于跑通那一刻",
    "宠物医院里小狗康复后开心摇尾巴",
]


# ----------------------------------------------------------------------------
# steps 构造（复现前端 OneClickVideoPage.tsx buildSteps 规则，模式 B）
# ----------------------------------------------------------------------------

def build_oneclick_steps(provider: str, topic: str, mode: str = "B") -> List[Dict[str, Any]]:
    """复现前端 buildSteps（OneClickVideoPage.tsx:1093-1227）的同构 steps 序列。

    模式 B（默认）：concept → (angle|pano) → video → (subtitle → hook → export)
    注意：**不传 max_retries**，保持零重试基线。
    """
    steps: List[Dict[str, Any]] = []

    # --- 素材描述（模拟前端 values.assets：1 个角色 + 1 个场景）---
    # 用 topic 派生简单的概念图文案，避免依赖外部资产
    character_prompt = f"{topic} 的主要角色设计，精致卡通风格"
    scene_prompt = f"{topic} 的故事场景，电影感氛围"
    aspect = "9:16"

    # 1) concept —— 角色
    s_concept_char = "s1_concept_character1"
    steps.append({
        "step_id": s_concept_char,
        "stage_id": "concept",
        "name": "概念图-角色",
        "provider_id": "comfyui",
        "params": {
            "prompt": character_prompt,
            "negative_prompt": "low quality, blurry, deformed, ugly",
            "content_type": "character",
            "width": 768,
            "height": 1024,
        },
        "input_asset_ids": [],
        "input_from_steps": [],
    })

    # 2) concept —— 场景
    s_concept_scene = "s2_concept_scene1"
    steps.append({
        "step_id": s_concept_scene,
        "stage_id": "concept",
        "name": "概念图-场景",
        "provider_id": "comfyui",
        "params": {
            "prompt": scene_prompt,
            "negative_prompt": "low quality, blurry, deformed, ugly",
            "content_type": "scene",
            "width": 1024,
            "height": 1024,
        },
        "input_asset_ids": [],
        "input_from_steps": [],
    })

    # 3) angle（角色多视图，条件触发）
    s_angle = "s3_angle_character1"
    steps.append({
        "step_id": s_angle,
        "stage_id": "angle",
        "name": "三视图-角色",
        "provider_id": "comfyui",
        "params": {
            "prompt": f"Multi-angle views of: {character_prompt}",
            "seed": 0,
        },
        "input_asset_ids": [],
        "input_from_steps": [s_concept_char],
    })

    # 4) video（图生视频，模式 B：ref 两张概念图）
    video_deps = [s_concept_char, s_concept_scene, s_angle]
    reference_image_files: List[str] = []  # 真实环境应由 concept 输出资产注入；此处留空由 backend 解析 input_from_steps
    seg_count = 4
    # ⭐ P2/P3 修复：每镜画面 prompt 融合该镜台词的动作语义 + 剧情连贯（含前情），
    #   让 H3 画面 DiT 与音频 DiT 对齐内容，消除"念了踩落叶、画面却没踩落叶"的声画脱节
    #   ⭐ 每镜时长按台词长度动态估算（P3：消除长台词被压进固定时长导致的语速突快）
    tts_texts = [
        f"清晨，一只小橘猫打着伞走进森林。",
        f"它踩着落叶，听着雨滴打在伞面上清脆作响。",
        f"远处忽然传来一声鹿鸣，它好奇地停下脚步。",
        f"最后，它把伞递给一只躲雨的小狐狸，转身离去。",
    ]
    segment_prompts = [
        f"{topic}，清晨的森林，小橘猫撑着伞走进林间小路，画面里有落叶和树木",
        f"{topic}，小橘猫的脚踩在落叶上，落叶被踩得沙沙作响、飘飞起来，雨滴打在伞面上",
        f"{topic}，小橘猫停下脚步，竖起耳朵，远处一只鹿的身影，森林深处传来鹿鸣",
        f"{topic}，小橘猫把伞递给一只躲在树下躲雨的小狐狸，小狐狸接过伞，猫转身离去",
    ]
    # 每镜台词长度（字符数）→ 估算该镜视频时长（秒），避免长台词被压缩导致语速突快
    _cjk_speed = 4.2  # 每秒约 4.2 个汉字（自然旁白语速）
    _min_seg = 4.0
    _max_seg = 6.5
    seg_durations = [
        max(_min_seg, min(_max_seg, len(t) / _cjk_speed + 1.2))
        for t in tts_texts
    ]
    video_params: Dict[str, Any] = {
        "prompt": topic,
        "duration": 8,
        "aspect_ratio": aspect,
        "resolution": "720p",
        "frame_rate": 24,
        "width": 720,
        "height": 1280,
        "segment_seconds": 2,
        "reference_image_files": reference_image_files,
        "segment_prompts": segment_prompts,
        # ⭐ P3：逐镜时长按台词长度动态分配（覆盖固定 segment_seconds）
        "segment_durations": seg_durations,
        # ⭐ 逐镜生成+拼接：minimax_h3 逐镜生成、拼对齐音画，消除"旁白整段拼 prompt 导致音画节奏乱/时长不一致"
        "segmented_oneclick": True,
        # ⭐ 带人声：开启 TTS，注入真实旁白台词
        "tts_enabled": True,
        "tts_texts": tts_texts,
        "tts_mode": "voice_design",
        "tts_volume": 1.0,
    }
    s_video = "s4_video"
    steps.append({
        "step_id": s_video,
        "stage_id": "video",
        "name": "视频生成",
        # 关键：前端 buildSteps 对 video 步骤硬编码 provider_id='minimax_h3'
        # （与 concept/angle 用 comfyui 不同），H3 提供方负责解析 model→workflow。
        "provider_id": "minimax_h3",
        "params": video_params,
        "input_asset_ids": [],
        "input_from_steps": video_deps,
    })

    # 5) subtitle（字幕，条件触发）
    # ⭐ P1 修复：字幕文本用真实台词（与 video 步骤 tts_texts 一致），
    #   不带显式时间戳 → subtitle_stage._build_timeline 按语速自动估算并压缩到全片时长，
    #   消除"占位文本 + 写死 0~8s 时间轴导致后半段无字幕"的问题
    s_subtitle = "s5_subtitle"
    steps.append({
        "step_id": s_subtitle,
        "stage_id": "subtitle",
        "name": "字幕叠加",
        "provider_id": "local",
        "params": {
            "subtitle_texts": [
                {"text": t} for t in tts_texts if t and t.strip()
            ],
            "keywords": ["治愈", "日常"],
            "margin_v": "0.13",
        },
        "input_asset_ids": [],
        "input_from_steps": [s_video],
    })

    # 6) hook_overlay（钩子，条件触发）
    s_hook = "s6_hook"
    steps.append({
        "step_id": s_hook,
        "stage_id": "hook_overlay",
        "name": "钩子文案叠加",
        "provider_id": "local",
        "params": {
            "hook_text": "最后 3 秒看到结局",
            "sub_text": "关注我看后续",
            "duration": 4,
            "position": "bottom",
            "margin": None,
        },
        "input_asset_ids": [],
        "input_from_steps": [s_subtitle],
    })

    # 7) export（平台导出，条件触发）
    s_export = "s7_export"
    steps.append({
        "step_id": s_export,
        "stage_id": "export",
        "name": "导出成片 抖音规格 (1080x1920)",
        "provider_id": "local",
        "params": {
            "resolution": "1080x1920",
            "format": "mp4",
            "codec": "libx264",
            "bitrate": "8M",
            "name": f"成片_抖音_1080x1920_{topic[:8]}",
        },
        "input_asset_ids": [],
        "input_from_steps": [s_hook],
    })

    return steps


# ----------------------------------------------------------------------------
# 单次运行驱动
# ----------------------------------------------------------------------------

def classify_error(err_text: Optional[str]) -> str:
    """将 step.error 文本归类，供 Pareto 分析。"""
    if not err_text:
        return "none"
    t = err_text.lower()
    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "429" in t or "rate" in t or "限流" in t:
        return "rate_limit"
    if "500" in t or "502" in t or "503" in t or "5xx" in t:
        return "provider_5xx"
    if "400" in t or "422" in t or "参数" in t or "param" in t:
        return "param_error"
    if "ffmpeg" in t or "ffprobe" in t:
        return "ffmpeg_error"
    if "not found" in t or "404" in t or "missing" in t:
        return "asset_missing"
    return "other"


async def run_once(
    client: httpx.AsyncClient,
    host: str,
    steps: List[Dict[str, Any]],
    timeout: int,
    skip_dry_run: bool,
) -> Dict[str, Any]:
    """创建 + 启动 + 轮询单次一键成片，返回逐 step 采集结果。"""
    run_record: Dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": None,
        "batch_status": "unknown",
        "steps": [],
        "error": None,
        "timeout": False,
    }
    try:
        # 1) 创建 batch
        create_payload = {"project_id": "baseline", "name": "基线测量", "steps": steps}
        r = await client.post(f"{host}/api/director/batches", json=create_payload, timeout=30)
        if r.status_code >= 400:
            run_record["error"] = f"create_http_{r.status_code}: {r.text[:300]}"
            run_record["batch_status"] = "create_failed"
            return run_record
        body = r.json()
        # 后端返回结构: {"success": True, "batch": {"batch_id": ...}}
        batch_id = (body.get("batch") or {}).get("batch_id") or body.get("id") or body.get("batch_id")
        if not batch_id:
            run_record["error"] = f"create_no_id: {r.text[:300]}"
            run_record["batch_status"] = "create_failed"
            return run_record
        run_record["batch_id"] = batch_id

        # 2) 可选 dry-run 预检
        if not skip_dry_run:
            try:
                dr = await client.post(f"{host}/api/director/batches/{batch_id}/dry-run", timeout=30)
                run_record["dry_run_status"] = dr.status_code
            except Exception:
                run_record["dry_run_status"] = "error"

        # 3) 启动
        start_r = await client.post(f"{host}/api/director/batches/{batch_id}/start", timeout=30)
        if start_r.status_code >= 400:
            run_record["error"] = f"start_http_{start_r.status_code}: {start_r.text[:300]}"
            run_record["batch_status"] = "start_failed"
            return run_record

        # 4) 轮询至终态
        deadline = time.monotonic() + timeout
        last_detail = None
        while time.monotonic() < deadline:
            await asyncio.sleep(15)
            try:
                g = await client.get(f"{host}/api/director/batches/{batch_id}", timeout=30)
                if g.status_code >= 400:
                    continue
                detail = g.json()
                last_detail = detail
                bstatus = (detail.get("status") or detail.get("batch", {}).get("status") or "").lower()
                if bstatus in BATCH_TERMINAL:
                    run_record["batch_status"] = bstatus
                    break
            except Exception:
                continue
        else:
            run_record["timeout"] = True
            run_record["batch_status"] = "timeout"

        # 5) 采集 step 明细
        if last_detail is None:
            try:
                last_detail = (await client.get(f"{host}/api/director/batches/{batch_id}", timeout=30)).json()
            except Exception:
                last_detail = {}
        step_list = (last_detail.get("steps") or last_detail.get("batch", {}).get("steps") or [])
        for s in step_list:
            run_record["steps"].append({
                "step_id": s.get("step_id"),
                "stage_id": s.get("stage_id"),
                "status": s.get("status"),
                "elapsed_ms": s.get("elapsed_ms", 0),
                "error": s.get("error"),
                "output_asset_id": s.get("output_asset_id"),
                "error_class": classify_error(s.get("error")),
            })
    except Exception as e:  # 绝不抛未捕获异常
        run_record["error"] = f"exception: {type(e).__name__}: {str(e)[:300]}"
        run_record["batch_status"] = "exception"
    return run_record


# ----------------------------------------------------------------------------
# 聚合统计
# ----------------------------------------------------------------------------

def _pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    k = (len(values) - 1) * p
    f = int(k)
    c = min(f + 1, len(values) - 1)
    return values[f] + (values[c] - values[f]) * (k - f)


def aggregate(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合多 runs：每环节成功率 / 全链路成功率 / P50-P95 延迟 / 错误分布。"""
    # 收集所有出现过的 stage_id（按首次出现顺序）
    stage_order: List[str] = []
    for run in runs:
        for s in run["steps"]:
            sid = s["stage_id"]
            if sid and sid not in stage_order:
                stage_order.append(sid)

    per_stage: Dict[str, Any] = {}
    for sid in stage_order:
        statuses: List[str] = []
        latencies: List[float] = []
        err_classes: Dict[str, int] = {}
        for run in runs:
            for s in run["steps"]:
                if s["stage_id"] != sid:
                    continue
                statuses.append(s["status"] or "unknown")
                if s["elapsed_ms"]:
                    latencies.append(s["elapsed_ms"] / 1000.0)  # 转秒
                ec = s.get("error_class", "none")
                err_classes[ec] = err_classes.get(ec, 0) + 1
        total = len(statuses)
        completed = sum(1 for x in statuses if x == "completed")
        lat_sorted = sorted(latencies)
        per_stage[sid] = {
            "total": total,
            "completed": completed,
            "failed": sum(1 for x in statuses if x == "failed"),
            "skipped": sum(1 for x in statuses if x == "skipped"),
            "success_rate": round(completed / total, 4) if total else 0.0,
            "latency_p50_s": round(_pct(lat_sorted, 0.5), 2),
            "latency_p95_s": round(_pct(lat_sorted, 0.95), 2),
            "latency_max_s": round(max(latencies), 2) if latencies else 0.0,
            "error_classes": err_classes,
        }

    # 全链路成功率：该 run 的所有 step 都是 completed 才算全链路成功
    full_ok = 0
    run_count = len(runs)
    for run in runs:
        if all(s["status"] == "completed" for s in run["steps"]):
            full_ok += 1

    # 错误码全局分布（Pareto）
    global_err: Dict[str, int] = {}
    for run in runs:
        for s in run["steps"]:
            ec = s.get("error_class", "none")
            if ec != "none":
                global_err[ec] = global_err.get(ec, 0) + 1

    # 最大失效率环节
    worst = max(per_stage.items(), key=lambda kv: kv[1]["success_rate"]) if per_stage else ("", {"success_rate": 1})
    worst_stage = min(per_stage.items(), key=lambda kv: kv[1]["success_rate"]) if per_stage else ("", {"success_rate": 1})

    return {
        "run_count": run_count,
        "full_chain_success": full_ok,
        "full_chain_success_rate": round(full_ok / run_count, 4) if run_count else 0.0,
        "per_stage": per_stage,
        "global_error_distribution": dict(sorted(global_err.items(), key=lambda kv: -kv[1])),
        "worst_stage": {"stage_id": worst_stage[0], "success_rate": worst_stage[1]["success_rate"]},
        "best_stage": {"stage_id": worst[0], "success_rate": worst[1]["success_rate"]},
    }


# ----------------------------------------------------------------------------
# 从磁盘已有 batch 聚合（不重跑，用于「复用已完成批次」产出基线）
# ----------------------------------------------------------------------------

def _load_run_from_batch_json(path: Path, include_running: bool = False) -> Optional[Dict[str, Any]]:
    """读取单个 batch JSON，转换为与 run_once 同构的 run_record。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    bstatus = (d.get("status") or "").lower()
    if bstatus == "running" and not include_running:
        return None
    steps = d.get("steps", [])
    rec = {
        "created_at": datetime.fromtimestamp(
            d.get("created_at") or d.get("updated_at") or 0, tz=timezone.utc
        ).isoformat() if (d.get("created_at") or d.get("updated_at")) else None,
        "batch_id": d.get("batch_id"),
        "batch_status": bstatus,
        "steps": [],
        "error": d.get("error"),
        "timeout": False,
    }
    for s in steps:
        rec["steps"].append({
            "step_id": s.get("step_id"),
            "stage_id": s.get("stage_id"),
            "status": s.get("status"),
            "elapsed_ms": s.get("elapsed_ms", 0),
            "error": s.get("error"),
            "output_asset_id": s.get("output_asset_id"),
            "error_class": classify_error(s.get("error")),
        })
    return rec


def _norm(s: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFC", (s or "").strip())


def collect_disk_runs(batch_dir: Path, batch_ids: Optional[List[str]],
                      name_filter: str = "基线测量",
                      video_provider: Optional[str] = None,
                      only_completed: bool = False) -> List[Dict[str, Any]]:
    """从磁盘扫描 batch 文件，构造 run_record 列表。

    - batch_ids 非空：仅加载这些 id（可带/不带 batch_ 前缀与 .json 后缀）
    - 否则：扫描 batch_dir 下所有 batch_*.json，按 name_filter / video_provider / only_completed 过滤
    name 比较做 NFC 归一化 + strip，规避 CLI 传参的 unicode normalization 差异。
    """
    runs: List[Dict[str, Any]] = []
    if batch_ids:
        for bid in batch_ids:
            bid = bid.strip()
            if not bid.endswith(".json"):
                bid = (bid if bid.startswith("batch_") else f"batch_{bid}") + ".json"
            p = batch_dir / bid
            if p.exists():
                r = _load_run_from_batch_json(p)
                if r:
                    runs.append(r)
        return runs

    for p in sorted(batch_dir.glob("batch_*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if name_filter and _norm(d.get("name")) != _norm(name_filter):
            continue
        if video_provider:
            vids = [s.get("provider_id") for s in d.get("steps", []) if s.get("stage_id") == "video"]
            if video_provider not in vids:
                continue
        if only_completed:
            statuses = [s.get("status") for s in d.get("steps", [])]
            if not all(x == "completed" for x in statuses):
                continue
        r = _load_run_from_batch_json(p)
        if r:
            runs.append(r)
    return runs


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------

def print_summary(agg: Dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("一键成片基线测量小结")
    print("=" * 60)
    print(f"运行次数: {agg['run_count']}")
    print(f"全链路成功率: {agg['full_chain_success_rate'] * 100:.1f}% "
          f"({agg['full_chain_success']}/{agg['run_count']})")
    print(f"最大失效率环节: {agg['worst_stage']['stage_id']} "
          f"({agg['worst_stage']['success_rate'] * 100:.1f}%)")
    print("-" * 60)
    print(f"{'环节':<14}{'成功率':>9}{'P50(s)':>9}{'P95(s)':>9}{'失败数':>8}")
    for sid, d in agg["per_stage"].items():
        print(f"{sid:<14}{d['success_rate']*100:>8.1f}%{d['latency_p50_s']:>9}{d['latency_p95_s']:>9}{d['failed']:>8}")
    print("-" * 60)
    print("全局错误分布:", agg["global_error_distribution"])
    print("=" * 60)


async def main_async(args: argparse.Namespace) -> None:
    host = args.host.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- 模式 A：从磁盘已有 batch 聚合（不重跑）---
    if args.from_disk or args.batch_id:
        # batch 文件与报告同处 data/ 目录：output_dir 默认 = backend/data，batches 在 data/batches
        batch_dir = output_dir / "batches"
        if not batch_dir.exists():
            batch_dir = output_dir.parent / "batches"
        if args.batch_id:
            runs = collect_disk_runs(batch_dir, args.batch_id, only_completed=args.only_completed)
        else:
            runs = collect_disk_runs(
                batch_dir,
                None,
                name_filter=args.disk_name_filter,
                video_provider=args.disk_video_provider,
                only_completed=args.only_completed,
            )
        print(f"[from-disk] 扫描到 {len(runs)} 个批次用于聚合")
        if not runs:
            print("无可用批次，退出")
            return
    # --- 模式 B：实时驱动 N 次 ---
    else:
        steps_template = build_oneclick_steps(args.provider, args.topic or TOPIC_POOL[0])
        runs = []
        timeout_per_batch = args.timeout
        async with httpx.AsyncClient() as client:
            for i in range(args.runs):
                topic = args.topic or TOPIC_POOL[i % len(TOPIC_POOL)]
                steps = build_oneclick_steps(args.provider, topic)
                print(f"[run {i+1}/{args.runs}] topic={topic[:20]!r} provider={args.provider} steps={len(steps)}")
                rec = await run_once(client, host, steps, timeout_per_batch, args.skip_dry_run)
                runs.append(rec)
                n_steps = len(rec["steps"])
                ok = sum(1 for s in rec["steps"] if s["status"] == "completed")
                print(f"    -> batch={rec['batch_id']} status={rec['batch_status']} "
                      f"steps_ok={ok}/{n_steps} timeout={rec['timeout']}")

    agg = aggregate(runs)
    print_summary(agg)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"oneclick_baseline_{ts}.json"
    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "host": host,
            "provider": args.provider if not (args.from_disk or args.batch_id) else (
                args.disk_video_provider or "mixed"),
            "runs": args.runs if not (args.from_disk or args.batch_id) else len(runs),
            "timeout_per_batch_s": args.timeout,
            "skip_dry_run": args.skip_dry_run,
            "source": "live" if not (args.from_disk or args.batch_id) else (
                "disk:explicit_ids" if args.batch_id else "disk:scan"),
            "note": "零重试基线（steps 不传 max_retries），用于校准 Phase 0 重试阈值/指标口径/门禁阈值",
        },
        "aggregate": agg,
        "runs": runs,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已落盘: {out_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="一键成片真实成功率基线测量工具")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"后端地址 (默认 {DEFAULT_HOST})")
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS, help=f"运行次数 (默认 {DEFAULT_RUNS})")
    p.add_argument("--provider", default=DEFAULT_PROVIDER,
                   help="video 步骤使用供应商 comfyui/volcengine/jimeng/... (默认 comfyui)")
    p.add_argument("--provider-key", default=None, help="供应商 key（若后端需显式传，留空走默认配置）")
    p.add_argument("--topic", default=None, help="固定测试主题；留空则用内置主题池轮换")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="单批硬超时秒 (默认 1200)")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="报告输出目录")
    p.add_argument("--skip-dry-run", action="store_true", help="跳过 dry-run 预检")
    # 从磁盘聚合（不重跑）
    p.add_argument("--from-disk", action="store_true",
                   help="从磁盘已有 batch JSON 聚合（不实时重跑）。配合 --disk-video-provider / --only-completed 过滤")
    p.add_argument("--batch-id", nargs="*", default=None,
                   help="显式指定要聚合的 batch id 列表（可带/不带 batch_ 前缀与 .json 后缀）")
    p.add_argument("--disk-name-filter", default="基线测量",
                   help="--from-disk 扫描时按 batch.name 过滤 (默认 '基线测量')")
    p.add_argument("--disk-video-provider", default="minimax_h3",
                   help="--from-disk 扫描时仅纳入 video 步骤用该 provider 的批次 (默认 minimax_h3)")
    p.add_argument("--only-completed", action="store_true",
                   help="仅纳入全 step completed 的批次（排除 failed/running/pending）")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main_async(parse_args()))
