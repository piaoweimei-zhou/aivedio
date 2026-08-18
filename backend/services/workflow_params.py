"""统一工作流参数契约层

解决 E 级问题（缺少统一参数契约）：
- E1: 5 种参数集表述（节点 inputs / config.json / kwargs / params dict / React state）
- E2: 缺少参数校验合约（类型/约束/绑定）
- E3: 前端提交无类型安全

本模块提供：
1. ParamSpec  - 单个参数规格（节点绑定 + 类型 + 约束 + 默认值）
2. WorkflowSchema - 工作流参数 schema（声明式参数定义）
3. ParamInjector - 参数注入引擎（替代散落的手写注入代码）
4. VideoGenerationParams - 视频生成统一 DTO（Phase 3）
5. WORKFLOW_SCHEMAS - 5 个核心工作流的 schema 注册表

使用方式：
    from services.workflow_params import ParamInjector, get_schema

    schema = get_schema("文生图影视级")
    injector = ParamInjector(schema, workflow_json)
    injector.inject({"width": 1280, "steps": 30, "cfg": 2.5})
    result = injector.workflow
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from services.video_time import resolve_video_duration
from services.video_resolution import resolve_video_resolution


# ============================================================
# Phase 1: 参数 Schema 定义
# ============================================================

@dataclass
class ParamSpec:
    """单个参数规格

    声明一个参数与工作流节点的绑定关系，以及类型约束。
    替代 config.json 中"只声明字段名、靠数字推断节点"的脆弱模式。
    """
    name: str                                    # 参数名（如 width/steps/cfg/prompt）
    node_id: str                                 # 绑定的 ComfyUI 节点 ID
    field: str                                   # 节点 inputs 中的字段名
    type: str = "string"                         # int/float/string/image/select
    default: Any = None                          # 默认值
    min: Optional[Union[int, float]] = None      # 最小值
    max: Optional[Union[int, float]] = None      # 最大值
    required: bool = False                       # 是否必填
    choices: Optional[List[Any]] = None          # 可选值列表（select 类型）
    description: str = ""                        # 描述

    def validate(self, value: Any) -> Any:
        """校验并转换参数值"""
        if value is None:
            if self.required:
                raise ValueError(f"缺少必填参数: {self.name}")
            return self.default

        # 类型转换
        if self.type == "int":
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValueError(f"参数 {self.name} 期望 int，得到 {type(value)}")
        elif self.type == "float":
            try:
                value = float(value)
            except (ValueError, TypeError):
                raise ValueError(f"参数 {self.name} 期望 float，得到 {type(value)}")

        # 范围校验
        if self.min is not None and value < self.min:
            raise ValueError(f"参数 {self.name}={value} 小于最小值 {self.min}")
        if self.max is not None and value > self.max:
            raise ValueError(f"参数 {self.name}={value} 超过最大值 {self.max}")

        # 选项校验
        if self.choices and value not in self.choices:
            raise ValueError(f"参数 {self.name}={value} 不在允许选项 {self.choices} 中")

        return value


@dataclass
class WorkflowSchema:
    """工作流参数 Schema

    一个声明文件覆盖工作流所有可覆写参数，代码自动生成注入逻辑。
    """
    name: str                                    # 工作流名称
    template: str                                # 工作流 JSON 文件名
    params: Dict[str, ParamSpec] = field(default_factory=dict)
    ui_groups: List[Dict[str, Any]] = field(default_factory=list)
    # 视频参数派生规则（如 duration = frame_count / fps）
    derived: Dict[str, str] = field(default_factory=dict)

    def add_param(self, spec: ParamSpec) -> "WorkflowSchema":
        self.params[spec.name] = spec
        return self

    def validate_all(self, user_params: Dict[str, Any]) -> Dict[str, Any]:
        """校验所有参数，返回转换后的参数字典"""
        result = {}
        for name, spec in self.params.items():
            result[name] = spec.validate(user_params.get(name))
        return result


# ============================================================
# Phase 2: 参数注入引擎
# ============================================================

class ParamInjector:
    """参数注入引擎

    根据 WorkflowSchema 将用户参数注入到工作流 JSON，替代手写注入代码。
    """

    def __init__(self, schema: WorkflowSchema, workflow: Dict[str, Any]):
        self.schema = schema
        self.workflow = workflow
        self.injected: Dict[str, Any] = {}

    def inject(self, user_params: Dict[str, Any], skip_missing: bool = True) -> Dict[str, Any]:
        """根据 schema 定义，将用户参数注入到工作流 JSON

        Args:
            user_params: 用户提交的参数字典
            skip_missing: True=节点不存在时跳过；False=抛出异常

        Returns:
            注入后的工作流 JSON（self.workflow 的引用）
        """
        validated = self.schema.validate_all(user_params)

        for name, spec in self.schema.params.items():
            value = validated.get(name)
            if value is None:
                continue

            node = self.workflow.get(spec.node_id)
            if not node:
                if skip_missing:
                    continue
                raise KeyError(f"工作流中不存在节点 {spec.node_id}（参数 {name}）")

            node.setdefault("inputs", {})[spec.field] = value
            self.injected[name] = value

        # 派生参数处理
        self._apply_derived(validated)
        return self.workflow

    def _apply_derived(self, params: Dict[str, Any]) -> None:
        """处理派生参数（如 duration = frame_count / fps）"""
        for derived_name, formula in self.schema.derived.items():
            if derived_name not in self.schema.params:
                continue
            try:
                # 简单表达式求值（仅支持 frame_count / fps 这类）
                if "/" in formula:
                    num_name, den_name = [n.strip() for n in formula.split("/", 1)]
                    num = params.get(num_name)
                    den = params.get(den_name)
                    if num and den and den > 0:
                        derived_value = num / den
                        spec = self.schema.params[derived_name]
                        node = self.workflow.get(spec.node_id)
                        if node:
                            node.setdefault("inputs", {})[spec.field] = derived_value
                            self.injected[derived_name] = derived_value
            except Exception:
                pass  # 派生参数失败不影响主流程


# ============================================================
# Phase 3: 视频生成统一 DTO
# ============================================================

@dataclass
class VideoGenerationParams:
    """统一视频生成参数——所有视频路径都用这个

    替代散落在各 provider / stage 中的不一致参数集。
    支持 width/height/resolution/aspect_ratio 互通，
    支持 frame_count/duration/fps 互通。
    """
    prompt: str = ""
    # 尺寸（三选一，自动转换）
    width: Optional[int] = None
    height: Optional[int] = None
    resolution: str = "480p"
    aspect_ratio: str = "16:9"
    # 时间控制（三选一，自动转换）
    frame_count: Optional[int] = None
    duration: Optional[float] = None
    fps: Optional[int] = None
    # 质量
    seed: int = -1
    cfg: float = 3.0
    steps: int = 20
    # 参考
    reference_images: List[str] = field(default_factory=list)
    background_image: Optional[str] = None
    # 扩展
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """自动解析尺寸和时间参数"""
        # 尺寸统一：若未指定 width/height，则从 resolution + aspect_ratio 推导
        self.width, self.height = resolve_video_resolution(
            width=self.width, height=self.height,
            resolution=self.resolution, aspect_ratio=self.aspect_ratio,
        )
        # 时间统一：frame_count / duration / fps 互转
        self.duration, self.frame_count, self.fps = resolve_video_duration(
            duration=self.duration, frame_count=self.frame_count, fps=self.fps,
        )

    @classmethod
    def from_dict(cls, params: Dict[str, Any]) -> "VideoGenerationParams":
        """从参数字典构造（兼容各种字段名）"""
        return cls(
            prompt=params.get("prompt", ""),
            width=params.get("width"),
            height=params.get("height"),
            resolution=params.get("resolution", "480p"),
            aspect_ratio=params.get("aspect_ratio", "16:9"),
            frame_count=params.get("frame_count"),
            duration=params.get("duration"),
            fps=params.get("fps") or params.get("frame_rate"),
            seed=params.get("seed", -1),
            cfg=params.get("cfg", 3.0),
            steps=params.get("steps", 20),
            reference_images=params.get("reference_images", []),
            background_image=params.get("background_image"),
            extra={k: v for k, v in params.items()
                   if k not in {"prompt", "width", "height", "resolution",
                                "aspect_ratio", "frame_count", "duration", "fps",
                                "seed", "cfg", "steps", "reference_images",
                                "background_image"}},
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 provider 调用）"""
        return {
            "prompt": self.prompt,
            "width": self.width,
            "height": self.height,
            "resolution": self.resolution,
            "aspect_ratio": self.aspect_ratio,
            "frame_count": self.frame_count,
            "duration": self.duration,
            "fps": self.fps,
            "seed": self.seed,
            "cfg": self.cfg,
            "steps": self.steps,
            "reference_images": self.reference_images,
            "background_image": self.background_image,
        }


