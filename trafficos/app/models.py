"""TrafficOS 数据模型（对齐 docs/01_规划/traffic_contract.openapi.yaml 与规划 v1.4 §7）"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    return time.time()


# ==================== 枚举（对齐契约 / 规划）====================

class Dimension(str, Enum):
    """内容维度：纯内容 / 知识讲解 / 产品软广"""
    PURE_CONTENT = "pure_content"
    KNOWLEDGE = "knowledge"
    SOFT_AD = "soft_ad"


class Monetizer(str, Enum):
    """7 轨变现方式"""
    ADSHARE = "adshare"        # 广告分成
    NETDISK = "netdisk"        # 网盘拉新
    XIANYU = "xianyu"          # 闲鱼工具
    SAAS = "saas"              # SaaS 系统
    RESOURCE = "resource"      # 虚拟资源
    COURSE = "course"          # 网课培训
    TOOL = "tool"              # 小工具产品


class AccountCadence(str, Enum):
    HIGH = "high"        # 高频（日更）
    MEDIUM = "medium"    # 中频（隔日）
    LOW = "low"          # 低频（周更）


# ==================== 内容战略层 ====================

class DimensionConfig(BaseModel):
    """维度配置（ⓞ 内容战略层）"""
    id: str = ""
    code: Dimension
    name: str
    target: str = ""        # 该维度目标（拉流量/建信任/促转化）
    action: str = ""        # 转化动作（主页导流/挂资源/下载购买）
    ratio: float = 0.0      # 生产配比（如 0.40）
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "DimensionConfig":
        self.id = self.id or _new_id("dim")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


class MonetizerConfig(BaseModel):
    """变现方式配置"""
    id: str = ""
    code: Monetizer
    name: str
    description: str = ""
    priority: int = 0       # 优先级（1=最高）
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "MonetizerConfig":
        self.id = self.id or _new_id("mon")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


class AccountConfig(BaseModel):
    """账号矩阵配置（维度 × 变现 × 人设 × 节奏）"""
    id: str = ""
    name: str
    dimension: Dimension
    monetizer: Monetizer
    persona: str = ""           # 人设
    cadence: AccountCadence = AccountCadence.MEDIUM
    platform: str = "douyin"
    bio: str = ""               # 主页简介/引流位
    active: bool = True
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "AccountConfig":
        self.id = self.id or _new_id("acc")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


# ==================== 选题层 ====================

class Topic(BaseModel):
    """选题（含打分）"""
    id: str = ""
    title: str
    dimension: Optional[Dimension] = None
    monetizer: Optional[Monetizer] = None
    source: str = "manual"       # manual/hot/bullet/signal
    weights: Dict[str, float] = Field(default_factory=dict)
    score: float = 0.0
    status: str = "pending"      # pending/used/discarded
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "Topic":
        self.id = self.id or _new_id("topic")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


# ==================== 工具传感器 ====================

class Signal(BaseModel):
    """需求信号（工具上报，脱敏聚合）"""
    id: str = ""
    field: str = ""             # 领域/类目
    keyword: str = ""
    heat: float = 0.0           # 热度
    source: str = "tool"        # 来源（去水印工具等）
    collected_at: float = 0.0

    def touch(self) -> "Signal":
        self.id = self.id or _new_id("sig")
        self.collected_at = self.collected_at or _now()
        return self


class ToolEvent(BaseModel):
    """工具行为事件（工具传感器上报，脱敏，不含个人信息）"""
    id: str = ""
    tool_name: str = ""         # 工具标识（如 watermark-remover）
    action: str = ""            # download/analyze/search/save
    url: str = ""
    title: str = ""             # 用户处理内容标题（可选）
    field: str = ""             # 领域（可选，服务端兜底 general）
    keyword: str = ""           # 关键词（工具可传；空则服务端从 title 粗提取）
    extra: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0

    def touch(self) -> "ToolEvent":
        self.id = self.id or _new_id("evt")
        self.created_at = self.created_at or _now()
        return self


class Hit(BaseModel):
    """爆款拆解记录（工具自动拆解 + 手动补充）"""
    id: str = ""
    url: str = ""
    title: str = ""
    source: str = "manual"       # auto(工具拆解)/manual
    raw_meta: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0

    def touch(self) -> "Hit":
        self.id = self.id or _new_id("hit")
        self.created_at = self.created_at or _now()
        return self


# ==================== 包装层 ====================

class PackagingTemplate(BaseModel):
    """包装模板（三维度 × 7 变现）"""
    id: str = ""
    dimension: Dimension
    monetizer: Monetizer
    title_templates: List[str] = Field(default_factory=list)
    cover_style: str = ""
    hook_templates: List[str] = Field(default_factory=list)
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "PackagingTemplate":
        self.id = self.id or _new_id("pkg")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


# ==================== 发布层 ====================

class PublishJob(BaseModel):
    """发布任务"""
    id: str = ""
    account_id: str = ""
    topic_id: str = ""
    content_id: str = ""        # 契约关联键
    platform: str = "douyin"
    status: str = "pending"     # pending/publishing/published/failed
    scheduled_at: float = 0.0
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def touch(self) -> "PublishJob":
        self.id = self.id or _new_id("pub")
        ts = _now()
        self.created_at = self.created_at or ts
        self.updated_at = ts
        return self


# ==================== 数据层 ====================

class MetricRecord(BaseModel):
    """内容表现指标（流量 + 变现转化）"""
    id: str = ""
    content_id: str = ""
    account_id: str = ""
    dimension: Optional[Dimension] = None
    monetizer: Optional[Monetizer] = None
    # 流量指标
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    follows: int = 0
    # 变现转化指标
    conversions: Dict[str, Any] = Field(default_factory=dict)
    revenue: float = 0.0
    roi_score: float = 0.0
    collected_at: float = 0.0

    def touch(self) -> "MetricRecord":
        self.id = self.id or _new_id("met")
        self.collected_at = self.collected_at or _now()
        return self
