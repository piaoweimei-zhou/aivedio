"""
ComfyUI 工作流构建器 — 共享工具与常量

节点查找、工作流加载、参考图解析、参数注入等公共函数。
"""

import copy
import json
import logging
import os
import re
import time
import random
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)

def find_node_by_class_type(wf: dict, class_type: str) -> list:
    """通过 class_type 查找所有匹配的节点，返回 [(node_id, node_data), ...]"""
    results = []
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get('class_type') == class_type:
            results.append((nid, ndata))
    return results


def find_node_by_title(wf: dict, title: str) -> list:
    """通过 _meta.title 查找所有匹配的节点，返回 [(node_id, node_data), ...]"""
    results = []
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and ndata.get('_meta', {}).get('title') == title:
            results.append((nid, ndata))
    return results


def find_first_node_by_class_type(wf: dict, class_type: str) -> tuple:
    """通过 class_type 查找第一个匹配的节点，返回 (node_id, node_data) 或 (None, None)"""
    results = find_node_by_class_type(wf, class_type)
    return results[0] if results else (None, None)


def find_first_node_by_class_type_contains(wf: dict, class_type_part: str) -> tuple:
    """通过 class_type 部分匹配查找第一个节点，返回 (node_id, node_data) 或 (None, None)"""
    for nid, ndata in wf.items():
        if isinstance(ndata, dict) and class_type_part in ndata.get('class_type', ''):
            return (nid, ndata)
    return (None, None)


def _infer_saveimage_type(wf: dict, save_nid: str, infer_map: dict) -> str:
    """根据 SaveImage 节点的上游链路推断输出类型

    遍历 SaveImage 的输入链路，查找上游节点的 class_type，
    与 infer_map 中的关键词匹配，返回最匹配的类型标签。

    Args:
        wf: 工作流 dict
        save_nid: SaveImage 节点 ID
        infer_map: {type_tag: [keyword_list]} 映射

    Returns:
        匹配的 type_tag，默认 "unknown"
    """
    MAX_DEPTH = 20  # 防止环形引用或极深链路导致无限遍历
    visited = set()
    queue = [(save_nid, 0)]  # (node_id, depth)

    while queue:
        nid, depth = queue.pop(0)
        if nid in visited or depth > MAX_DEPTH:
            continue
        visited.add(nid)

        ndata = wf.get(nid)
        if not isinstance(ndata, dict):
            continue

        class_type = ndata.get("class_type", "").lower()
        # 检查 class_type 是否匹配任一类型
        for type_tag, keywords in infer_map.items():
            if any(kw in class_type for kw in keywords):
                return type_tag

        # 特判：AIO_Preprocessor 等预处理节点，检查其 "preprocessor" 输入参数值
        # 因为 AIO_Preprocessor 的 class_type 不含具体类型关键词
        # (如 "lineart", "depth")，类型信息在 preprocessor 参数中
        preprocessor_val = ndata.get("inputs", {}).get("preprocessor", "")
        if preprocessor_val:
            preprocessor_lower = preprocessor_val.lower()
            for type_tag, keywords in infer_map.items():
                if any(kw in preprocessor_lower for kw in keywords):
                    return type_tag

        # 追溯输入链路
        for input_val in ndata.get("inputs", {}).values():
            if isinstance(input_val, list) and len(input_val) >= 2:
                upstream_nid = str(input_val[0])
                if upstream_nid in wf and upstream_nid not in visited:
                    queue.append((upstream_nid, depth + 1))

    return "unknown"
_WF_DIR = Path(__file__).parent.parent.parent / "workflows"

# 文生图工作流模板（支持多版本，按 content_type 切换）
_WF_STANDARD = _WF_DIR / "文生图.json"      # 旧标准版（8步/cfg=1）
_WF_CINEMATIC = _WF_DIR / "最终文生图.json"  # 最终优化版（25步/cfg=2/AuraFlow）
_WF_PROP = _WF_DIR / "最终道具工作流.json"   # 道具专用版（+SeedVR2超分）

