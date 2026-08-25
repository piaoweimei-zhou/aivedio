"""
项目服务 (ProjectService)
导演工作台 — 项目维度管理

核心职责：
- 项目 CRUD（创建/读取/更新/删除）
- JSON 文件持久化（data/projects/{project_id}.json）
- 项目-资产关联查询（通过 AssetService 的 project_id 字段过滤）
- 项目统计（资产数、任务数）

设计原则：
- 向后兼容：现有资产无 project_id 视为"全局/未分类"
- 不破坏现有 AssetService 接口
- 轻量级，无数据库依赖
"""
from services.paths import PROJECTS_DIR

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 持久化目录（相对于 backend 工作目录）
_DEFAULT_PROJECTS_DIR = PROJECTS_DIR


@dataclass
class Project:
    """项目数据模型"""
    project_id: str
    name: str
    description: str = ""
    status: str = "active"          # active / archived / completed
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.project_id:
            self.project_id = f"proj_{uuid.uuid4().hex[:10]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        return cls(
            project_id=data.get("project_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            status=data.get("status", "active"),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )


class ProjectService:
    """项目服务"""

    def __init__(self, projects_dir: str = ""):
        self._projects_dir = Path(projects_dir or os.path.join(
            os.path.dirname(__file__), "..", _DEFAULT_PROJECTS_DIR
        ))
        self._projects: Dict[str, Project] = {}
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ── 持久化 ──────────────────────────────────────────────

    def _project_file(self, project_id: str) -> Path:
        return self._projects_dir / f"{project_id}.json"

    def _save_project(self, project: Project):
        """保存单个项目到磁盘"""
        try:
            path = self._project_file(project.project_id)
            path.write_text(
                json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[ProjectService] 持久化失败 | id={project.project_id} | error={e}")

    def _delete_project_file(self, project_id: str):
        try:
            path = self._project_file(project_id)
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.warning(f"[ProjectService] 删除文件失败 | id={project_id} | error={e}")

    def _load(self):
        """从磁盘加载所有项目"""
        if not self._projects_dir.exists():
            return
        loaded = 0
        for path in self._projects_dir.glob("proj_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                project = Project.from_dict(data)
                self._projects[project.project_id] = project
                loaded += 1
            except Exception as e:
                logger.warning(f"[ProjectService] 加载失败 | file={path.name} | error={e}")
        if loaded:
            logger.info(f"[ProjectService] 加载完成 | count={loaded}")

    # ── CRUD ────────────────────────────────────────────────

    def create(
        self,
        name: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Project:
        """创建项目"""
        project = Project(
            project_id="",
            name=name,
            description=description,
            metadata=metadata or {},
        )
        self._projects[project.project_id] = project
        self._save_project(project)
        logger.info(f"[ProjectService] 创建项目 | id={project.project_id} | name={name}")
        return project

    def get(self, project_id: str) -> Optional[Project]:
        """获取单个项目"""
        return self._projects.get(project_id)

    def list_projects(self, status: Optional[str] = None) -> List[Project]:
        """列出项目（支持状态过滤）"""
        results = list(self._projects.values())
        if status:
            results = [p for p in results if p.status == status]
        return sorted(results, key=lambda p: p.updated_at, reverse=True)

    def update(
        self,
        project_id: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Project]:
        """更新项目"""
        project = self._projects.get(project_id)
        if not project:
            return None
        if name is not None:
            project.name = name
        if description is not None:
            project.description = description
        if status is not None:
            project.status = status
        if metadata is not None:
            project.metadata.update(metadata)
        project.updated_at = time.time()
        self._save_project(project)
        return project

    def delete(self, project_id: str) -> bool:
        """删除项目（不删除关联资产，资产 project_id 变为悬空）"""
        if project_id not in self._projects:
            return False
        del self._projects[project_id]
        self._delete_project_file(project_id)
        logger.info(f"[ProjectService] 删除项目 | id={project_id}")
        return True

    # ── 聚合查询 ────────────────────────────────────────────

    def get_stats(self, project_id: str) -> Dict[str, Any]:
        """获取项目统计（资产数按类型分组）"""
        project = self._projects.get(project_id)
        if not project:
            return {}

        # 延迟导入避免循环依赖
        from services.asset_service import get_asset_service
        asset_svc = get_asset_service()
        assets = asset_svc.list_assets(project_id=project_id)

        # 按类型分组统计
        type_counts: Dict[str, int] = {}
        for a in assets:
            type_counts[a.asset_type] = type_counts.get(a.asset_type, 0) + 1

        return {
            "project_id": project_id,
            "asset_count": len(assets),
            "type_counts": type_counts,
            "updated_at": project.updated_at,
        }


# ============================================================
# 单例
# ============================================================

_instance: Optional[ProjectService] = None


def get_project_service() -> ProjectService:
    global _instance
    if _instance is None:
        _instance = ProjectService()
    return _instance