# ============================================================
# 5 个核心工作流 Schema 注册表
# ============================================================

def _build_schemas() -> Dict[str, WorkflowSchema]:
    """构建核心工作流 schema 注册表"""

    schemas: Dict[str, WorkflowSchema] = {}

    # 1. 最终文生图（KSampler + EmptyLatentImage）
    schemas["最终文生图"] = WorkflowSchema(
        name="最终文生图",
        template="最终文生图.json",
    ).add_param(ParamSpec("prompt", "41", "text", "string", required=False, description="正向提示词")) \
     .add_param(ParamSpec("negative", "32", "text", "string", default="", description="负向提示词")) \
     .add_param(ParamSpec("width", "34", "width", "int", default=1080, min=512, max=4096)) \
     .add_param(ParamSpec("height", "34", "height", "int", default=1920, min=512, max=4096)) \
     .add_param(ParamSpec("steps", "28", "steps", "int", default=25, min=1, max=100)) \
     .add_param(ParamSpec("cfg", "28", "cfg", "float", default=2.0, min=0.5, max=10.0)) \
     .add_param(ParamSpec("seed", "28", "seed", "int", default=-1)) \
     .add_param(ParamSpec("sampler", "28", "sampler_name", "select", default="res_multistep",
                          choices=["res_multistep", "euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"])) \
     .add_param(ParamSpec("denoise", "28", "denoise", "float", default=1.0, min=0.0, max=1.0))

    # 2. 文生图影视级（cinematic 路径，KSampler + EmptyLatentImage）
    # ⭐ 节点 ID 与真实工作流模板「最终文生图.json」对齐：
    #    41=CLIPTextEncode(正向) 32=CLIPTextEncode(负向) 34=EmptyLatentImage 28=KSampler
    # ⭐ prompt/negative 标为非必填：workflow_builder 已手写注入含质量前缀的提示词，
    #    ParamInjector 兜底时仅校验尺寸/步数等参数，不重复注入 prompt
    schemas["文生图影视级"] = WorkflowSchema(
        name="文生图影视级",
        template="最终文生图.json",
    ).add_param(ParamSpec("prompt", "41", "text", "string", required=False, description="正向提示词")) \
     .add_param(ParamSpec("negative", "32", "text", "string", default="", description="负向提示词")) \
     .add_param(ParamSpec("width", "34", "width", "int", default=1080, min=512, max=4096)) \
     .add_param(ParamSpec("height", "34", "height", "int", default=1920, min=512, max=4096)) \
     .add_param(ParamSpec("steps", "28", "steps", "int", default=25, min=1, max=100)) \
     .add_param(ParamSpec("cfg", "28", "cfg", "float", default=2.0, min=0.5, max=10.0)) \
     .add_param(ParamSpec("seed", "28", "seed", "int", default=-1)) \
     .add_param(ParamSpec("sampler", "28", "sampler_name", "select", default="res_multistep",
                          choices=["res_multistep", "euler", "euler_ancestral", "dpmpp_2m", "dpmpp_sde"])) \
     .add_param(ParamSpec("denoise", "28", "denoise", "float", default=1.0, min=0.0, max=1.0))

    # 3. LTX-2.3 视频通用路径（video_only）
    schemas["LTX-2.3_video_only"] = WorkflowSchema(
        name="LTX-2.3_video_only",
        template="LTX-2.3_video_only.json",
    ).add_param(ParamSpec("global_prompt", "99", "global_prompt", "string", required=True, description="全局场景描述")) \
     .add_param(ParamSpec("local_prompts", "99", "local_prompts", "string", default="", description="分镜描述")) \
     .add_param(ParamSpec("width", "43", "value", "int", default=1280, min=256, max=4096)) \
     .add_param(ParamSpec("height", "44", "value", "int", default=720, min=256, max=4096)) \
     .add_param(ParamSpec("frame_count", "28", "frame_count", "int", default=41, min=1)) \
     .add_param(ParamSpec("total_length", "50", "value", "int", default=361, min=1, description="总帧数")) \
     .add_param(ParamSpec("fps", "7", "frame_rate", "int", default=24, min=1, max=60)) \
     .add_param(ParamSpec("seed", "15", "noise_seed", "int", default=-1)) \
     .add_param(ParamSpec("cfg", "37", "cfg", "float", default=1.0, min=0.1, max=10.0)) \
     .add_param(ParamSpec("video_fps", "19", "fps", "int", default=24, min=1, max=60, description="输出视频帧率"))

    # 4. LTX-2.3 MSR 多角色视频
    schemas["LTX-2.3_MSR"] = WorkflowSchema(
        name="LTX-2.3_MSR",
        template="LTX-2.3_MSR_sample_workflow_V2.json",
    ).add_param(ParamSpec("global_prompt", "99", "global_prompt", "string", required=True)) \
     .add_param(ParamSpec("local_prompts", "99", "local_prompts", "string", default="")) \
     .add_param(ParamSpec("width", "43", "value", "int", default=1280, min=256, max=4096)) \
     .add_param(ParamSpec("height", "44", "value", "int", default=720, min=256, max=4096)) \
     .add_param(ParamSpec("frame_count", "28", "frame_count", "int", default=41, min=1)) \
     .add_param(ParamSpec("total_length", "50", "value", "int", default=361, min=1)) \
     .add_param(ParamSpec("fps", "7", "frame_rate", "int", default=24, min=1, max=60)) \
     .add_param(ParamSpec("seed", "15", "noise_seed", "int", default=-1)) \
     .add_param(ParamSpec("cfg", "37", "cfg", "float", default=3.0, min=0.1, max=10.0)) \
     .add_param(ParamSpec("video_fps", "19", "fps", "int", default=24, min=1, max=60))

    # 5. 1人分镜（多提示词分镜工作流）
    schemas["1人分镜"] = WorkflowSchema(
        name="1人分镜",
        template="1人分镜.json",
    ).add_param(ParamSpec("prompt_1", "1199", "prompt", "string", required=True, description="分镜1提示词")) \
     .add_param(ParamSpec("prompt_2", "1625", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_3", "2023", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_4", "2048", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_5", "2072", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_6", "2094", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_7", "2172", "prompt", "string", default="")) \
     .add_param(ParamSpec("prompt_8", "2210", "prompt", "string", default=""))

    return schemas


# 全局注册表
WORKFLOW_SCHEMAS: Dict[str, WorkflowSchema] = _build_schemas()


def get_schema(workflow_name: str) -> Optional[WorkflowSchema]:
    """获取工作流 schema"""
    return WORKFLOW_SCHEMAS.get(workflow_name)


def list_schemas() -> List[str]:
    """列出所有已注册的工作流名称"""
    return list(WORKFLOW_SCHEMAS.keys())


def inject_workflow_params(
    workflow_name: str,
    workflow_json: Dict[str, Any],
    user_params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """便捷函数：根据工作流名称注入参数

    Args:
        workflow_name: 工作流名称（必须在 WORKFLOW_SCHEMAS 中注册）
        workflow_json: 工作流 JSON（会被原地修改）
        user_params: 用户参数

    Returns:
        (注入后的工作流 JSON, 实际注入的参数字典)
    """
    schema = get_schema(workflow_name)
    if not schema:
        raise KeyError(f"未注册的工作流: {workflow_name}，已注册: {list_schemas()}")

    injector = ParamInjector(schema, workflow_json)
    injector.inject(user_params)
    return injector.workflow, injector.injected