def _load_workflow(file_path: Path, label: str = "") -> Dict[str, Any]:
    """安全加载工作流 JSON 文件"""
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wf = json.load(f)
            logger.info(f"[Workflow] 从 {file_path} 加载 ({len(wf)} 节点) {label}")
            return wf
        except Exception as e:
            logger.warning(f"[Workflow] 加载失败 {file_path}: {e}")
    else:
        logger.warning(f"[Workflow] {file_path} 不存在")
    return {}

# 向后兼容：保留 BASE_WORKFLOW 符号引用
BASE_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_STANDARD, "(标准版)")
CINEMATIC_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_CINEMATIC, "(影视级)")
PROP_WORKFLOW: Dict[str, Any] = _load_workflow(_WF_PROP, "(道具版)")

# 瑶光版负向提示词（推荐留空，使用 ConditioningZeroOut）
YAOGUANG_DEFAULT_NEGATIVE = ""

# 其他支持的附加 LoRA
ADDITIONAL_LORAS = {
    "moody": {
        "lora_name": "moodyPornMix_zitV10DPO.safetensors",
        "strength_model": 0.8,
    },
}
QWEN_WORKFLOW_FILE = Path(__file__).parent.parent.parent / "Qwen Image Edit - Remix AIO v2.0 全功能合集工作流 By：肥猴 (1).json"
def format_qwen_prompt(
    keep: str = "",
    change: str = "",
    maintain: str = "",
    avoid: str = "",
    fallback: str = "",
) -> str:
    """
    格式化5段式提示词
    
    Args:
        keep: [KEEP] 保留元素
        change: [CHANGE] 改变指令
        maintain: [MAINTAIN] 一致性保持
        avoid: [AVOID] 负面约束
        fallback: [FALLBACK] 冲突解决
    """
    sections = []
    
    if keep:
        sections.append(f"[KEEP]\n{keep}")
    if change:
        sections.append(f"[CHANGE]\n{change}")
    if maintain:
        sections.append(f"[MAINTAIN]\n{maintain}")
    if avoid:
        sections.append(f"[AVOID]\n{avoid}")
    if fallback:
        sections.append(f"[FALLBACK]\n{fallback}")
    
    return "\n\n".join(sections)
# ComfyUI 目录（统一从 config 模块读取）
from services.comfyui.config import COMFYUI_DIR as _COMFYUI_DIR, COMFYUI_OUTPUT_DIR as _COMFYUI_OUTPUT_DIR
_COMFYUI_INPUT_DIR = os.path.join(_COMFYUI_DIR, "input") if _COMFYUI_DIR else ""
def _resolve_comfyui_image(ref: str) -> str:
    """
    将参考图像 URL/路径解析为 ComfyUI LoadImage 可用的文件名。
    
    ComfyUI 的 LoadImage 节点只接受 input 目录下的文件名（不带路径），
    而生成的图片在 output 目录中。此函数会：
    1. 从 URL (如 /api/comfyui/image?filename=xxx.png) 中提取 filename
    2. 将文件从 output 目录复制到 input 目录
    3. 返回纯文件名
    
    Args:
        ref: 参考图像 URL 或本地路径
    
    Returns:
        ComfyUI input 目录下的文件名
    """
    if not ref:
        return ref

    # 从 URL 中提取 filename 参数
    filename = ref
    if '?' in ref or ref.startswith('/api/'):
        parsed = urlparse(ref)
        params = parse_qs(parsed.query)
        if 'filename' in params:
            filename = params['filename'][0]
        elif parsed.path:
            # 可能是 /api/comfyui/image/filename.png 格式
            filename = parsed.path.rsplit('/', 1)[-1]

    # 如果已经只是文件名（不含目录分隔符），无需复制
    if '/' not in filename and '\\' not in filename:
        # 确保文件在 input 目录中
        src = os.path.join(_COMFYUI_OUTPUT_DIR, filename)
        dst = os.path.join(_COMFYUI_INPUT_DIR, filename)
        if os.path.exists(src):
            # ⭐ 始终用 output 目录的最新文件覆盖 input 目录
            # 避免同名旧文件导致超分/精修使用了错误的图片
            need_copy = not os.path.exists(dst)
            if not need_copy:
                # 文件都存在，比较大小判断是否需要更新
                src_sz = os.path.getsize(src)
                dst_sz = os.path.getsize(dst)
                need_copy = src_sz != dst_sz
            if need_copy:
                try:
                    os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
                    shutil.copy2(src, dst)
                    logger.info(f"[Qwen] 复制/更新图片到 ComfyUI input: {filename}")
                except Exception as e:
                    logger.warning(f"[Qwen] 复制图片失败: {e}")
        elif os.path.exists(dst):
            # input 存在但 output 不存在（可能是上传的文件），直接使用
            pass
        else:
            logger.warning(f"[Qwen] output 目录未找到图片: {src}（将由上游 service 的缓存/HTTP fallback 处理）")
        return filename

    # 如果是完整本地路径，直接提取文件名
    fname = os.path.basename(filename)


