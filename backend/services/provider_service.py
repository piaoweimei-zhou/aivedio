"""
供应商抽象层服务 (ProviderService)
导演工作台 Layer 2：统一管理所有 AI 供应商

核心职责：
- 供应商注册/发现
- 统一生成接口（图像/视频/文本）
- API Key 动态检测
- 结果模型统一（ProviderResult）

供应商来源：
- ComfyUI (本地开源) — 包装 ai-ide-v2 的 comfyui_service
- 即梦 CLI (闭源视频) — 从 Infinite-Canvas 提取
- RunningHub (闭源图+视频) — 从 Infinite-Canvas 提取
- 火山引擎 (闭源图+视频) — 从 Infinite-Canvas 提取
- OpenAI 兼容 (闭源图) — 从 Infinite-Canvas 提取
- Gemini (闭源图) — 从 Infinite-Canvas 提取
- ModelScope (闭源图+三视图) — 从 Infinite-Canvas 提取
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# 统一结果模型
# ============================================================

@dataclass
class ProviderResult:
    """统一供应商结果 — 与 ComfyUIGenResult 对齐"""
    image_url: str = ""
    images: List[str] = field(default_factory=list)
    filenames: List[str] = field(default_factory=list)
    seed: int = 0
    elapsed_ms: int = 0
    prompt: str = ""
    provider_id: str = ""          # 来源供应商
    model: str = ""                # 使用的模型
    raw: Any = None                # 原始响应（调试用）
    prompt_id: str = ""            # ComfyUI prompt_id（用于反查生成历史）

    # 视频扩展字段
    video_url: str = ""            # 视频结果 URL
    duration: float = 0.0          # 视频时长
    last_frame_url: str = ""       # 最后一帧 URL

    # 任务状态（异步供应商）
    task_id: str = ""
    status: str = "succeeded"      # succeeded / running / failed

    # 通用元数据（文本生成放 text/usage；视频放 reference_images 等）
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.images is None:
            self.images = [self.image_url] if self.image_url else []
        if self.filenames is None:
            self.filenames = []

    def to_comfyui_result(self):
        """兼容现有 pipeline_executor 的 ComfyUIGenResult 接口"""
        from services.comfyui_service import ComfyUIGenResult
        return ComfyUIGenResult(
            image_url=self.image_url,
            filename=self.filenames[0] if self.filenames else "",
            images=self.images,
            filenames=self.filenames,
            seed=self.seed,
            elapsed_ms=self.elapsed_ms,
            prompt=self.prompt,
        )


# ============================================================
# 供应商插件基类
# ============================================================

class ProviderPlugin(ABC):
    """供应商插件基类"""

    provider_id: str = ""
    provider_name: str = ""
    capabilities: List[str] = []   # ["image", "video", "text"]

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs
    ) -> ProviderResult:
        """生成图像"""
        ...

    async def generate_video(
        self,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：统一视频参数（具体 provider 按需使用）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs
    ) -> ProviderResult:
        """生成视频（默认未实现）

        参数优先级：
        - frame_count > duration（若提供 frame_count，则 duration 被 frame_count/fps 替代）
        - width/height > resolution（若提供 width/height，则 resolution 被忽略）
        """
        raise NotImplementedError(f"{self.provider_name} 不支持视频生成")

    async def generate_text(
        self,
        prompt: str,
        system: str = "",
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs
    ) -> ProviderResult:
        """生成文本（默认未实现）— 通过 /v1/chat/completions 调用 LLM"""
        raise NotImplementedError(f"{self.provider_name} 不支持文本生成")

    def is_available(self) -> bool:
        """检测供应商是否可用（API Key / CLI 环境等）"""
        return True

    def info(self) -> Dict[str, Any]:
        """供应商信息"""
        return {
            "id": self.provider_id,
            "name": self.provider_name,
            "capabilities": self.capabilities,
            "available": self.is_available(),
        }


# ============================================================
# ProviderService
# ============================================================

class ProviderService:
    """供应商路由服务 — 统一管理所有供应商"""

    def __init__(self):
        self._providers: Dict[str, ProviderPlugin] = {}
        self._register_all()

    def _register_all(self):
        """注册所有供应商（延迟导入避免循环依赖）"""
        # 延迟导入：providers 包依赖本模块的 ProviderPlugin/ProviderResult，
        # 不能在模块级导入，否则会循环依赖
        from services.providers.comfyui_provider import ComfyUIProvider
        from services.providers.openai_provider import OpenAICompatProvider
        from services.providers.runninghub_provider import RunningHubProvider
        from services.providers.jimeng_provider import JimengProvider
        from services.providers.volcengine_provider import VolcEngineProvider
        from services.providers.gemini_provider import GeminiProvider
        from services.providers.modelscope_provider import ModelScopeProvider

        all_providers = [
            ComfyUIProvider(),
            OpenAICompatProvider(),
            RunningHubProvider(),
            JimengProvider(),
            VolcEngineProvider(),
            GeminiProvider(),
            ModelScopeProvider(),
        ]
        for p in all_providers:
            self._providers[p.provider_id] = p
            available = p.is_available()
            logger.info(
                f"[ProviderService] 注册供应商 | id={p.provider_id} "
                f"name={p.provider_name} available={available}"
            )

    async def generate_image(
        self,
        provider_id: str,
        prompt: str,
        size: str = "1024x1024",
        model: str = "",
        reference_images: Optional[List[Dict]] = None,
        **kwargs
    ) -> ProviderResult:
        """统一图像生成接口"""
        provider = self._providers.get(provider_id)
        if not provider:
            available = [p for p, v in self._providers.items() if "image" in v.capabilities]
            raise ValueError(f"供应商 {provider_id} 不可用，支持图像的供应商: {available}")
        if not provider.is_available():
            raise ValueError(f"供应商 {provider_id} 未配置 API Key 或环境")
        return await provider.generate_image(
            prompt=prompt,
            size=size,
            model=model,
            reference_images=reference_images,
            **kwargs
        )

    async def generate_video(
        self,
        provider_id: str,
        prompt: str,
        images: Optional[List[str]] = None,
        videos: Optional[List[str]] = None,
        model: str = "",
        duration: float = 5.0,
        aspect_ratio: str = "16:9",
        resolution: str = "480p",
        # ⭐ 修复 A2：显式声明视频核心参数（替代 **kwargs 隐式传递）
        width: Optional[int] = None,
        height: Optional[int] = None,
        frame_count: Optional[int] = None,
        seed: Optional[int] = None,
        fps: Optional[int] = None,
        **kwargs
    ) -> ProviderResult:
        """统一视频生成接口

        参数说明：
        - duration: 视频时长（秒），与 frame_count/fps 互为换算关系
        - frame_count: 视频总帧数（优先于 duration，若提供则忽略 duration）
        - fps: 帧率（默认 24，部分 provider 可能固定）
        - width/height: 视频分辨率（像素，优先于 resolution 字符串）
        - resolution: 分辨率档位（"480p"/"720p"/"1080p"），当 width/height 未提供时使用
        """
        # ⭐ 断裂点4修复：VideoGenerationParams DTO 统一解析
        # 在入口处用 DTO 一次性解析所有参数（resolution/width/height + duration/frame_count/fps）
        # 消除散参数传递的语义混乱，确保下游 provider 拿到的都是解析后的明确值
        try:
            from services.workflow_params import VideoGenerationParams
            normalized = VideoGenerationParams(
                prompt=prompt,
                width=width,
                height=height,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                frame_count=frame_count,
                duration=duration,
                fps=fps,
                seed=seed if seed is not None else -1,
            )
            # 用解析后的值覆盖散参数（resolution/aspect_ratio 不再传递，已转为 width/height）
            width = normalized.width
            height = normalized.height
            frame_count = normalized.frame_count
            duration = normalized.duration if normalized.duration is not None else duration
            fps = normalized.fps if normalized.fps is not None else fps
        except Exception as e:
            # DTO 解析失败不阻断主流程，记录警告后用原始散参数
            import logging
            logging.getLogger(__name__).warning(
                f"[ProviderSvc] VideoGenerationParams 解析失败，降级散参数: {e}"
            )

        provider = self._providers.get(provider_id)
        if not provider:
            available = [p for p, v in self._providers.items() if "video" in v.capabilities]
            raise ValueError(f"供应商 {provider_id} 不可用，支持视频的供应商: {available}")
        if not provider.is_available():
            raise ValueError(f"供应商 {provider_id} 未配置 API Key 或环境")
        return await provider.generate_video(
            prompt=prompt,
            images=images,
            videos=videos,
            model=model,
            duration=duration,
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            width=width,
            height=height,
            frame_count=frame_count,
            seed=seed,
            fps=fps,
            **kwargs
        )

    async def generate_text(
        self,
        provider_id: str,
        prompt: str,
        system: str = "",
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 4096,
        response_format: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs
    ) -> ProviderResult:
        """统一文本生成接口（LLM 路由）"""
        provider = self._providers.get(provider_id)
        if not provider:
            available = [p for p, v in self._providers.items() if "text" in v.capabilities]
            raise ValueError(f"供应商 {provider_id} 不可用，支持文本的供应商: {available}")
        if not provider.is_available():
            raise ValueError(f"供应商 {provider_id} 未配置 API Key 或环境")
        return await provider.generate_text(
            prompt=prompt,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            history=history,
            **kwargs
        )

    def available_providers(self, capability: str = "") -> List[Dict[str, Any]]:
        """返回可用供应商列表"""
        result = []
        for p in self._providers.values():
            if capability and capability not in p.capabilities:
                continue
            result.append(p.info())
        return result

    def pre_check_batch(self, steps: List[Any]) -> Dict[str, Any]:
        """批量任务启动前的 Provider 健康检查

        检查所有步骤用到的 provider 是否可用，避免执行到一半才发现 provider 不可用。

        Args:
            steps: BatchStep 列表（只需 stage_id 和 provider_id 字段）

        Returns:
            {
                "ok": bool,           # 是否全部可用
                "checked": int,       # 检查的步骤数
                "unavailable": [      # 不可用的 provider 列表
                    {"step_id": "xxx", "stage_id": "concept", "provider_id": "comfyui", "reason": "..."}
                ],
                "providers_status": [ # 所有 provider 状态
                    {"provider_id": "comfyui", "available": False, "name": "ComfyUI"}
                ]
            }
        """
        from services.stage_service import get_stage_service

        stage_svc = get_stage_service()
        unavailable: List[Dict[str, Any]] = []
        providers_checked: Dict[str, bool] = {}

        for step in steps:
            stage = stage_svc._stages.get(step.stage_id)
            if not stage:
                unavailable.append({
                    "step_id": step.step_id,
                    "stage_id": step.stage_id,
                    "provider_id": step.provider_id or "",
                    "reason": f"未知阶段: {step.stage_id}",
                })
                continue

            # 确定该步骤使用的 provider
            provider_id = step.provider_id or stage.stage_def.default_provider

            # 缓存 provider 检查结果
            if provider_id not in providers_checked:
                provider = self._providers.get(provider_id)
                if provider:
                    providers_checked[provider_id] = provider.is_available()
                else:
                    providers_checked[provider_id] = False

            if not providers_checked[provider_id]:
                unavailable.append({
                    "step_id": step.step_id,
                    "stage_id": step.stage_id,
                    "provider_id": provider_id,
                    "reason": f"Provider {provider_id} 不可用",
                })

        # 构建 provider 状态列表
        providers_status = []
        for pid, available in providers_checked.items():
            p = self._providers.get(pid)
            providers_status.append({
                "provider_id": pid,
                "name": p.provider_name if p else pid,
                "available": available,
            })

        return {
            "ok": len(unavailable) == 0,
            "checked": len(steps),
            "unavailable": unavailable,
            "providers_status": providers_status,
        }

    def health_check(self) -> List[Dict[str, Any]]:
        """所有 Provider 健康检查（用于 /api/providers/health 端点）

        Returns:
            [{"provider_id": "comfyui", "name": "ComfyUI", "available": False, "capabilities": [...]}]
        """
        result = []
        for p in self._providers.values():
            try:
                available = p.is_available()
            except Exception as e:
                available = False
                logger.warning(f"[ProviderService] 健康检查异常 | provider={p.provider_id} | error={e}")
            result.append({
                "provider_id": p.provider_id,
                "name": p.provider_name,
                "available": available,
                "capabilities": list(p.capabilities),
            })
        return result

    def get_provider(self, provider_id: str) -> Optional[ProviderPlugin]:
        """获取供应商实例"""
        return self._providers.get(provider_id)


# ============================================================
# 单例
# ============================================================

_instance: Optional[ProviderService] = None

def get_provider_service() -> ProviderService:
    global _instance
    if _instance is None:
        _instance = ProviderService()
    return _instance


def reset_provider_service():
    """重置单例，用于单元测试隔离"""
    global _instance
    _instance = None
