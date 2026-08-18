"""
AI 剧本生成阶段

通过 LLM（默认 DeepSeek via openai_compat）生成结构化短剧剧本。
覆盖 6 种视频类型：
  - problem_solving  问题解决型（痛点场景 → 工具3秒解决）
  - efficiency_compare 效率对比型（手动 vs 工具 反差）
  - review_tutorial  测评教程型（分享口吻介绍宝藏工具）
  - fun_drama        趣味剧情型（小剧场，工具当关键道具）
  - full_ai_short    全AI情景短剧（古今穿越/职场反转）
  - image_story      图文叙事型（第一人称求助/吐槽）

输出：script 资产（JSON 文件，包含 acts/scenes/characters/tts_texts/covers 等结构）
下游可对接：concept_stage（按角色描述生成图）→ storyboard → video → tts → edit
"""

import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.provider_service import get_provider_service
from services.stage_service import StageDef, StagePlugin
from services.style_registry import get_style

logger = logging.getLogger(__name__)

# ============================================================
# 6 种视频类型的剧本模板（system prompt 注入）
# ============================================================
_VIDEO_TYPE_TEMPLATES = {
    "problem_solving": {
        "label": "问题解决型",
        "structure": "第一幕：呈现用户痛点场景（30秒内）\n第二幕：工具3秒解决问题（核心爽点）\n第三幕：结尾钩子，引导互动（10秒）",
        "tone": "紧凑、直击痛点，避免冗长铺垫",
    },
    "efficiency_compare": {
        "label": "效率对比型",
        "structure": "左屏：手动操作（耗时、易错、繁琐）\n右屏：工具操作（3秒完成、零失误）\n结尾：对比反差强化，引导转化",
        "tone": "反差强烈，制造爽感",
    },
    "review_tutorial": {
        "label": "测评教程型",
        "structure": "以朋友分享口吻介绍宝藏工具\n分3-5个功能点演示\n每个功能点配场景痛点说明",
        "tone": "真诚、亲切，建立信任感",
    },
    "fun_drama": {
        "label": "趣味剧情型",
        "structure": "小剧场设定（职场/家庭/校园）\n冲突：工具缺失导致的窘境\n解决：工具作为关键道具登场\n结尾钩子：评论区扣1获取",
        "tone": "戏剧化、有反转，避免硬广",
    },
    "full_ai_short": {
        "label": "全AI情景短剧",
        "structure": "古今穿越/职场反转/科幻设定\n三幕结构：设定→冲突→解决\n角色全程AI生成，零真人出镜\n结尾钩子强转化",
        "tone": "剧情吸引，完播率优先",
    },
    "image_story": {
        "label": "图文叙事型",
        "structure": "第一人称求助/吐槽\n痛点放大：时间紧迫、任务重\n工具出场：3秒解决\n情感升华：跪下叫爸爸/想跪",
        "tone": "走心、情绪化，主打共鸣",
    },
}

# 默认 LLM 模型（DeepSeek-Chat）
_DEFAULT_LLM_MODEL = os.getenv("OPENAI_TEXT_MODEL", "deepseek-chat")


