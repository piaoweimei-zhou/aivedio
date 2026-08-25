"""
DAG 执行引擎 (DagExecutor)

将 BatchTask 的步骤列表视为有向无环图（DAG），按拓扑排序分层并行执行。

核心能力：
1. 拓扑排序：自动计算依赖层级，用户无需手动排序
2. 并行执行：同层独立步骤 asyncio.gather 并发（受 max_concurrent 限制）
3. 超时控制：按 stage 类型自动设置超时，可在 step.params.timeout 覆盖
4. 失败终止：任一步骤失败立即终止，保留已完成状态，支持断点续跑
5. 依赖跳过：失败步骤的下游自动标记为 skipped

设计原则：
- 不修改 BatchStep/BatchTask 数据结构，向后兼容
- 不直接调用 Stage，通过回调函数执行（解耦）
- 持久化由调用方（BatchTaskService）负责

使用示例：
    executor = DagExecutor(max_concurrent=2)
    await executor.execute(
        steps=batch.steps,
        run_step_callback=my_run_step_func,  # async (step) -> success: bool
        on_step_update=my_update_func,       # sync (step) -> None
    )
"""

import asyncio
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ============================================================
# 重试配置（可通过环境变量覆盖）
# ============================================================
import os  # noqa: E402

RETRY_DELAY_SECS = float(os.environ.get("DAG_RETRY_DELAY_SECS", "1.0"))


# ============================================================
# 超时配置（按 stage_id 分类）
# ============================================================
STAGE_TIMEOUTS: Dict[str, int] = {
    "concept": 600,  # 文生图 10分钟（ComfyUI 本地串行，留足时间）
    "refine": 600,  # 精修图
    "angle": 600,  # 三视图
    "pano": 900,  # 全景图（拼接耗时）
    "storyboard": 600,  # 分镜
    "batch_storyboard": 900,  # 批量分镜
    "multi_person": 600,  # 多人分镜
    "video": 5400,  # 视频生成 90分钟（LTX-2.3 长视频4×15s分段+拼接≈50分钟）
    "edit": 300,  # 视频剪辑 5分钟
    "export": 120,  # 导出
    "depth_map": 300,
    "lineart_extraction": 300,
    "pose_extraction": 300,
    "layered_render": 600,
    "extract_all": 600,
    "template_batch_extract": 600,
    "template_clean": 300,
    "template_pose": 300,
}
DEFAULT_TIMEOUT = 600  # 默认 10 分钟


def get_stage_timeout(stage_id: str, params: Dict[str, Any]) -> int:
    """获取步骤超时时间（秒）

    优先级：step.params.timeout > STAGE_TIMEOUTS[stage_id] > DEFAULT_TIMEOUT
    """
    if params and params.get("timeout"):
        try:
            return int(params["timeout"])
        except (ValueError, TypeError):
            pass
    return STAGE_TIMEOUTS.get(stage_id, DEFAULT_TIMEOUT)


# ============================================================
# 拓扑排序
# ============================================================
@dataclass
class DagLayer:
    """DAG 的一层（同层步骤互相独立，可并行）"""

    layer_index: int
    step_ids: List[str] = field(default_factory=list)


