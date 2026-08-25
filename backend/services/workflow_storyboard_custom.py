"""
分镜工作流构建器 — 4套定制分镜模板

包含工作流：
- single_person:  单人分镜 (1人分镜.json)
- dual_person:    双人融合分镜 (2人分镜.json)
- local_multi:    本地多人分镜 (本地多人分镜.json)
- gpt_storyboard: GPT-Image 分镜 (GPT分镜.json)
"""

import json
import logging
import os
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WF_DIR = Path(__file__).parent.parent.parent / "workflows"


def _load_template(name: str) -> Dict[str, Any]:
    """加载工作流 JSON 模板"""
    path = _WF_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"工作流模板不存在: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_nodes_by_class(wf: Dict[str, Any], class_type: str) -> List[Tuple[str, Dict]]:
    """根据 class_type 查找所有匹配节点"""
    return [
        (nid, node)
        for nid, node in wf.items()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]  # noqa: E501


def _find_node_by_title(wf: Dict[str, Any], title: str) -> Optional[Tuple[str, Dict]]:
    """根据 _meta.title 查找节点"""
    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("_meta", {}).get("title") == title:
            return (nid, node)
    return None


def _inject_seed(wf: Dict[str, Any], seed: int):
    """向所有 KSampler 节点注入 seed"""
    for nid, node in _find_nodes_by_class(wf, "KSampler"):
        node["inputs"]["seed"] = seed


def _inject_filename_prefix(wf: Dict[str, Any], prefix: str):
    """向所有 SaveImage 节点注入 filename_prefix"""
    for nid, node in _find_nodes_by_class(wf, "SaveImage"):
        node["inputs"]["filename_prefix"] = prefix


# ============================================================
# 1. 单人分镜
# ============================================================


