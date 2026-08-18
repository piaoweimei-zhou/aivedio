"""
供应商插件包

每个供应商实现 ProviderPlugin 接口，独立文件。
"""

from services.providers.comfyui_provider import ComfyUIProvider
from services.providers.openai_provider import OpenAICompatProvider
from services.providers.runninghub_provider import RunningHubProvider
from services.providers.jimeng_provider import JimengProvider
from services.providers.volcengine_provider import VolcEngineProvider
from services.providers.gemini_provider import GeminiProvider
from services.providers.modelscope_provider import ModelScopeProvider

__all__ = [
    "ComfyUIProvider",
    "OpenAICompatProvider",
    "RunningHubProvider",
    "JimengProvider",
    "VolcEngineProvider",
    "GeminiProvider",
    "ModelScopeProvider",
]
