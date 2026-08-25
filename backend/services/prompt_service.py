"""
提示词中心服务 (PromptService)

集中管理所有阶段的提示词，支持变量替换、分类标签、项目级隔离。
解决"提示词散落、无法复用、无法统一管理"的痛点。

核心职责：
- 提示词库 CRUD（创建/读取/更新/删除）
- 变量模板解析（{variable} 占位符替换）
- 分类/标签/搜索
- 项目级隔离 + 全局共享
- 质量评分 + 使用统计
- JSON 持久化

设计原则：
- 纯新增，不修改现有 Stage 逻辑
- 现有 params.prompt 传入方式完全兼容
- Prompt Hub 只负责"生成最终 prompt 字符串"，Stage 拿到的还是 string

PromptEntry 模型：
- prompt_id: 唯一标识
- name: 提示词名称
- category: 分类（action/dialogue/scene/transition/style/custom）
- stage_id: 绑定阶段（空=通用）
- content: 提示词内容（支持 {variable} 占位符）
- variables: 变量定义列表 [{name, default, description}]
- tags: 标签列表
- project_id: 所属项目（空=全局）
- quality_score: 质量评分（0-5）
- usage_count: 使用次数
- created_at / updated_at
"""
from services.paths import PROMPTS_DIR

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_PROMPT_DIR = PROMPTS_DIR

# 变量占位符正则：{variable_name}
_VAR_PATTERN = re.compile(r"\{(\w+)\}")


@dataclass
class PromptVariable:
    """提示词变量定义"""
    name: str                           # 变量名
    default: str = ""                   # 默认值
    description: str = ""               # 变量说明
    required: bool = False              # 是否必填

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PromptVariable":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PromptEntry:
    """提示词条目"""
    prompt_id: str
    name: str
    content: str                                     # 提示词内容（支持 {variable}）
    category: str = "custom"                         # action/dialogue/scene/transition/style/custom
    stage_id: str = ""                               # 绑定阶段（空=通用）
    variables: List[Dict[str, Any]] = field(default_factory=list)  # 变量定义
    tags: List[str] = field(default_factory=list)    # 标签
    project_id: str = ""                             # 所属项目（空=全局）
    quality_score: float = 0.0                       # 质量评分 0-5
    usage_count: int = 0                             # 使用次数
    description: str = ""                            # 提示词说明
    is_default: bool = False                         # 是否为项目+阶段的默认提示词
    version: int = 1                                 # 当前版本号
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    # ⭐ Phase 5：提示词系统与参数系统合并
    # 一个提示词条目可携带关联的工作流参数（steps/cfg/width/height 等）
    # 用户选择"预设风格"时，一次获取 prompt + 所有生成参数
    # 现有数据无此字段时默认空 dict，完全向后兼容
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PromptEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def extract_variables(self) -> List[str]:
        """从 content 中提取所有 {variable} 占位符"""
        return list(set(_VAR_PATTERN.findall(self.content)))