_TEMPLATE_DIR = Path(__file__).parent.parent.parent / "workflows" / "templates"


def _resolve_template_asset(template_name: str, asset_type: str) -> str:
    """
    从 workflows/templates/ 加载模板文件并复制到 ComfyUI input 目录。
    
    Args:
        template_name: 如 "T01_双人正面对话"
        asset_type: "mask" / "depth_clean" / "pose"
    
    Returns:
        ComfyUI input 目录下的文件名，或空字符串（文件不存在时）
    """
    if asset_type == "mask":
        tmpl_filename = f"{template_name}_mask.png"
    elif asset_type == "depth_clean":
        tmpl_filename = f"{template_name}_depth_clean.png"
    elif asset_type == "pose":
        tmpl_filename = f"{template_name}_pose.png"
    else:
        return ""

    tmpl_path = _TEMPLATE_DIR / tmpl_filename
    if not tmpl_path.exists():
        logger.warning(f"[WorkflowBuilder][模板] 文件不存在: {tmpl_path}")
        return ""

    dst = os.path.join(_COMFYUI_INPUT_DIR, tmpl_filename)
    try:
        os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
        shutil.copy2(str(tmpl_path), dst)
        logger.info(f"[WorkflowBuilder][模板] 已加载 {asset_type}: {tmpl_filename}")
        return tmpl_filename
    except Exception as e:
        logger.warning(f"[WorkflowBuilder][模板] 复制失败: {e}")
        return ""
    src = os.path.dirname(os.path.abspath(filename))
    dst = os.path.join(_COMFYUI_INPUT_DIR, fname)
    if os.path.exists(os.path.join(src, fname)) and not os.path.exists(dst):
        try:
            os.makedirs(_COMFYUI_INPUT_DIR, exist_ok=True)
            shutil.copy2(os.path.join(src, fname), dst)
            logger.info(f"[Qwen] 复制图片到 ComfyUI input: {fname}")
        except Exception as e:
            logger.warning(f"[Qwen] 复制图片失败: {e}")
    return fname
