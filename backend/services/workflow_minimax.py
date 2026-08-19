"""MiniMax H3 视频工作流构建器（文本→视频）

基于用户的「MiniMax+H3全能参考工作流.json」裁剪出单段纯文本→视频链路，
剥离多段并行/参考图/参考音视频，仅保留核心生成链：

  UNET(fl2va) → SageAttentionPatch → LoRA(turbo)
  CLIP(qwen3vl) ─────────────────────┐
                                     ├─→ MiniMaxH3AudioConditioningT8（prompt/尺寸/帧数/音频）
                                     │
  MultiRateSamplerEXPT8(model, av_latent) → BasicGuider ┐
  RandomNoise(seed) ────────────────────────────────────┼─→ SamplerCustomAdvanced
                                                         ┘
  MiniMaxH3AVDecodeT8 → VHS_VideoCombine（图片帧 + 同步音频 → mp4）

MiniMax H3 是音视频统一模型，audio_mode=True 时同步生成环境音（含对话/音效），
与后续 TTS 台词配音可并存。
"""
from typing import Any, Dict


def _as_multiline(text: str) -> str:
    """把含换行的提示词转成 ComfyUI STRING widget 的多行表示（\\n 转义）。"""
    return text.replace("\\n", "\n").replace("\n", "\\n")

# —— 该工作流依赖的权重文件名（与新 ComfyUI 目录 models 对应）——
UNET_NAME = "minimax_h3_fl2va_int8_convrot.safetensors"
CLIP_NAME = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
LORA_NAME = "minimax_h3_turbo_4STEPS_comfyui.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"

# MiniMax H3 生成参数（turbo 4 步）默认值，参照官方全能参考工作流
DEFAULT_VIDEO_STEPS = 8
DEFAULT_AUDIO_STEPS = 10
DEFAULT_SHIFT_VIDEO = 12.0
DEFAULT_SHIFT_AUDIO = 3.0
DEFAULT_FRAME_RATE = 24
_MOTION_BLOCK = 17  # 官方用 ComfyMathExpression 将帧数对齐到 17 的整数倍

# 视频分辨率的宽高参考（default 16:9）
_REF_RESOLUTIONS = {
    "9:16": (480, 864),
    "16:9": (864, 486),
    "1:1": (648, 648),
    "4:3": (720, 540),
    "3:4": (540, 720),
}


def resolve_minimax_size(width: int, height: int) -> (int, int):
    """把输入尺寸映射到 MiniMax 分辨率选择器支持的比例（0.4MP 档）。
    返回 32 对齐的宽高（Minimax旋律需要 32 的倍数）。"""
    size = (width or 480, height or 864)
    return size[0] // 32 * 32, size[1] // 32 * 32


def frames_for_duration(duration_seconds: float) -> int:
    """由秒数推算帧数并 17 对齐，与官方 ComfyMathExpression 一致：
    max(5, round(a*24)) + (5 - (max(5, round(a*24)) % 17)) % 17"""
    base = max(5, int(round(float(duration_seconds) * DEFAULT_FRAME_RATE)))
    return base + ((5 - (base % _MOTION_BLOCK)) % _MOTION_BLOCK)


# ============================================================
# 输出槽名 → 整数索引（ComfyUI API prompt 输出引用必须用整数索引）
# ============================================================
_SLOT_INDEX = {
    "unet": {"MODEL": 0},
    "clip": {"CLIP": 0},
    "vae_video": {"VAE": 0},
    "vae_audio": {"VAE": 0},
    "patched": {"model": 0},
    "lora": {"MODEL": 0},
    "rate": {"MODEL": 0, "sampler": 1, "sigmas": 2},
    "cond": {"positive": 0, "av_latent": 1},
    "noise": {"NOISE": 0},
    "guider": {"GUIDER": 0},
    "sampler": {"output": 0},
    "avdecode": {"frames": 0, "generated_audio": 1},
}