class ScriptStage(StagePlugin):
    """AI 剧本生成阶段"""

    stage_def = StageDef(
        stage_id="script",
        name="AI剧本生成",
        input_types=[],  # 纯文本输入，不需要资产
        output_type="script",
        default_provider="openai_compat",
        supported_providers=["openai_compat", "volcengine"],
        description="通过 LLM 生成结构化短剧剧本（6种视频类型可选）",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        start = time.time()
        asset_svc = get_asset_service()
        provider_svc = get_provider_service()

        # ── 参数解析 ──────────────────────────────────────────
        topic = params.get("topic", "").strip()
        if not topic:
            return self._error_result("剧本生成需要 topic 参数（如：批量重命名工具-古今穿越剧）")

        video_type = params.get("video_type", "full_ai_short")
        if video_type not in _VIDEO_TYPE_TEMPLATES:
            return self._error_result(
                f"不支持的 video_type: {video_type}，"
                f"可选: {list(_VIDEO_TYPE_TEMPLATES.keys())}"
            )
        template = _VIDEO_TYPE_TEMPLATES[video_type]

        acts = int(params.get("acts", 3))
        duration_seconds = int(params.get("duration_seconds", 30))
        chars_raw = params.get("characters", []) or []
        characters = [c.strip() for c in chars_raw if isinstance(c, str) and c.strip()]
        tone_extra = params.get("tone_extra", "").strip()
        target_audience = params.get("target_audience", "").strip()
        hook_style = params.get("hook_style", "comment_1")  # comment_1 / main_page / dm
        style_id = params.get("style_id", "")
        # 仅显式指定 style_id 时注入风格（保持 API 向后兼容）
        style = get_style(style_id) if style_id else None
        style_guidance = style.get("script_guidance", "") if style else ""
        model = params.get("model", "")
        temperature = float(params.get("temperature", 0.85))
        max_tokens = int(params.get("max_tokens", 6000))

        # ── 构建 system + user prompt ─────────────────────────
        system_prompt = self._build_system_prompt(
            video_type=video_type,
            template=template,
            acts=acts,
            duration_seconds=duration_seconds,
            characters=characters,
            tone_extra=tone_extra,
            target_audience=target_audience,
            hook_style=hook_style,
            style_guidance=style_guidance,
        )
        user_prompt = self._build_user_prompt(
            topic=topic,
            video_type=video_type,
            template=template,
            acts=acts,
            characters=characters,
            duration_seconds=duration_seconds,
            tone_extra=tone_extra,
            target_audience=target_audience,
        )

        provider_id = self._resolve_provider(provider_id)
        if not model:
            # 火山引擎走 VOLCENGINE_TEXT_MODEL（Endpoint ID），其余走 OPENAI_TEXT_MODEL
            model = (
                os.getenv("VOLCENGINE_TEXT_MODEL", "doubao-seed-2-0-pro")
                if provider_id == "volcengine"
                else _DEFAULT_LLM_MODEL
            )
        logger.info(
            f"[ScriptStage] 生成剧本 | provider={provider_id} | model={model} "
            f"| video_type={video_type} | topic={topic[:50]} | acts={acts}"
        )

        # ── 调用 LLM，要求 JSON 输出 ─────────────────────────
        try:
            result = await provider_svc.generate_text(
                provider_id=provider_id,
                prompt=user_prompt,
                system=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"[ScriptStage] LLM 调用失败: {e}")
            return self._error_result(f"LLM 调用失败: {e}")

        text = (result.metadata or {}).get("text", "") or ""
        if not text:
            # 尝试从 raw 解析
            try:
                text = result.raw["choices"][0]["message"]["content"] or ""
            except Exception:
                pass

        if not text:
            return self._error_result("LLM 返回空内容")

        # ── 解析 JSON 剧本 ────────────────────────────────────
        script_data = self._parse_script_json(text)
        if not script_data:
            # 解析失败也保留原始文本，便于人工修改
            script_data = {
                "title": f"{topic} - 剧本",
                "raw_text": text,
                "parse_error": "无法解析为标准 JSON 剧本结构，已保留原始文本",
            }

        # 补充元信息
        script_data.setdefault("title", f"{topic} - 剧本")
        script_data["meta"] = {
            "video_type": video_type,
            "video_type_label": template["label"],
            "topic": topic,
            "acts": acts,
            "duration_seconds": duration_seconds,
            "characters": characters,
            "tone_extra": tone_extra,
            "target_audience": target_audience,
            "hook_style": hook_style,
            "style_id": style["style_id"] if style else "",
            "style_name": style["name"] if style else "",
            "model": result.model,
            "provider_id": provider_id,
            "generated_at": time.time(),
        }

        # ── 持久化剧本 JSON 到 data/generated/ ─────────────────
        script_url, script_path = await self._save_script_json(script_data)

        elapsed = int((time.time() - start) * 1000)
        new_asset = await self._register_asset_direct(
            asset_svc,
            asset_type="script",
            name=script_data.get("title", topic),
            urls=[script_url],
            input_assets=input_assets,
            extra_metadata={
                "script_url": script_url,
                "script_path": script_path,
                "video_type": video_type,
                "topic": topic,
                "acts": acts,
                "characters": characters,
                "duration_seconds": duration_seconds,
                "model": result.model,
                "provider_id": provider_id,
                "usage": (result.metadata or {}).get("usage", {}),
                "acts_count": len(script_data.get("acts", [])),
                "tts_texts_count": sum(
                    len(act.get("tts_texts", []))
                    for act in script_data.get("acts", [])
                ),
            },
            content_type="",
        )

        logger.info(
            f"[ScriptStage] 剧本生成完成 | id={new_asset.asset_id} | "
            f"acts={len(script_data.get('acts', []))} | elapsed={elapsed}ms"
        )

        return AssetProduceResult(
            asset=new_asset,
            success=True,
            elapsed_ms=elapsed,
        )

    # ──────────────────────────────────────────────────────────
    # Prompt 构建
    # ──────────────────────────────────────────────────────────
    def _build_system_prompt(
        self,
        video_type: str,
        template: Dict[str, str],
        acts: int,
        duration_seconds: int,
        characters: List[str],
        tone_extra: str,
        target_audience: str,
        hook_style: str,
        style_guidance: str = "",
    ) -> str:
        hook_desc = {
            "comment_1": "评论区扣1（强互动，私信发链接）",
            "main_page": "主页引导进粉丝群（沉淀私域）",
            "dm": "私信引导（一对一沟通）",
        }.get(hook_style, hook_style)

        chars_str = "、".join(characters) if characters else "由你自行设计2-3个鲜明角色"
        audience_str = target_audience or "短视频平台泛流量用户"

        style_block = f"\n9. 网感风格：{style_guidance}\n" if style_guidance else ""

        return (
            "你是一位专业短视频剧本编剧，擅长制作能在抖音/B站/小红书爆款的短剧脚本。\n"
            "你的剧本必须满足：\n"
            f"1. 视频类型：{template['label']}\n"
            f"2. 结构要求：\n{template['structure']}\n"
            f"3. 总时长：约 {duration_seconds} 秒\n"
            f"4. 幕数：{acts} 幕\n"
            f"5. 角色：{chars_str}\n"
            f"6. 目标用户：{audience_str}\n"
            f"7. 情感基调：{template['tone']}"
            + (f"，{tone_extra}" if tone_extra else "")
            + "\n"
            f"8. 结尾钩子方式：{hook_desc}"
            + style_block
            + "\n"
            "输出要求：返回严格的 JSON 对象，结构如下：\n"
            "{\n"
            '  "title": "视频标题（吸引眼球，含数字/疑问/反差）",\n'
            '  "video_type": "' + video_type + '",\n'
            '  "hook": "结尾钩子文案",\n'
            '  "characters": [\n'
            '    {"name": "角色名", "desc": "外貌/性格/服饰描述（用于后续AI生图）", "role": "主角/配角/旁白"}\n'
            '  ],\n'
            '  "covers": [\n'
            '    {"title": "封面大字", "subtitle": "副标题", "layout": "top_title/bottom_title/split_compare"}\n'
            '  ],\n'
            '  "acts": [\n'
            '    {\n'
            '      "act": 1,\n'
            '      "scene": "场景描述（用于AI场景生图）",\n'
            '      "narration": "旁白/字幕文本",\n'
            '      "dialogues": [\n'
            '        {"character": "角色名", "line": "台词"}\n'
            '      ],\n'
            '      "tts_texts": ["本幕配音文本1", "本幕配音文本2"],\n'
            '      "duration_seconds": 10\n'
            '    }\n'
            '  ]\n'
            "}\n\n"
            "约束：\n"
            "- 字幕简洁有力，每幕字幕≤30字\n"
            "- 台词口语化，符合角色设定\n"
            "- tts_texts 是直接用于 TTS 配音的连续文本（不要包含舞台说明）\n"
            "- characters[].desc 要详细到足以生成 AI 概念图（含服饰/表情/姿势）\n"
            "- covers 至少给1个，layout 三选一\n"
            "- 不要输出任何 JSON 以外的解释文字"
        )

    def _build_user_prompt(
        self,
        topic: str,
        video_type: str,
        template: Dict[str, str],
        acts: int,
        characters: List[str],
        duration_seconds: int,
        tone_extra: str,
        target_audience: str,
    ) -> str:
        chars_hint = f"\n参考角色：{'、'.join(characters)}" if characters else ""
        return (
            f"请按上述要求为以下主题生成剧本：\n\n"
            f"主题：{topic}\n"
            f"视频类型：{template['label']}\n"
            f"时长目标：{duration_seconds} 秒\n"
            f"幕数：{acts} 幕{chars_hint}\n"
            + (f"\n额外基调要求：{tone_extra}\n" if tone_extra else "")
            + (f"\n目标用户：{target_audience}\n" if target_audience else "")
            + "\n请直接输出 JSON。"
        )

    # ──────────────────────────────────────────────────────────
    # 解析与持久化
    # ──────────────────────────────────────────────────────────
    def _parse_script_json(self, text: str) -> Optional[Dict[str, Any]]:
        """尝试从 LLM 文本中解析出 JSON 剧本"""
        text = text.strip()
        # 去除可能的 ```json ... ``` 包裹
        if text.startswith("```"):
            text = text.split("\n", 1)[-1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError as e:
            logger.warning(f"[ScriptStage] JSON 解析失败: {e} | text 前300字: {text[:300]}")
        # 尝试提取首个 {...} 块
        try:
            first = text.find("{")
            last = text.rfind("}")
            if first >= 0 and last > first:
                data = json.loads(text[first:last + 1])
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return None

    async def _save_script_json(self, script_data: Dict[str, Any]) -> tuple:
        """持久化剧本 JSON 文件，返回 (url, local_path)"""
        from services.providers.provider_utils import output_path_for, output_url_for
        filename = f"script_{uuid.uuid4().hex[:8]}_{int(time.time())}.json"
        path = output_path_for(filename, "output")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        url = output_url_for(filename, "output")
        logger.info(f"[ScriptStage] 剧本已保存 | path={path}")
        return url, path


# ============================================================
# 视频类型字典（供前端 UI 拉取选项）
# ============================================================
def list_video_types() -> List[Dict[str, str]]:
    """返回 6 种视频类型元信息"""
    return [
        {"value": k, "label": v["label"], "structure": v["structure"], "tone": v["tone"]}
        for k, v in _VIDEO_TYPE_TEMPLATES.items()
    ]