_REFINE_LORA_STRENGTH = {
    "character": 1.0,   "scene": 0.6,   "prop": 0.4,   "": 1.0,
}
_REFINE_SCALE_LENGTH = {
    "character": 1344,  "scene": 1344,  "prop": 1024,  "": 1344,
}
def _detect_age_in_prompt(prompt: str):
    """
    从提示词中检测年龄信息，返回 (age_group, description)。

    age_group: child/teen/young/adult/elder/unknown
    description: 拼接在触发词中的年龄描述
    """
    # 1. 精确匹配 "X岁"
    m = re.search(r'(\d+)\s*岁', prompt)
    if m:
        age = int(m.group(1))
        if age <= 3:
            return ("child", "婴儿，可爱面孔，")
        elif age <= 10:
            return ("child", "儿童，可爱面容，")
        elif age <= 14:
            return ("teen", "少年，年少面孔，")
        elif age <= 18:
            return ("teen", "青少年，年轻面孔，")
        elif age <= 30:
            return ("young", "青年，年轻成人，")
        elif age <= 50:
            return ("adult", "中年成年人，成熟面容，")
        else:
            return ("elder", "中老年，成熟面孔，")

    # 2. 关键词匹配
    child_kw = ["小孩", "儿童", "宝宝", "婴儿", "萝莉", "正太", "小男孩", "小女孩", "小朋友", "孩童"]
    teen_kw = ["青少年", "少女", "少年", "男生", "女生"]
    adult_kw = ["美女", "帅哥", "男人", "女人", "女士", "先生", "大叔", "阿姨", "成年"]

    for kw in child_kw:
        if kw in prompt:
            return ("child", "儿童，可爱面容，")
    for kw in teen_kw:
        if kw in prompt:
            return ("teen", "青少年，年轻面孔，")
    for kw in adult_kw:
        if kw in prompt:
            return ("young", "青年，年轻成人，")

    return ("unknown", "")
def _get_denoise_sequence() -> List[Tuple[float, str, float]]:
    """返回 Fish 融合步骤序列

    Fish 工作流始终为 1 步融合：
    scheduler=beta57, cfg=1, LoRA=0.25, steps=4, denoise=1
    """
    return [
        (1.0, "primary_char", 1.0),
    ]
