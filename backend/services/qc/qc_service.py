"""本地视频质检服务（质量 / 平台规则 / 版权 三合一，100 分制）。

设计原则（与一期「零侵入业务」一致）：
- 纯工具模块，不修改任何既有 DAG / 前端 / 业务服务。
- cv2 客观质检永远可用（不依赖大模型）。
- Qwen2.5-VL 语义质检走本地 llama-server（OpenAI 兼容 /v1/chat/completions），
  按需启停（方案 2）：调用前拉起，质检完退出，避免常驻占满 16G 显存与 ComfyUI 流水线冲突。
- 结果只产出可复核 JSON 报告，默认「报告不拦截」——由人决定是否发布。

依赖：
- cv2（客观质检）
- httpx（调本地 llama-server）
- 本地 llama.cpp b9113（带 mtmd 多模态后端）：D:/llama-b9113-bin-win-cuda-13.1-x64/llama-server.exe
- 模型：ggml-org/Qwen2.5-VL-7B-Instruct-GGUF 的 Q8_0 主模型 + mmproj
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import cv2
import httpx

# ----------------------------------------------------------------------------
# 配置（集中放，便于将来改）
# ----------------------------------------------------------------------------

LLAMA_DIR = r"D:\llama-b9113-bin-win-cuda-13.1-x64"
LLAMA_SERVER = os.path.join(LLAMA_DIR, "llama-server.exe")
QC_PORT = 8082
QC_HOST = "127.0.0.1"

# 本地多模态模型自动探测：优先 Qwen3-VL-8B（Q4_K_M，已下），回退 Qwen2.5-VL-7B
_MODEL_CANDIDATES = [
    # (主模型路径, mmproj路径, 展示名)
    (
        r"D:\models\qwen3-vl-8b\Qwen3VL-8B-Instruct-Q4_K_M.gguf",
        r"D:\models\qwen3-vl-8b\mmproj-Qwen3VL-8B-Instruct-F16.gguf",
        "Qwen3-VL-8B-Instruct-Q4_K_M (local llama.cpp)",
    ),
    (
        r"D:\models\qwen3-vl-8b\Qwen3VL-8B-Instruct-Q8_0.gguf",
        r"D:\models\qwen3-vl-8b\mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf",
        "Qwen3-VL-8B-Instruct-Q8_0 (local llama.cpp)",
    ),
    (
        r"D:\models\qwen2.5-vl\Qwen2.5-VL-7B-Instruct-Q8_0.gguf",
        r"D:\models\qwen2.5-vl\mmproj-Qwen2.5-VL-7B-Instruct-Q8_0.gguf",
        "Qwen2.5-VL-7B-Instruct-Q8_0 (local llama.cpp)",
    ),
]


def _resolve_model() -> tuple[str, str, str]:
    """返回 (主模型路径, mmproj路径, 展示名)，优先已下载的本地模型。"""
    for main, mmproj, name in _MODEL_CANDIDATES:
        if os.path.exists(main) and os.path.exists(mmproj):
            return main, mmproj, name
    # 都没下则默认指向 8B Q4_KM（报错信息更明确）
    return _MODEL_CANDIDATES[0][0], _MODEL_CANDIDATES[0][1], _MODEL_CANDIDATES[0][2]


MAIN_MODEL, MMPROJ, MODEL_DISPLAY = _resolve_model()

# 100 分制权重（画质/配音/构图客观走 cv2+ffmpeg 客观质检，语义维度走 AI 语义打分；
# 版权不占权重，作风险提示+一票否决）
# 维度总数 8：quality/consistency/lip_sync/composition/composition_cv/rhythm/voice/compliance
WEIGHTS: Dict[str, int] = {
    "quality": 18,  # 画质清晰度（cv2 客观：黑屏/模糊/分辨率/时长）
    "consistency": 16,  # 人物一致性（跨镜头，语义）
    "lip_sync": 12,  # 口型同步（语义）
    "composition": 14,  # 构图与美学（语义）
    "composition_cv": 8,  # 构图客观分（cv2：三分法对齐/边缘清晰度/主体亮度，客观不依赖模型）
    "rhythm": 10,  # 节奏与完播（语义）
    "voice": 12,  # 配音质量（ffmpeg 客观：响度/静音占比/采样率/削波；不依赖模型）
    "compliance": 10,  # 平台合规（低俗/政治/医疗/标题党，语义+本地关键词）
}

# 平台规则：高风险语义关键词分级（中文语境，兜底用；AI 语义理解才是主）。
# 等级：
#   hard   —— 法律/平台严重违规，一票否决拦截（政治/色情/暴力/血腥/赌博/毒品）
#   medium —— 中度违规，不直接拦截，但合规维度扣分 + 风险提示（医疗夸大/标题党/诱导加私信等）
#   soft   —— 轻度风险，仅提示，不扣分不拦截
COMPLIANCE_RULES: Dict[str, str] = {
    # hard
    "政治": "hard",
    "领导人": "hard",
    "色情": "hard",
    "低俗": "hard",
    "暴力": "hard",
    "血腥": "hard",
    "赌博": "hard",
    "毒品": "hard",
    # medium
    "医疗": "medium",
    "功效": "medium",
    "治愈": "medium",
    "减肥": "medium",
    "丰胸": "medium",
    "贷款": "medium",
    "投资": "medium",
    "保本": "medium",
    "封建迷信": "medium",
    "算命": "medium",
    "风水": "medium",
    "二维码": "medium",
    "微信号": "medium",
    "加微信": "medium",
    "私聊": "medium",
    "私信": "medium",
    "加私信": "medium",
    "诱导": "medium",
    "关注": "medium",
    "加好友": "medium",
    "联系方式": "medium",
    # soft（仅提示）
    "标题党": "soft",
}

# 常见高版权风险 IP（提示用，非穷举）。版权为法律红线，统一硬一票否决。
# 注意：仅收录【高确信、不易误伤】的专有 IP/品牌；泛词（如"苹果"水果/公司、
# "王者荣耀"/"原神"等游戏名非必然侵权）已移除，避免对正常内容误杀。
# 匹配用【双向子串】——品牌词是命中短语子串，或短语是品牌词子串。
COPYRIGHT_RISK_BRANDS = [
    "迪士尼",
    "漫威",
    "皮克斯",
    "宝可梦",
    "Pokémon",
    "米老鼠",
    "Hello Kitty",
    "哆啦A梦",
    "名侦探柯南",
    "麦当劳",
    "肯德基",
    "Nike",
    "Adidas",
    "喜羊羊",
    "熊出没",
    "小猪佩奇",
]

# 合规扣分：命中 medium 时合规维度额外扣的分（在模型合规分基础上叠加惩罚）
COMPLIANCE_MEDIUM_PENALTY = 20

SYSTEM_PROMPT = (
    "你是一个严格的短视频质量与合规审核员。用户会给你一段视频的关键帧序列"
    "以及视频的文案/字幕。请按以下维度逐一评分（0-100）并给出简短理由：\n"
    "1. composition（构图与美学）：画面构图、景别、信息密度、美观度。\n"
    "2. consistency（人物一致性）：若存在人物，跨镜头脸/服装/体型是否同一人。\n"
    "3. lip_sync（口型同步）：若存在人声口播，嘴型与语音是否对齐；无口播则给 80 分。\n"
    "4. rhythm（节奏与完播）：前 3 秒是否有钩子、节奏是否拖沓、信息密度是否合适。\n"
    "5. compliance（平台规则）：是否含低俗、暴力、政治敏感、医疗夸大、标题党、诱导加私信等违规内容；"
    "命中则填入 compliance_hits。\n"
    "6. copyright（版权风险检测）：画面是否出现明显受版权保护的 IP 形象/品牌 logo/水印/影视角色；"
    "命中则填入 copyright_hits（此项不评分，仅检测）。\n"
    "必须只输出一个 JSON 对象，格式：\n"
    '{"composition":<int>,"consistency":<int>,"lip_sync":<int>,"rhythm":<int>,"compliance":<int>,'
    '"compliance_hits":[<str>],"copyright_hits":[<str>],"summary":"<str>"}\n'
    "不要输出任何其他文字。"
)


# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------


@dataclass
class QcResult:
    total_score: float = 0.0
    passed: bool = False
    dimensions: Dict[str, Any] = field(default_factory=dict)
    technical: Dict[str, Any] = field(default_factory=dict)
    compliance_hits: List[str] = field(default_factory=list)
    copyright_hits: List[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reasons: List[str] = field(default_factory=list)
    summary: str = ""
    model_used: str = ""
    notes: List[str] = field(default_factory=list)
    # 合规命中分级明细：[{hit, severity}]，severity ∈ hard|medium|soft
    compliance_detail: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        dims = self.dimensions or {}
        dimensions_list = [
            {"name": k, "score": int(round(v)) if isinstance(v, (int, float)) else v}
            for k, v in dims.items()
        ]
        return {
            "total_score": round(self.total_score, 1),
            "passed": self.passed,
            "weights": WEIGHTS,
            "dimensions": dims,
            "dimensions_list": dimensions_list,
            "technical": self.technical,
            "compliance_hits": self.compliance_hits,
            "compliance_detail": self.compliance_detail,
            "copyright_hits": self.copyright_hits,
            "blocked": self.blocked,
            "blocked_reasons": self.blocked_reasons,
            "summary": self.summary,
            "model_used": self.model_used,
            "notes": self.notes,
        }


# ----------------------------------------------------------------------------
# 1. cv2 客观质检（不依赖模型）
# ----------------------------------------------------------------------------


def _probe_video(path: str) -> Dict[str, Any]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"readable": False}
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = (frame_count / fps) if fps > 0 else 0

    # 抽若干帧算模糊度（方差）与黑屏比例
    blur_vals: List[float] = []
    black_frames = 0
    sampled = 0
    if frame_count > 0:
        step = max(1, frame_count // 10)
        idx = 0
        while idx < frame_count:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            blur = cv2.Laplacian(gray, cv2.CV_64F).var()
            blur_vals.append(blur)
            mean_b = float(gray.mean())
            if mean_b < 12:  # 接近全黑
                black_frames += 1
            sampled += 1
            idx += step
    cap.release()

    avg_blur = sum(blur_vals) / len(blur_vals) if blur_vals else 0.0
    black_ratio = (black_frames / sampled) if sampled else 0.0

    # 视频码率（用于缩放劣化/压缩劣化检测）
    bitrate = 0
    try:
        import subprocess

        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        _info = json.loads(out.stdout or "{}")
        _streams = _info.get("streams") or []
        if _streams:
            bitrate = int(_streams[0].get("bit_rate") or 0)
    except Exception:
        pass

    # 每像素码率密度 bpp = 视频码率(bps) / (宽*高*帧率)
    bpp = 0.0
    if bitrate > 0 and width > 0 and height > 0 and fps > 0:
        bpp = bitrate / (width * height * fps)
    # 缩放劣化 / 压缩劣化提示
    quality_notes: List[str] = []
    if bitrate > 0:
        if bpp < 0.05:
            quality_notes.append(f"码率密度过低({bpp:.3f}bpp，压缩劣化风险)")
        elif bpp < 0.10:
            quality_notes.append(f"码率密度偏低({bpp:.3f}bpp)")
    return {
        "readable": True,
        "fps": round(fps, 2),
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_s": round(duration, 1),
        "avg_laplacian_blur": round(avg_blur, 2),
        "black_frame_ratio": round(black_ratio, 3),
        "sampled_frames": sampled,
        "video_bitrate": bitrate,
        "bpp": round(bpp, 4),
        "quality_notes": quality_notes,
    }


def _audio_probe(path: str) -> Dict[str, Any]:
    """配音客观质检：ffprobe 探测音轨存在 + ffmpeg 抽 PCM 算客观指标（响度/静音占比/采样率/削波）。
    返回 {has_audio, voice(0-100 客观配音分), audio_metrics{...}}。不依赖任何模型。
    """
    import subprocess

    try:
        # 1) 探测音轨（含原始采样率与码率）
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type,sample_rate,channels,bit_rate",
                "-of",
                "json",
                path,
            ],  # noqa: E501
            capture_output=True,
            text=True,
            timeout=30,
        )
        info = json.loads(probe.stdout or "{}")
        streams = info.get("streams", [])
        if not streams:
            return {"has_audio": False, "voice": 0, "audio_metrics": {"note": "无音轨"}}
        orig_sample_rate = 0
        audio_bitrate = 0
        try:
            orig_sample_rate = int(streams[0].get("sample_rate") or 0)
        except Exception:
            pass
        try:
            audio_bitrate = int(streams[0].get("bit_rate") or 0)
        except Exception:
            pass
    except Exception as e:
        return {"has_audio": None, "voice": 0, "audio_metrics": {"note": f"ffprobe 不可用: {e}"}}

    # 2) 抽单声道 16bit PCM 算客观指标
    import tempfile
    import struct

    try:
        with tempfile.NamedTemporaryFile(suffix=".pcm", delete=False) as tf:
            pcm_path = tf.name
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-f",
                "s16le",
                pcm_path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        with open(pcm_path, "rb") as f:
            raw = f.read()
        os.remove(pcm_path)
        n = len(raw) // 2
        if n == 0:
            return {"has_audio": True, "voice": 0, "audio_metrics": {"note": "音轨为空"}}
        samples = struct.unpack("<%dh" % n, raw)
        # RMS 响度（归一化到 0-1）
        rms = (sum(s * s for s in samples) / n) ** 0.5 / 32768.0
        # 静音段占比（|s| < 阈值 500/32768）
        silent = sum(1 for s in samples if abs(s) < 500)
        silent_ratio = silent / n
        # 削波占比（接近满幅）
        clip = sum(1 for s in samples if abs(s) > 32000)
        clip_ratio = clip / n
        sr = 16000
        # 客观配音分：响度适中(0.02~0.25 最佳)给高分；静音过多/削波扣分
        score = 100
        if rms < 0.01:
            score -= 45  # 几乎听不到
        elif rms < 0.02:
            score -= 20
        elif rms > 0.3:
            score -= 15  # 可能过载
        if silent_ratio > 0.5:
            score -= 30  # 一半以上是静音，配音空洞
        elif silent_ratio > 0.3:
            score -= 15
        if clip_ratio > 0.02:
            score -= 20  # 明显削波失真
        # 采样率检查：<44.1kHz 音质闷/不清晰，扣分
        sr_note = ""
        if orig_sample_rate:
            if orig_sample_rate < 32000:
                score -= 25
                sr_note = f"采样率过低({orig_sample_rate}Hz)"
            elif orig_sample_rate < 44100:
                score -= 12
                sr_note = f"采样率偏低({orig_sample_rate}Hz)"
        # 音频码率检查：过低说明压缩劣化
        br_note = ""
        if audio_bitrate and audio_bitrate < 48000:
            score -= 10
            br_note = f"音频码率偏低({audio_bitrate // 1000}kbps)"
        score = max(0, min(100, score))
        return {
            "has_audio": True,
            "voice": score,
            "audio_metrics": {
                "rms": round(rms, 4),
                "silent_ratio": round(silent_ratio, 3),
                "clip_ratio": round(clip_ratio, 4),
                "sample_rate": orig_sample_rate or sr,
                "audio_bitrate": audio_bitrate,
                "notes": " / ".join(filter(None, [sr_note, br_note])),
            },
        }
    except Exception as e:
        return {"has_audio": True, "voice": 60, "audio_metrics": {"note": f"音频指标计算失败: {e}"}}


def _composition_cv_probe(path: str) -> Dict[str, Any]:
    """构图客观质检（不依赖模型）：对若干关键帧用 cv2 评估
    - 三分法对齐度：主体边缘/高梯度点是否落在三分线附近
    - 边缘清晰度：整体拉普拉斯方差（与画质互补，这里聚焦构图锐度）
    - 主体亮度分布：是否过曝/过暗、主体是否居中或合理偏移
    返回 {composition_cv(0-100), compo_metrics{...}}。
    """
    import numpy as np

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"composition_cv": 0, "compo_metrics": {"note": "无法解码"}}
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    scores: List[float] = []
    rule_hits: List[str] = []
    sampled = 0
    if frame_count > 0:
        step = max(1, frame_count // 8)
        idx = 0
        while idx < frame_count:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                break
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # 三分法交点（画面 1/3、2/3 处）
            third_x, third_y = w / 3.0, h / 3.0
            # 用梯度幅值找显著区域中心点
            gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
            gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
            mag = (gx**2 + gy**2) ** 0.5
            # 显著点质心
            ys, xs = np.where(mag > np.percentile(mag, 85))
            if len(xs) > 0:
                cx, cy = xs.mean(), ys.mean()
                # 离最近三分线交点的归一化距离（越近越好）
                dx = min(abs(cx - third_x), abs(cx - 2 * third_x)) / (w / 2)
                dy = min(abs(cy - third_y), abs(cy - 2 * third_y)) / (h / 2)
                dist = (dx + dy) / 2.0
                rule_score = max(0, 100 - dist * 120)  # 落在三分线上≈100，居中≈约40
            else:
                rule_score = 40.0
            # 亮度分布：过曝/过暗惩罚
            mean_b = float(gray.mean())
            if mean_b > 235:
                rule_score -= 25
                rule_hits.append("过曝")
            elif mean_b < 18:
                rule_score -= 25
                rule_hits.append("过暗")
            # 边缘清晰度（构图锐度）
            edge = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            if edge > 50:
                rule_score += 5
            scores.append(max(0, min(100, rule_score)))
            sampled += 1
            idx += step
    cap.release()
    if not scores:
        return {"composition_cv": 0, "compo_metrics": {"note": "无有效帧"}}
    avg = sum(scores) / len(scores)
    return {
        "composition_cv": round(avg, 1),
        "compo_metrics": {
            "sampled_frames": sampled,
            "mean_brightness": round(float(np.mean([s for s in scores])), 1),
            "rules": list(dict.fromkeys(rule_hits)),
        },
    }


def run_technical_qc(video_path: str) -> Dict[str, Any]:
    tech = _probe_video(video_path)
    tech.update(_audio_probe(video_path))
    tech.update(_composition_cv_probe(video_path))
    issues: List[str] = []
    score = 100
    if not tech.get("readable"):
        issues.append("视频无法解码")
        score = 0
    else:
        if tech.get("width", 0) < 720 or tech.get("height", 0) < 720:
            issues.append(f"分辨率偏低 {tech.get('width')}x{tech.get('height')}")
            score -= 30
        if tech.get("duration_s", 0) < 1:
            issues.append("时长过短")
            score -= 20
        if tech.get("avg_laplacian_blur", 999) < 30:
            issues.append(f"画面模糊(拉普拉斯方差={tech.get('avg_laplacian_blur')})")
            score -= 30
        if tech.get("black_frame_ratio", 0) > 0.3:
            issues.append(f"黑屏比例过高 {tech.get('black_frame_ratio')}")
            score -= 30
        # 码率密度（bpp）过低 → 压缩/缩放劣化风险
        # 正常 1080p≈0.2+ bpp；<0.18 即偏压缩，<0.10 严重劣化
        bpp = tech.get("bpp", 0)
        if bpp and bpp < 0.10:
            issues.append(f"码率密度过低({bpp:.3f}bpp，画质压缩劣化)")
            score -= 25
        elif bpp and bpp < 0.18:
            issues.append(f"码率密度偏低({bpp:.3f}bpp，分辨率放大压缩)")
            score -= 12
        has_audio = tech.get("has_audio")
        if has_audio is False:
            issues.append("无音轨")
            score -= 15
        elif has_audio is None:
            issues.append("音轨未检测（ffprobe 不可用）")
    score = max(0, min(100, score))
    tech["score"] = score
    tech["issues"] = issues
    return tech


# ----------------------------------------------------------------------------
# 2. llama-server 按需启停
# ----------------------------------------------------------------------------

_SERVER_PROC: Optional[subprocess.Popen] = None


def _start_server() -> bool:
    global _SERVER_PROC
    if not os.path.exists(LLAMA_SERVER):
        raise FileNotFoundError(f"未找到 llama-server: {LLAMA_SERVER}")
    if not os.path.exists(MAIN_MODEL):
        raise FileNotFoundError(f"未找到主模型: {MAIN_MODEL}")
    if not os.path.exists(MMPROJ):
        raise FileNotFoundError(f"未找到 mmproj: {MMPROJ}")

    if _SERVER_PROC is not None and _SERVER_PROC.poll() is None:
        return True  # 已在运行

    _SERVER_PROC = subprocess.Popen(
        [
            LLAMA_SERVER,
            "-m",
            MAIN_MODEL,
            "--mmproj",
            MMPROJ,
            "--port",
            str(QC_PORT),
            "-ngl",
            "99",
            "--host",
            QC_HOST,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 等就绪
    for _ in range(60):
        try:
            r = httpx.get(f"http://{QC_HOST}:{QC_PORT}/health", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def _stop_server() -> None:
    global _SERVER_PROC
    if _SERVER_PROC is not None:
        try:
            _SERVER_PROC.terminate()
            _SERVER_PROC.wait(timeout=10)
        except Exception:
            try:
                _SERVER_PROC.kill()
            except Exception:
                pass
        _SERVER_PROC = None


# ----------------------------------------------------------------------------
# 3. Qwen2.5-VL 语义打分
# ----------------------------------------------------------------------------


def _extract_frames(video_path: str, max_frames: int = 6, target_width: int = 768) -> List[str]:
    """从视频均匀抽帧，返回 base64 JPEG 列表（llama.cpp 不支持 video_url，需转多图）。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        cap.release()
        return []
    step = max(1, total // max_frames)
    frames: List[str] = []
    idx = 0
    while idx < total and len(frames) < max_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if ok:
            h, w = frame.shape[:2]
            if w > target_width:
                frame = cv2.resize(frame, (target_width, int(h * target_width / w)))
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok2:
                frames.append(base64.b64encode(buf.tobytes()).decode("ascii"))
        idx += step
    cap.release()
    return frames


def _build_messages(video_path: str, caption: str) -> List[Dict[str, Any]]:
    content: List[Dict[str, Any]] = []
    # 视觉：抽帧转 base64 多图（llama.cpp OpenAI 兼容接口仅支持 image_url）
    frames = _extract_frames(video_path)
    if frames:
        for b64 in frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )
    txt = "请审核这段视频（以上为关键帧序列）。"
    if caption:
        txt += f"\n视频文案/字幕：{caption}"
    content.append({"type": "text", "text": txt})
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}]


