"""
预设服务 (PresetService)

保存单个 Stage 的参数配置 + 参考资产，方便反复使用。
解决"每次重来"问题，提升复用效率。

核心职责：
- 预设 CRUD（创建/读取/更新/删除）
- JSON 持久化
- 项目级默认预设绑定

设计原则：
- 纯新增，不修改现有 Stage/Asset/Batch 逻辑
- 预设只是参数快照，应用时由前端填充到表单
- 向后兼容：现有页面不使用预设也能正常工作

Preset 模型：
- preset_id: 唯一标识
- name: 预设名称
- project_id: 所属项目（空=全局预设）
- stage_id: 对应的 Stage ID（如 video / storyboard / concept）
- provider_id: 供应商（可选）
- params: 参数字典（prompt / seed / size 等）
- reference_asset_ids: 参考资产 ID 列表
- is_default: 是否为项目的默认预设
- created_at / updated_at
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PRESET_DIR = "data/presets"


@dataclass
class Preset:
    """任务预设"""
    preset_id: str
    name: str
    stage_id: str                                    # 对应 Stage ID
    project_id: str = ""                             # 所属项目（空=全局）
    provider_id: str = ""                            # 供应商
    params: Dict[str, Any] = field(default_factory=dict)  # 阶段参数
    reference_asset_ids: List[str] = field(default_factory=list)  # 参考资产
    is_default: bool = False                         # 项目默认预设
    description: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Preset":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class PresetService:
    """预设服务"""

    def __init__(self, preset_dir: str = _PRESET_DIR):
        self._preset_dir = Path(preset_dir)
        self._preset_dir.mkdir(parents=True, exist_ok=True)
        self._presets: Dict[str, Preset] = {}
        self._load()

    def _load(self):
        if self._preset_dir.exists():
            for path in self._preset_dir.glob("preset_*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    preset = Preset.from_dict(data)
                    self._presets[preset.preset_id] = preset
                except Exception as e:
                    logger.warning(f"[Preset] 加载失败 | file={path.name} | error={e}")
        logger.info(f"[Preset] 加载 {len(self._presets)} 个预设")

    def _save(self, preset: Preset):
        try:
            data = preset.to_dict()
            self._preset_dir.joinpath(f"{preset.preset_id}.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Preset] 持久化失败 | id={preset.preset_id} | error={e}")

    # ================================================================
    # CRUD
    # ================================================================

    def list_presets(
        self,
        project_id: str = "",
        stage_id: str = "",
    ) -> List[Preset]:
        """列出预设"""
        presets = list(self._presets.values())
        if project_id:
            # 包含全局预设（project_id 为空）和指定项目的预设
            presets = [p for p in presets if p.project_id == project_id or p.project_id == ""]
        if stage_id:
            presets = [p for p in presets if p.stage_id == stage_id]
        return sorted(presets, key=lambda p: (p.project_id != "", -p.created_at))

    def get(self, preset_id: str) -> Optional[Preset]:
        return self._presets.get(preset_id)

    def create(
        self,
        name: str,
        stage_id: str,
        project_id: str = "",
        provider_id: str = "",
        params: Dict[str, Any] = None,
        reference_asset_ids: List[str] = None,
        description: str = "",
        is_default: bool = False,
        metadata: Dict[str, Any] = None,
    ) -> Preset:
        """创建预设"""
        preset_id = f"preset_{uuid.uuid4().hex[:12]}"
        now = time.time()
        preset = Preset(
            preset_id=preset_id,
            name=name,
            stage_id=stage_id,
            project_id=project_id,
            provider_id=provider_id,
            params=params or {},
            reference_asset_ids=reference_asset_ids or [],
            is_default=False,  # 先创建，再单独设置默认
            description=description,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self._presets[preset_id] = preset
        self._save(preset)

        # 如果设为默认，更新项目其他预设的默认状态
        if is_default and project_id:
            self.set_default(preset_id, project_id)

        logger.info(f"[Preset] 创建预设 | id={preset_id} | name={name} | stage={stage_id}")
        return preset

    def update(self, preset_id: str, updates: Dict[str, Any]) -> Optional[Preset]:
        """更新预设"""
        preset = self._presets.get(preset_id)
        if not preset:
            return None

        if "name" in updates:
            preset.name = updates["name"]
        if "description" in updates:
            preset.description = updates["description"]
        if "stage_id" in updates:
            preset.stage_id = updates["stage_id"]
        if "provider_id" in updates:
            preset.provider_id = updates["provider_id"]
        if "params" in updates:
            preset.params = updates["params"]
        if "reference_asset_ids" in updates:
            preset.reference_asset_ids = updates["reference_asset_ids"]
        if "project_id" in updates:
            preset.project_id = updates["project_id"]
        preset.updated_at = time.time()
        self._save(preset)
        return preset

    def delete(self, preset_id: str) -> bool:
        """删除预设"""
        preset = self._presets.get(preset_id)
        if not preset:
            return False
        del self._presets[preset_id]
        try:
            self._preset_dir.joinpath(f"{preset_id}.json").unlink()
        except Exception:
            pass
        return True

    # ================================================================
    # 项目默认预设
    # ================================================================

    def set_default(self, preset_id: str, project_id: str) -> bool:
        """设置项目默认预设"""
        preset = self._presets.get(preset_id)
        if not preset:
            return False

        # 取消该项目下其他默认预设
        for p in self._presets.values():
            if p.project_id == project_id and p.is_default and p.preset_id != preset_id:
                p.is_default = False
                self._save(p)

        preset.is_default = True
        preset.project_id = project_id
        self._save(preset)
        logger.info(f"[Preset] 设置默认 | preset={preset_id} | project={project_id}")
        return True

    def get_default(self, project_id: str, stage_id: str = "") -> Optional[Preset]:
        """获取项目默认预设"""
        for p in self._presets.values():
            if p.project_id == project_id and p.is_default:
                if stage_id and p.stage_id != stage_id:
                    continue
                return p
        return None

    def apply(self, preset_id: str) -> Optional[Dict[str, Any]]:
        """应用预设 — 返回参数快照供前端填充表单"""
        preset = self._presets.get(preset_id)
        if not preset:
            return None
        return {
            "preset_id": preset.preset_id,
            "stage_id": preset.stage_id,
            "provider_id": preset.provider_id,
            "params": dict(preset.params),
            "reference_asset_ids": list(preset.reference_asset_ids),
        }


# ============================================================
# 单例
# ============================================================

_instance: Optional[PresetService] = None


def get_preset_service() -> PresetService:
    global _instance
    if _instance is None:
        _instance = PresetService()
    return _instance