class PromptService:
    """提示词中心服务"""

    def __init__(self, prompt_dir: str = _PROMPT_DIR):
        self._prompt_dir = Path(prompt_dir)
        self._prompt_dir.mkdir(parents=True, exist_ok=True)
        self._history_dir = self._prompt_dir / "history"
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._prompts: Dict[str, PromptEntry] = {}
        self._load()

    def _load(self):
        if self._prompt_dir.exists():
            for path in self._prompt_dir.glob("prompt_*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entry = PromptEntry.from_dict(data)
                    self._prompts[entry.prompt_id] = entry
                except Exception as e:
                    logger.warning(f"[Prompt] 加载失败 | file={path.name} | error={e}")
        logger.info(f"[Prompt] 加载 {len(self._prompts)} 个提示词")

    def _save(self, entry: PromptEntry):
        try:
            data = entry.to_dict()
            self._prompt_dir.joinpath(f"{entry.prompt_id}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Prompt] 持久化失败 | id={entry.prompt_id} | error={e}")

    # ================================================================
    # CRUD
    # ================================================================

    def list_prompts(
        self,
        project_id: str = "",
        stage_id: str = "",
        category: str = "",
        tag: str = "",
        keyword: str = "",
    ) -> List[PromptEntry]:
        """列出提示词（支持多维度过滤）"""
        prompts = list(self._prompts.values())

        # 项目过滤：包含全局（project_id 为空）+ 指定项目
        if project_id:
            prompts = [p for p in prompts if p.project_id == project_id or p.project_id == ""]
        if stage_id:
            prompts = [p for p in prompts if p.stage_id == stage_id or p.stage_id == ""]
        if category:
            prompts = [p for p in prompts if p.category == category]
        if tag:
            prompts = [p for p in prompts if tag in p.tags]
        if keyword:
            kw = keyword.lower()
            prompts = [
                p for p in prompts
                if kw in p.name.lower() or kw in p.content.lower() or kw in p.description.lower()
            ]

        # 按质量评分降序 + 使用次数降序
        return sorted(prompts, key=lambda p: (-p.quality_score, -p.usage_count, -p.created_at))

    def get(self, prompt_id: str) -> Optional[PromptEntry]:
        return self._prompts.get(prompt_id)

    def create(
        self,
        name: str,
        content: str,
        category: str = "custom",
        stage_id: str = "",
        variables: List[Dict[str, Any]] = None,
        tags: List[str] = None,
        project_id: str = "",
        description: str = "",
        quality_score: float = 0.0,
        metadata: Dict[str, Any] = None,
        params: Dict[str, Any] = None,
    ) -> PromptEntry:
        """创建提示词

        Args:
            params: ⭐ Phase 5 关联的工作流参数（实现"预设风格 = 提示词 + 参数"）
                    如 {"width": 1080, "height": 1920, "steps": 25, "cfg": 2.0}
        """
        prompt_id = f"prompt_{uuid.uuid4().hex[:12]}"
        now = time.time()

        # 自动从 content 提取变量（如果未显式提供）
        entry = PromptEntry(
            prompt_id=prompt_id,
            name=name,
            content=content,
            category=category,
            stage_id=stage_id,
            variables=variables or [],
            tags=tags or [],
            project_id=project_id,
            quality_score=quality_score,
            description=description,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
            params=params or {},  # ⭐ Phase 5：关联工作流参数
        )

        # 如果未提供 variables 定义，自动从 content 提取
        if not entry.variables:
            extracted = entry.extract_variables()
            entry.variables = [
                {"name": v, "default": "", "description": "", "required": False}
                for v in extracted
            ]

        self._prompts[prompt_id] = entry
        self._save(entry)
        logger.info(f"[Prompt] 创建提示词 | id={prompt_id} | name={name} | vars={len(entry.variables)}")
        return entry

    def update(self, prompt_id: str, updates: Dict[str, Any]) -> Optional[PromptEntry]:
        """更新提示词（自动记录版本历史）"""
        entry = self._prompts.get(prompt_id)
        if not entry:
            return None

        # ⭐ 保存版本历史（更新前的快照）
        self._save_history(entry)

        if "name" in updates:
            entry.name = updates["name"]
        if "content" in updates:
            entry.content = updates["content"]
            # content 变更后重新提取变量
            if "variables" not in updates:
                extracted = entry.extract_variables()
                existing_names = {v.get("name") for v in entry.variables}
                new_vars = [v for v in extracted if v not in existing_names]
                entry.variables.extend([
                    {"name": v, "default": "", "description": "", "required": False}
                    for v in new_vars
                ])
        if "category" in updates:
            entry.category = updates["category"]
        if "stage_id" in updates:
            entry.stage_id = updates["stage_id"]
        if "variables" in updates:
            entry.variables = updates["variables"]
        if "tags" in updates:
            entry.tags = updates["tags"]
        if "project_id" in updates:
            entry.project_id = updates["project_id"]
        if "description" in updates:
            entry.description = updates["description"]
        if "quality_score" in updates:
            entry.quality_score = float(updates["quality_score"])
        if "params" in updates:
            # ⭐ Phase 5：支持更新关联工作流参数
            entry.params = updates["params"] or {}

        # 版本号递增
        entry.version += 1
        entry.updated_at = time.time()
        self._save(entry)
        return entry

    # ================================================================
    # 版本历史
    # ================================================================

    def _save_history(self, entry: PromptEntry):
        """保存提示词历史版本快照"""
        try:
            history_file = self._history_dir / f"{entry.prompt_id}_v{entry.version}.json"
            history_file.write_text(
                json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Prompt] 保存历史版本失败 | id={entry.prompt_id} | v={entry.version} | error={e}")

    def get_history(self, prompt_id: str) -> List[Dict[str, Any]]:
        """获取提示词的所有历史版本"""
        versions = []
        if not self._history_dir.exists():
            return versions
        for path in self._history_dir.glob(f"{prompt_id}_v*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                versions.append({
                    "version": data.get("version", 0),
                    "content": data.get("content", ""),
                    "name": data.get("name", ""),
                    "updated_at": data.get("updated_at", 0),
                    "file": path.name,
                })
            except Exception:
                continue
        # 按版本号降序
        return sorted(versions, key=lambda v: -v["version"])

    def rollback(self, prompt_id: str, version: int) -> Optional[PromptEntry]:
        """回滚到指定历史版本"""
        history_file = self._history_dir / f"{prompt_id}_v{version}.json"
        if not history_file.exists():
            return None

        entry = self._prompts.get(prompt_id)
        if not entry:
            return None

        # 保存当前版本到历史
        self._save_history(entry)

        # 从历史版本恢复
        try:
            data = json.loads(history_file.read_text(encoding="utf-8"))
            old_entry = PromptEntry.from_dict(data)
            # 保留当前 prompt_id 和 version（递增）
            old_entry.prompt_id = entry.prompt_id
            old_entry.version = entry.version + 1
            old_entry.updated_at = time.time()
            self._prompts[prompt_id] = old_entry
            self._save(old_entry)
            logger.info(f"[Prompt] 回滚 | id={prompt_id} | 恢复到 v{version} | 新版本 v{old_entry.version}")
            return old_entry
        except Exception as e:
            logger.warning(f"[Prompt] 回滚失败 | id={prompt_id} | v={version} | error={e}")
            return None

    # ================================================================
    # 项目级默认提示词
    # ================================================================

    def set_default(self, prompt_id: str, project_id: str, stage_id: str = "") -> bool:
        """设置项目+阶段的默认提示词

        Args:
            prompt_id: 提示词 ID
            project_id: 项目 ID
            stage_id: 阶段 ID（空=该项目的全局默认）
        """
        entry = self._prompts.get(prompt_id)
        if not entry:
            return False

        # 取消该项目+阶段的其他默认提示词
        for p in self._prompts.values():
            if (p.project_id == project_id
                and p.stage_id == stage_id
                and p.is_default
                and p.prompt_id != prompt_id):
                p.is_default = False
                self._save(p)

        entry.is_default = True
        entry.project_id = project_id
        entry.stage_id = stage_id
        self._save(entry)
        logger.info(f"[Prompt] 设置默认 | id={prompt_id} | project={project_id} | stage={stage_id or '全局'}")
        return True

    def get_default(self, project_id: str, stage_id: str = "") -> Optional[PromptEntry]:
        """获取项目+阶段的默认提示词

        优先级：
        1. 项目 + 阶段 的默认
        2. 项目 + 通用（stage_id 为空）的默认
        3. 全局 + 阶段 的默认
        4. 全局 + 通用 的默认
        """
        # 1. 项目 + 阶段
        for p in self._prompts.values():
            if (p.project_id == project_id
                and p.stage_id == stage_id
                and p.is_default):
                return p
        # 2. 项目 + 通用
        for p in self._prompts.values():
            if (p.project_id == project_id
                and p.stage_id == ""
                and p.is_default):
                return p
        # 3. 全局 + 阶段
        for p in self._prompts.values():
            if (p.project_id == ""
                and p.stage_id == stage_id
                and p.is_default):
                return p
        # 4. 全局 + 通用
        for p in self._prompts.values():
            if (p.project_id == ""
                and p.stage_id == ""
                and p.is_default):
                return p
        return None

    def unset_default(self, prompt_id: str) -> bool:
        """取消默认提示词"""
        entry = self._prompts.get(prompt_id)
        if not entry:
            return False
        entry.is_default = False
        self._save(entry)
        return True

    def delete(self, prompt_id: str) -> bool:
        """删除提示词"""
        entry = self._prompts.get(prompt_id)
        if not entry:
            return False
        del self._prompts[prompt_id]
        try:
            self._prompt_dir.joinpath(f"{prompt_id}.json").unlink()
        except Exception:
            pass
        return True

    # ================================================================
    # 变量解析
    # ================================================================

    def resolve(
        self,
        prompt_id: str,
        variables: Dict[str, str] = None,
    ) -> Optional[Tuple[str, PromptEntry]]:
        """解析提示词 — 替换变量占位符，返回最终 prompt 字符串

        Args:
            prompt_id: 提示词 ID
            variables: 变量值映射 {name: value}

        Returns:
            (resolved_content, entry) 或 None（不存在）
        """
        entry = self._prompts.get(prompt_id)
        if not entry:
            return None

        variables = variables or {}

        # 合并变量默认值
        var_map = {}
        for v in entry.variables:
            name = v.get("name", "")
            if name:
                var_map[name] = v.get("default", "")

        # 用户传入的变量覆盖默认值
        var_map.update(variables)

        # 替换占位符
        def _replace(match):
            var_name = match.group(1)
            return var_map.get(var_name, match.group(0))  # 未找到则保留原占位符

        resolved = _VAR_PATTERN.sub(_replace, entry.content)

        # 更新使用次数
        entry.usage_count += 1
        self._save(entry)

        logger.info(f"[Prompt] 解析提示词 | id={prompt_id} | vars={len(var_map)} | result_len={len(resolved)}")
        return resolved, entry

    def resolve_content(
        self,
        content: str,
        variables: Dict[str, str] = None,
    ) -> str:
        """直接解析提示词内容（不需要 prompt_id）

        Args:
            content: 含 {variable} 占位符的内容
            variables: 变量值映射

        Returns:
            解析后的字符串
        """
        variables = variables or {}

        def _replace(match):
            var_name = match.group(1)
            return variables.get(var_name, match.group(0))

        return _VAR_PATTERN.sub(_replace, content)

    # ================================================================
    # 统计
    # ================================================================

    def get_categories(self) -> List[Dict[str, Any]]:
        """获取所有分类及其数量"""
        cat_map: Dict[str, int] = {}
        for p in self._prompts.values():
            cat_map[p.category] = cat_map.get(p.category, 0) + 1
        return [{"category": k, "count": v} for k, v in sorted(cat_map.items())]

    def get_tags(self) -> List[Dict[str, Any]]:
        """获取所有标签及其数量"""
        tag_map: Dict[str, int] = {}
        for p in self._prompts.values():
            for t in p.tags:
                tag_map[t] = tag_map.get(t, 0) + 1
        return [{"tag": k, "count": v} for k, v in sorted(tag_map.items(), key=lambda x: -x[1])]

    def get_stats(self, project_id: str = "") -> Dict[str, Any]:
        """获取统计信息"""
        prompts = list(self._prompts.values())
        if project_id:
            prompts = [p for p in prompts if p.project_id == project_id or p.project_id == ""]
        return {
            "total": len(prompts),
            "by_category": {c["category"]: c["count"] for c in self.get_categories()},
            "by_stage": {},
            "avg_quality": sum(p.quality_score for p in prompts) / max(len(prompts), 1),
            "total_usage": sum(p.usage_count for p in prompts),
        }


# ============================================================
# 单例
# ============================================================

_instance: Optional[PromptService] = None


def get_prompt_service() -> PromptService:
    global _instance
    if _instance is None:
        _instance = PromptService()
    return _instance