def _resolve_slots(wf: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """把 [node, 输出名] 引用转成 [node, 整数索引]（ComfyUI 需要整数）。"""
    for node in wf.values():
        for key, val in node["inputs"].items():
            if isinstance(val, list) and len(val) == 2 and isinstance(val[1], str):
                src, name = val
                idx = _SLOT_INDEX.get(src, {}).get(name)
                if idx is None:
                    raise ValueError(f"未知输出槽: {src}.{name}")
                node["inputs"][key] = [src, idx]
    return wf


def build_minimax_h3_video_workflow(
    prompt: str,
    width: int = 480,
    height: int = 864,
    duration_seconds: float = 5.0,
    seed: int = 0,
    audio_mode: str = "native",
    filename_prefix: str = "minimax_h3",
    video_steps: int = DEFAULT_VIDEO_STEPS,
    audio_steps: int = DEFAULT_AUDIO_STEPS,
    shift_video: float = DEFAULT_SHIFT_VIDEO,
    shift_audio: float = DEFAULT_SHIFT_AUDIO,
    frame_rate: int = DEFAULT_FRAME_RATE,
) -> Dict[str, Dict[str, Any]]:
    """构建 MiniMax H3 文本→视频工作流（ComfyUI API prompt 格式）。"""
    width, height = resolve_minimax_size(width, height)
    frames = frames_for_duration(duration_seconds)

    prompt = prompt.strip() or "一个简单的纯色背景上漂浮的物体。"
    # 真正的随机化由调用方传入的 seed 决定；这里默认取系统时间微秒
    import time
    seed = int(seed) or (int(time.time() * 1000) % (2 ** 32))

    wf: Dict[str, Dict[str, Any]] = {
        "vae_video": {"class_type": "VAELoader", "inputs": {"vae_name": VIDEO_VAE}},
        "vae_audio": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_NAME, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP_NAME, "type": "minimax", "device": "default"}},
        "patched": {"class_type": "MiniMaxH3MemoryEfficientSageAttentionPatch", "inputs": {
            "model": ["unet", "MODEL"],
        }},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": ["patched", "model"],
            "lora_name": LORA_NAME, "strength_model": 1.0,
        }},
        "cond": {"class_type": "MiniMaxH3AudioConditioningT8", "inputs": {
            "clip": ["clip", "CLIP"],
            "video_vae": ["vae_video", "VAE"],
            "audio_vae": ["vae_audio", "VAE"],
            "prompt": _as_multiline(prompt),
            "width": width, "height": height, "length": frames,
            "task_type": "auto",
            "audio_mode": audio_mode,
            "audio_denoise_strength": 0.0,
            "add_source_as_reference": False,
            "prompt_primary_audio_ordinal": 0,
            "strict_prompt_tags": False,
            "ref_image_size": "match",
            "reference_video_policy": "official_2_to_15s",
        }},
        "rate": {"class_type": "MiniMaxH3MultiRateSamplerEXPT8", "inputs": {
            "model": ["lora", "MODEL"],
            "av_latent": ["cond", "av_latent"],
            "video_steps": video_steps, "audio_steps": audio_steps,
            "shift_video": shift_video, "shift_audio": shift_audio,
        }},
        "guider": {"class_type": "BasicGuider", "inputs": {
            "model": ["rate", "MODEL"],
            "conditioning": ["cond", "positive"],
        }},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "sampler": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["noise", "NOISE"],
            "guider": ["guider", "GUIDER"],
            "sampler": ["rate", "sampler"],
            "sigmas": ["rate", "sigmas"],
            "latent_image": ["cond", "av_latent"],
        }},
        "avdecode": {"class_type": "MiniMaxH3AVDecodeT8", "inputs": {
            "av_latent": ["sampler", "output"],
            "video_vae": ["vae_video", "VAE"],
            "audio_vae": ["vae_audio", "VAE"],
        }},
        "combine": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["avdecode", "frames"],
            "audio": ["avdecode", "generated_audio"],
            "frame_rate": frame_rate, "loop_count": 1,
            "filename_prefix": filename_prefix, "format": "video/h264-mp4",
            "pingpong": False, "save_output": True, "pix_fmt": "yuv420p",
            "crf": 15, "save_metadata": True, "trim_to_audio": False,
            "videopreview": False,
        }},
    }
    return _resolve_slots(wf)


def build_minimax_h3_size_presets() -> list:
    return [{"ratio": k, "width": w, "height": h} for k, (w, h) in _REF_RESOLUTIONS.items()]