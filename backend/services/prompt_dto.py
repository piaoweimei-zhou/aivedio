"""提示词数据传输对象（DTO）标准

解决 B3 问题：prompt 字段在多层被重命名为不同名字。

层 → 字段名映射（历史遗留，建议新代码统一使用 prompt）：
- 前端提交      → prompt
- stage_api.py   → params.prompt
- stage.execute  → params.get("prompt")
- provider_svc   → prompt= 参数
- comfyui_provider → prompt_text= 再转为 prompt_json
- workflow_builder → positive_prompt 参数
- qwen_workflow  → prompt_text 参数

统一规则（新代码遵循）：
1. 对外 API（前端 ↔ 后端）：统一使用 `prompt`
2. Service 层参数：统一使用 `prompt`
3. 工作流构建层：保留 `positive_prompt` / `negative_prompt`（ComfyUI 语义）
4. Provider 内部转换：在 provider 边界完成 prompt → prompt_text/prompt_json 转换

此模块仅作为文档化标准，不强制重命名现有代码（避免破坏性变更）。
新代码应遵循此标准。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class PromptDTO:
    """统一的提示词数据传输对象

    用于 Service 层之间传递提示词，替代散落的 prompt/prompt_text/positive_prompt 等字段。
    """

    text: str = ""  # 主提示词文本
    negative: str = ""  # 负向提示词
    variables: Dict[str, Any] = field(default_factory=dict)  # 变量替换映射
    prompt_id: Optional[str] = None  # 提示词库 ID（可选）
    structured: Optional[Dict[str, Any]] = None  # 结构化提示词（ComfyUI prompt_json）

    def to_workflow_params(self) -> Dict[str, str]:
        """转换为工作流构建层参数（positive_prompt / negative_prompt）"""
        return {
            "positive_prompt": self.text,
            "negative_prompt": self.negative,
        }

    def to_provider_params(self) -> Dict[str, str]:
        """转换为 Provider 层参数（prompt）"""
        return {"prompt": self.text}
