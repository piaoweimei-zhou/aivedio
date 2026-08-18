"""
工作流模板服务 (WorkflowTemplateService)

预置常见生产流水线模板，用户可一键从模板创建批量任务。
支持自定义模板（CRUD），模板定义了步骤编排和默认参数。

设计原则：
- 复用 BatchTaskService，模板只是批量任务的"预设配置"
- 不修改现有 Stage 执行流程
- 向后兼容：现有批量任务功能不受影响

预置模板：
- concept_to_video: 概念图 → 分镜 → 视频 → 导出
- storyboard_to_video: 分镜 → 视频 → 导出
- batch_storyboard_to_video: CSV批量分镜 → 视频
- character_pipeline: 概念图 → 三视图 → 分镜 → 视频
- refine_pipeline: 精修/超分流水线
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.batch_task_service import get_batch_task_service, BatchTask

logger = logging.getLogger(__name__)

_DEFAULT_TEMPLATE_DIR = "data/workflow_templates"


@dataclass
class WorkflowStepTemplate:
    """工作流模板中的步骤定义"""
    stage_id: str                                   # Stage ID
    name: str = ""                                  # 步骤名称
    input_from_steps: List[str] = field(default_factory=list)  # 引用前序步骤
    input_mode: str = "auto"                        # auto / fixed / user_select
    # auto: 自动引用前序步骤输出
    # fixed: 使用固定 input_asset_ids（模板预设）
    # user_select: 用户创建时选择输入资产
    input_asset_ids: List[str] = field(default_factory=list)   # 固定输入（input_mode=fixed时）
    provider_id: str = ""                           # 供应商（空=默认）
    params: Dict[str, Any] = field(default_factory=dict)       # 默认参数
    max_retries: int = 0                            # 最大重试次数
    description: str = ""                           # 步骤说明

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowStepTemplate":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class WorkflowTemplate:
    """工作流模板"""
    template_id: str
    name: str
    description: str = ""
    category: str = "custom"                        # preset / custom
    steps: List[WorkflowStepTemplate] = field(default_factory=list)
    # 模板需要的输入资产描述（用于前端提示用户选择）
    required_inputs: List[Dict[str, Any]] = field(default_factory=list)
    # [{"key": "character", "label": "角色概念图", "asset_type": "concept", "content_type": "character"}]
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "steps": [s.to_dict() for s in self.steps],
            "required_inputs": self.required_inputs,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowTemplate":
        steps_data = data.pop("steps", [])
        steps = [WorkflowStepTemplate.from_dict(s) for s in steps_data]
        return cls(steps=steps, **{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# 预置工作流模板
# ============================================================

def _build_preset_templates() -> List[WorkflowTemplate]:
    """构建预置工作流模板"""
    now = time.time()
    templates = []

    # 1. 概念图 → 分镜 → 视频 → 导出（完整流水线）
    templates.append(WorkflowTemplate(
        template_id="preset_concept_to_video",
        name="概念图到视频完整流水线",
        description="从概念图生成分镜，再生成视频并导出成片。适合单角色单场景的完整制作。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="storyboard",
                name="生成分镜",
                input_mode="user_select",
                input_from_steps=[],
                # ⭐ 修复 P0 #2：补充分镜默认参数（避免英文硬编码 "Storyboard scene composition"）
                params={
                    "size": "1365x768",       # 分镜帧尺寸（16:9 适配视频）
                    "steps": 4,                # Fish 融合步数（速度/质量平衡）
                    "cfg": 1.5,
                },
                description="从角色和场景概念图生成分镜帧",
            ),
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="auto",
                input_from_steps=["step_1"],
                # ⭐ 修复 P0 #1：补充视频默认参数（避免默认 480p 低清）
                params={
                    "resolution": "720p",     # 视频分辨率（高于默认 480p）
                    "duration": 5,             # 单段时长（秒）
                    "fps": 24,
                    "cfg": 3.0,
                    "steps": 8,                # LTX 蒸馏 8 步
                },
                description="从分镜帧生成视频",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_2"],
                description="导出最终成片",
            ),
        ],
        required_inputs=[
            {"key": "character", "label": "角色概念图", "asset_type": "concept", "content_type": "character"},
            {"key": "scene", "label": "场景概念图", "asset_type": "concept", "content_type": "scene"},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 2. 分镜 → 视频 → 导出（已有分镜的快速流水线）
    templates.append(WorkflowTemplate(
        template_id="preset_storyboard_to_video",
        name="分镜到视频流水线",
        description="从已有分镜帧生成视频并导出。适合分镜已完成的场景。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="user_select",
                input_from_steps=[],
                # ⭐ 修复 P0 #1：补充视频默认参数
                params={
                    "resolution": "720p",
                    "duration": 5,
                    "fps": 24,
                    "cfg": 3.0,
                    "steps": 8,
                },
                description="从分镜帧生成视频",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_1"],
                description="导出最终成片",
            ),
        ],
        required_inputs=[
            {"key": "storyboard", "label": "分镜帧", "asset_type": "storyboard", "content_type": ""},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 3. CSV批量分镜 → 视频（批量制作流水线）
    templates.append(WorkflowTemplate(
        template_id="preset_batch_storyboard_to_video",
        name="CSV批量分镜到视频",
        description="从CSV分镜脚本批量生成分镜，再逐个生成视频。适合批量制作。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="batch_storyboard",
                name="CSV批量分镜",
                input_mode="user_select",
                input_from_steps=[],
                params={
                    "csv_data": "",
                    "size": "1365x768",
                    "steps": 4,
                    "cfg": 1.5,
                },
                description="从CSV脚本批量生成分镜",
            ),
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="auto",
                input_from_steps=["step_1"],
                # ⭐ 修复 P0 #1：补充视频默认参数
                params={
                    "resolution": "720p",
                    "duration": 5,
                    "fps": 24,
                    "cfg": 3.0,
                    "steps": 8,
                },
                description="从分镜帧生成视频",
            ),
        ],
        required_inputs=[
            {"key": "characters", "label": "角色资产（可选）", "asset_type": "concept", "content_type": "character"},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 4. 角色制作流水线：概念图 → 三视图 → 分镜
    templates.append(WorkflowTemplate(
        template_id="preset_character_pipeline",
        name="角色制作流水线",
        description="从概念图生成三视图，再生成分镜。适合角色资产的标准化制作。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="angle",
                name="生成三视图",
                input_mode="user_select",
                input_from_steps=[],
                description="从角色概念图生成三视图",
            ),
            WorkflowStepTemplate(
                stage_id="storyboard",
                name="生成分镜",
                input_mode="auto",
                input_from_steps=["step_1"],
                description="使用三视图生成分镜",
            ),
        ],
        required_inputs=[
            {"key": "character", "label": "角色概念图", "asset_type": "concept", "content_type": "character"},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 5. 精修流水线：精修/超分
    templates.append(WorkflowTemplate(
        template_id="preset_refine_pipeline",
        name="精修超分流水线",
        description="对图像进行精修或超分辨率放大。适合后期质量提升。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="refine",
                name="精修/超分",
                input_mode="user_select",
                input_from_steps=[],
                params={"mode": "refine"},
                description="精修或超分图像",
            ),
        ],
        required_inputs=[
            {"key": "image", "label": "待精修图像", "asset_type": "", "content_type": ""},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 6. 视频剪辑流水线
    templates.append(WorkflowTemplate(
        template_id="preset_video_edit_pipeline",
        name="视频剪辑导出流水线",
        description="多个视频片段剪辑拼接后导出成片。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="edit",
                name="视频剪辑",
                input_mode="user_select",
                input_from_steps=[],
                params={"mode": "concat"},
                description="拼接多个视频片段",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_1"],
                description="导出最终成片",
            ),
        ],
        required_inputs=[
            {"key": "videos", "label": "视频片段（多个）", "asset_type": "video", "content_type": ""},
        ],
        created_at=now,
        updated_at=now,
    ))

    # 7. 穿越剧流水线：剧本 → 概念图 → 分镜 → 视频 → 剪辑 → 导出
    templates.append(WorkflowTemplate(
        template_id="preset_time_travel_drama",
        name="穿越剧完整流水线",
        description="AI 生成穿越剧剧本，再到角色概念图、分镜、视频、剪辑、导出。适合古今穿越题材的剧情短视频。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="script",
                name="AI 剧本生成",
                input_mode="fixed",
                input_from_steps=[],
                params={
                    "topic": "现代工具人穿越古代当军师，用 Office 解决军务",
                    "video_type": "full_ai_short",
                    "acts": 4,
                    "duration_seconds": 60,
                    "characters": ["主角-现代人", "皇帝", "大臣"],
                    "tone_extra": "古今冲突+冷幽默，节奏明快",
                    "hook_style": "comment_1",
                },
                description="生成穿越剧剧本（full_ai_short）",
            ),
            WorkflowStepTemplate(
                stage_id="concept",
                name="生成角色概念图",
                input_mode="auto",
                input_from_steps=["step_1"],
                params={"concept_type": "character"},
                description="根据剧本角色生成概念图",
            ),
            WorkflowStepTemplate(
                stage_id="storyboard",
                name="生成分镜",
                input_mode="auto",
                input_from_steps=["step_1", "step_2"],
                description="根据剧本和概念图生成分镜",
            ),
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="auto",
                input_from_steps=["step_1", "step_3"],
                description="根据分镜生成视频",
            ),
            WorkflowStepTemplate(
                stage_id="edit",
                name="视频剪辑",
                input_mode="auto",
                input_from_steps=["step_4"],
                params={"mode": "concat"},
                description="拼接所有视频片段",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_5"],
                description="导出最终穿越剧成片",
            ),
        ],
        required_inputs=[],
        metadata={"video_type": "full_ai_short", "genre": "穿越剧"},
        created_at=now,
        updated_at=now,
    ))

    # 8. 职场剧流水线：剧本 → 概念图 → 分镜 → 视频 → 剪辑 → 导出
    templates.append(WorkflowTemplate(
        template_id="preset_office_drama",
        name="职场剧完整流水线",
        description="AI 生成职场剧剧本，再到概念图、分镜、视频、剪辑、导出。适合办公场景的痛点剧情短视频。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="script",
                name="AI 剧本生成",
                input_mode="fixed",
                input_from_steps=[],
                params={
                    "topic": "用批量处理工具解决周报重复劳动的痛点",
                    "video_type": "problem_solving",
                    "acts": 3,
                    "duration_seconds": 30,
                    "characters": ["打工人A", "老板B"],
                    "tone_extra": "职场真实场景+反转，直击周报痛点",
                    "hook_style": "main_page",
                },
                description="生成职场剧剧本（problem_solving）",
            ),
            WorkflowStepTemplate(
                stage_id="concept",
                name="生成场景概念图",
                input_mode="auto",
                input_from_steps=["step_1"],
                params={"concept_type": "scene"},
                description="根据剧本生成办公场景概念图",
            ),
            WorkflowStepTemplate(
                stage_id="storyboard",
                name="生成分镜",
                input_mode="auto",
                input_from_steps=["step_1", "step_2"],
                description="根据剧本和场景生成分镜",
            ),
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="auto",
                input_from_steps=["step_1", "step_3"],
                description="根据分镜生成视频",
            ),
            WorkflowStepTemplate(
                stage_id="edit",
                name="视频剪辑",
                input_mode="auto",
                input_from_steps=["step_4"],
                params={"mode": "concat"},
                description="拼接所有视频片段",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_5"],
                description="导出最终职场剧成片",
            ),
        ],
        required_inputs=[],
        metadata={"video_type": "problem_solving", "genre": "职场剧"},
        created_at=now,
        updated_at=now,
    ))

    # 9. 图文叙事流水线：剧本 → 概念图 → 分镜 → 视频 → 分屏合成 → 导出
    templates.append(WorkflowTemplate(
        template_id="preset_image_narrative",
        name="图文叙事完整流水线",
        description="AI 生成图文叙事剧本，再到概念图、分镜、视频、分屏合成、导出。适合图片+文字+配音的叙事短视频。",
        category="preset",
        steps=[
            WorkflowStepTemplate(
                stage_id="script",
                name="AI 剧本生成",
                input_mode="fixed",
                input_from_steps=[],
                params={
                    "topic": "用图文叙事讲清楚一个工具的核心价值",
                    "video_type": "image_story",
                    "acts": 4,
                    "duration_seconds": 45,
                    "characters": [],
                    "tone_extra": "图文配合，画面节奏感强，配音有感染力",
                    "hook_style": "dm",
                },
                description="生成图文叙事剧本（image_story）",
            ),
            WorkflowStepTemplate(
                stage_id="concept",
                name="生成关键画面",
                input_mode="auto",
                input_from_steps=["step_1"],
                params={"concept_type": "scene"},
                description="根据剧本生成关键画面概念图",
            ),
            WorkflowStepTemplate(
                stage_id="storyboard",
                name="生成分镜",
                input_mode="auto",
                input_from_steps=["step_1", "step_2"],
                description="根据剧本和画面生成分镜",
            ),
            WorkflowStepTemplate(
                stage_id="video",
                name="生成视频",
                input_mode="auto",
                input_from_steps=["step_1", "step_3"],
                description="根据分镜生成视频",
            ),
            WorkflowStepTemplate(
                stage_id="compose",
                name="分屏合成",
                input_mode="auto",
                input_from_steps=["step_4"],
                params={
                    "layout": "split_compare",
                    "gap": 20,
                    "labels": ["原始", "精修"],
                    "size": "1920x1080",
                    "duration": 45,
                    "bg_color": "0x000000",
                },
                description="左右对比合成（图文叙事常用）",
            ),
            WorkflowStepTemplate(
                stage_id="export",
                name="导出成片",
                input_mode="auto",
                input_from_steps=["step_5"],
                description="导出最终图文叙事成片",
            ),
        ],
        required_inputs=[],
        metadata={"video_type": "image_story", "genre": "图文叙事"},
        created_at=now,
        updated_at=now,
    ))

    return templates


class WorkflowTemplateService:
    """工作流模板服务

    核心职责：
    - 管理预置和自定义工作流模板
    - 从模板创建批量任务（解析输入、构建步骤）
    - JSON 持久化自定义模板
    """

    def __init__(self, template_dir: str = _DEFAULT_TEMPLATE_DIR):
        self._template_dir = Path(template_dir)
        self._template_dir.mkdir(parents=True, exist_ok=True)
        self._templates: Dict[str, WorkflowTemplate] = {}
        self._load()

    def _load(self):
        # 1. 加载预置模板
        for tpl in _build_preset_templates():
            self._templates[tpl.template_id] = tpl

        # 2. 加载自定义模板
        if self._template_dir.exists():
            for path in self._template_dir.glob("wf_*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    tpl = WorkflowTemplate.from_dict(data)
                    self._templates[tpl.template_id] = tpl
                except Exception as e:
                    logger.warning(f"[WorkflowTemplate] 加载失败 | file={path.name} | error={e}")

        logger.info(f"[WorkflowTemplate] 加载 {len(self._templates)} 个模板（预置+自定义）")

    def _save_template(self, tpl: WorkflowTemplate):
        try:
            data = tpl.to_dict()
            self._template_dir.joinpath(f"{tpl.template_id}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[WorkflowTemplate] 持久化失败 | id={tpl.template_id} | error={e}")

    # ================================================================
    # CRUD
    # ================================================================

    def list_templates(self, category: str = "") -> List[WorkflowTemplate]:
        templates = list(self._templates.values())
        if category:
            templates = [t for t in templates if t.category == category]
        return sorted(templates, key=lambda t: (t.category != "preset", t.name))

    def get(self, template_id: str) -> Optional[WorkflowTemplate]:
        return self._templates.get(template_id)

    def create_custom(
        self,
        name: str,
        description: str,
        steps: List[Dict[str, Any]],
        required_inputs: List[Dict[str, Any]] = None,
        metadata: Dict[str, Any] = None,
    ) -> WorkflowTemplate:
        """创建自定义模板"""
        template_id = f"wf_{uuid.uuid4().hex[:12]}"
        now = time.time()
        tpl_steps = [WorkflowStepTemplate.from_dict(s) for s in steps]
        tpl = WorkflowTemplate(
            template_id=template_id,
            name=name,
            description=description,
            category="custom",
            steps=tpl_steps,
            required_inputs=required_inputs or [],
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._templates[template_id] = tpl
        self._save_template(tpl)
        logger.info(f"[WorkflowTemplate] 创建自定义模板 | id={template_id} | name={name}")
        return tpl

    def update(self, template_id: str, updates: Dict[str, Any]) -> Optional[WorkflowTemplate]:
        """更新自定义模板（预置模板不可更新）"""
        tpl = self._templates.get(template_id)
        if not tpl or tpl.category == "preset":
            return None

        if "name" in updates:
            tpl.name = updates["name"]
        if "description" in updates:
            tpl.description = updates["description"]
        if "steps" in updates:
            tpl.steps = [WorkflowStepTemplate.from_dict(s) for s in updates["steps"]]
        if "required_inputs" in updates:
            tpl.required_inputs = updates["required_inputs"]
        tpl.updated_at = time.time()
        self._save_template(tpl)
        return tpl

    def delete(self, template_id: str) -> bool:
        """删除自定义模板（预置模板不可删除）"""
        tpl = self._templates.get(template_id)
        if not tpl or tpl.category == "preset":
            return False
        del self._templates[template_id]
        try:
            self._template_dir.joinpath(f"{template_id}.json").unlink()
        except Exception:
            pass
        return True

    # ================================================================
    # 从模板创建批量任务
    # ================================================================

    def create_batch_from_template(
        self,
        template_id: str,
        name: str,
        project_id: str = "",
        input_assets: Dict[str, List[str]] = None,
        step_params: Dict[str, Dict[str, Any]] = None,
        stop_on_failure: bool = True,
    ) -> Optional[BatchTask]:
        """从模板创建批量任务

        Args:
            template_id: 模板 ID
            name: 批量任务名称
            project_id: 所属项目
            input_assets: 用户选择的输入资产映射
                {"character": ["asset_id1"], "scene": ["asset_id2"]}
                键对应模板的 required_inputs[].key
            step_params: 步骤参数覆盖
                {"step_1": {"prompt": "..."}, "step_2": {...}}
            stop_on_failure: 失败时停止
        """
        tpl = self._templates.get(template_id)
        if not tpl:
            return None

        input_assets = input_assets or {}
        step_params = step_params or {}

        # 构建批量任务步骤
        batch_steps = []
        for i, step_tpl in enumerate(tpl.steps):
            step_id = f"step_{i + 1}"

            # 解析步骤输入
            input_asset_ids = []
            if step_tpl.input_mode == "fixed":
                input_asset_ids = list(step_tpl.input_asset_ids)
            elif step_tpl.input_mode == "user_select":
                # 从用户选择的输入资产中获取
                # 按 required_inputs 的 key 顺序匹配
                for req in tpl.required_inputs:
                    key = req.get("key", "")
                    if key in input_assets:
                        input_asset_ids.extend(input_assets[key])
            # input_mode == "auto" 时不传 input_asset_ids，依赖 input_from_steps

            # 合并参数：模板默认 + 用户覆盖
            params = dict(step_tpl.params)
            if step_id in step_params:
                params.update(step_params[step_id])

            batch_steps.append({
                "step_id": step_id,
                "stage_id": step_tpl.stage_id,
                "name": step_tpl.name,
                "input_asset_ids": input_asset_ids,
                "input_from_steps": [f"step_{int(s.split('_')[1])}" if s.startswith("step_") else s
                                     for s in step_tpl.input_from_steps],
                "provider_id": step_tpl.provider_id,
                "params": params,
                "max_retries": step_tpl.max_retries,
            })

        # 创建批量任务
        batch_svc = get_batch_task_service()
        batch = batch_svc.create(
            name=name,
            steps=batch_steps,
            project_id=project_id,
            stop_on_failure=stop_on_failure,
            auto_inherit_project=True,
            metadata={"template_id": template_id, "template_name": tpl.name},
        )
        logger.info(
            f"[WorkflowTemplate] 从模板创建批量任务 | "
            f"template={template_id} batch={batch.batch_id}"
        )
        return batch


# ============================================================
# 单例
# ============================================================

_instance: Optional[WorkflowTemplateService] = None


def get_workflow_template_service() -> WorkflowTemplateService:
    global _instance
    if _instance is None:
        _instance = WorkflowTemplateService()
    return _instance
