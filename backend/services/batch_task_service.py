"""
批量任务编排服务 (BatchTaskService)

在 GenTaskManager 之上构建批量任务编排层：
- 将多个 Stage 任务组合成一个批量任务顺序执行
- 支持上下文传递：前一个任务的输出资产作为后一个任务的输入
- 支持失败重试、跳过、取消
- JSON 持久化，服务重启不丢失

设计原则：
- 复用现有 GenTaskManager 执行单个任务，不重复造轮子
- 不修改现有 Stage 执行流程，仅做编排
- 向后兼容：现有单任务执行不受影响

批量任务结构：
    BatchTask
    ├── step_1: concept 生成（输入：无 → 输出：concept_asset）
    ├── step_2: storyboard 生成（输入：step_1.concept_asset → 输出：storyboard_asset）
    └── step_3: video 生成（输入：step_2.storyboard_asset → 输出：video_asset）
"""
from services.paths import BATCHES_DIR

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.stage_service import get_stage_service
from services.gen_task_manager import get_gen_task_manager
from services.asset_service import get_asset_service

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_DIR = BATCHES_DIR


@dataclass
class BatchStep:
    """批量任务中的一个步骤"""
    step_id: str                                    # 步骤唯一 ID
    stage_id: str                                   # 执行的 Stage ID
    name: str = ""                                  # 步骤名称
    # 输入来源：固定资产 ID 列表 / 引用前序步骤输出 / 动态解析
    input_asset_ids: List[str] = field(default_factory=list)        # 固定输入
    input_from_steps: List[str] = field(default_factory=list)       # 引用前序步骤的输出（step_id 列表）
    provider_id: str = ""                           # 供应商（空=默认）
    params: Dict[str, Any] = field(default_factory=dict)            # 阶段参数
    # 运行时状态
    status: str = "pending"                         # pending/running/completed/failed/skipped
    output_asset_id: str = ""                       # 输出资产 ID
    gen_task_id: str = ""                           # 关联的 GenTask ID
    prompt_id: str = ""                             # ComfyUI prompt_id（用于反查生成历史）
    error: str = ""
    elapsed_ms: int = 0
    retry_count: int = 0
    max_retries: int = 0                            # 最大重试次数（0=不重试）
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "BatchStep":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class BatchTask:
    """批量任务"""
    batch_id: str
    name: str
    project_id: str = ""                            # 所属项目
    steps: List[BatchStep] = field(default_factory=list)
    status: str = "pending"                         # pending/running/completed/failed/cancelled
    current_step_index: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    # 配置
    stop_on_failure: bool = True                    # 失败时是否停止后续步骤
    auto_inherit_project: bool = True               # 自动继承项目归属
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "name": self.name,
            "project_id": self.project_id,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "current_step_index": self.current_step_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "stop_on_failure": self.stop_on_failure,
            "auto_inherit_project": self.auto_inherit_project,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BatchTask":
        steps_data = data.pop("steps", [])
        steps = [BatchStep.from_dict(s) for s in steps_data]
        return cls(steps=steps, **{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def progress(self) -> float:
        """进度百分比 0-100"""
        if not self.steps:
            return 0.0
        completed = sum(1 for s in self.steps if s.status in ("completed", "skipped"))
        return round(completed / len(self.steps) * 100, 1)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")


class BatchTaskService:
    """批量任务编排服务

    核心职责：
    - 创建/持久化批量任务
    - 顺序执行步骤，支持上下文传递
    - 失败重试、取消
    - 状态查询
    """

    def __init__(self, batch_dir: str = _DEFAULT_BATCH_DIR):
        self._batch_dir = Path(batch_dir)
        self._batch_dir.mkdir(parents=True, exist_ok=True)
        self._batches: Dict[str, BatchTask] = {}
        self._lock = asyncio.Lock()  # 保护 _batches 字典并发读写
        self._load()
        # 正在运行的批量任务（防止重复执行）
        self._running: Dict[str, asyncio.Task] = {}

    # ================================================================
    # 持久化
    # ================================================================

    def _batch_file(self, batch_id: str) -> Path:
        return self._batch_dir / f"{batch_id}.json"

    def _save_batch(self, batch: BatchTask):
        """保存 batch 到磁盘（原子写入：先写临时文件 → os.replace 替换）"""
        try:
            data = batch.to_dict()
            path = self._batch_file(batch.batch_id)
            tmp_path = str(path) + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, str(path))
        except Exception as e:
            # 清理临时文件
            try:
                os.remove(tmp_path)
            except (OSError, NameError):
                pass
            logger.warning(f"[BatchTask] 持久化失败 | id={batch.batch_id} | error={e}")

    def _load(self):
        if not self._batch_dir.exists():
            return
        for path in self._batch_dir.glob("batch_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                batch = BatchTask.from_dict(data)
                # 重启后运行中的任务标记为中断
                if batch.status == "running":
                    batch.status = "failed"
                    batch.error = "服务重启，批量任务中断"
                    batch.updated_at = time.time()
                    self._save_batch(batch)
                self._batches[batch.batch_id] = batch
            except Exception as e:
                logger.warning(f"[BatchTask] 加载失败 | file={path.name} | error={e}")
        logger.info(f"[BatchTask] 加载 {len(self._batches)} 个批量任务")

    # ================================================================
    # CRUD
    # ================================================================

    def create(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        project_id: str = "",
        stop_on_failure: bool = True,
        auto_inherit_project: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> BatchTask:
        """创建批量任务

        Args:
            name: 批量任务名称
            steps: 步骤定义列表，每个步骤包含：
                - stage_id: Stage ID（必填）
                - name: 步骤名称
                - input_asset_ids: 固定输入资产 ID 列表
                - input_from_steps: 引用前序步骤输出的 step_id 列表
                - provider_id: 供应商
                - params: 阶段参数
                - max_retries: 最大重试次数
            project_id: 所属项目
            stop_on_failure: 失败时停止
            auto_inherit_project: 自动继承项目归属
            metadata: 元数据
        """
        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        now = time.time()
        batch_steps = []
        for i, s in enumerate(steps):
            step = BatchStep(
                step_id=s.get("step_id") or f"step_{i+1}",
                stage_id=s["stage_id"],
                name=s.get("name", ""),
                input_asset_ids=s.get("input_asset_ids", []),
                input_from_steps=s.get("input_from_steps", []),
                provider_id=s.get("provider_id", ""),
                params=s.get("params", {}),
                max_retries=s.get("max_retries", 0),
            )
            batch_steps.append(step)

        batch = BatchTask(
            batch_id=batch_id,
            name=name,
            project_id=project_id,
            steps=batch_steps,
            stop_on_failure=stop_on_failure,
            auto_inherit_project=auto_inherit_project,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._batches[batch_id] = batch
        self._save_batch(batch)
        logger.info(f"[BatchTask] 创建批量任务 | id={batch_id} | name={name} | steps={len(batch_steps)}")
        return batch

    async def get(self, batch_id: str) -> Optional[BatchTask]:
        async with self._lock:
            return self._batches.get(batch_id)

    async def get_dag(self, batch_id: str) -> Optional[Dict[str, Any]]:
        """获取批量任务的 DAG 结构（用于前端可视化）"""
        async with self._lock:
            batch = self._batches.get(batch_id)
        if not batch:
            return None
        from services.dag_executor import get_dag_structure
        return get_dag_structure(batch.steps)

    async def list_batches(
        self,
        status: str = "",
        project_id: str = "",
    ) -> List[BatchTask]:
        async with self._lock:
            batches = list(self._batches.values())
        if status:
            batches = [b for b in batches if b.status == status]
        if project_id:
            batches = [b for b in batches if b.project_id == project_id]
        return sorted(batches, key=lambda b: b.created_at, reverse=True)

    async def delete(self, batch_id: str) -> bool:
        async with self._lock:
            batch = self._batches.get(batch_id)
            if not batch:
                return False
            if batch.status == "running":
                return False  # 运行中不可删除
            del self._batches[batch_id]
        try:
            self._batch_file(batch_id).unlink()
        except Exception:
            pass
        return True

    # ================================================================
    # 执行引擎
    # ================================================================

    async def start(self, batch_id: str, dry_run: bool = False) -> bool:
        """启动批量任务（异步执行，立即返回）

        Args:
            batch_id: 批量任务 ID
            dry_run: True=只做预检不执行（检查 DAG 结构 + Provider 可用性）
        """
        batch = self._batches.get(batch_id)
        if not batch:
            return False
        if batch.status == "running":
            return False  # 已在运行
        if batch_id in self._running:
            return False

        # dry_run 模式：只预检，不执行
        if dry_run:
            return await self._dry_run(batch)

        batch.status = "running"
        batch.started_at = time.time()
        batch.updated_at = time.time()
        batch.error = ""
        self._save_batch(batch)

        # 异步执行（不阻塞）—— 统一使用 DAG 引擎
        task = asyncio.create_task(self._run_batch_dag(batch_id))
        self._running[batch_id] = task
        logger.info(f"[BatchTask] 启动批量任务 | id={batch_id} | engine=DAG")
        return True

    async def _dry_run(self, batch: BatchTask) -> bool:
        """预检模式：验证 DAG 结构 + Provider 可用性，不执行"""
        from services.dag_executor import topological_sort, get_dag_structure
        from services.provider_service import get_provider_service

        logger.info(f"[BatchTask] dry-run 预检 | id={batch.batch_id}")

        # 1. 检查 DAG 结构（循环依赖等）
        try:
            layers = topological_sort(batch.steps)
            logger.info(
                f"[BatchTask] DAG 结构合法 | {len(layers)} 层 | "
                f"{len(batch.steps)} 步骤"
            )
        except ValueError as e:
            batch.error = f"DAG 结构错误: {e}"
            self._save_batch(batch)
            logger.error(f"[BatchTask] DAG 结构错误 | id={batch.batch_id} | {e}")
            return False

        # 2. 检查 Provider 可用性
        provider_svc = get_provider_service()
        check_result = provider_svc.pre_check_batch(batch.steps)

        if not check_result["ok"]:
            unavailable = check_result["unavailable"]
            error_lines = [
                f"  • {u['step_id']} ({u['stage_id']}): {u['reason']}"
                for u in unavailable[:5]
            ]
            batch.error = (
                f"Provider 预检失败：{len(unavailable)} 个步骤的 provider 不可用\n"
                + "\n".join(error_lines)
            )
            self._save_batch(batch)
            logger.warning(
                f"[BatchTask] Provider 预检失败 | id={batch.batch_id} | "
                f"{len(unavailable)} 步骤不可用"
            )
            return False

        logger.info(
            f"[BatchTask] dry-run 预检通过 | DAG={len(layers)}层 | "
            f"providers={len(check_result['providers_status'])}个全部可用"
        )

        # 3. 检查步骤间资产类型一致性
        type_errors = self._validate_step_asset_types(batch)
        if type_errors:
            error_lines = [f"  • {e}" for e in type_errors[:10]]
            batch.error = (
                f"步骤间资产类型校验失败：{len(type_errors)} 个错误\n"
                + "\n".join(error_lines)
            )
            self._save_batch(batch)
            logger.warning(
                f"[BatchTask] 资产类型校验失败 | id={batch.batch_id} | "
                f"{len(type_errors)} 错误"
            )
            return False

        logger.info(f"[BatchTask] 资产类型校验通过 | {len(batch.steps)} 步骤")
        return True

    def _validate_step_asset_types(self, batch: BatchTask) -> List[str]:
        """校验步骤间资产类型一致性

        检查每个步骤的 input_from_steps 引用的前序步骤输出类型，
        是否与当前步骤的 StageDef.input_types 兼容。

        Returns:
            错误信息列表（空列表表示通过）
        """
        from services.stage_service import get_stage_service

        errors: List[str] = []
        stage_svc = get_stage_service()

        # 构建 step_id -> output_type 映射
        step_output_types: Dict[str, str] = {}
        for step in batch.steps:
            stage_def = stage_svc.get_stage_def(step.stage_id)
            if stage_def:
                step_output_types[step.step_id] = stage_def.output_type

        for step in batch.steps:
            stage_def = stage_svc.get_stage_def(step.stage_id)
            if not stage_def:
                errors.append(f"步骤 {step.step_id}: 未知阶段 {step.stage_id}")
                continue

            allowed_input_types = set(stage_def.input_types)
            if not allowed_input_types:
                # 该阶段不限制输入类型，跳过
                continue

            # 检查 input_from_steps 引用的输出类型
            for ref_step_id in step.input_from_steps:
                ref_output_type = step_output_types.get(ref_step_id)
                if ref_output_type is None:
                    errors.append(
                        f"步骤 {step.step_id}: 引用的前序步骤 {ref_step_id} 不存在或无输出类型"
                    )
                    continue
                if ref_output_type not in allowed_input_types:
                    errors.append(
                        f"步骤 {step.step_id} ({stage_def.stage_id}): "
                        f"前序步骤 {ref_step_id} 输出类型 '{ref_output_type}' "
                        f"不在允许列表 {allowed_input_types}"
                    )

        return errors

    async def _run_batch_dag(self, batch_id: str):
        """DAG 执行引擎：拓扑排序 + 并行执行 + 超时 + 失败终止"""
        from services.dag_executor import DagExecutor, get_dag_structure
        from services import ws_service
        from services.structured_logging import set_trace_id, clear_trace_id

        batch = self._batches.get(batch_id)
        if not batch:
            return

        # 设置 trace_id，贯穿整个批量任务执行链（batch → dag → stage → comfyui）
        set_trace_id(batch_id)
        try:
            await self._run_batch_dag_impl(batch_id, batch)
        finally:
            clear_trace_id()

    async def _run_batch_dag_impl(self, batch_id: str, batch):
        """_run_batch_dag 的实际实现（trace_id 已设置）"""
        from services.dag_executor import DagExecutor, get_dag_structure
        from services import ws_service
        await ws_service.notify_batch_started(batch_id, len(batch.steps))

        stage_svc = get_stage_service()

        # 创建 DAG 执行器
        # ComfyUI 本地单 GPU 串行处理，并行提交会导致队列堵塞超时
        # 因此 max_concurrent=1（同层步骤串行执行，避免 ComfyUI 队列冲突）
        executor = DagExecutor(max_concurrent=1)

        # 持久化回调
        def on_step_update(step):
            batch.updated_at = time.time()
            self._save_batch(batch)

        # 取消检查
        def is_cancelled():
            return batch.status == "cancelled"

        # 单步执行回调（含输入解析 + 重试）
        async def run_step(step):
            # 通知步骤开始
            await ws_service.notify_step_started(batch_id, step.step_id, step.stage_id)

            # 解析输入
            input_ids = await self._resolve_step_inputs(batch, step)
            if input_ids is None:
                step.status = "failed"
                step.error = "输入资产解析失败"
                self._save_batch(batch)
                # 仅在最终失败时通知前端（避免重试中 UI 闪烁）
                if step.retry_count >= step.max_retries:
                    completed = sum(1 for s in batch.steps if s.status == "completed")
                    await ws_service.notify_step_failed(
                        batch_id, step.step_id, step.stage_id, step.error,
                        completed, len(batch.steps),
                    )
                return False

            # 注入项目归属
            params = dict(step.params)
            if batch.auto_inherit_project and batch.project_id:
                params.setdefault("project_id", batch.project_id)

            # ⭐ 修复 P0 #1：从前序步骤继承关键参数（避免每个阶段"重新开始"）
            # 仅继承用户未显式设置的参数，且仅继承跨阶段通用参数
            _INHERITABLE_KEYS = {
                "prompt", "size", "resolution", "aspect_ratio",
                "width", "height", "steps", "cfg", "seed",
                "duration", "fps", "frame_count",
                "content_type", "style", "model",
            }
            for prev_step_id in step.input_from_steps:
                prev_step = next((s for s in batch.steps if s.step_id == prev_step_id), None)
                if prev_step and prev_step.params:
                    for k, v in prev_step.params.items():
                        if k in _INHERITABLE_KEYS and k not in params:
                            params[k] = v
                    # 已完成步骤有实际执行后的 params（含 prompt 解析结果），优先继承
                    if prev_step.status == "completed" and hasattr(prev_step, "executed_params"):
                        for k, v in (prev_step.executed_params or {}).items():
                            if k in _INHERITABLE_KEYS and k not in params:
                                params[k] = v

            # 单次执行（重试由 DagExecutor._run_single_step 统一处理）
            try:
                result = await stage_svc.execute(
                    stage_id=step.stage_id,
                    input_asset_ids=input_ids,
                    provider_id=step.provider_id,
                    params=params,
                )
                step.elapsed_ms = result.elapsed_ms

                if result.success:
                    step.status = "completed"
                    step.output_asset_id = result.asset.asset_id
                    step.prompt_id = result.prompt_id  # 透传 prompt_id
                    logger.info(
                        f"[BatchTask-DAG] 步骤完成 | batch={batch_id} "
                        f"step={step.step_id} stage={step.stage_id} "
                        f"output={step.output_asset_id}"
                    )
                    # 通知步骤完成
                    completed = sum(1 for s in batch.steps if s.status == "completed")
                    await ws_service.notify_step_completed(
                        batch_id, step.step_id, step.stage_id,
                        step.output_asset_id, completed, len(batch.steps),
                    )
                    return True
                else:
                    step.status = "failed"
                    step.error = result.error or "未知错误"
                    logger.warning(
                        f"[BatchTask-DAG] 步骤失败 | batch={batch_id} "
                        f"step={step.step_id} error={step.error}"
                    )
            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                logger.error(
                    f"[BatchTask-DAG] 步骤异常 | batch={batch_id} "
                    f"step={step.step_id} error={e}"
                )

            # 仅在最终失败时通知前端（避免重试中 UI 闪烁）
            if step.retry_count >= step.max_retries:
                completed = sum(1 for s in batch.steps if s.status == "completed")
                await ws_service.notify_step_failed(
                    batch_id, step.step_id, step.stage_id, step.error,
                    completed, len(batch.steps),
                )
            return False

        try:
            result = await executor.execute(
                steps=batch.steps,
                run_step_callback=run_step,
                on_step_update=on_step_update,
                is_cancelled=is_cancelled,
            )

            # 更新批量任务状态
            if batch.status == "cancelled":
                logger.info(f"[BatchTask-DAG] 批量任务已取消 | id={batch_id}")
            elif result.success:
                batch.status = "completed"
                batch.completed_at = time.time()
                logger.info(
                    f"[BatchTask-DAG] 批量任务完成 | id={batch_id} | "
                    f"completed={result.completed_steps} "
                    f"skipped={result.skipped_steps} "
                    f"elapsed={result.elapsed_ms}ms"
                )
                # 通知批量任务完成
                await ws_service.notify_batch_completed(
                    batch_id, result.completed_steps, result.total_steps,
                    result.elapsed_ms,
                )
            else:
                batch.status = "failed"
                batch.error = (
                    f"步骤 {result.failed_step_id} 失败: {result.error} | "
                    f"已完成 {result.completed_steps}/{result.total_steps}，"
                    f"可重新 start() 断点续跑"
                )
                logger.warning(
                    f"[BatchTask-DAG] 批量任务失败 | id={batch_id} | "
                    f"failed_step={result.failed_step_id} | "
                    f"completed={result.completed_steps}/{result.total_steps}"
                )
                # 通知批量任务失败
                await ws_service.notify_batch_failed(
                    batch_id, result.failed_step_id, result.error,
                    result.completed_steps, result.total_steps,
                )

        except asyncio.CancelledError:
            batch.status = "cancelled"
            logger.info(f"[BatchTask-DAG] 批量任务取消 | id={batch_id}")
        except Exception as e:
            batch.status = "failed"
            batch.error = str(e)
            logger.error(
                f"[BatchTask-DAG] 批量任务异常 | id={batch_id} error={e}",
                exc_info=True,
            )
        finally:
            batch.updated_at = time.time()
            self._save_batch(batch)
            self._running.pop(batch_id, None)

    async def _resolve_step_inputs(self, batch: BatchTask, step: BatchStep) -> Optional[List[str]]:
        """解析步骤输入资产 ID

        合并固定输入 + 引用前序步骤输出
        """
        input_ids = list(step.input_asset_ids)

        # 引用前序步骤的输出
        for ref_step_id in step.input_from_steps:
            ref_step = next((s for s in batch.steps if s.step_id == ref_step_id), None)
            if not ref_step:
                logger.warning(f"[BatchTask] 引用步骤不存在 | step={step.step_id} ref={ref_step_id}")
                return None
            if ref_step.status != "completed" or not ref_step.output_asset_id:
                logger.warning(
                    f"[BatchTask] 引用步骤未完成 | step={step.step_id} "
                    f"ref={ref_step_id} status={ref_step.status}"
                )
                return None
            if ref_step.output_asset_id not in input_ids:
                input_ids.append(ref_step.output_asset_id)

        return input_ids

    async def cancel(self, batch_id: str) -> bool:
        """取消批量任务"""
        batch = self._batches.get(batch_id)
        if not batch or batch.status != "running":
            return False

        task = self._running.get(batch_id)
        if task:
            task.cancel()

        batch.status = "cancelled"
        batch.updated_at = time.time()
        self._save_batch(batch)
        logger.info(f"[BatchTask] 取消批量任务 | id={batch_id}")
        return True

    async def retry(self, batch_id: str, from_step: str = "") -> bool:
        """重试批量任务（从失败步骤或指定步骤开始）"""
        batch = self._batches.get(batch_id)
        if not batch:
            return False
        if batch.status == "running":
            return False

        # 重置步骤状态
        start_index = 0
        if from_step:
            for i, s in enumerate(batch.steps):
                if s.step_id == from_step:
                    start_index = i
                    break

        for i, step in enumerate(batch.steps):
            if i < start_index:
                continue
            step.status = "pending"
            step.error = ""
            step.output_asset_id = ""
            step.retry_count = 0
            step.started_at = 0.0
            step.completed_at = 0.0

        batch.status = "pending"
        batch.error = ""
        batch.current_step_index = start_index
        batch.updated_at = time.time()
        self._save_batch(batch)

        return await self.start(batch_id)


# ============================================================
# 单例
# ============================================================

_instance: Optional[BatchTaskService] = None


def get_batch_task_service() -> BatchTaskService:
    global _instance
    if _instance is None:
        _instance = BatchTaskService()
    return _instance