def topological_sort(steps: List[Any]) -> List[DagLayer]:
    """拓扑排序：将步骤按依赖关系分层

    Args:
        steps: BatchStep 列表，每个 step 需有 step_id 和 input_from_steps

    Returns:
        分层列表，每层内的步骤可并行执行

    Raises:
        ValueError: 检测到循环依赖
    """
    # 构建 step_id → step 映射
    step_map: Dict[str, Any] = {s.step_id: s for s in steps}

    # 构建依赖图：step_id → 它依赖的 step_id 列表
    # 只保留存在于 step_map 中的依赖（过滤外部引用）
    dependencies: Dict[str, Set[str]] = {}
    dependents: Dict[str, Set[str]] = defaultdict(set)  # 反向：被谁依赖

    for s in steps:
        deps = set()
        for ref in s.input_from_steps:
            if ref in step_map:  # 只考虑图内依赖
                deps.add(ref)
                dependents[ref].add(s.step_id)
        dependencies[s.step_id] = deps

    # Kahn 算法分层
    layers: List[DagLayer] = []
    completed: Set[str] = set()
    remaining = set(step_map.keys())

    layer_index = 0
    while remaining:
        # 找出所有依赖已满足的步骤
        ready = [sid for sid in remaining if dependencies[sid].issubset(completed)]
        if not ready:
            # 循环依赖检测
            raise ValueError(
                f"检测到循环依赖，涉及步骤: {remaining}。" f"请检查 input_from_steps 配置。"
            )

        layers.append(DagLayer(layer_index=layer_index, step_ids=sorted(ready)))
        completed.update(ready)
        remaining -= set(ready)
        layer_index += 1

    return layers


# ============================================================
# DAG 执行器
# ============================================================
@dataclass
class DagExecutionResult:
    """DAG 执行结果"""

    success: bool
    completed_steps: int
    failed_steps: int
    skipped_steps: int
    total_steps: int
    elapsed_ms: int
    failed_step_id: str = ""
    error: str = ""