def _call_qwen(messages: List[Dict[str, Any]], timeout: int = 180) -> Optional[Dict[str, Any]]:
    url = f"http://{QC_HOST}:{QC_PORT}/v1/chat/completions"
    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1500,
        # 关闭 Qwen3 的 think 模式，避免前导思考污染 JSON 输出
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, json=payload)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        return _parse_json_object(content)
    except Exception as e:
        raise RuntimeError(f"Qwen 调用失败: {e}")
    return None


async def _call_qwen_async(
    messages: List[Dict[str, Any]], timeout: int = 180
) -> Optional[Dict[str, Any]]:  # noqa: E501
    """异步版本：模型推理（常达 1-3 分钟）期间释放事件循环，避免占用 to_thread 线程池。"""
    url = f"http://{QC_HOST}:{QC_PORT}/v1/chat/completions"
    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1500,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url, json=payload)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        return _parse_json_object(content)
    except Exception as e:
        raise RuntimeError(f"Qwen 调用失败: {e}")
    return None


def _parse_json_object(content: str) -> Optional[Dict[str, Any]]:
    """从模型输出里稳健抽取 JSON 对象，兼容：
    - 裸 JSON
    - ```json ... ``` 代码块包裹
    - 前导/后缀含思考或说明文字
    """
    if not content:
        return None
    s = content.strip()
    # 1) 直接尝试整段
    try:
        return json.loads(s)
    except Exception:
        pass
    # 2) ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) 抽取第一个 {...}（最长匹配，容忍被截断）
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return None