def build_single_person_workflow(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "storyboard",
    width: Optional[int] = None,
    height: Optional[int] = None,
    **kwargs,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    单人分镜：基于 Qwen Image Edit 逐帧生成，支持多场景连续生成。

    reference_images 支持:
        character: 角色参考图
        scene: 场景参考图
        pose: 姿态参考图（可选）
        cloth: 服装参考图（可选）
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    wf = _load_template("1人分镜")

    # 注入输入图 — 同时搜索中英文标题（1人分镜.json 使用英文标题）
    img_mapping = {
        "character": {
            "titles": ["角色图", "IMAGE 1"],
            "url": reference_images.get("character", ""),
        },
        "scene": {
            "titles": ["场景图", "IMAGE 3", "IMAGE 3"],
            "url": reference_images.get("scene", ""),
        },
        "cloth": {
            "titles": ["服装图", "IMAGE 2"],
            "url": reference_images.get("cloth", ""),
        },
    }
    for role, info in img_mapping.items():
        img_url = info["url"]
        if img_url:
            # 依次尝试多个标题
            match = None
            for title in info["titles"]:
                match = _find_node_by_title(wf, title)
                if match:
                    break
            if match:
                nid, node = match
                # 从 URL 提取文件名
                fname = img_url.rsplit("=", 1)[-1] if "=" in img_url else img_url.rsplit("/", 1)[-1]
                if fname:
                    node["inputs"]["image"] = fname

    # 注入姿态参考图 — 同时搜索中英文标题
    pose_url = reference_images.get("pose", "") or kwargs.get("pose_reference_image", "")
    if pose_url:
        pose_match = _find_node_by_title(wf, "姿态参考图") or _find_node_by_title(
            wf, "IMAGE 4 POSE"
        )
        if pose_match:
            nid, node = pose_match
            fname = pose_url.rsplit("=", 1)[-1] if "=" in pose_url else pose_url.rsplit("/", 1)[-1]
            if fname:
                node["inputs"]["image"] = fname

    # ⭐ 关键修复：将所有 LoadImage 节点中引用不存在文件的默认值，替换为角色参考图
    # 分镜模板导出时包含临时文件名（如 ComfyUI_01246_.png），这些文件不存在导致验证失败
    character_fname = ""
    char_url = reference_images.get("character", "")
    if char_url:
        character_fname = (
            char_url.rsplit("=", 1)[-1] if "=" in char_url else char_url.rsplit("/", 1)[-1]
        )  # noqa: E501

    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            default_image = node.get("inputs", {}).get("image", "")
            # 检查是否引用了可能不存在的 ComfyUI 临时文件
            if default_image.startswith("ComfyUI_") or default_image.startswith("ComfyUI_temp_"):
                # 优先使用角色图作为 fallback
                if character_fname:
                    node["inputs"]["image"] = character_fname
                else:
                    # 使用场景图作为 fallback
                    scene_url = reference_images.get("scene", "")
                    if scene_url:
                        scene_fname = (
                            scene_url.rsplit("=", 1)[-1]
                            if "=" in scene_url
                            else scene_url.rsplit("/", 1)[-1]
                        )  # noqa: E501
                        if scene_fname:
                            node["inputs"]["image"] = scene_fname

    # 注入 prompt - 查找 TextEncodeQwenImageEditPlus 节点
    for nid, node in _find_nodes_by_class(wf, "TextEncodeQwenImageEditPlus"):
        if "text" in node["inputs"]:
            node["inputs"]["text"] = prompt_text
        if "instruction" in node["inputs"]:
            node["inputs"]["instruction"] = prompt_text

    # 注入 seed
    _inject_seed(wf, actual_seed)

    # 注入 filename_prefix
    _inject_filename_prefix(wf, filename_prefix)

    # 覆写尺寸
    if width and height:
        for nid, node in _find_nodes_by_class(wf, "EmptySD3LatentImage"):
            if node["inputs"].get("width"):
                node["inputs"]["width"] = width
            if node["inputs"].get("height"):
                node["inputs"]["height"] = height

    metadata = {
        "template": "single_person",
        "seed": actual_seed,
        "steps": 1,
    }
    return [[wf], ["单人分镜"], metadata]  # 注意：1人分镜可能有多步，此处简化


# ============================================================
# 2. 双人融合分镜
# ============================================================


def build_dual_person_workflow(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "storyboard",
    width: Optional[int] = None,
    height: Optional[int] = None,
    **kwargs,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    双人融合分镜：人物A + 人物B + 场景三图输入，使用高级文本编码器融合。
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    wf = _load_template("2人分镜")

    # 注入输入图
    img_mapping = {
        "人物A（4视图）": reference_images.get("character", ""),
        "人物B（4视图）": reference_images.get("character2", ""),
    }
    for title, img_url in img_mapping.items():
        if img_url:
            match = _find_node_by_title(wf, title)
            if match:
                nid, node = match
                fname = img_url.rsplit("=", 1)[-1] if "=" in img_url else img_url.rsplit("/", 1)[-1]
                if fname:
                    node["inputs"]["image"] = fname

    # 注入场景图（查找第三个 LoadImage）
    load_images = _find_nodes_by_class(wf, "LoadImage")
    if len(load_images) >= 3:
        # 第3个 LoadImage 是场景图
        scene_url = reference_images.get("scene", "")
        if scene_url:
            nid, node = load_images[2]
            fname = (
                scene_url.rsplit("=", 1)[-1] if "=" in scene_url else scene_url.rsplit("/", 1)[-1]
            )  # noqa: E501
            if fname:
                node["inputs"]["image"] = fname

    # 注入 prompt - 查找高级文本编码器
    for nid, node in _find_nodes_by_class(wf, "TextEncodeQwenImageEditPlusAdvance_lrzjason"):
        if "prompt" in node["inputs"]:
            node["inputs"]["prompt"] = prompt_text
        if "text" in node["inputs"]:
            node["inputs"]["text"] = prompt_text

    # 注入 seed
    _inject_seed(wf, actual_seed)

    # 注入 filename_prefix
    _inject_filename_prefix(wf, filename_prefix)

    # 覆写尺寸
    if width and height:
        for nid, node in _find_nodes_by_class(wf, "EmptyLatentImage"):
            if node["inputs"].get("width"):
                node["inputs"]["width"] = width
            if node["inputs"].get("height"):
                node["inputs"]["height"] = height

    metadata = {"template": "dual_person", "seed": actual_seed}
    return [[wf], ["双人融合分镜"], metadata]


# ============================================================
# 3. 本地多人分镜（含AI分镜描述）
# ============================================================


def build_local_multi_workflow(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "storyboard",
    width: Optional[int] = None,
    height: Optional[int] = None,
    **kwargs,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    本地多人分镜：多角色输入 + AI 分镜描述生成（Qwen3-VL）
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    wf = _load_template("本地多人分镜")

    # 注入参考图 - 查找 LoadImage 节点（按顺序）
    load_images = _find_nodes_by_class(wf, "LoadImage")
    img_keys = ["character", "character2", "character3", "scene", "prop"]
    for i, (nid, node) in enumerate(load_images):
        if i < len(img_keys) and img_keys[i] in reference_images:
            img_url = reference_images[img_keys[i]]
            fname = img_url.rsplit("=", 1)[-1] if "=" in img_url else img_url.rsplit("/", 1)[-1]
            if fname:
                node["inputs"]["image"] = fname

    # 注入 prompt
    for nid, node in _find_nodes_by_class(wf, "TextEncodeQwenImageEditPlusAdvance_lrzjason"):
        if "prompt" in node["inputs"]:
            node["inputs"]["prompt"] = prompt_text

    # 注入分镜描述（ModelScopeImageCaptionNode）
    for nid, node in _find_nodes_by_class(wf, "ModelScopeImageCaptionNode"):
        if "text" in node["inputs"]:
            node["inputs"]["text"] = prompt_text

    # 注入 seed
    _inject_seed(wf, actual_seed)

    # 注入 filename_prefix
    _inject_filename_prefix(wf, filename_prefix)

    # 覆写尺寸
    if width and height:
        for nid, node in _find_nodes_by_class(wf, "EmptyLatentImage"):
            if node["inputs"].get("width"):
                node["inputs"]["width"] = width
            if node["inputs"].get("height"):
                node["inputs"]["height"] = height

    metadata = {"template": "local_multi", "seed": actual_seed}
    return [[wf], ["本地多人分镜"], metadata]


# ============================================================
# 4. GPT分镜（OpenAI GPT-Image-2 API）
# ============================================================


def build_gpt_storyboard_workflow(
    reference_images: Dict[str, str],
    prompt_text: str,
    seed: Optional[int] = None,
    filename_prefix: str = "storyboard",
    width: Optional[int] = None,
    height: Optional[int] = None,
    **kwargs,
) -> Tuple[List[Dict[str, Any]], List[str], Dict[str, Any]]:
    """
    GPT 分镜：调用 OpenAI GPT-Image-2 API 云端生成。

    注意：此工作流通过 Comfly 自定义节点调用外部 API，
    需要配置 OPENAI_API_KEY 环境变量。
    """
    actual_seed = seed or random.randint(0, 2**31 - 1)
    wf = _load_template("GPT分镜")

    # 注入参考图
    load_images = _find_nodes_by_class(wf, "LoadImage")
    img_keys = list(reference_images.keys())
    for i, (nid, node) in enumerate(load_images):
        if i < len(img_keys):
            img_url = reference_images[img_keys[i]]
            fname = img_url.rsplit("=", 1)[-1] if "=" in img_url else img_url.rsplit("/", 1)[-1]
            if fname:
                node["inputs"]["image"] = fname

    # 注入 prompt — 查找 PrimitiveStringMultiline / Text Multiline 节点
    text_nodes = _find_nodes_by_class(wf, "PrimitiveStringMultiline")
    text_nodes += _find_nodes_by_class(wf, "Text Multiline")
    for nid, node in text_nodes:
        if "text" in node["inputs"] and len(str(node["inputs"].get("text", ""))) < 50:
            # 只替换短文本的节点（长文本是分镜剧本模板）
            node["inputs"]["text"] = prompt_text

    # 注入 GPT API key (从环境变量读取)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if api_key:
        # 查找 Comfly_gpt_image 节点
        for nid, node in wf.items():
            if isinstance(node, dict) and "gpt" in node.get("class_type", "").lower():
                if "api_key" in node["inputs"]:
                    node["inputs"]["api_key"] = api_key

    # 注入 seed
    _inject_seed(wf, actual_seed)

    # 注入 filename_prefix
    _inject_filename_prefix(wf, filename_prefix)

    metadata = {"template": "gpt_storyboard", "seed": actual_seed}
    return [[wf], ["GPT分镜"], metadata]