class DagExecutor:
    """DAG 执行引擎

    按拓扑排序分层并行执行步骤，支持超时和失败终止。
    """

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max(1, max_concurrent)
        logger.info(f"[DagExecutor] 初始化 | max_concurrent={self.max_concurrent}")

    async def execute(
        self,
        steps: List[Any],
        run_step_callback: Callable[[Any], Any],
        on_step_update: Optional[Callable[[Any], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> DagExecutionResult:
        """执行 DAG

        Args:
            steps: BatchStep 列表
            run_step_callback: 异步函数，接收 step，返回 bool（成功/失败）
            on_step_update: 同步回调，step 状态变更时调用（用于持久化）
            is_cancelled: 同步函数，返回 True 时终止执行

        Returns:
            DagExecutionResult
        """
        start_time = time.time()
        step_map = {s.step_id: s for s in steps}

        # 1. 拓扑排序
        try:
            layers = topological_sort(steps)
        except ValueError as e:
            logger.error(f"[DagExecutor] 拓扑排序失败: {e}")
            return DagExecutionResult(
                success=False,
                completed_steps=0,
                failed_steps=0,
                skipped_steps=0,
                total_steps=len(steps),
                elapsed_ms=int((time.time() - start_time) * 1000),
                error=str(e),
            )

        logger.info(f"[DagExecutor] 拓扑排序完成 | {len(layers)} 层 | " f"{len(steps)} 步骤")
        for layer in layers:
            logger.info(f"[DagExecutor]   层 {layer.layer_index}: {layer.step_ids}")

        # 2. 收集已完成步骤（断点续跑）
        completed_set: Set[str] = set()
        failed_step_id = ""
        error_msg = ""

        for s in steps:
            if s.status == "completed":
                completed_set.add(s.step_id)

        # 3. 逐层执行
        for layer in layers:
            # 检查取消
            if is_cancelled and is_cancelled():
                logger.info("[DagExecutor] 收到取消信号，停止执行")
                break

            # 如果之前有失败，终止执行
            if failed_step_id:
                logger.info(
                    f"[DagExecutor] 因步骤 {failed_step_id} 失败，"
                    f"跳过层 {layer.layer_index} 及后续所有层"
                )
                break

            # 过滤出本层需要执行的步骤（跳过已完成的）
            to_run = [step_map[sid] for sid in layer.step_ids if sid not in completed_set]

            if not to_run:
                logger.info(f"[DagExecutor] 层 {layer.layer_index} 全部已完成，跳过")
                continue

            logger.info(
                f"[DagExecutor] 执行层 {layer.layer_index} | "
                f"{len(to_run)} 步骤 | ids={layer.step_ids}"
            )

            # 并行执行本层（受 max_concurrent 限制）
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def run_with_limit(step):
                async with semaphore:
                    return await self._run_single_step(
                        step=step,
                        run_step_callback=run_step_callback,
                        on_step_update=on_step_update,
                    )

            results = await asyncio.gather(
                *[run_with_limit(s) for s in to_run],
                return_exceptions=True,
            )

            # 处理结果
            for step, result in zip(to_run, results):
                if isinstance(result, Exception):
                    # 异常视为失败
                    step.status = "failed"
                    step.error = f"DAG执行异常: {result}"
                    if on_step_update:
                        on_step_update(step)
                    if not failed_step_id:
                        failed_step_id = step.step_id
                        error_msg = step.error
                    logger.error(
                        f"[DagExecutor] 步骤异常 | step={step.step_id} | " f"error={result}"
                    )
                elif not result:
                    # 返回 False 视为失败
                    if not failed_step_id:
                        failed_step_id = step.step_id
                        error_msg = step.error or "步骤执行失败"
                    logger.warning(
                        f"[DagExecutor] 步骤失败 | step={step.step_id} | " f"error={step.error}"
                    )
                else:
                    completed_set.add(step.step_id)

        # 4. 标记失败步骤的下游为 skipped（不执行）
        if failed_step_id:
            self._mark_downstream_skipped(
                steps=steps,
                failed_step_id=failed_step_id,
                completed_set=completed_set,
                on_step_update=on_step_update,
            )

        # 5. 统计结果
        completed_count = sum(1 for s in steps if s.status == "completed")
        failed_count = sum(1 for s in steps if s.status == "failed")
        skipped_count = sum(1 for s in steps if s.status == "skipped")

        elapsed_ms = int((time.time() - start_time) * 1000)
        success = not failed_step_id

        logger.info(
            f"[DagExecutor] 执行完成 | success={success} | "
            f"completed={completed_count} failed={failed_count} "
            f"skipped={skipped_count} total={len(steps)} | "
            f"elapsed={elapsed_ms}ms"
        )

        return DagExecutionResult(
            success=success,
            completed_steps=completed_count,
            failed_steps=failed_count,
            skipped_steps=skipped_count,
            total_steps=len(steps),
            elapsed_ms=elapsed_ms,
            failed_step_id=failed_step_id,
            error=error_msg,
        )

    async def _run_single_step(
        self,
        step: Any,
        run_step_callback: Callable[[Any], Any],
        on_step_update: Optional[Callable[[Any], None]],
    ) -> bool:
        """执行单个步骤（带超时 + 重试）

        重试语义统一在此处：每次尝试独立计时，单次超时后仍可重试。
        callback 内不应再实现重试循环（避免双重重试）。
        """
        max_retries = getattr(step, "max_retries", 0) or 0
        timeout = get_stage_timeout(step.stage_id, step.params)
        step.started_at = time.time()
        step.error = ""

        logger.info(
            f"[DagExecutor] 开始执行 | step={step.step_id} "
            f"stage={step.stage_id} timeout={timeout}s max_retries={max_retries}"
        )

        last_error = ""
        for attempt in range(max_retries + 1):
            step.status = "running"
            step.retry_count = attempt
            if on_step_update:
                on_step_update(step)

            try:
                # 单次带超时执行（callback 返回 bool，不再内部重试）
                success = await asyncio.wait_for(
                    run_step_callback(step),
                    timeout=timeout,
                )

                if success:
                    step.completed_at = time.time()
                    step.elapsed_ms = int((step.completed_at - step.started_at) * 1000)
                    step.status = "completed"
                    step.error = ""
                    logger.info(
                        f"[DagExecutor] 步骤完成 | step={step.step_id} | "
                        f"attempt={attempt+1} elapsed={step.elapsed_ms}ms"
                    )
                    if on_step_update:
                        on_step_update(step)
                    return True

                # callback 返回 False（业务失败）
                last_error = step.error or "步骤返回失败"
                logger.warning(
                    f"[DagExecutor] 步骤失败 | step={step.step_id} | "
                    f"attempt={attempt+1} error={last_error}"
                )

            except asyncio.TimeoutError:
                last_error = f"步骤超时（{timeout}s）"
                logger.error(
                    f"[DagExecutor] 步骤超时 | step={step.step_id} | "
                    f"attempt={attempt+1} timeout={timeout}s"
                )

            except Exception as e:
                last_error = f"步骤异常: {e}"
                logger.error(
                    f"[DagExecutor] 步骤异常 | step={step.step_id} | "
                    f"attempt={attempt+1} error={e}",
                    exc_info=True,
                )

            # 还有重试机会 → 短暂等待后重试
            if attempt < max_retries:
                logger.info(
                    f"[DagExecutor] 准备重试 | step={step.step_id} | "
                    f"attempt={attempt+1}/{max_retries+1}"
                )
                await asyncio.sleep(RETRY_DELAY_SECS)

        # 所有重试均失败
        step.completed_at = time.time()
        step.elapsed_ms = int((step.completed_at - step.started_at) * 1000)
        step.status = "failed"
        step.error = last_error
        if on_step_update:
            on_step_update(step)
        return False

    def _mark_downstream_skipped(
        self,
        steps: List[Any],
        failed_step_id: str,
        completed_set: Set[str],
        on_step_update: Optional[Callable[[Any], None]],
    ):
        """标记失败步骤的所有下游为 skipped（不执行）"""
        step_map = {s.step_id: s for s in steps}

        # BFS 找出所有下游
        to_skip: Set[str] = set()
        queue = deque([failed_step_id])

        while queue:
            current = queue.popleft()
            for s in steps:
                if current in s.input_from_steps and s.step_id not in to_skip:
                    if s.step_id not in completed_set:
                        to_skip.add(s.step_id)
                        queue.append(s.step_id)

        for sid in to_skip:
            step = step_map[sid]
            if step.status in ("pending", "running"):
                step.status = "skipped"
                step.error = f"上游步骤 {failed_step_id} 失败，自动跳过"
                if on_step_update:
                    on_step_update(step)
                logger.info(
                    f"[DagExecutor] 自动跳过下游 | step={sid} | "
                    f"reason=上游 {failed_step_id} 失败"
                )


# ============================================================
# DAG 结构可视化（用于 API 返回）
# ============================================================
def get_dag_structure(steps: List[Any]) -> Dict[str, Any]:
    """获取 DAG 结构信息（用于前端可视化）

    Returns:
        {
            "layers": [
                {"layer": 0, "steps": ["step1", "step2"]},
                {"layer": 1, "steps": ["step3"]},
            ],
            "edges": [
                {"from": "step1", "to": "step3"},
            ],
            "nodes": [
                {"id": "step1", "stage_id": "concept", "status": "pending"},
            ]
        }
    """
    try:
        layers = topological_sort(steps)
    except ValueError as e:
        return {"error": str(e), "layers": [], "edges": [], "nodes": []}

    step_map = {s.step_id: s for s in steps}

    # 节点
    nodes = [
        {
            "id": s.step_id,
            "stage_id": s.stage_id,
            "name": s.name,
            "status": s.status,
            "output_asset_id": s.output_asset_id,
        }
        for s in steps
    ]

    # 边（依赖关系）
    edges = []
    for s in steps:
        for ref in s.input_from_steps:
            if ref in step_map:
                edges.append({"from": ref, "to": s.step_id})

    # 层
    layer_info = [{"layer": layer.layer_index, "steps": layer.step_ids} for layer in layers]

    return {
        "layers": layer_info,
        "edges": edges,
        "nodes": nodes,
    }
