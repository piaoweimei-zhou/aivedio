"""
Qwen Workflow 配置管理器
支持从 YAML 配置文件动态加载工作流配置
"""

import logging
import os
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List

import yaml

logger = logging.getLogger(__name__)


class QwenWorkflowConfig:
    """Qwen工作流配置管理器"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Optional[str] = None, force_reinit: bool = False):
        """
        初始化配置管理器

        Args:
            config_path: 自定义配置文件路径
            force_reinit: 是否强制重新初始化（用于测试或多环境切换）
        """
        if hasattr(self, "_initialized") and self._initialized and not force_reinit:
            # 如果已初始化且未强制重新初始化，检查是否需要更新路径
            if config_path and config_path != self.config_path:
                logger.warning(
                    f"[QwenConfig] 配置路径已设置为 {self.config_path}，新路径 {config_path} 将被忽略。使用 force_reinit=True 强制重新初始化。"  # noqa: E501
                )  # noqa: E501
            return

        self._initialized = True
        self.config_path = config_path or self._find_config_path()
        self._config_lock = threading.RLock()
        self._fallback_mode = False
        self.config = self._load_config()

    def _find_config_path(self) -> str:
        """查找配置文件路径"""
        possible_paths = [
            "config/qwen_workflow_config.yaml",
            "../config/qwen_workflow_config.yaml",
            "../../config/qwen_workflow_config.yaml",
            os.path.join(os.path.dirname(__file__), "../config/qwen_workflow_config.yaml"),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                logger.info(f"[QwenConfig] 找到配置文件: {path}")
                return path

        logger.warning("[QwenConfig] 未找到配置文件，使用默认配置")
        return None

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not self.config_path or not os.path.exists(self.config_path):
            self._fallback_mode = True
            logger.warning("[QwenConfig] 配置文件不存在，使用默认配置（fallback模式）")
            return self._get_default_config()

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            self._fallback_mode = False
            logger.info(f"[QwenConfig] 配置加载成功: {self.config_path}")
            return config
        except Exception as e:
            self._fallback_mode = True
            logger.warning(f"[QwenConfig] 配置加载失败: {e}，使用默认配置（fallback模式）")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "nodes": {
                "txt_encode": 266,
                "switch": 285,
                "save_image": 205,
                "seed": 274,
                "load_image1": None,
                "load_image2": None,
                "load_image3": None,
            },
            "workflow": {
                "default_file": "Qwen Image Edit - Remix AIO v2.0 全功能合集工作流 By：肥猴 (1).json",
                "search_paths": list(
                    dict.fromkeys(
                        [
                            ".",
                            "./workflows",
                            "../workflows",
                            "../../workflows",
                            "../../../workflows",
                            "/workflows",
                        ]
                    )
                ),  # 去重
                "available_workflows": [
                    {"name": "文生图", "file": "文生图.json", "description": "纯文本生成图像"},
                    {"name": "精修优化", "file": "精修优化.json", "description": "单图编辑优化"},
                    {"name": "分镜优化", "file": "分镜优化.json", "description": "分镜生成与优化"},
                    {"name": "多场景", "file": "多场景.json", "description": "多场景生成"},
                    {"name": "图像放大", "file": "图像放大.json", "description": "图像放大"},
                    {
                        "name": "LTX2.3导演2",
                        "file": "LTX2.3导演2.json",
                        "description": "导演工作流",
                    },
                    {
                        "name": "Qwen3+TTS+音色设计",
                        "file": "Qwen3+TTS+音色设计.json",
                        "description": "TTS音色设计",
                    },  # noqa: E501
                    {
                        "name": "Qwen3+TTS+音频克隆",
                        "file": "Qwen3+TTS+音频克隆.json",
                        "description": "TTS音频克隆",
                    },  # noqa: E501
                    {
                        "name": "seed视频放大工作流",
                        "file": "seed视频放大工作流.json",
                        "description": "视频放大工作流",
                    },
                ],
            },
            "modes": {
                "text_to_image": {"name": "文生图", "switch_index": 1},
                "single_edit": {"name": "单图编辑", "switch_index": 2},
                "inpaint": {"name": "局部重绘", "switch_index": 3},
                "outpaint": {"name": "扩图", "switch_index": 4},
                "fusion": {"name": "多图融合", "switch_index": 5},
            },
            "defaults": {
                "seed": None,
                "width": 1536,
                "height": 1024,
                "filename_prefix": "QwenEdit",
            },
            "stages": {
                "refinement": {"mode": "single_edit", "width": 1536, "height": 1024},
                "standardization": {"mode": "fusion", "width": 2048, "height": 1024},
                "storyboard": {"mode": "fusion", "width": 2048, "height": 1536},
            },
            "prompt_templates": {
                "refinement": {
                    "template": "[KEEP]\n{keep}\n\n[CHANGE]\n{change}\n\n[MAINTAIN]\n{maintain}\n\n[AVOID]\n{avoid}\n\n[FALLBACK]\n{fallback}",  # noqa: E501
                    "defaults": {
                        "keep": "",
                        "change": "",
                        "maintain": "",
                        "avoid": "",
                        "fallback": "",
                    },
                },
                "standardization": {
                    "template": "{character_name}\n{lock_elements}\n{optimizations}",
                    "defaults": {
                        "character_name": "",
                        "lock_elements": "",
                        "optimizations": "",
                    },
                },
            },
        }

    def is_fallback_mode(self) -> bool:
        """检查是否处于 fallback 模式"""
        return self._fallback_mode

    def get_node_id(self, node_name: str) -> Optional[int]:
        """获取节点ID"""
        with self._config_lock:
            return self.config.get("nodes", {}).get(node_name)

    def get_workflow_file_path(self, filename: Optional[str] = None) -> Path:
        """获取工作流文件路径"""
        target_file = filename or self.config.get("workflow", {}).get("default_file", "")

        if not target_file:
            raise ValueError("工作流文件名不能为空")

        # 1. 先尝试配置中的搜索路径
        search_paths = self.config.get("workflow", {}).get("search_paths", ["."])

        for path in search_paths:
            full_path = Path(path) / target_file
            if full_path.exists():
                logger.info(f"[QwenConfig] 找到工作流文件: {full_path}")
                return full_path

        # 2. 尝试相对于当前文件的路径 (backend/services/ -> 项目根目录)
        project_root = Path(__file__).parent.parent.parent
        project_workflows = project_root / "workflows" / target_file
        if project_workflows.exists():
            logger.info(f"[QwenConfig] 从项目根目录找到工作流文件: {project_workflows}")
            return project_workflows

        # 3. 尝试项目根目录直接查找
        project_root_file = project_root / target_file
        if project_root_file.exists():
            logger.info(f"[QwenConfig] 从项目根目录找到工作流文件: {project_root_file}")
            return project_root_file

        # 4. 返回默认路径（可能不存在）
        return project_workflows

    def get_available_workflows(self) -> List[Dict[str, str]]:
        """获取可用工作流列表"""
        with self._config_lock:
            return self.config.get("workflow", {}).get("available_workflows", [])

    def find_workflow_by_name(self, name: str) -> Optional[str]:
        """根据名称查找工作流文件"""
        workflows = self.get_available_workflows()
        for wf in workflows:
            if wf.get("name") == name:
                return wf.get("file")
        return None

    def get_switch_index(self, mode: str) -> int:
        """获取模式对应的Switch索引"""
        with self._config_lock:
            return self.config.get("modes", {}).get(mode, {}).get("switch_index", 2)

    def get_mode_name(self, mode: str) -> str:
        """获取模式显示名称"""
        with self._config_lock:
            return self.config.get("modes", {}).get(mode, {}).get("name", mode)

    def get_default(self, key: str, default: Any = None) -> Any:
        """获取默认参数"""
        with self._config_lock:
            return self.config.get("defaults", {}).get(key, default)

    def get_stage_config(self, stage_name: str) -> Dict[str, Any]:
        """获取阶段配置"""
        with self._config_lock:
            return self.config.get("stages", {}).get(stage_name, {})

    def get_prompt_template(self, stage_name: str) -> Dict[str, str]:
        """获取提示词模板"""
        with self._config_lock:
            return self.config.get("prompt_templates", {}).get(stage_name, {})

    def get_prompt_template_string(self, stage_name: str) -> str:
        """获取提示词模板字符串"""
        template = self.get_prompt_template(stage_name)
        return template.get("template", "")

    def get_prompt_template_defaults(self, stage_name: str) -> Dict[str, str]:
        """获取提示词模板默认值"""
        template = self.get_prompt_template(stage_name)
        return template.get("defaults", {})

    def get_standard_views(self, views_count: int = 3) -> List[str]:
        """获取标准视图列表"""
        with self._config_lock:
            views = self.get_stage_config("standardization").get("views", {})
            return views.get(views_count, views.get(3, ["正面视图", "侧面视图", "背面视图"]))

    def reload(self):
        """重新加载配置"""
        with self._config_lock:
            self.config = self._load_config()
            logger.info("[QwenConfig] 配置已重新加载")

    def update_config(self, updates: Dict[str, Any], persist: bool = True):
        """
        更新配置

        Args:
            updates: 要更新的配置字典
            persist: 是否持久化到磁盘（默认 True）
        """
        with self._config_lock:
            self._deep_update(self.config, updates)
            if persist and self.config_path:
                self._save_config()
            logger.info(f"[QwenConfig] 配置已更新，持久化: {persist}")

    def _save_config(self):
        """将配置保存到磁盘"""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"[QwenConfig] 配置已保存到: {self.config_path}")
        except Exception as e:
            logger.error(f"[QwenConfig] 配置保存失败: {e}")

    def _deep_update(self, d: Dict, u: Dict):
        """深度更新字典"""
        for k, v in u.items():
            if isinstance(v, dict) and k in d and isinstance(d[k], dict):
                self._deep_update(d[k], v)
            else:
                d[k] = v


# 全局配置实例
qwen_config = QwenWorkflowConfig()