def _server_healthy() -> bool:
    """探活：优先 /health，回落 /v1/models（llama.cpp 两种端点都支持其一）。"""
    for path in ("/health", "/v1/models"):
        try:
            r = httpx.get(f"http://{QC_HOST}:{QC_PORT}{path}", timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            continue
    return False


def run_semantic_qc(
    video_path: str, caption: str = "", manage_server: bool = False
) -> Dict[str, Any]:
    """调用本地 llama-server 做语义质检，返回原始语义评分 dict。

    manage_server=False（默认）：不托管 server，直接调用【调用方已起的常驻】llama-server
      （如用户手动在 8082 启动的 Qwen3-VL-8B）。若常驻不可达则抛错，绝不误杀调用方的进程。
    manage_server=True（按需模式）：由本函数拉起 / 关闭 server（仅当确实需要且未检测到常驻时）。
    """
    if not manage_server:
        if not _server_healthy():
            raise RuntimeError(
                f"常驻 llama-server 不可达 http://{QC_HOST}:{QC_PORT}。"
                "请先启动：llama-server.exe -m <主模型> --mmproj <mmproj> "
                f"--port {QC_PORT} -ngl 99 --host {QC_HOST}"
            )
        msgs = _build_messages(video_path, caption)
        return _call_qwen(msgs) or {}
    # 按需模式：仅当常驻不可用时才自己拉起（避免双开抢端口）
    if not _server_healthy():
        if not _start_server():
            raise RuntimeError("llama-server 启动失败")
        own = True
    else:
        own = False
    try:
        msgs = _build_messages(video_path, caption)
        return _call_qwen(msgs) or {}
    finally:
        if own:
            _stop_server()


async def run_semantic_qc_async(
    video_path: str, caption: str = "", manage_server: bool = False
) -> Dict[str, Any]:
    """异步语义质检：抽帧（CPU 密集）丢线程池，模型推理（长 I/O）走异步，
    全程不长时间占用 to_thread 线程池，支持多视频并发 QC。"""
    if not manage_server:
        if not _server_healthy():
            raise RuntimeError(
                f"常驻 llama-server 不可达 http://{QC_HOST}:{QC_PORT}。"
                "请先启动：llama-server.exe -m <主模型> --mmproj <mmproj> "
                f"--port {QC_PORT} -ngl 99 --host {QC_HOST}"
            )
        msgs = await asyncio.to_thread(_build_messages, video_path, caption)
        return (await _call_qwen_async(msgs)) or {}
    if not _server_healthy():
        if not _start_server():
            raise RuntimeError("llama-server 启动失败")
        own = True
    else:
        own = False
    try:
        msgs = await asyncio.to_thread(_build_messages, video_path, caption)
        return (await _call_qwen_async(msgs)) or {}
    finally:
        if own:
            _stop_server()


# ----------------------------------------------------------------------------
# 4. 100 分制聚合
# ----------------------------------------------------------------------------

# 语义维度 → 100 分制维度映射（copyright 不占权重，仅作风险检测）
_SEM_MAP = {
    "composition": "composition",
    "consistency": "consistency",
    "lip_sync": "lip_sync",
    "rhythm": "rhythm",
    "compliance": "compliance",
}


def aggregate(
    technical: Dict[str, Any], semantic: Dict[str, Any], threshold: float = 60.0
) -> QcResult:
    res = QcResult()
    res.technical = technical

    # 画质维度（quality）由 cv2 客观质检分提供
    tech_score = technical.get("score", 0)
    res.dimensions["quality"] = tech_score

    # 客观配音质量（voice）与构图客观分（composition_cv）由 cv2/ffmpeg 客观质检提供
    if isinstance(technical.get("voice"), (int, float)):
        res.dimensions["voice"] = int(technical["voice"])
    if isinstance(technical.get("composition_cv"), (int, float)):
        res.dimensions["composition_cv"] = int(technical["composition_cv"])

    # AI 语义维度
    sem_dims = {}
    for k_src, k_dst in _SEM_MAP.items():
        v = semantic.get(k_src)
        if isinstance(v, (int, float)):
            sem_dims[k_dst] = int(v)
            res.dimensions[k_dst] = int(v)
    # ── 语义分校准：防止模型对"图生视频/无真人口播"场景给虚高 lip_sync 分 ──
    # 图生视频（概念图动态化）通常无真实口型同步，模型常按 prompt 默认给 80，
    # 该分不可信。此处结合技术侧信号做保守化处理。
    if "lip_sync" in res.dimensions:
        # 信号：音频采样率低 / 音频极简 / 无音轨 → 无真实口型依据
        _has_audio = technical.get("has_audio")
        _audio_note = (technical.get("audio_metrics") or {}).get("notes", "")
        _sr = (technical.get("audio_metrics") or {}).get("sample_rate", 0)
        _reduced_lipsync = (
            _has_audio is False
            or (_sr and _sr <= 32000)  # 32kHz 即偏低，无真实口型高频信息
            or ("采样率过低" in _audio_note)
            or ("采样率偏低" in _audio_note)
            or ("无音轨" in _audio_note)
        )
        if _reduced_lipsync:
            # 无真实口型依据 → 不采信模型的乐观分，降为保守 60 并提示
            if res.dimensions["lip_sync"] >= 75:
                res.dimensions["lip_sync"] = 60
                res.notes.append(
                    "lip_sync 无真实口型依据（图生视频/音频异常），已从模型分降为保守分 60"
                )
    # 语义总分乐观校准：若模型给的分普遍≥80 而客观维度明显低于此（如画质差），
    # 说明模型宽松，总分将自动被低客观维度拉低（无需额外惩罚，靠权重体现）。

    if not sem_dims:
        res.notes.append("语义质检未返回有效分数（模型未加载或调用失败），仅画质分有效")

    # 合规/版权命中
    res.compliance_hits = semantic.get("compliance_hits", []) or []
    res.copyright_hits = semantic.get("copyright_hits", []) or []
    res.summary = semantic.get("summary", "")
    res.model_used = MODEL_DISPLAY

    # 合规分级处理：hard→拦截；medium→合规维度扣分+提示；soft→仅提示
    # 注：模型返回的 compliance_hits 多为描述性短语（如"诱导加私信"），
    #     故用【双向子串匹配】——预设关键词是短语子串，或短语是关键词子串，均命中。
    hard_hits, medium_hits, soft_hits = [], [], []
    for h in res.compliance_hits:
        sev = "hard"  # 默认最高级，避免漏拦
        for kw, level in COMPLIANCE_RULES.items():
            if kw in h or h in kw:
                sev = level
                break
        res.compliance_detail.append({"hit": h, "severity": sev})
        (hard_hits if sev == "hard" else medium_hits if sev == "medium" else soft_hits).append(h)

    if hard_hits:
        res.blocked = True
        res.blocked_reasons.append(f"严重合规红线命中(拦截): {', '.join(hard_hits)}")
    if medium_hits:
        # medium：不直接拦截，但合规维度扣惩罚分，并写入提示
        res.notes.append(f"中度合规风险(扣分不拦截): {', '.join(medium_hits)}")
        cur = res.dimensions.get("compliance")
        if isinstance(cur, (int, float)):
            new_c = max(0, cur - COMPLIANCE_MEDIUM_PENALTY * len(medium_hits))
            res.dimensions["compliance"] = int(new_c)
    if soft_hits:
        res.notes.append(f"轻度合规提示: {', '.join(soft_hits)}")

    # 加权（在合规扣分之后计算，保证总分与维度自洽）
    present = {d: w for d, w in WEIGHTS.items() if isinstance(res.dimensions.get(d), (int, float))}
    if present:
        wsum = sum(present.values())
        total = sum(res.dimensions[d] * w for d, w in present.items())
        res.total_score = round(total / wsum, 1)  # 按现存维度归一化
    else:
        res.total_score = 0.0

    # 红线 2：版权高风险 IP 一票否决（法律红线，硬拦截）
    for brand in COPYRIGHT_RISK_BRANDS:
        if any(brand in h for h in res.copyright_hits):
            res.blocked = True
            res.blocked_reasons.append(f"版权高风险命中(拦截): {brand}")

    res.passed = (not res.blocked) and res.total_score >= threshold
    return res


# ----------------------------------------------------------------------------
# 对外主入口
# ----------------------------------------------------------------------------


def run_qc(
    video_path: str,
    caption: str = "",
    threshold: float = 60.0,
    use_semantic: bool = True,
    manage_server: bool = False,
) -> QcResult:
    """完整质检：技术 + 语义（按需）。返回 QcResult。

    manage_server=False（默认）：语义审核调用【调用方已起的常驻】llama-server，
      不会拉起/关闭进程，避免误杀用户常驻的 8082 server。
    manage_server=True：按需启停 server（仅当常驻不可用时才自拉起）。
    """
    technical = run_technical_qc(video_path)
    semantic: Dict[str, Any] = {}
    if use_semantic:
        try:
            semantic = run_semantic_qc(video_path, caption, manage_server=manage_server)
        except Exception as e:
            technical.setdefault("issues", []).append(f"语义质检跳过: {e}")
    # 本地关键词兜底：文案命中高危词 → 注入合规命中（不依赖模型）
    if caption:
        hits = [kw for kw in COMPLIANCE_RULES if kw in caption]
        if hits:
            semantic.setdefault("compliance_hits", []).extend(hits)
    if not semantic:
        technical.setdefault("issues", []).append(
            "语义质检未返回有效结果（常驻 llama-server 不可达或未启用 useSemantic），仅画质分有效"
        )
    return aggregate(technical, semantic, threshold)


async def run_qc_async(
    video_path: str,
    caption: str = "",
    threshold: float = 60.0,
    use_semantic: bool = True,
    manage_server: bool = False,
) -> QcResult:
    """真正的异步质检流程（供 stage 调用）：
    - 技术质检（cv2，CPU 密集）offload 线程池，不阻塞事件循环
    - 语义质检：抽帧 offload 线程池 + 模型推理走异步 httpx（长 I/O 释放事件循环）
    - 支持多视频并发 QC，不再长时间占用 to_thread 线程池
    """
    technical = await asyncio.to_thread(run_technical_qc, video_path)
    semantic: Dict[str, Any] = {}
    if use_semantic:
        try:
            semantic = await run_semantic_qc_async(video_path, caption, manage_server=manage_server)
        except Exception as e:
            technical.setdefault("issues", []).append(f"语义质检跳过: {e}")
    if caption:
        hits = [kw for kw in COMPLIANCE_RULES if kw in caption]
        if hits:
            semantic.setdefault("compliance_hits", []).extend(hits)
    if not semantic:
        technical.setdefault("issues", []).append(
            "语义质检未返回有效结果（常驻 llama-server 不可达或未启用 useSemantic），仅画质分有效"
        )
    return aggregate(technical, semantic, threshold)
