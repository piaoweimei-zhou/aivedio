"""FastAPI 依赖注入工厂函数

为所有 Service 提供 Depends 工厂函数，让 API 路由显式声明依赖，替代模块内 import 单例。

使用方式：
    from fastapi import Depends
    from api.dependencies import get_asset_service_dep, get_gen_task_manager_dep

    @router.post("/assets")
    async def create_asset(
        asset_svc: AssetService = Depends(get_asset_service_dep),
    ):
        ...

优势：
- 显式依赖声明，便于单元测试时替换
- 路由签名清晰，可自动生成 OpenAPI 文档
- 统一管理所有 Service 的工厂函数

注意：仍保留 services/*_service.py 中的 get_*_service() 函数，便于非 API 代码使用（如 main.py lifespan）。
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.asset_service import AssetService
    from services.batch_task_service import BatchTaskService
    from services.canvas_service import CanvasService
    from services.comfyui_service import ComfyUIService
    from services.gen_task_manager import GenTaskManager
    from services.preset_service import PresetService
    from services.project_service import ProjectService
    from services.prompt_service import PromptService
    from services.provider_service import ProviderService
    from services.stage_service import StageService
    from services.workflow_template_service import WorkflowTemplateService
    from services.ws_service import WsConnectionManager


def get_asset_service_dep() -> "AssetService":
    """AssetService 依赖工厂（用于 FastAPI Depends）"""
    from services.asset_service import get_asset_service
    return get_asset_service()


def get_canvas_service_dep() -> "CanvasService":
    """CanvasService 依赖工厂"""
    from services.canvas_service import get_canvas_service
    return get_canvas_service()


def get_comfyui_service_dep() -> "ComfyUIService":
    """ComfyUIService 依赖工厂"""
    from services.comfyui_service import get_comfyui_service
    return get_comfyui_service()


def get_batch_task_service_dep() -> "BatchTaskService":
    """BatchTaskService 依赖工厂"""
    from services.batch_task_service import get_batch_task_service
    return get_batch_task_service()


def get_gen_task_manager_dep() -> "GenTaskManager":
    """GenTaskManager 依赖工厂"""
    from services.gen_task_manager import get_gen_task_manager
    return get_gen_task_manager()


def get_project_service_dep() -> "ProjectService":
    """ProjectService 依赖工厂"""
    from services.project_service import get_project_service
    return get_project_service()


def get_preset_service_dep() -> "PresetService":
    """PresetService 依赖工厂"""
    from services.preset_service import get_preset_service
    return get_preset_service()


def get_prompt_service_dep() -> "PromptService":
    """PromptService 依赖工厂"""
    from services.prompt_service import get_prompt_service
    return get_prompt_service()


def get_provider_service_dep() -> "ProviderService":
    """ProviderService 依赖工厂"""
    from services.provider_service import get_provider_service
    return get_provider_service()


def get_stage_service_dep() -> "StageService":
    """StageService 依赖工厂"""
    from services.stage_service import get_stage_service
    return get_stage_service()


def get_workflow_template_service_dep() -> "WorkflowTemplateService":
    """WorkflowTemplateService 依赖工厂"""
    from services.workflow_template_service import get_workflow_template_service
    return get_workflow_template_service()


def get_ws_manager_dep() -> "WsConnectionManager":
    """WsConnectionManager 依赖工厂"""
    from services.ws_service import get_ws_manager
    return get_ws_manager()
