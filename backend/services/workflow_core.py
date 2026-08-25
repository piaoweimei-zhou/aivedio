"""
ComfyUI 工作流构建器 — 主流程工作流构建（build_comfyui_workflow）

从 workflow_core.py 拆分（P2 大文件治理），Qwen 相关构建函数移至
services/workflow_core_qwen.py，此处 re-export 保持 API 兼容。
"""

from services.workflow_helpers import (
    ADDITIONAL_LORAS,
    BASE_WORKFLOW,
    CINEMATIC_WORKFLOW,
    PROP_WORKFLOW,
    YAOGUANG_DEFAULT_NEGATIVE,
    _detect_age_in_prompt,
    find_first_node_by_class_type,
    find_first_node_by_class_type_contains,
    find_node_by_class_type,
)

import copy
import logging
import random
import time
from typing import Any, Dict, Optional

# API 兼容 re-export：供外部 `from services.workflow_core import build_qwen_workflow` 等使用
from services.workflow_core_qwen import (  # noqa: F401
    _build_fallback_workflow,
    build_qwen_workflow,
    build_refinement_workflow,
    build_scene_multiangle_workflow,
    build_standardization_workflow,
    structured_prompt_to_comfyui_prompt,
)

logger = logging.getLogger(__name__)


def build_comfyui_workflow(
    positive_prompt: str,
    negative_prompt: str = "",
    width: Optional[int] = None,  # None=使用工作流模板自带的尺寸
    height: Optional[int] = None,  # None=使用工作流模板自带的尺寸
    seed: Optional[int] = None,
    steps: Optional[int] = None,  # None=使用工作流模板自带的步数
    cfg: Optional[float] = None,  # None=使用工作流模板自带的CFG
    additional_lora: Optional[str] = None,
    reference_image: str = "",
    content_type: str = "",
    workflow: str = "cinematic",  # "standard" | "cinematic"
    **kwargs,
) -> Dict[str, Any]:
    """
    从结构化提示词构建 Z-Image 瑶光版工作流（文生图为主）

    Args:
        positive_prompt: 正向提示词（纯文本）
        negative_prompt: 负向提示词
        width: 图像宽度
        height: 图像高度
        seed: 随机种子（None=自动）
        steps: 采样步数
        cfg: CFG 强度
        additional_lora: 附加 LoRA 名称（可选）
        reference_image: 参考图路径（图生图模式，使用瑶光自带图生图）
        content_type: 内容类型（character/scene/prop/""），驱动 UNet 切换和 LoRA 强度
        workflow: 工作流模板 "standard"（8步/标准）或 "cinematic"（25步/AuraFlow/影视级）

    Returns:
        dict: 完整的 ComfyUI 工作流 JSON
    """
    # 选择工作流模板
    # ⭐ 保存原始 workflow 类型字符串（line 969 后 workflow 变量会被覆盖为 dict）
    workflow_type = workflow
    is_prop_workflow = workflow == "prop" and PROP_WORKFLOW
    if is_prop_workflow:
        wf_template = PROP_WORKFLOW
        has_auraflow = True
    elif workflow == "cinematic" and CINEMATIC_WORKFLOW:
        wf_template = CINEMATIC_WORKFLOW
        has_auraflow = True
    else:
        wf_template = BASE_WORKFLOW if BASE_WORKFLOW else CINEMATIC_WORKFLOW
        has_auraflow = False
    # ⭐ 修复 P2：空工作流字典保护
    # _load_workflow 失败时返回 {}，下游节点索引会 KeyError 导致所有生成请求崩溃
    # 此处显式抛异常，让调用方感知配置问题而非静默失败
    if not wf_template:
        raise RuntimeError(
            f"工作流模板加载失败（空字典）：workflow={workflow_type} | "
            f"请检查 workflows/ 目录下的 JSON 文件是否存在或损坏"
        )
    workflow = copy.deepcopy(wf_template)

    # ⭐ 道具工作流快速路径：仅注入正面提示词和 seed，保持模板其余配置不变
    # 模板中提示词结构：KSampler → CLIPTextEncode(20) → PrimitiveStringMultiline(21)
    # 需要注入到 PrimitiveStringMultiline 的 value 字段，而非 CLIPTextEncode 的 text
    if is_prop_workflow:
        # 注入正面提示词到 PrimitiveStringMultiline 节点
        nid_ps, ps_data = find_first_node_by_class_type(workflow, "PrimitiveStringMultiline")
        if nid_ps and ps_data:
            workflow[nid_ps]["inputs"]["value"] = positive_prompt
            logger.info(
                f"[Workflow] 道具工作流注入正面提示词到节点{nid_ps}: {positive_prompt[:80]}..."
            )
        else:
            # 降级：直接覆写 CLIPTextEncode 的 text
            nid_ks, ks_data = find_first_node_by_class_type(workflow, "KSampler")
            if nid_ks and ks_data:
                pos_clip_id = str(ks_data["inputs"]["positive"][0])
                if pos_clip_id in workflow:
                    workflow[pos_clip_id]["inputs"]["text"] = positive_prompt
            logger.warning(
                "[Workflow] 道具工作流未找到 PrimitiveStringMultiline，降级到 CLIPTextEncode"
            )

        # 注入 seed
        nid_ks, ks_data = find_first_node_by_class_type(workflow, "KSampler")
        if nid_ks and ks_data:
            if seed is not None:
                workflow[nid_ks]["inputs"]["seed"] = seed
            else:
                workflow[nid_ks]["inputs"]["seed"] = int(time.time() * 1000) % (2**63)

        # 设置 filename_prefix
        nid_save, _ = find_first_node_by_class_type(workflow, "SaveImage")
        if nid_save:
            workflow[nid_save]["inputs"]["filename_prefix"] = (
                f"prop_{workflow[nid_ks]['inputs']['seed']}" if nid_ks else "prop"
            )

        # 覆写图像尺寸（仅用户明确指定时，否则保持模板尺寸）
        if width is not None and height is not None:
            nid_empty, empty_data = find_first_node_by_class_type(workflow, "EmptyLatentImage")
            if nid_empty and empty_data:
                workflow[nid_empty]["inputs"]["width"] = width
                workflow[nid_empty]["inputs"]["height"] = height
                logger.info(f"[Workflow] 道具工作流尺寸覆写: {width}×{height}")
        else:
            logger.info("[Workflow] 道具工作流保持模板尺寸不变")

        logger.info(
            f"[Workflow] 道具工作流快速路径完成 | "
            f"seed={workflow[nid_ks]['inputs']['seed'] if nid_ks else '?'} | "
            f"size={width}×{height} | 保持模板LoRA/负向提示词不变"  # noqa: E501
        )
        return workflow

    # ── content_type 驱动的节点覆写 ──────────────────────────

    # UNet 切换 + 模型链管理（兼容 AuraFlow 和标准链）
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, "KSampler")

    # 检测工作流中是否有 ModelSamplingAuraFlow 节点
    nid_aura, _ = (
        find_first_node_by_class_type(workflow, "ModelSamplingAuraFlow")
        if has_auraflow
        else (None, None)
    )

    if content_type == "scene":
        # 场景：找到 moodyPornMix LoRA 节点，将 KSampler 的 model 输入指向它
        nid_lora_scene, _ = find_first_node_by_class_type_contains(workflow, "LoraLoader")
        if nid_lora_scene and nid_ksampler:
            if nid_aura:
                # 有 AuraFlow：将 AuraFlow 的 model 输入指向 LoRA
                workflow[nid_aura]["inputs"]["model"] = [nid_lora_scene, 0]
            else:
                workflow[nid_ksampler]["inputs"]["model"] = [nid_lora_scene, 0]
    elif content_type in ("character", "prop", ""):
        # 角色/道具/默认：找到瑶光 LoRA 节点
        nid_lora_char, _ = find_first_node_by_class_type(workflow, "LoraLoaderModelOnly")
        if nid_lora_char and nid_ksampler:
            if nid_aura:
                # 有 AuraFlow：保持链 KSampler → AuraFlow → LoRA → UNet
                # 只需确保 AuraFlow 的 model 输入指向 LoRA
                workflow[nid_aura]["inputs"]["model"] = [nid_lora_char, 0]
            else:
                # 标准链：KSampler 直接指向 LoRA
                workflow[nid_ksampler]["inputs"]["model"] = [nid_lora_char, 0]

    # LoRA 强度调节：通过 class_type 查找所有 LoRA 节点
    # 注意：LoRA 强度过高会覆盖用户提示词的语义
    # 降低 character 的强度，让基础模型更好地响应用户描述
    _LORA_STRENGTH = {
        "character": {"LoraLoaderModelOnly": 0.6, "LoraLoader": 0.3},
        "scene": {"LoraLoaderModelOnly": 0.1, "LoraLoader": 0.45},
        "prop": {"LoraLoaderModelOnly": 0.0, "LoraLoader": 0.45},
        "": {"LoraLoaderModelOnly": 0.6, "LoraLoader": 0.3},
    }
    lora_cfg = _LORA_STRENGTH.get(content_type, _LORA_STRENGTH[""])
    for lora_type, strength in lora_cfg.items():
        for nid, ndata in find_node_by_class_type(workflow, lora_type):
            if "strength_model" in ndata.get("inputs", {}):
                workflow[nid]["inputs"]["strength_model"] = strength

    # ── 通用节点设置 ──────────────────────────────────────────

    # 1. 设置正向提示词 — 质量 + content_type 触发词 + 用户描述
    _QUALITY_PREFIX = "超高清写实摄影，杰作，最佳质量，8K UHD，raw photo，超高细节，锐利焦点。"

    # 根据用户提示词动态匹配年龄段，返回（正向年龄描述, 负向排除项）
    _age_trigger = _detect_age_in_prompt(positive_prompt)
    _NEGATIVE_AGE_MAP = {
        "child": "adult face, mature, wrinkle, woman, man, beard, ",
        "teen": "child, baby, elder, elderly, ",
        "young": "child, baby, elder, elderly, ",
        "adult": "child, baby, teenager, ",
        "elder": "child, baby, young face, ",
        "unknown": "",
    }
    age_neg = _NEGATIVE_AGE_MAP.get(_age_trigger[0], "")

    _TRIGGER_BY_TYPE = {
        "character": f"全身站立从头到脚完整呈现，中心构图，{_age_trigger[1]}",
        "scene": "广角视角，宏大场景，丰富环境细节，远处有山脉/城市，空无一人，",
        "prop": "纯黑背景，工作室环形灯打光，微距摄影，极度锐利，边缘清晰，单一物体，",
        "": "",
    }
    trigger = _TRIGGER_BY_TYPE.get(content_type, "")

    # ⭐ 修复 P0 #5：提示词语义冲突
    # 原：仅检测 trigger 完整字符串是否在 prompt 中（检测不到"近景"vs"全身站立"反义词）
    # 新：检测 prompt 是否已包含任何构图类关键词，若有则跳过强制构图 trigger
    if trigger and content_type == "character":
        # 用户已显式指定构图/景别时，不强制"全身站立"
        _SHOT_KEYWORDS = (
            "近景",
            "特写",
            "半身",
            "肖像",
            "头像",
            "胸像",
            "面部",
            "close-up",
            "portrait",
            "headshot",
            "bust",
            "face",
            "中景",
            "坐姿",
            "蹲",
            "奔跑",
            "跳跃",
            "动作",
        )
        _prompt_lower = positive_prompt.lower()
        if any(kw in positive_prompt or kw in _prompt_lower for kw in _SHOT_KEYWORDS):
            # 保留年龄触发词，移除构图强制词
            _age_only = _age_trigger[1].rstrip("，")
            trigger = f"{_age_only}，" if _age_only else ""
            logger.info(
                f"[WorkflowBuilder] 检测到景别关键词，跳过强制构图 trigger | prompt={positive_prompt[:40]}"
            )
        elif trigger.rstrip("，") in positive_prompt:
            trigger = ""
    elif trigger and trigger.rstrip("，") in positive_prompt:
        trigger = ""

    # 2. 正负提示词注入 — 通过 KSampler 的连接追踪，避免依赖节点遍历顺序
    _COMMON_NEGATIVE = "close-up shot, portrait, headshot, bust, chest shot, "
    _NEGATIVE_BY_TYPE = {
        "scene": "symmetrical composition, tiled repetition, repetitive pattern, "
        + (negative_prompt or YAOGUANG_DEFAULT_NEGATIVE),
        "character": _COMMON_NEGATIVE + age_neg + (negative_prompt or YAOGUANG_DEFAULT_NEGATIVE),
        "prop": negative_prompt or YAOGUANG_DEFAULT_NEGATIVE,
        "": negative_prompt or YAOGUANG_DEFAULT_NEGATIVE,
    }
    # 从 KSampler 的 positive/negative 输入找到正确的 CLIPTextEncode 节点
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, "KSampler")
    if nid_ksampler and ks_data:
        pos_clip_id = str(ks_data["inputs"]["positive"][0])
        neg_clip_id = str(ks_data["inputs"]["negative"][0])
        if pos_clip_id in workflow:
            workflow[pos_clip_id]["inputs"]["text"] = f"{_QUALITY_PREFIX}{trigger}{positive_prompt}"
        if neg_clip_id in workflow:
            workflow[neg_clip_id]["inputs"]["text"] = _NEGATIVE_BY_TYPE.get(
                content_type, negative_prompt or YAOGUANG_DEFAULT_NEGATIVE
            )
    else:
        # 降级：按遍历顺序（兼容旧工作流）
        nid_clip, clip_data = find_first_node_by_class_type(workflow, "CLIPTextEncode")
        if nid_clip and clip_data:
            workflow[nid_clip]["inputs"]["text"] = f"{_QUALITY_PREFIX}{trigger}{positive_prompt}"
        clip_nodes = find_node_by_class_type(workflow, "CLIPTextEncode")
        if len(clip_nodes) >= 2:
            nid_neg = clip_nodes[1][0]
            workflow[nid_neg]["inputs"]["text"] = _NEGATIVE_BY_TYPE.get(
                content_type, negative_prompt or YAOGUANG_DEFAULT_NEGATIVE
            )

    # 3. 设置图像尺寸（EmptyLatentImage 节点）
    # ⚠️ 仅在用户明确指定 width/height 时覆写，否则保持工作流模板默认值
    # （修复 C1：原代码无 None 检查，会写入 None 破坏工作流）
    if width is not None and height is not None:
        nid_empty, empty_data = find_first_node_by_class_type(workflow, "EmptyLatentImage")
        if nid_empty and empty_data:
            workflow[nid_empty]["inputs"]["width"] = width
            workflow[nid_empty]["inputs"]["height"] = height
            logger.info(f"[Workflow] cinematic 工作流尺寸覆写: {width}×{height}")
        else:
            logger.warning("[Workflow] 未找到 EmptyLatentImage 节点，无法覆写尺寸")
    else:
        logger.info("[Workflow] cinematic 工作流保持模板尺寸不变")

    # 4. 设置种子 — 按 content_type 策略
    nid_ksampler, ks_data = find_first_node_by_class_type(workflow, "KSampler")
    if nid_ksampler and ks_data:
        if seed is not None:
            workflow[nid_ksampler]["inputs"]["seed"] = seed
        elif content_type == "scene":
            workflow[nid_ksampler]["inputs"]["seed"] = random.randint(0, 2**63 - 1)
        else:
            workflow[nid_ksampler]["inputs"]["seed"] = int(time.time() * 1000) % (2**63)
        if steps is not None:
            workflow[nid_ksampler]["inputs"]["steps"] = steps
        if cfg is not None:
            workflow[nid_ksampler]["inputs"]["cfg"] = cfg

    # 6. 图生图模式（参考图，Qwen-Image-2.5 / Z-Image 图生图）
    if reference_image:
        logger.info(f"[Workflow] Z-Image图生图模式，参考图: {reference_image}")
        # 动态查找 VAELoader 节点 ID
        nid_vae, _ = find_first_node_by_class_type(workflow, "VAELoader")
        vae_ref = nid_vae if nid_vae else "11"  # 降级兼容旧工作流
        # 动态查找 KSampler 节点 ID（复用前面已查找的结果）
        nid_ksampler_img, _ = find_first_node_by_class_type(workflow, "KSampler")
        ksampler_ref = nid_ksampler_img if nid_ksampler_img else "22"
        # 添加 LoadImage 节点（节点99）
        workflow["99"] = {
            "inputs": {"image": reference_image},
            "class_type": "LoadImage",
            "_meta": {"title": "参考图"},
        }
        # 添加 VAEEncode 节点（节点98），将 LoadImage 输出编码为 latent
        workflow["98"] = {
            "inputs": {
                "pixels": ["99", 0],
                "vae": [vae_ref, 0],
            },
            "class_type": "VAEEncode",
            "_meta": {"title": "VAE编码（参考图）"},
        }
        # 将 KSampler 的 latent_image 输入改为 VAEEncode 输出，而非 EmptyLatentImage
        workflow[ksampler_ref]["inputs"]["latent_image"] = ["98", 0]
        # 降低 denoise（图生图模式）
        workflow[ksampler_ref]["inputs"]["denoise"] = 0.85
        workflow["reference_image"] = reference_image
        logger.info("[Workflow] 图生图模式已激活（LoadImage→VAEEncode→KSampler, denoise=0.85）")

    # 7. 附加 LoRA（可选）——注入当前为空实现（保留占位），见历史 P0 修复
    if additional_lora and additional_lora in ADDITIONAL_LORAS:
        pass

    # ⭐ 修复 P1：ParamInjector 兜底注入
    # 手写注入逻辑可能遗漏参数，此处用 ParamInjector 做二次校验
    # 仅对已注册 schema 的 workflow 类型生效；prop 路径节点结构不同，跳过
    try:
        from services.workflow_params import inject_workflow_params

        schema_map = {
            "cinematic": "文生图影视级",
            "standard": "文生图影视级",  # standard 复用同 schema（节点结构相似）
        }
        schema_name = schema_map.get(workflow_type) if not is_prop_workflow else None
        if schema_name:
            # ⭐ 修复 P0 #5：ParamInjector 兜底时排除 prompt/negative
            # 这两个字段已由上方手写注入处理（含 _QUALITY_PREFIX + trigger + 用户描述）
            # 若此处再用原始 positive_prompt 覆盖，会丢失质量前缀和触发词
            user_params = {
                "width": width,
                "height": height,
                "steps": steps,
                "cfg": cfg,
                "seed": seed,
            }
            # 过滤 None 值（避免覆盖工作流模板默认值）
            user_params = {k: v for k, v in user_params.items() if v is not None}
            _, injected = inject_workflow_params(schema_name, workflow, user_params)
            if injected:
                logger.info(
                    f"[Workflow] ParamInjector 兜底注入 ({schema_name}): {list(injected.keys())}"
                )
    except Exception as pie:
        # 兜底失败不影响主流程（手写注入已完成核心参数）
        logger.warning(f"[Workflow] ParamInjector 兜底失败（不影响主流程）: {pie}")

    return workflow
