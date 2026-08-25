"""
阶段路由服务 (StageService)
导演工作台 Layer 3：管理生产阶段插件

核心职责：
- 阶段注册/发现
- 输入→输出类型匹配
- 阶段执行调度
- 与 AssetService + ProviderService 协作

设计原则：
- 新增能力 = 注册一个新 Stage + 配一个新 Provider，不修改核心架构
- 每个 Stage 声明 input_type[] / output_type / provider
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.asset_service import AssetRef, AssetProduceResult, get_asset_service
from services.asset_organizer import organize_asset_files
from services.provider_service import get_provider_service

logger = logging.getLogger(__name__)

# 当前阶段的 project_id 上下文（ContextVar 替代实例属性，避免并发竞态）
_current_project_id_var: ContextVar[Optional[str]] = ContextVar(
    "_current_project_id_var", default=None
)  # noqa: E501
# 最近一次 ComfyUI 调用的 prompt_id（ContextVar 替代 stage 实例属性，避免同层并行竞态）
_last_prompt_id_var: ContextVar[str] = ContextVar("_last_prompt_id_var", default="")

# ============================================================
# 阶段定义
# ============================================================


@dataclass
class StageDef:
    """阶段定义 — 描述一个生产阶段的元信息"""

    stage_id: str  # 唯一标识
    name: str  # 显示名
    input_types: List[str]  # 输入资产类型（生产阶段维度）
    output_type: str  # 输出资产类型
    default_provider: str  # 默认供应商
    supported_providers: List[str]  # 支持的供应商列表
    description: str = ""  # 阶段描述
    input_content_types: List[str] = field(
        default_factory=list
    )  # 输入内容类型（character/scene/prop），空=不限制  # noqa: E501


# ============================================================
# 阶段插件基类
# ============================================================


class StagePlugin(ABC):
    """阶段插件基类 — 每个生产阶段实现此接口"""

    stage_def: StageDef = None

    @abstractmethod
    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        """
        执行生产阶段

        Args:
            input_assets: 输入资产列表
            provider_id: 供应商 ID（空则用默认）
            params: 阶段参数

        Returns:
            AssetProduceResult: 生产结果（含新资产引用）
        """
        ...

    def validate_inputs(self, input_assets: List[AssetRef]) -> Optional[str]:
        """验证输入资产是否符合要求（同时检查 asset_type 和 content_type）"""
        if not self.stage_def:
            return "阶段定义未设置"
        # 检查 asset_type：每个输入资产的 asset_type 必须在允许列表中
        allowed_types = set(self.stage_def.input_types)
        if allowed_types:
            for a in input_assets:
                if a.asset_type not in allowed_types:
                    return f"不接受的输入类型: {a.asset_type}（允许: {allowed_types}）"
        # 检查 content_type：如果定义了 input_content_types，至少有一个输入资产满足
        required_content = set(self.stage_def.input_content_types)
        if required_content:
            provided_content = set(a.content_type for a in input_assets if a.content_type)
            missing_content = required_content - provided_content
            if missing_content:
                return f"缺少内容类型: {missing_content}"
        return None

    # ---- 模板方法：减少子类重复代码 ----

    def _resolve_provider(self, provider_id: str) -> str:
        """解析供应商 ID，空则用默认"""
        return provider_id or self.stage_def.default_provider

    def _get_services(self):
        """获取 AssetService 和 ProviderService 单例"""
        return get_asset_service(), get_provider_service()

    def _error_result(self, error: str) -> AssetProduceResult:
        """快速构建错误结果"""
        return AssetProduceResult(
            asset=AssetRef(asset_id="", asset_type="", name=""),
            success=False,
            error=error,
        )

    async def _organize_urls(self, urls: List[str], content_keyword: str = "") -> List[str]:
        """把本地生成资产整理到 data/generated/{project}/{stage}/ 语义目录

        远程 URL（云端 provider 产物）无法本地定位，会原样保留。
        使用移动而非复制：_persist_output_files 已在根目录留有副本，避免重复堆积。
        """
        if not urls:
            return urls
        project_id = _current_project_id_var.get()
        stage_id = self.stage_def.stage_id if self.stage_def else "asset"
        try:
            organized, _skipped = await asyncio.to_thread(
                organize_asset_files,
                urls,
                project_id or "",
                stage_id,
                content_keyword or "",
                move=True,
            )
            if organized:
                return organized
        except Exception as e:
            logger.warning(f"[StagePlugin] 资产整理失败，保留原 URL | {e}")
        return urls

    async def _register_asset_direct(
        self,
        asset_svc,
        asset_type: str,
        name: str,
        urls: List[str],
        input_assets: List[AssetRef] = None,
        extra_metadata: Dict[str, Any] = None,
        content_type: str = "",
        project_id: Optional[str] = None,
    ) -> Any:
        """直接创建资产（无 ProviderResult 场景，如本地后处理 stage）

        自动补全 parent_id（取首个输入资产）和 project_id（从 ContextVar 继承），
        确保 edit/compose/export 等后处理 stage 的产物也正确归属项目。
        """
        # parent_id：取首个输入资产（若无则为 None）
        parent_id = input_assets[0].asset_id if input_assets else None

        # project_id：未显式传入时从 ContextVar 继承
        if project_id is None:
            project_id = _current_project_id_var.get()

        metadata = extra_metadata or {}

        # 统一整理到语义目录（本地文件），远程 URL 原样保留
        urls = await self._organize_urls(urls, content_keyword=content_type)

        return await asset_svc.create(
            asset_type=asset_type,
            name=name,
            urls=urls,
            metadata=metadata,
            parent_id=parent_id,
            content_type=content_type,
            project_id=project_id,
        )

    def _require_input(self, input_assets: List[AssetRef], min_count: int = 1) -> Optional[str]:
        """校验输入资产数量，返回错误信息或 None"""
        if not input_assets or len(input_assets) < min_count:
            return f"{self.stage_def.name}需要至少 {min_count} 个输入资产"
        return None

    def _require_urls(self, asset: AssetRef) -> Optional[str]:
        """校验资产是否有 URL，返回错误信息或 None"""
        if not asset.urls:
            return f"资产 {asset.asset_id} 无图片 URL"
        return None

    async def _register_asset(
        self,
        asset_svc,
        result,
        asset_type: str,
        name: str,
        parent_id: str = "",
        extra_metadata: Dict[str, Any] = None,
        content_type: str = "",
        project_id: Optional[str] = None,
    ) -> AssetRef:
        """从 ProviderResult 注册新资产

        project_id 自动继承：若未显式传入，则使用当前上下文的 project_id
        （由 StageService.execute 设置 ContextVar，避免单例实例属性的并发竞态）
        """
        metadata = extra_metadata or {}
        metadata.setdefault(
            "provider_id", result.provider_id if hasattr(result, "provider_id") else ""
        )  # noqa: E501
        metadata.setdefault("seed", result.seed if hasattr(result, "seed") else 0)
        metadata.setdefault("elapsed_ms", result.elapsed_ms if hasattr(result, "elapsed_ms") else 0)

        # 捕获 prompt_id（供 StageService.execute 提取到 AssetProduceResult）
        # 使用 ContextVar 避免同层并行步骤竞态（stage 实例是单例）
        prompt_id = getattr(result, "prompt_id", "")
        if prompt_id:
            _last_prompt_id_var.set(prompt_id)
            metadata.setdefault("prompt_id", prompt_id)

        # 项目归属继承：未显式传入时，使用当前上下文的 project_id
        # （由 StageService.execute 设置 ContextVar，避免单例实例属性的并发竞态）
        if project_id is None:
            project_id = _current_project_id_var.get()

        # 统一整理到语义目录（本地文件），远程 URL 原样保留
        urls = result.images or ([result.image_url] if result.image_url else [])
        urls = await self._organize_urls(urls, content_keyword=content_type)

        return await asset_svc.create(
            asset_type=asset_type,
            name=name,
            urls=urls,
            metadata=metadata,
            parent_id=parent_id or None,
            content_type=content_type,
            project_id=project_id,
        )


# ============================================================
# 内置阶段定义
# ============================================================

BUILTIN_STAGES = {
    # 图像生产阶段
    "concept": StageDef(
        stage_id="concept",
        name="概念图生成",
        input_types=[],
        output_type="concept",
        default_provider="comfyui",
        supported_providers=["comfyui", "openai_compat", "modelscope"],
        description="从文本描述生成角色/场景/道具概念图",
    ),
    "angle": StageDef(
        stage_id="angle",
        name="三视图生成",
        input_types=["concept"],
        input_content_types=["character"],
        output_type="multi_view",
        default_provider="modelscope",
        supported_providers=["modelscope", "comfyui"],
        description="从概念图生成正面/侧面/背面三视图",
    ),
    "pano": StageDef(
        stage_id="pano",
        name="360全景生成",
        input_types=[],
        input_content_types=["scene"],
        output_type="pano",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="从场景图生成360度全景图",
    ),
    "storyboard": StageDef(
        stage_id="storyboard",
        name="分镜生成",
        input_types=[],
        input_content_types=["character", "scene"],
        output_type="storyboard",
        default_provider="comfyui",
        supported_providers=["comfyui", "runninghub", "openai_compat", "gemini", "volcengine"],
        description="角色+场景融合生成分镜帧（复用 V6.0 分镜模板系统，支持 script 输入批量生成）",
    ),
    "refine": StageDef(
        stage_id="refine",
        name="精修/超分",
        input_types=["concept", "storyboard", "edit"],
        output_type="edit",
        default_provider="comfyui",
        supported_providers=["comfyui"],
        description="对图像进行精修或超分辨率放大",
    ),
    # 视频生产阶段
    "video": StageDef(
        stage_id="video",
        name="视频生成",
        input_types=["storyboard", "concept"],
        output_type="video",
        default_provider="comfyui",
        supported_providers=["comfyui", "jimeng", "runninghub", "volcengine"],
        description="从图片生成视频（图生视频，支持本地 LTX-2.3 和云端 provider）",
    ),
    # 后期阶段
    "edit": StageDef(
        stage_id="edit",
        name="视频剪辑",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="视频剪辑、拼接、转场",
    ),
    "export": StageDef(
        stage_id="export",
        name="成片导出",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="导出最终成片（编码/格式转换）",
    ),
    # 文本/剧本阶段（新增）
    "script": StageDef(
        stage_id="script",
        name="AI剧本生成",
        input_types=[],
        output_type="script",
        default_provider="openai_compat",
        supported_providers=["openai_compat"],
        description="通过 LLM 生成结构化短剧剧本（6种视频类型可选：问题解决/效率对比/测评教程/趣味剧情/全AI短剧/图文叙事）",
    ),
    # 录屏阶段（新增）
    "screen_record": StageDef(
        stage_id="screen_record",
        name="屏幕录制",
        input_types=[],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="屏幕录制（支持 ffmpeg gdigrab/x11grab/avfoundation 自动录制和上传文件两种模式）",
    ),
    # 分屏合成阶段（新增）
    "compose": StageDef(
        stage_id="compose",
        name="分屏合成",
        input_types=["video", "image"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="多素材分屏合成（horizontal/vertical/grid/split_compare 四种布局，支持视频和图片混合）",
    ),
    # 短视频后期阶段（Phase 1：字幕 + 钩子）
    "subtitle": StageDef(
        stage_id="subtitle",
        name="字幕烧录",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="竖版大字幕烧录（关键词高亮描边，纯 ffmpeg）",
    ),
    "hook_overlay": StageDef(
        stage_id="hook_overlay",
        name="结尾钩子引导框",
        input_types=["video"],
        output_type="video",
        default_provider="local",
        supported_providers=["local"],
        description="结尾固定引导框（评论区扣1领工具模板化），ffmpeg overlay",
    ),
    # 质检合规阶段（Phase 0 新增，零侵入：仅注册为可选 stage，不改默认一键成片序列）
    "qc": StageDef(
        stage_id="qc",
        name="质检合规",
        input_types=["video"],
        output_type="qc_report",
        default_provider="local",
        supported_providers=["local"],
        description="对成片做 100 分制质量/平台规则/版权质检，产出可复核报告（不拦截发布）",
    ),
}


# ============================================================
# StageService
# ============================================================


class StageService:
    """阶段路由服务 — 管理所有生产阶段"""

    def __init__(self):
        self._stages: Dict[str, StagePlugin] = {}
        self._defs: Dict[str, StageDef] = dict(BUILTIN_STAGES)
        self._register_builtin_stages()

    def _register_builtin_stages(self):
        """自动注册内置阶段插件（延迟导入避免循环依赖）

        基于 stage_id → stage_cls 映射表自动加载，新增 stage 只需在映射表添加一行。
        """
        # stage_id → stage_cls 映射表（单一来源，避免 BUILTIN_STAGES 与硬编码列表双系统）
        _STAGE_CLS_MAP = {
            "concept": "services.stages.concept_stage:ConceptStage",
            "refine": "services.stages.refine_stage:RefineStage",
            "angle": "services.stages.angle_stage:AngleStage",
            "pano": "services.stages.pano_stage:PanoStage",
            "storyboard": "services.stages.storyboard_stage:StoryboardStage",
            "video": "services.stages.video_stage:VideoStage",
            "edit": "services.stages.edit_stage:EditStage",
            "export": "services.stages.export_stage:ExportStage",
            "pose_extraction": "services.stages.pose_extraction_stage:PoseExtractionStage",
            "lineart_extraction": "services.stages.lineart_extraction_stage:LineartExtractionStage",
            "depth_map": "services.stages.depth_map_stage:DepthMapStage",
            "extract_all": "services.stages.extract_all_stage:ExtractAllStage",
            "multi_person": "services.stages.multi_person_stage:MultiPersonStage",
            "layered_render": "services.stages.layered_render_stage:LayeredRenderStage",
            "batch_storyboard": "services.stages.batch_storyboard_stage:BatchStoryboardStage",
            "template_batch_extract": "services.stages.template_batch_extract_stage:TemplateBatchExtractStage",  # noqa: E501
            "template_clean": "services.stages.template_clean_stage:TemplateCleanStage",
            "template_pose": "services.stages.template_pose_stage:TemplatePoseStage",
            "script": "services.stages.script_stage:ScriptStage",
            "screen_record": "services.stages.screen_record_stage:ScreenRecordStage",
            "compose": "services.stages.compose_stage:ComposeStage",
            "graphic": "services.stages.graphic_stage:GraphicStage",
            "tts": "services.stages.tts_stage:TtsStage",
            "subtitle": "services.stages.subtitle_stage:SubtitleStage",
            "hook_overlay": "services.stages.hook_overlay_stage:HookOverlayStage",
            "qc": "services.stages.qc_stage:QcStage",
        }

        import importlib

        for stage_id, cls_path in _STAGE_CLS_MAP.items():
            try:
                module_path, cls_name = cls_path.rsplit(":", 1)
                module = importlib.import_module(module_path)
                stage_cls = getattr(module, cls_name)
                self.register(stage_cls())
            except Exception as e:
                logger.warning(
                    f"[StageService] 注册阶段失败 | stage_id={stage_id} | cls={cls_path} | error={e}"
                )  # noqa: E501

    def register(self, stage: StagePlugin):
        """注册阶段插件"""
        if not stage.stage_def:
            raise ValueError(f"阶段插件 {stage.__class__.__name__} 缺少 stage_def")
        self._stages[stage.stage_def.stage_id] = stage
        self._defs[stage.stage_def.stage_id] = stage.stage_def
        logger.info(
            f"[StageService] 注册阶段 | id={stage.stage_def.stage_id} name={stage.stage_def.name}"
        )  # noqa: E501

    async def execute(
        self,
        stage_id: str,
        input_asset_ids: List[str],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        """
        执行生产阶段

        Args:
            stage_id: 阶段 ID
            input_asset_ids: 输入资产 ID 列表
            provider_id: 供应商 ID（空则用默认）
            params: 阶段参数
        """
        # 1. 查找阶段
        stage = self._stages.get(stage_id)
        if not stage:
            raise ValueError(f"未知阶段: {stage_id}，可用: {list(self._stages.keys())}")

        # 2. 获取输入资产
        asset_svc = get_asset_service()
        input_assets = asset_svc.consume_multi(input_asset_ids)
        # 允许无输入资产的阶段（如 concept / batch_storyboard）跳过输入校验
        # concept: input_types=[] 文生图不依赖已有资产
        # batch_storyboard: CSV数据来自 params，不需要传入资产
        if not input_assets and stage.stage_def.input_types:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error="无有效输入资产",
            )

        # 3. 验证输入
        validation_error = stage.validate_inputs(input_assets)
        if validation_error:
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=validation_error,
            )

        # 4. 确定供应商
        if not provider_id:
            provider_id = stage.stage_def.default_provider

        # 4.5 项目归属继承：优先使用 params.project_id，否则从输入资产继承
        # 让 Stage 产物自动归属到输入资产所在的项目
        params = params or {}
        inherited_project_id = params.get("project_id")
        if not inherited_project_id and input_assets:
            for a in input_assets:
                if a.project_id:
                    inherited_project_id = a.project_id
                    break
        # 通过 ContextVar 传递给 _register_asset（避免单例实例属性的并发竞态）
        _current_project_id_var.set(inherited_project_id)

        # 4.6 提示词中心集成：若 params 含 prompt_id，自动解析为 prompt 字符串
        # 设计原则：Stage 代码完全不变，仍读取 params["prompt"]，此处只负责注入
        # 阶段 C：若未显式传入 prompt 且无 prompt_id，自动使用项目默认提示词
        params["_stage_id_for_default"] = stage_id  # 供 _resolve_prompt_params 查找项目默认
        params = _resolve_prompt_params(params)
        params.pop("_stage_id_for_default", None)  # 清理临时字段

        # 5. 执行
        logger.info(
            f"[StageService] 执行阶段 | stage={stage_id} "
            f"inputs={[a.asset_id for a in input_assets]} provider={provider_id} "
            f"project={inherited_project_id or '-'}"
        )
        start = time.time()
        # 清空上次 prompt_id（避免误用，ContextVar 在并行 Task 中独立）
        _last_prompt_id_var.set("")
        try:
            result = await stage.execute(
                input_assets=input_assets,
                provider_id=provider_id,
                params=params or {},
            )
            elapsed = int((time.time() - start) * 1000)
            result.elapsed_ms = elapsed
            # 提取 prompt_id（由 _register_asset 通过 ContextVar 捕获）
            if not result.prompt_id:
                result.prompt_id = _last_prompt_id_var.get()
            logger.info(
                f"[StageService] 阶段完成 | stage={stage_id} "
                f"success={result.success} elapsed={elapsed}ms"
            )
            return result
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            logger.error(f"[StageService] 阶段失败 | stage={stage_id} error={e}")
            return AssetProduceResult(
                asset=AssetRef(asset_id="", asset_type="", name=""),
                success=False,
                error=str(e),
                elapsed_ms=elapsed,
            )

    def resolve(self, input_types: List[str]) -> List[StageDef]:
        """根据输入类型查找可用的阶段"""
        results = []
        input_set = set(input_types)
        for stage_def in self._defs.values():
            required = set(stage_def.input_types)
            if required.issubset(input_set) or not required:
                results.append(stage_def)
        return results

    def list_stages(self) -> List[Dict[str, Any]]:
        """列出所有阶段"""
        return [
            {
                "stage_id": d.stage_id,
                "name": d.name,
                "input_types": d.input_types,
                "input_content_types": d.input_content_types,
                "output_type": d.output_type,
                "default_provider": d.default_provider,
                "supported_providers": d.supported_providers,
                "description": d.description,
                "has_plugin": d.stage_id in self._stages,
            }
            for d in self._defs.values()
        ]

    def get_stage_def(self, stage_id: str) -> Optional[StageDef]:
        """获取阶段定义"""
        return self._defs.get(stage_id)


# ============================================================
# 单例
# ============================================================

_instance: Optional[StageService] = None


def get_stage_service() -> StageService:
    global _instance
    if _instance is None:
        _instance = StageService()
    return _instance


def reset_stage_service():
    """重置单例，用于单元测试隔离"""
    global _instance
    _instance = None


# ============================================================
# 提示词中心集成：prompt_id → prompt 字符串自动解析
# ============================================================


def _resolve_prompt_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """解析 params 中的 prompt_id，注入 prompt 字符串

    支持两种模式：
    1. prompt_id + prompt_variables：从提示词库解析
       params = {
           "prompt_id": "prompt_xxx",
           "prompt_variables": {"character": "小明", "action": "走路"}
       }
       → params["prompt"] = "小明正在走路..."

    2. prompt_a_id / prompt_b_id：CSV 批量分镜的区域提示词
       params = {
           "prompt_a_id": "prompt_yyy",
           "prompt_b_id": "prompt_zzz",
           "prompt_variables": {"scene": "教室"}
       }
       → params["prompt_a"] = "..."
       → params["prompt_b"] = "..."

    继承优先级（从高到低）：
    1. params.prompt（用户显式传入）       ← 最高
    2. params.prompt_id（引用提示词库）
    3. 项目+阶段的默认提示词               ← 阶段 C 新增
    4. 无（保持原样）

    设计原则：
    - 若 prompt_id 不存在或解析失败，保持 params 原样（不影响现有流程）
    - 若 params 已有 prompt 字段，prompt_id 解析结果不覆盖（用户显式传入优先）
    - Stage 代码完全不变，仍读取 params["prompt"]
    """
    prompt_id = params.get("prompt_id")
    prompt_a_id = params.get("prompt_a_id")
    prompt_b_id = params.get("prompt_b_id")
    prompt_global_id = params.get("prompt_global_id")

    # 没有任何 prompt_id，且无显式 prompt → 尝试项目默认提示词
    if not any([prompt_id, prompt_a_id, prompt_b_id, prompt_global_id]):
        # 若用户已显式传入 prompt，不覆盖
        if params.get("prompt"):
            return params
        # ⭐ 阶段 C：项目默认提示词继承
        project_id = params.get("project_id", "")
        stage_id = params.get("_stage_id_for_default", "")  # 由调用方注入
        if project_id:
            try:
                from services.prompt_service import get_prompt_service

                svc = get_prompt_service()
                default_entry = svc.get_default(project_id, stage_id)
                if default_entry:
                    # 解析默认提示词的变量（使用默认值）
                    var_map = {}
                    for v in default_entry.variables:
                        name = v.get("name", "")
                        if name:
                            var_map[name] = v.get("default", "")
                    result = svc.resolve(default_entry.prompt_id, var_map)
                    if result:
                        params["prompt"] = result[0]
                        params["_from_default_prompt"] = True
                        logger.info(
                            f"[StageService] 使用项目默认提示词 | project={project_id} | stage={stage_id or '通用'} | id={default_entry.prompt_id}"  # noqa: E501
                        )  # noqa: E501
            except Exception as e:
                logger.warning(f"[StageService] 项目默认提示词解析失败（忽略）: {e}")
        return params

    try:
        from services.prompt_service import get_prompt_service

        svc = get_prompt_service()
        variables = params.get("prompt_variables") or {}

        # 主提示词
        if prompt_id:
            result = svc.resolve(prompt_id, variables)
            if result:
                resolved, entry = result
                # 仅在用户未显式传入 prompt 时注入
                if not params.get("prompt"):
                    params["prompt"] = resolved
                    logger.info(
                        f"[StageService] prompt_id 解析 | id={prompt_id} | len={len(resolved)}"
                    )  # noqa: E501
                # ⭐ Phase 5：合并提示词携带的生成参数（steps/cfg/width 等）
                # 仅合并用户未显式传入的参数，避免覆盖用户选择
                # ⭐ 修复 P1 #4：增加 stage 级白名单，防止参数跨阶段泄露
                # （如 concept 的 steps/cfg 不应合并到 video stage）
                stage_id = params.get("_stage_id_for_default", "")
                _STAGE_PARAM_WHITELIST = {
                    "concept": {
                        "size",
                        "width",
                        "height",
                        "steps",
                        "cfg",
                        "seed",
                        "content_type",
                        "style",
                        "model",
                    },  # noqa: E501
                    "storyboard": {"size", "steps", "cfg", "template", "content_type"},
                    "video": {
                        "resolution",
                        "width",
                        "height",
                        "duration",
                        "fps",
                        "frame_count",
                        "cfg",
                        "seed",
                        "model",
                    },  # noqa: E501
                    "refine": {"size", "width", "height", "steps", "cfg", "seed", "denoise"},
                    "export": {"format", "quality", "fps"},
                    "batch_storyboard": {"size", "steps", "cfg", "csv_data"},
                    "multi_person": {"size", "steps", "cfg", "template"},
                }
                allowed_keys = _STAGE_PARAM_WHITELIST.get(stage_id)
                if entry.params:
                    merged_keys = []
                    for k, v in entry.params.items():
                        # 跳过 prompt 相关字段（已单独处理）
                        if k in {"prompt", "prompt_id", "prompt_variables"}:
                            continue
                        # 用户已显式传入的不覆盖
                        if k in params:
                            continue
                        # stage 白名单过滤：未识别的 stage 放行全部（向后兼容）
                        if allowed_keys and k not in allowed_keys:
                            continue
                        params[k] = v
                        merged_keys.append(k)
                    if merged_keys:
                        logger.info(
                            f"[StageService] 提示词参数合并 | id={prompt_id} | stage={stage_id} | keys={merged_keys}"  # noqa: E501
                        )  # noqa: E501

        # CSV 批量分镜的区域提示词
        if prompt_a_id:
            result = svc.resolve(prompt_a_id, variables)
            if result and not params.get("prompt_a"):
                params["prompt_a"] = result[0]

        if prompt_b_id:
            result = svc.resolve(prompt_b_id, variables)
            if result and not params.get("prompt_b"):
                params["prompt_b"] = result[0]

        if prompt_global_id:
            result = svc.resolve(prompt_global_id, variables)
            if result and not params.get("prompt_global"):
                params["prompt_global"] = result[0]

    except Exception as e:
        logger.warning(f"[StageService] prompt_id 解析失败（忽略，使用原 params）: {e}")

    return params


def build_reference_images(
    input_assets: List[AssetRef],
    multi_group: bool = False,
) -> List[Dict[str, str]]:
    """从输入资产构建参考图列表，按 asset_type 映射到 workflow_refs 的 key

    消除 multi_person_stage 和 layered_render_stage 中的重复逻辑。

    映射规则：
        - concept/multi_view/storyboard → character, character2, character3...
        - pose → pose
        - depth → depth
        - lineart → mask
        - 其他 → 原始 asset_type

    Args:
        input_assets: 输入资产列表
        multi_group: True=分层渲染（前2个角色为A组，后续为B组 character3/4...）
                     False=双人渲染（只有 character/character2）

    Returns:
        [{"url": ..., "role": ..., "type": ..., "name": ...}, ...]
    """
    reference_images = []
    char_count = 0
    for asset in input_assets:
        url = next((u for u in (asset.urls or []) if u), "")
        if not url:
            continue
        atype = asset.asset_type
        if atype in (
            "concept",
            "multi_view",
            "storyboard",
            "storyboard_multi",
            "storyboard_layered",
        ):  # noqa: E501
            char_count += 1
            if multi_group:
                if char_count <= 2:
                    ref_type = "character" if char_count == 1 else "character2"
                else:
                    ref_type = f"character{char_count}"
            else:
                ref_type = "character" if char_count == 1 else "character2"
        elif atype == "pose":
            ref_type = "pose"
        elif atype == "depth":
            ref_type = "depth"
        elif atype == "lineart":
            ref_type = "mask"
        else:
            ref_type = atype

        reference_images.append(
            {
                "url": url,
                "role": ref_type,
                "type": ref_type,
                "name": asset.name,
            }
        )
    return reference_images


def collect_content_type(input_assets: List[AssetRef]) -> str:
    """从输入资产中收集第一个非空的 content_type

    消除 batch_storyboard/edit/layered_render/multi_person/storyboard 等
    Stage 中重复的 content_type 收集逻辑。

    Args:
        input_assets: 输入资产列表

    Returns:
        第一个非空 content_type，无则返回空字符串
    """
    for a in input_assets:
        if a.content_type:
            return a.content_type
    return ""