def _load_workflow_template(template_name: str) -> Dict[str, Any]:
    """从 workflows/ 目录加载 JSON 模板"""
    wf_dir = Path(__file__).parent.parent.parent / "workflows"
    wf_path = wf_dir / f"{template_name}.json"
    if not wf_path.exists():
        # 回退：依次尝试多个现有模板
        fallback_names = ["精修优化.json", "1人分镜.json", "GPT分镜.json", "最终文生图.json"]
        for fallback_name in fallback_names:
            fallback = wf_dir / fallback_name
            if fallback.exists():
                logger.info(f"[Workflow] 模板 {template_name}.json 不存在，回退到 {fallback_name}")
                with open(fallback, "r", encoding="utf-8") as f:
                    return json.load(f)
        raise FileNotFoundError(f"工作流模板不存在: {wf_path}")
    with open(wf_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _set_ksampler_params(
    wf: Dict[str, Any],
    denoise: float,
    cfg_scale: float,
    seed: int,
    steps: Optional[int] = None,  # ⭐ V1.11: 可选步数覆写（融合步骤需要更高 steps）
    scheduler: Optional[str] = None,  # ⭐ V4.0: 可选 scheduler 覆写（如 "beta57"）
) -> Dict[str, Any]:
    """覆盖 KSampler 节点的 denoise、cfg、seed、steps 和 scheduler"""
    # 节点 ID 因模板而异，查找第一个 KSampler 类型节点
    for node_id, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "KSampler":
            if "denoise" in node.get("inputs", {}):
                old_d = node["inputs"].get("denoise", "?")
                node["inputs"]["denoise"] = denoise
                logger.info(f"[Workflow] KSampler({node_id}) denoise {old_d}→{denoise}")
            if "cfg" in node.get("inputs", {}):
                old_c = node["inputs"].get("cfg", "?")
                node["inputs"]["cfg"] = cfg_scale
                logger.info(f"[Workflow] KSampler({node_id}) cfg {old_c}→{cfg_scale}")
            if "seed" in node.get("inputs", {}):
                old_s = node["inputs"].get("seed", "?")
                if isinstance(old_s, (int, float)) or old_s == "?":
                    node["inputs"]["seed"] = seed
            # ⭐ V1.11: 融合步骤需要更多步数（模板默认4太少了）
            if steps is not None and "steps" in node.get("inputs", {}):
                old_st = node["inputs"].get("steps", "?")
                node["inputs"]["steps"] = steps
                logger.info(f"[Workflow] KSampler({node_id}) steps {old_st}→{steps}")
            # ⭐ V4.0: scheduler 覆写（参考工作流两图/三图融合使用 beta57）
            if scheduler is not None and "scheduler" in node.get("inputs", {}):
                old_sched = node["inputs"].get("scheduler", "?")
                node["inputs"]["scheduler"] = scheduler
                logger.info(f"[Workflow] KSampler({node_id}) scheduler {old_sched}→{scheduler}")
            return wf
    logger.warning("[Workflow] 未找到 KSampler 节点，denoise/cfg/seed 未注入")
    return wf


def _set_reference_image(
    wf: Dict[str, Any],
    node_id: str = "",
    image_path: str = "",
    class_type: str = "LoadImage",
) -> Dict[str, Any]:
    """覆盖 LoadImage 节点的图片路径

    优先使用 class_type 查找节点；如果指定了 node_id 则直接使用（向后兼容）
    """
    resolved = _resolve_comfyui_image(image_path or "")
    if node_id and node_id in wf and "inputs" in wf[node_id]:
        wf[node_id]["inputs"]["image"] = resolved
    else:
        nid, ndata = find_first_node_by_class_type(wf, class_type)
        if nid and ndata and 'image' in ndata.get('inputs', {}):
            wf[nid]['inputs']['image'] = resolved
    return wf


def _set_clip_text(
    wf: Dict[str, Any],
    node_id: str = "",
    text: str = "",
    class_type: str = "",
) -> Dict[str, Any]:
    """覆盖 CLIPTextEncode / QwenImageEditPlusAdvance 节点的文本

    优先使用 class_type 查找节点；如果指定了 node_id 则直接使用（向后兼容）
    ⚠️ CLIP语言兼容：如果底层SDXL CLIP不支持中文，此函数内部需先翻译
    """
    if node_id and node_id in wf and "inputs" in wf[node_id]:
        if "text" in wf[node_id]["inputs"]:
            wf[node_id]["inputs"]["text"] = text
        elif "prompt" in wf[node_id]["inputs"]:
            wf[node_id]["inputs"]["prompt"] = text
    elif class_type:
        nid, ndata = find_first_node_by_class_type_contains(wf, class_type)
        if nid and ndata:
            if "text" in ndata.get('inputs', {}):
                wf[nid]['inputs']['text'] = text
            elif "prompt" in ndata.get('inputs', {}):
                wf[nid]['inputs']['prompt'] = text
    return wf


def _set_filename_prefix(wf: Dict[str, Any], prefix: str) -> Dict[str, Any]:
    """覆盖全部 SaveImage 节点的 filename_prefix"""
    found = 0
    for node_id, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == "SaveImage":
            node["inputs"]["filename_prefix"] = prefix
            found += 1
    if found == 0:
        logger.warning("[Workflow] 未找到 SaveImage 节点")
    return wf
def _detect_fusion_type(
    reference_images: Dict[str, str],
) -> Dict[str, str]:
    """根据 reference_images 检测融合类型，返回图像槽位映射

    ⭐ V5.0: 不再自动生成复杂提示词，Fisher 用用户原始 prompt
    仅返回图像槽位分配信息。

    返回:
        {
            "type": "char_scene" | "char_char" | "char_prop" | "scene_prop",
            "image_1": str (图1槽位文件名, 节点10),
            "image_2": str (图2槽位文件名 = 场景, 节点11),
            "image_3": str (图3槽位文件名, 节点12),
        }
    """
    scene = reference_images.get("scene", "")
    char1 = reference_images.get("character", "")
    char2 = reference_images.get("character2", "")
    prop = reference_images.get("prop", "")

    has_char1 = bool(char1)
    has_char2 = bool(char2)
    has_scene = bool(scene)
    has_prop = bool(prop)

    result = {
        "image_2": scene,  # 图2(节点11) 永远是场景
    }

    if has_char1 and has_char2:
        result["type"] = "char_char"
        result["image_1"] = char1
        result["image_3"] = char2
    elif has_char1 and has_prop:
        result["type"] = "char_prop"
        result["image_1"] = char1
        result["image_3"] = prop
    elif has_char1 and has_scene:
        result["type"] = "char_scene"
        result["image_1"] = char1
        result["image_3"] = scene
    elif has_prop and has_scene:
        result["type"] = "scene_prop"
        result["image_1"] = prop
        result["image_3"] = scene
    else:
        # 兜底：只有场景或只有角色
        result["type"] = "char_scene"
        result["image_1"] = char1 or prop or scene
        result["image_3"] = scene or char1
        result["prompt"] = "让图1的内容融入图2和图3的环境里，保持原有特征不变。"

    return result


def _build_character_fusion_step(
    character_image: str,
    prompt_text: str,
    seed: int,
    filename_prefix: str,
    original_scene: str = "",
    fusion_info: Optional[Dict[str, str]] = None,
    fusion_mode: str = "3img",
    previous_shot_url: str = "",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Dict[str, Any]:
    """构建融合步骤工作流 — 完全复刻 Fish 工作流节点结构

    ⭐ V5.0: 简化为 Fish 融合参数，直接使用用户 prompt：
    - scheduler=beta57, cfg=1, LoRA=0.25, steps=4, denoise=1, sampler=euler_ancestral
    - 不使用 DeepSeek 优化、不拼接位置控制提示词
    - 两图/三图融合参数完全一致，唯一区别：2图模式 image3=场景图

    LoadImage 节点映射（按 ID 排序）：
    - 第1个 = 主要元素（角色/道具）
    - 第2个 = 原始场景（环境参考）
    - 第3个 = 次要元素（角色B/道具/双重场景）
    """
    wf = _load_workflow_template("Fish融合")

    logger.info(
        f"[Workflow] Fish融合({fusion_mode}): "
        f"scheduler=beta57, cfg=1, lora=0.25, steps=4, denoise=1"
    )

    # Fish 模板已内置正确参数，只需覆写 seed 和 filename_prefix
    wf = _set_ksampler_params(wf, denoise=1.0, cfg_scale=1.0, seed=seed,
                               steps=4, scheduler="beta57")

    # 获取 LoadImage 节点（按 ID 排序）
    load_nodes = find_node_by_class_type(wf, 'LoadImage')
    load_nodes.sort(key=lambda x: x[0])
    # 便捷访问
    def _set_img(idx, img_path):
        if idx < len(load_nodes) and img_path:
            wf[load_nodes[idx][0]]['inputs']['image'] = img_path
    def _get_img(idx):
        if idx < len(load_nodes):
            return wf[load_nodes[idx][0]]['inputs'].get('image', '')
        return ''

    # ⭐ 图2(第2个 LoadImage) = 原始场景环境参考（永远不变）
    scene_file = _resolve_comfyui_image(original_scene or "")
    if scene_file:
        _set_img(1, scene_file)
    else:
        base_file = _resolve_comfyui_image(character_image or "")
        if base_file:
            _set_img(1, base_file)

    # 兜底值
    base_file = _resolve_comfyui_image(character_image or "")
    fallback_image = base_file or scene_file or ""

    # ⭐ 两图融合模式 — 图3槽位设为与图2相同
    if fusion_mode == "2img":
        img_10_file = fallback_image
        if fusion_info:
            img_10_file = _resolve_comfyui_image(fusion_info.get("image_1", "") or "") or fallback_image
        # 图1 = 主要元素
        _set_img(0, img_10_file)
        # 图3 = 与图2相同（场景），两图融合不需要第三张独立参考图
        if scene_file:
            _set_img(2, scene_file)
    # ⭐ 迭代模式 — 使用上次融合结果作为图1参考
    elif previous_shot_url:
        prev_file = _resolve_comfyui_image(previous_shot_url)
        _set_img(0, prev_file)
        logger.info(f"[Workflow] 迭代模式: 图1使用上次融合结果")
        if fusion_info:
            img_12 = _resolve_comfyui_image(fusion_info.get("image_3", "") or "")
            if img_12:
                _set_img(2, img_12)
            else:
                _set_img(2, scene_file or fallback_image)
        else:
            _set_img(2, scene_file or fallback_image)
    # ⭐ 三图融合默认路径 — 根据 fusion_info 分配图1 和 图3
    elif fusion_info:
        img_10 = _resolve_comfyui_image(fusion_info.get("image_1", "") or "")
        img_12 = _resolve_comfyui_image(fusion_info.get("image_3", "") or "")
        if img_10:
            _set_img(0, img_10)
        else:
            _set_img(0, fallback_image)
        if img_12:
            _set_img(2, img_12)
        else:
            _set_img(2, scene_file or fallback_image)
    else:
        # 无 fusion_info 时：图1=角色, 图3=场景（经典模式）
        char_file = _resolve_comfyui_image(character_image or "")
        if char_file:
            _set_img(0, char_file)
        else:
            _set_img(0, fallback_image)
        _set_img(2, scene_file or fallback_image)

    # ⭐ V5.0: 直接使用用户 prompt
    nid_enc, enc_data = find_first_node_by_class_type_contains(wf, 'QwenImageEditPlusAdvance')
    if nid_enc and enc_data and 'prompt' in enc_data.get('inputs', {}):
        wf[nid_enc]['inputs']['prompt'] = prompt_text
        logger.info(f"[Workflow] Fish融合prompt (type={fusion_info.get('type','?') if fusion_info else 'legacy'}): {prompt_text[:80]}")

    # ⭐ 两图融合模式：image3 连接设为与 image2 相同（复刻 Fish 两图融合）
    if fusion_mode == "2img" and nid_enc and enc_data:
        if "image3" in enc_data.get('inputs', {}):
            # 找到第2个 LoadImage 节点的 ID，作为 image3 的链接源
            if len(load_nodes) >= 2:
                wf[nid_enc]['inputs']['image3'] = [load_nodes[1][0], 0]

    # ⭐ 尺寸覆写：如果指定了 width/height，覆写 EmptyLatentImage 节点
    if width and height:
        nid_el, el_data = find_first_node_by_class_type(wf, 'EmptyLatentImage')
        if nid_el and el_data:
            wf[nid_el]['inputs']['width'] = width
            wf[nid_el]['inputs']['height'] = height
            logger.info(f"[Workflow] Fish融合尺寸覆写: {width}×{height}")

    wf = _set_filename_prefix(wf, f"{filename_prefix}_{seed}")
    return wf
STORYBOARD_TEMPLATES = [
    "costume_change", "multi_frame", "panorama", "pose_transfer",
    "upscale", "3view", "pose_extraction", "lineart_extraction", "depth_map",
    # ⭐ 4套定制分镜模板
    "single_person", "dual_person", "local_multi", "gpt_storyboard",
]
_EXTRACTION_TEMPLATES = {
    "pose_extraction": "姿态迁移骨骼图.json",
    "lineart_extraction": "lineart_extraction.json",
    "depth_map": "depth_map.json",
    "extract_all": "三个骨架图.json",
}
def get_workflow_node_summary(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """获取工作流节点摘要（用于调试）"""
    summary = {}
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type"):
            summary[node_id] = {
                "class": node["class_type"],
                "title": node.get("_meta", {}).get("title", ""),
            }
    return summary
