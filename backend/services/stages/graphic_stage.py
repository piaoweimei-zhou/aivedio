"""
图文生成阶段

通过 LLM 生成结构化内容 + Pillow 渲染为 PNG 图文卡片。
覆盖 6 种图文类型：
  - infographic   信息图（标题 + 多信息块）
  - comparison    对比图（左右两栏对比）
  - tutorial      教程图（步骤式）
  - checklist     清单图（列表式）
  - quote         金句图（大字金句卡片）
  - data_chart    数据图（条形数据图）

输出：image 资产（PNG）
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

logger = logging.getLogger(__name__)

# ============================================================
# 6 种图文类型模板
# ============================================================
_GRAPHIC_TYPE_TEMPLATES = {
    "infographic": {
        "label": "信息图",
        "desc": "标题 + 多个信息块（图标/标题/正文）",
        "json_schema": '{"title":"...","subtitle":"...","sections":[{"heading":"...","body":"..."}]}',  # noqa: E501
    },
    "comparison": {
        "label": "对比图",
        "desc": "左右两栏对比，突出差异",
        "json_schema": '{"title":"...","left_label":"...","left_items":["..."],"right_label":"...","right_items":["..."]}',  # noqa: E501
    },
    "tutorial": {
        "label": "教程图",
        "desc": "步骤式教程（序号+标题+描述，可选箭头标注）",
        "json_schema": '{"title":"...","steps":[{"title":"...","description":"...","arrow_hint":"可选，例如：点击右上角按钮"}]}',  # noqa: E501
    },
    "checklist": {
        "label": "清单图",
        "desc": "清单/列表式",
        "json_schema": '{"title":"...","items":["..."]}',
    },
    "quote": {
        "label": "金句图",
        "desc": "大字金句卡片 + 作者/来源",
        "json_schema": '{"quote":"...","author":"...","source":"..."}',
    },
    "data_chart": {
        "label": "数据图",
        "desc": "条形数据图（标签+数值）",
        "json_schema": '{"title":"...","unit":"...","bars":[{"label":"...","value":100}]}',
    },
    "video_cover": {
        "label": "视频封面图",
        "desc": "标题大字 + 对比画面（吸引点击）",
        "json_schema": '{"title":"...","subtitle":"...","left_label":"...","right_label":"...","hook":"..."}',  # noqa: E501
    },
    "emotional_scene": {
        "label": "情感共鸣图",
        "desc": "情绪场景图（深夜工位/崩溃表情等共鸣画面）",
        "json_schema": '{"title":"...","scene_desc":"...","emotion":"...","caption":"..."}',
    },
}

_DEFAULT_LLM_MODEL = os.getenv("OPENAI_TEXT_MODEL", "deepseek-chat")

# 中文字体路径（Windows）
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",  # 微软雅黑粗体
    "C:/Windows/Fonts/simhei.ttf",  # 黑体
    "C:/Windows/Fonts/simsun.ttc",  # 宋体
    "/System/Library/Fonts/PingFang.ttc",  # macOS
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
]


def _find_font(bold: bool = False) -> str:
    """查找可用中文字体"""
    candidates = _FONT_CANDIDATES
    if bold:
        bold_first = [
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
        candidates = bold_first + [f for f in _FONT_CANDIDATES if f not in bold_first]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


class GraphicStage(StagePlugin):
    """图文生成阶段"""

    stage_def = StageDef(
        stage_id="graphic",
        name="图文生成",
        input_types=[],  # 纯参数输入，不需要资产
        output_type="image",
        default_provider="openai_compat",
        supported_providers=["openai_compat"],
        description="通过 LLM 生成结构化内容 + Pillow 渲染为 6 种图文卡片",
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

        graphic_type = params.get("graphic_type", "infographic")
        topic = params.get("topic", "").strip()
        title = params.get("title", "").strip()
        style = params.get("style", "modern")  # modern/minimal/warm/tech
        model = params.get("model", _DEFAULT_LLM_MODEL)
        temperature = float(params.get("temperature", 0.7))
        max_tokens = int(params.get("max_tokens", 2048))
        width = int(params.get("width", 1080))
        height = int(params.get("height", 1350))  # 4:5 竖版卡片

        template = _GRAPHIC_TYPE_TEMPLATES.get(graphic_type)
        if not template:
            return self._error(f"不支持的图文类型: {graphic_type}")

        if not topic:
            topic = title or "请生成一张关于[主题]的图文卡片"

        provider_id = self._resolve_provider(provider_id)
        logger.info(
            f"[GraphicStage] 生成图文 | type={graphic_type} | provider={provider_id} "
            f"| model={model} | topic={topic[:50]} | size={width}x{height}"
        )

        # ── 调用 LLM 生成结构化内容 ───────────────────────────
        try:
            system_prompt = self._build_system_prompt(graphic_type, template)
            user_prompt = self._build_user_prompt(graphic_type, topic, title, params)
            result = await get_provider_service().generate_text(
                provider_id=provider_id,
                prompt=user_prompt,
                system=system_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error(f"[GraphicStage] LLM 调用失败: {e}")
            return self._error(f"LLM 调用失败: {e}")

        text = (result.metadata or {}).get("text", "") or ""
        if not text:
            try:
                text = result.raw["choices"][0]["message"]["content"] or ""
            except Exception:
                pass
        if not text:
            return self._error("LLM 返回空内容")

        # ── 解析 JSON 内容 ────────────────────────────────────
        content = self._parse_json(text)
        if not content:
            content = {
                "title": title or topic,
                "raw_text": text,
                "parse_error": "无法解析为 JSON，已保留原始文本",
            }

        # ── 渲染为 PNG ────────────────────────────────────────
        try:
            image_url = await self._render_graphic(graphic_type, content, style, width, height)
        except Exception as e:
            logger.error(f"[GraphicStage] 渲染失败: {e}")
            return self._error(f"图片渲染失败: {e}")

        if not image_url:
            return self._error("渲染未生成图片")

        # ── 创建资产 ──────────────────────────────────────────
        asset_title = content.get("title", title or topic)
        new_asset = await self._register_asset_direct(
            asset_svc,
            asset_type="image",
            name=f"{asset_title} - {template['label']}",
            urls=[image_url],
            input_assets=input_assets,
            extra_metadata={
                "graphic_type": graphic_type,
                "graphic_type_label": template["label"],
                "topic": topic,
                "style": style,
                "width": width,
                "height": height,
                "image_url": image_url,
                "content": content,
                "model": result.model,
                "provider_id": provider_id,
                "generated_at": time.time(),
                "elapsed_ms": int((time.time() - start) * 1000),
            },
        )

        logger.info(
            f"[GraphicStage] 完成 | type={graphic_type} | asset={new_asset.asset_id} "
            f"| elapsed={int((time.time() - start) * 1000)}ms"
        )

        return AssetProduceResult(asset=new_asset, success=True)

    # ============================================================
    # LLM Prompt 构建
    # ============================================================
    def _build_system_prompt(self, graphic_type: str, template: Dict) -> str:
        return (
            f"你是一位专业的{template['label']}设计师。"
            f"根据用户给定的主题，生成结构化的图文卡片内容。\n"
            f"图文类型说明：{template['desc']}\n"
            f"输出要求：\n"
            f"1. 严格输出 JSON 格式，不要输出 markdown 代码块标记\n"
            f"2. 内容简洁有力，适合手机竖屏阅读\n"
            f"3. 标题控制在20字以内，正文每条控制在50字以内\n"
            f"4. 数据要具体、有说服力\n"
            f"5. JSON 结构如下：{template['json_schema']}\n"
        )

    def _build_user_prompt(self, graphic_type: str, topic: str, title: str, params: Dict) -> str:
        parts = [f"主题：{topic}"]
        if title:
            parts.append(f"标题（可参考）：{title}")
        extra = params.get("extra_instructions", "")
        if extra:
            parts.append(f"额外要求：{extra}")

        if graphic_type == "infographic":
            parts.append("请生成4-6个信息块，每个包含标题和正文")
        elif graphic_type == "comparison":
            parts.append("请生成左右各3-5条对比项，突出差异")
        elif graphic_type == "tutorial":
            parts.append("请生成4-6个步骤，每个包含标题和描述")
        elif graphic_type == "checklist":
            parts.append("请生成5-8条清单项")
        elif graphic_type == "quote":
            parts.append("金句控制在30字以内，要有冲击力")
        elif graphic_type == "data_chart":
            parts.append("请生成4-6条数据，数值为正整数")
        elif graphic_type == "video_cover":
            parts.append("标题大字控制在8字以内，左右对比标签各3-5字，结尾钩子10字以内")
        elif graphic_type == "emotional_scene":
            parts.append(
                "场景描述用于 AI 绘图参考（含光线/氛围/主体），情绪标签1词，配文20字以内走心"
            )

        return "\n".join(parts)

    # ============================================================
    # JSON 解析（带容错）
    # ============================================================
    def _parse_json(self, text: str) -> Optional[Dict]:
        text = text.strip()
        # 去除 markdown 代码块标记
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 尝试提取第一个 JSON 块
        import re

        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None

    # ============================================================
    # Pillow 渲染
    # ============================================================
    async def _render_graphic(
        self,
        graphic_type: str,
        content: Dict,
        style: str,
        width: int,
        height: int,
    ) -> str:
        """渲染图文为 PNG，返回 URL"""
        from PIL import Image, ImageDraw, ImageFont

        # 配色方案
        palette = self._get_palette(style)

        # 创建画布
        img = Image.new("RGB", (width, height), palette["bg"])
        draw = ImageDraw.Draw(img)

        # 加载字体
        font_path = _find_font()
        font_bold = _find_font(bold=True) or font_path
        f_title = (
            ImageFont.truetype(font_bold or font_path, 48)
            if font_path
            else ImageFont.load_default()
        )  # noqa: E501
        f_subtitle = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()
        f_body = ImageFont.truetype(font_path, 26) if font_path else ImageFont.load_default()
        f_small = ImageFont.truetype(font_path, 22) if font_path else ImageFont.load_default()
        f_quote = (
            ImageFont.truetype(font_bold or font_path, 52)
            if font_path
            else ImageFont.load_default()
        )  # noqa: E501
        f_number = (
            ImageFont.truetype(font_bold or font_path, 36)
            if font_path
            else ImageFont.load_default()
        )  # noqa: E501

        margin = 60
        y = margin

        # 顶部装饰条
        draw.rectangle([0, 0, width, 8], fill=palette["accent"])

        # 按类型渲染
        if graphic_type == "infographic":
            y = self._render_infographic(
                draw, content, palette, f_title, f_subtitle, f_body, f_small, width, margin, y
            )  # noqa: E501
        elif graphic_type == "comparison":
            y = self._render_comparison(
                draw,
                content,
                palette,
                f_title,
                f_subtitle,
                f_body,
                f_small,
                width,
                height,
                margin,
                y,
            )  # noqa: E501
        elif graphic_type == "tutorial":
            y = self._render_tutorial(
                draw, content, palette, f_title, f_body, f_small, f_number, width, margin, y
            )  # noqa: E501
        elif graphic_type == "checklist":
            y = self._render_checklist(
                draw, content, palette, f_title, f_body, f_small, width, margin, y
            )  # noqa: E501
        elif graphic_type == "quote":
            y = self._render_quote(
                draw, content, palette, f_quote, f_body, width, height, margin, y
            )  # noqa: E501
        elif graphic_type == "data_chart":
            y = self._render_data_chart(
                draw, content, palette, f_title, f_body, f_small, width, margin, y
            )  # noqa: E501
        elif graphic_type == "video_cover":
            y = self._render_video_cover(
                draw,
                content,
                palette,
                f_title,
                f_subtitle,
                f_body,
                f_small,
                f_quote,
                width,
                height,
                margin,
                y,
            )  # noqa: E501
        elif graphic_type == "emotional_scene":
            y = self._render_emotional_scene(
                draw, content, palette, f_title, f_body, f_small, f_quote, width, height, margin, y
            )  # noqa: E501

        # 底部水印
        watermark = "AI 导演工作台"
        bbox = draw.textbbox((0, 0), watermark, font=f_small)
        draw.text(
            (width - bbox[2] - margin, height - 40),
            watermark,
            font=f_small,
            fill=palette["muted"],
        )

        # 保存
        from services.providers.provider_utils import output_path_for, output_url_for

        filename = f"graphic_{graphic_type}_{uuid.uuid4().hex[:8]}.png"
        path = output_path_for(filename, "graphic")
        img.save(path, "PNG", optimize=True)

        return output_url_for(filename, "graphic")

    def _get_palette(self, style: str) -> Dict[str, str]:
        """获取配色方案"""
        palettes = {
            "modern": {
                "bg": "#FFFFFF",
                "accent": "#1890FF",
                "text": "#262626",
                "muted": "#BFBFBF",
                "card": "#F5F7FA",
                "card_border": "#E8E8E8",
            },  # noqa: E501
            "minimal": {
                "bg": "#FAFAFA",
                "accent": "#595959",
                "text": "#262626",
                "muted": "#BFBFBF",
                "card": "#F0F0F0",
                "card_border": "#D9D9D9",
            },  # noqa: E501
            "warm": {
                "bg": "#FFF8F0",
                "accent": "#FA8C16",
                "text": "#5C3D2E",
                "muted": "#D4B896",
                "card": "#FFF0E0",
                "card_border": "#F5D5B0",
            },  # noqa: E501
            "tech": {
                "bg": "#0D1117",
                "accent": "#58A6FF",
                "text": "#C9D1D9",
                "muted": "#484F58",
                "card": "#161B22",
                "card_border": "#30363D",
            },  # noqa: E501
        }
        return palettes.get(style, palettes["modern"])

    def _text_width(self, draw, text: str, font) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0]

    def _wrap_text(self, text: str, max_chars: int) -> List[str]:
        """中文文本按字数换行"""
        lines = []
        current = ""
        for ch in text:
            current += ch
            if len(current) >= max_chars:
                lines.append(current)
                current = ""
        if current:
            lines.append(current)
        return lines

    def _draw_wrapped_text(self, draw, xy, text, font, fill, max_chars, line_gap=8):
        """绘制自动换行文本，返回结束 y"""
        x, y = xy
        for line in self._wrap_text(text, max_chars):
            draw.text((x, y), line, font=font, fill=fill)
            y += font.size + line_gap
        return y

    # ── 信息图 ────────────────────────────────────────────────
    def _render_infographic(
        self, draw, content, palette, f_title, f_subtitle, f_body, f_small, width, margin, y
    ):  # noqa: E501
        title = content.get("title", "信息图")
        subtitle = content.get("subtitle", "")
        sections = content.get("sections", [])

        # 标题
        draw.text((margin, y), title, font=f_title, fill=palette["accent"])
        y += 70
        if subtitle:
            draw.text((margin, y), subtitle, font=f_subtitle, fill=palette["muted"])
            y += 45
        y += 20

        # 信息块
        card_w = width - margin * 2
        for sec in sections:
            heading = sec.get("heading", "")
            body = sec.get("body", "")
            block_h = 40 + 36 + (len(body) // 18 + 1) * 34 + 20

            # 卡片背景
            draw.rounded_rectangle(
                [margin, y, margin + card_w, y + block_h],
                radius=12,
                fill=palette["card"],
                outline=palette["card_border"],
                width=1,
            )
            # 左侧色条
            draw.rectangle([margin, y, margin + 6, y + block_h], fill=palette["accent"])

            inner_y = y + 20
            draw.text((margin + 24, inner_y), heading, font=f_subtitle, fill=palette["accent"])
            inner_y += 40
            inner_y = self._draw_wrapped_text(
                draw, (margin + 24, inner_y), body, f_body, palette["text"], 22
            )

            y += block_h + 16
        return y

    # ── 对比图 ────────────────────────────────────────────────
    def _render_comparison(
        self, draw, content, palette, f_title, f_subtitle, f_body, f_small, width, height, margin, y
    ):  # noqa: E501
        title = content.get("title", "对比图")
        left_label = content.get("left_label", "方案A")
        left_items = content.get("left_items", [])
        right_label = content.get("right_label", "方案B")
        right_items = content.get("right_items", [])

        draw.text((margin, y), title, font=f_title, fill=palette["accent"])
        y += 75

        col_w = (width - margin * 3) // 2
        left_x = margin
        right_x = margin * 2 + col_w

        # 标签
        draw.text((left_x, y), left_label, font=f_subtitle, fill=palette["text"])
        draw.text((right_x, y), right_label, font=f_subtitle, fill=palette["accent"])

        y += 50

        # 左右卡片
        card_h = max(len(left_items), len(right_items)) * 50 + 40
        draw.rounded_rectangle(
            [left_x, y, left_x + col_w, y + card_h],
            radius=12,
            fill=palette["card"],
            outline=palette["card_border"],
            width=1,
        )  # noqa: E501
        draw.rounded_rectangle(
            [right_x, y, right_x + col_w, y + card_h],
            radius=12,
            fill=palette["card"],
            outline=palette["accent"],
            width=2,
        )  # noqa: E501

        ly = y + 20
        for item in left_items:
            draw.ellipse([left_x + 16, ly + 8, left_x + 24, ly + 16], fill=palette["muted"])
            ly = self._draw_wrapped_text(
                draw, (left_x + 36, ly), str(item), f_body, palette["text"], 14
            )  # noqa: E501
            ly += 12

        ry = y + 20
        for item in right_items:
            draw.ellipse([right_x + 16, ry + 8, right_x + 24, ry + 16], fill=palette["accent"])
            ry = self._draw_wrapped_text(
                draw, (right_x + 36, ry), str(item), f_body, palette["accent"], 14
            )  # noqa: E501
            ry += 12

        y += card_h + 20
        return y

    # ── 教程图 ────────────────────────────────────────────────
    def _render_tutorial(
        self, draw, content, palette, f_title, f_body, f_small, f_number, width, margin, y
    ):  # noqa: E501
        title = content.get("title", "教程图")
        steps = content.get("steps", [])

        draw.text((margin, y), title, font=f_title, fill=palette["accent"])
        y += 75

        for i, step in enumerate(steps):
            step_title = step.get("title", f"步骤{i+1}")
            desc = step.get("description", "")
            arrow_hint = step.get("arrow_hint", "").strip()

            # 序号圆圈
            circle_r = 28
            cx = margin + circle_r
            cy = y + circle_r
            draw.ellipse(
                [cx - circle_r, cy - circle_r, cx + circle_r, cy + circle_r], fill=palette["accent"]
            )  # noqa: E501
            num_text = str(i + 1)
            bbox = draw.textbbox((0, 0), num_text, font=f_number)
            draw.text(
                (cx - (bbox[2] - bbox[0]) // 2, cy - (bbox[3] - bbox[1]) // 2 - 4),
                num_text,
                font=f_number,
                fill="#FFFFFF",
            )  # noqa: E501

            # 步骤内容
            text_x = margin + circle_r * 2 + 24
            draw.text((text_x, y + 4), step_title, font=f_body, fill=palette["accent"])
            y += 40
            y = self._draw_wrapped_text(draw, (text_x, y), desc, f_small, palette["text"], 20)

            # 箭头标注（若提供）
            if arrow_hint:
                hint_y = y + 4
                # 箭头符号 →
                draw.text((text_x, hint_y), "\u2192", font=f_body, fill=palette["accent"])
                # 标注文案
                hint_bg_x = text_x + 40
                hint_w = self._text_width(draw, arrow_hint, f_small) + 24
                draw.rounded_rectangle(
                    [hint_bg_x, hint_y - 4, hint_bg_x + hint_w, hint_y + f_small.size + 14],
                    radius=8,
                    fill=palette["accent"],
                )
                draw.text((hint_bg_x + 12, hint_y + 4), arrow_hint, font=f_small, fill="#FFFFFF")
                y = hint_y + f_small.size + 24
            else:
                y += 30

        return y

    # ── 清单图 ────────────────────────────────────────────────
    def _render_checklist(self, draw, content, palette, f_title, f_body, f_small, width, margin, y):
        title = content.get("title", "清单图")
        items = content.get("items", [])

        draw.text((margin, y), title, font=f_title, fill=palette["accent"])
        y += 75

        card_w = width - margin * 2
        card_h = len(items) * 48 + 30
        draw.rounded_rectangle(
            [margin, y, margin + card_w, y + card_h],
            radius=12,
            fill=palette["card"],
            outline=palette["card_border"],
            width=1,
        )  # noqa: E501

        iy = y + 20
        for item in items:
            # 勾选框
            box_size = 22
            draw.rounded_rectangle(
                [margin + 16, iy, margin + 16 + box_size, iy + box_size],
                radius=4,
                outline=palette["accent"],
                width=2,
            )  # noqa: E501
            draw.text((margin + 20, iy - 2), "✓", font=f_small, fill=palette["accent"])
            # 文本
            self._draw_wrapped_text(draw, (margin + 56, iy), str(item), f_body, palette["text"], 22)
            iy += 48

        y += card_h + 20
        return y

    # ── 金句图 ────────────────────────────────────────────────
    def _render_quote(self, draw, content, palette, f_quote, f_body, width, height, margin, y):
        quote = content.get("quote", "")
        author = content.get("author", "")
        source = content.get("source", "")

        # 居中显示大引号
        draw.text((margin, y + 20), "\u201c", font=f_quote, fill=palette["accent"])
        y += 100

        # 金句（居中换行）
        lines = self._wrap_text(quote, 12)
        for line in lines:
            tw = self._text_width(draw, line, f_quote)
            draw.text(((width - tw) // 2, y), line, font=f_quote, fill=palette["text"])
            y += 65

        y += 40
        # 作者
        if author:
            author_text = f"— {author}"
            tw = self._text_width(draw, author_text, f_body)
            draw.text(((width - tw) // 2, y), author_text, font=f_body, fill=palette["muted"])
            y += 40
        if source:
            tw = self._text_width(draw, source, f_body)
            draw.text(((width - tw) // 2, y), source, font=f_body, fill=palette["muted"])

        return height - margin

    # ── 数据图 ────────────────────────────────────────────────
    def _render_data_chart(
        self, draw, content, palette, f_title, f_body, f_small, width, margin, y
    ):  # noqa: E501
        title = content.get("title", "数据图")
        unit = content.get("unit", "")
        bars = content.get("bars", [])

        draw.text((margin, y), title, font=f_title, fill=palette["accent"])
        y += 75

        if not bars:
            return y

        max_val = max((b.get("value", 0) for b in bars), default=1)
        chart_w = width - margin * 2 - 200
        bar_h = 36
        gap = 20

        for bar in bars:
            label = str(bar.get("label", ""))
            value = bar.get("value", 0)

            # 标签
            draw.text((margin, y), label, font=f_body, fill=palette["text"])
            y += 42

            # 条形图背景
            draw.rounded_rectangle(
                [margin, y, margin + chart_w + 160, y + bar_h], radius=6, fill=palette["card"]
            )  # noqa: E501
            # 条形图前景
            bar_w = int(chart_w * (value / max_val)) if max_val > 0 else 0
            if bar_w > 0:
                draw.rounded_rectangle(
                    [margin, y, margin + bar_w, y + bar_h], radius=6, fill=palette["accent"]
                )  # noqa: E501

            # 数值
            val_text = f"{value}{unit}"
            draw.text(
                (margin + chart_w + 170, y + 4), val_text, font=f_body, fill=palette["accent"]
            )  # noqa: E501
            y += bar_h + gap

        return y

    # ── 视频封面图（标题大字 + 对比画面） ─────────────────────
    def _render_video_cover(
        self,
        draw,
        content,
        palette,
        f_title,
        f_subtitle,
        f_body,
        f_small,
        f_quote,
        width,
        height,
        margin,
        y,
    ):
        title = content.get("title", "")
        subtitle = content.get("subtitle", "")
        left_label = content.get("left_label", "前")
        right_label = content.get("right_label", "后")
        hook = content.get("hook", "")

        # 顶部大标题（冲击力）
        if title:
            # 标题若超长则换行
            lines = self._wrap_text(title, 6)
            for line in lines:
                tw = self._text_width(draw, line, f_quote)
                draw.text(((width - tw) // 2, y), line, font=f_quote, fill=palette["accent"])
                y += f_quote.size + 8

        if subtitle:
            sw = self._text_width(draw, subtitle, f_subtitle)
            draw.text(((width - sw) // 2, y + 4), subtitle, font=f_subtitle, fill=palette["muted"])
            y += 50

        y += 30

        # 中部对比画面占位（左右两块，背景色区分）
        box_h = 380
        gap_w = 20
        box_w = (width - margin * 2 - gap_w) // 2

        # 左侧"前"（暗色）
        left_box_y = y
        draw.rounded_rectangle(
            [margin, left_box_y, margin + box_w, left_box_y + box_h],
            radius=12,
            fill=palette["card"],
            outline=palette["card_border"],
            width=2,
        )
        # 左侧标签
        lw = self._text_width(draw, left_label, f_title)
        draw.text(
            ((margin + box_w // 2 - lw // 2), left_box_y + box_h // 2 - 24),
            left_label,
            font=f_title,
            fill=palette["muted"],
        )
        # 左侧大 ×
        draw.text(
            (margin + box_w // 2 - 24, left_box_y + box_h - 80),
            "\u00d7",
            font=f_quote,
            fill="#FF4D4F",
        )

        # 右侧"后"（亮色）
        right_x = margin + box_w + gap_w
        draw.rounded_rectangle(
            [right_x, left_box_y, right_x + box_w, left_box_y + box_h],
            radius=12,
            fill=palette["accent"],
            outline=palette["accent"],
            width=2,
        )
        # 右侧标签
        rw = self._text_width(draw, right_label, f_title)
        draw.text(
            (right_x + box_w // 2 - rw // 2, left_box_y + box_h // 2 - 24),
            right_label,
            font=f_title,
            fill="#FFFFFF",
        )
        # 右侧大 ✓
        draw.text(
            (right_x + box_w // 2 - 20, left_box_y + box_h - 80),
            "\u2713",
            font=f_quote,
            fill="#FFFFFF",
        )

        y = left_box_y + box_h + 30

        # 底部钩子
        if hook:
            # 背景
            hook_lines = self._wrap_text(hook, 14)
            block_h = 30 + len(hook_lines) * (f_body.size + 6) + 20
            draw.rounded_rectangle(
                [margin, y, width - margin, y + block_h],
                radius=10,
                fill=palette["accent"],
            )
            inner_y = y + 15
            for line in hook_lines:
                lw = self._text_width(draw, line, f_body)
                draw.text(
                    ((width - lw) // 2, inner_y),
                    line,
                    font=f_body,
                    fill="#FFFFFF",
                )
                inner_y += f_body.size + 6
            y += block_h + 10

        return y

    # ── 情感共鸣图（情绪场景图） ──────────────────────────────
    def _render_emotional_scene(
        self,
        draw,
        content,
        palette,
        f_title,
        f_body,
        f_small,
        f_quote,
        width,
        height,
        margin,
        y,
    ):
        title = content.get("title", "")
        scene_desc = content.get("scene_desc", "")
        emotion = content.get("emotion", "")
        caption = content.get("caption", "")

        # 顶部标题
        if title:
            lines = self._wrap_text(title, 10)
            for line in lines:
                tw = self._text_width(draw, line, f_title)
                draw.text(((width - tw) // 2, y), line, font=f_title, fill=palette["text"])
                y += f_title.size + 8
            y += 20

        # 中部场景描述框（用于 AI 绘图参考 + 占位）
        scene_h = 360
        draw.rounded_rectangle(
            [margin, y, width - margin, y + scene_h],
            radius=12,
            fill=palette["card"],
            outline=palette["card_border"],
            width=2,
        )
        # 左侧色条
        draw.rectangle([margin, y, margin + 6, y + scene_h], fill=palette["accent"])

        inner_x = margin + 30
        inner_y = y + 30

        # 情绪标签
        if emotion:
            tag = f"  {emotion}  "
            tw = self._text_width(draw, tag, f_small)
            draw.rounded_rectangle(
                [inner_x, inner_y, inner_x + tw + 20, inner_y + f_small.size + 14],
                radius=8,
                fill=palette["accent"],
            )
            draw.text((inner_x + 10, inner_y + 6), tag, font=f_small, fill="#FFFFFF")
            inner_y += f_small.size + 30

        # 场景描述（多行）
        if scene_desc:
            inner_y = self._draw_wrapped_text(
                draw,
                (inner_x, inner_y),
                scene_desc,
                f_body,
                palette["text"],
                max_chars=18,
                line_gap=10,
            )

        # 居中提示："此处为场景图占位，AI 绘图可参考上述描述"
        tip = "[ 场景图占位 ]"
        tw = self._text_width(draw, tip, f_small)
        draw.text(
            ((width - tw) // 2, y + scene_h - 40),
            tip,
            font=f_small,
            fill=palette["muted"],
        )

        y += scene_h + 30

        # 底部走心配文（引号包裹）
        if caption:
            cap_lines = self._wrap_text(caption, 16)
            block_h = 40 + len(cap_lines) * (f_quote.size + 6) + 30
            draw.rounded_rectangle(
                [margin, y, width - margin, y + block_h],
                radius=12,
                fill=palette["card"],
                outline=palette["accent"],
                width=2,
            )
            inner_y = y + 20
            # 开头引号
            draw.text((margin + 20, inner_y), "\u201c", font=f_quote, fill=palette["accent"])
            inner_y += 10
            for line in cap_lines:
                lw = self._text_width(draw, line, f_quote)
                draw.text(((width - lw) // 2, inner_y), line, font=f_quote, fill=palette["text"])
                inner_y += f_quote.size + 6
            y += block_h + 10

        return y

    # ============================================================
    # 工具方法
    # ============================================================
    def _error(self, msg: str) -> AssetProduceResult:
        return AssetProduceResult(
            asset=AssetRef(asset_id="", asset_type="", name=""),
            success=False,
            error=msg,
        )


# ============================================================
# 图文类型字典（供前端 UI 拉取选项）
# ============================================================
def list_graphic_types() -> List[Dict[str, str]]:
    """返回全部图文类型元信息（含 6 种基础 + 2 种营销素材）"""
    return [
        {"type": k, "label": v["label"], "desc": v["desc"]}
        for k, v in _GRAPHIC_TYPE_TEMPLATES.items()
    ]
