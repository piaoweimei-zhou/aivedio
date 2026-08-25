"""
ComfyUI 生成任务管理器

将同步轮询模式改造为异步任务队列模式：
- POST /stages/execute → 立即返回 task_id (202 Accepted)
- GET /stages/task/{id} → 轮询任务状态（轻量查询，连接瞬间释放）

解决的核心问题：
- 视频生成耗时 1-10 分钟，同步轮询会耗尽 FastAPI 连接池
- 多个长任务并行时，系统直接不可用

P2+: 任务状态持久化
- 任务状态变更时自动写入磁盘（JSON 文件）
- 服务重启后从磁盘恢复未完成任务
- 已完成/失败任务自动过期清理
"""
from services.paths import TASK_STATE_DIR

import asyncio
import dataclasses
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.id_utils import gen_task_id

logger = logging.getLogger(__name__)

# 默认持久化目录（相对于 backend 工作目录）
_DEFAULT_PERSIST_DIR = TASK_STATE_DIR
# 已完成/失败任务的过期时间（秒），默认 1 小时
_COMPLETED_TTL = 3600
# 任务执行超时（秒），默认 30 分钟；0 表示不限制
_DEFAULT_TASK_TIMEOUT = 1800


@dataclass
class GenTask:
    """ComfyUI 生成任务"""
    task_id: str
    status: str = "pending"       # pending / running / completed / failed
    stage_id: str = ""            # 执行的阶段 ID
    prompt_id: str = ""           # ComfyUI prompt_id
    result: Optional[Dict[str, Any]] = None  # 执行结果
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    elapsed_ms: int = 0
    progress: float = 0.0         # 0-100

    # 执行参数（后台任务使用）
    _execute_fn: Optional[Callable] = field(default=None, repr=False)
    _execute_args: tuple = field(default=(), repr=False)
    _execute_kwargs: dict = field(default_factory=dict, repr=False)

    @staticmethod
    def _serialize_value(val: Any) -> Any:
        """递归地将不可序列化的对象转换为纯 dict/list/基本类型"""
        if val is None or isinstance(val, (str, int, float, bool)):
            return val
        if isinstance(val, (list, tuple)):
            return [GenTask._serialize_value(v) for v in val]
        if isinstance(val, dict):
            return {k: GenTask._serialize_value(v) for k, v in val.items()}
        if dataclasses.is_dataclass(val):
            return GenTask._serialize_value(dataclasses.asdict(val))
        return str(val)  # fallback

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "stage_id": self.stage_id,
            "prompt_id": self.prompt_id,
            "result": self._serialize_value(self.result),
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "elapsed_ms": self.elapsed_ms,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GenTask":
        """从字典恢复任务（用于磁盘持久化恢复）"""
        return cls(
            task_id=data.get("task_id", ""),
            status=data.get("status", "pending"),
            stage_id=data.get("stage_id", ""),
            prompt_id=data.get("prompt_id", ""),
            result=data.get("result"),
            error=data.get("error", ""),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            elapsed_ms=data.get("elapsed_ms", 0),
            progress=data.get("progress", 0.0),
        )


class GenTaskManager:
    """生成任务管理器

    核心职责：
    - 创建并提交异步生成任务
    - 后台执行任务（不阻塞 HTTP 连接）
    - 提供轻量级状态查询接口
    - 任务完成/失败时通知回调
    - 任务状态持久化到磁盘，服务重启不丢失
    """

    def __init__(
        self,
        max_concurrent: int = 2,
        persist_dir: str = _DEFAULT_PERSIST_DIR,
        completed_ttl: int = _COMPLETED_TTL,
        max_tasks: int = 1000,
        task_timeout: float = _DEFAULT_TASK_TIMEOUT,
    ):
        self._tasks: Dict[str, GenTask] = {}
        self._callbacks: Dict[str, List[Callable]] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._max_tasks = max_tasks  # ⭐ 修复 P3：最大任务上限，防止字典无限增长
        self._task_timeout = task_timeout  # 任务执行超时（秒），0=不限制
        self._lock = asyncio.Lock()  # 保护 _tasks 字典并发读写

        # 持久化配置
        self._persist_dir = Path(persist_dir)
        self._completed_ttl = completed_ttl
        self._persist_dir.mkdir(parents=True, exist_ok=True)

        # 任务完成后的全局回调列表（支持多回调注册，避免互相覆盖）
        self._on_done_callbacks: List[Callable] = []

        # 启动时从磁盘恢复任务
        self._restore_from_disk()

    # ================================================================
    # 持久化：磁盘读写
    # ================================================================

    def _task_file(self, task_id: str) -> Path:
        """获取任务持久化文件路径"""
        return self._persist_dir / f"{task_id}.json"

    def _save_task_to_disk(self, task: GenTask):
        """将任务状态写入磁盘（原子写入：先写临时文件 → os.replace 替换）"""
        try:
            data = task.to_dict()
            path = self._task_file(task.task_id)
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
            logger.warning(f"[GenTask] 持久化写入失败 | id={task.task_id} | error={e}")

    def _remove_task_from_disk(self, task_id: str):
        """从磁盘删除任务文件"""
        try:
            path = self._task_file(task_id)
            if path.exists():
                path.unlink()
        except Exception as e:
            logger.warning(f"[GenTask] 持久化删除失败 | id={task_id} | error={e}")

    def _restore_from_disk(self):
        """从磁盘恢复任务状态"""
        if not self._persist_dir.exists():
            return

        now = time.time()
        restored = 0
        expired = 0

        for path in self._persist_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                task = GenTask.from_dict(data)

                # 已完成/失败的任务：超过 TTL 则跳过（不恢复）
                if task.status in ("completed", "failed"):
                    if now - task.updated_at > self._completed_ttl:
                        path.unlink()
                        expired += 1
                        continue

                # 运行中/等待中的任务：重启后标记为 failed（无法恢复执行上下文）
                if task.status in ("running", "pending"):
                    task.status = "failed"
                    task.error = "服务重启，任务中断"
                    task.updated_at = now

                self._tasks[task.task_id] = task
                self._save_task_to_disk(task)  # 更新磁盘状态
                restored += 1

            except Exception as e:
                logger.warning(f"[GenTask] 恢复任务失败 | file={path.name} | error={e}")

        if restored or expired:
            logger.info(
                f"[GenTask] 磁盘恢复完成 | "
                f"restored={restored} | expired={expired}"
            )

    async def _cleanup_expired_tasks(self):
        """清理内存和磁盘中已过期的已完成/失败任务"""
        now = time.time()
        async with self._lock:
            to_remove = [
                tid for tid, t in self._tasks.items()
                if t.status in ("completed", "failed")
                and now - t.updated_at > self._completed_ttl
            ]
            for tid in to_remove:
                del self._tasks[tid]
                self._callbacks.pop(tid, None)

        # 磁盘清理在锁外执行（IO 操作不阻塞字典访问）
        for tid in to_remove:
            self._remove_task_from_disk(tid)

        if to_remove:
            logger.info(f"[GenTask] 清理过期任务 | count={len(to_remove)}")

    # ================================================================
    # 任务生命周期
    # ================================================================

    async def create_task(
        self,
        stage_id: str = "",
        execute_fn: Optional[Callable] = None,
        *args,
        **kwargs,
    ) -> GenTask:
        """创建生成任务"""
        task_id = gen_task_id()
        task = GenTask(
            task_id=task_id,
            stage_id=stage_id,
            created_at=time.time(),
            updated_at=time.time(),
            _execute_fn=execute_fn,
            _execute_args=args,
            _execute_kwargs=kwargs,
        )
        async with self._lock:
            # ⭐ 修复 P3：队列深度上限检查，防止短时间大量提交导致 _tasks 无限增长
            if len(self._tasks) >= self._max_tasks:
                raise RuntimeError(
                    f"任务队列已满（{len(self._tasks)}/{self._max_tasks}），请稍后重试或清理已完成任务"
                )
            self._tasks[task_id] = task
        self._save_task_to_disk(task)
        logger.info(f"[GenTask] 创建任务 | id={task_id} | stage={stage_id}")
        return task

    async def submit_task(self, task_id: str) -> GenTask:
        """提交任务并异步执行（不阻塞调用方）"""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError(f"任务不存在: {task_id}")
            task.status = "running"
            task.updated_at = time.time()
        self._save_task_to_disk(task)

        # 异步执行（不 await，立即返回）
        asyncio.create_task(self._execute_with_semaphore(task_id))

        return task

    async def _execute_with_semaphore(self, task_id: str):
        """带并发控制的任务执行"""
        async with self._semaphore:
            await self._execute_task(task_id)

    async def _execute_task(self, task_id: str):
        """执行生成任务"""
        task = self._tasks.get(task_id)
        if not task:
            return

        start = time.time()
        try:
            if task._execute_fn is None:
                raise RuntimeError("任务没有执行函数")

            # 用户可能已在执行前取消
            if task.status == "cancelled":
                return

            if self._task_timeout > 0:
                result = await asyncio.wait_for(
                    task._execute_fn(*task._execute_args, **task._execute_kwargs),
                    timeout=self._task_timeout,
                )
            else:
                result = await task._execute_fn(*task._execute_args, **task._execute_kwargs)

            # 执行期间用户可能已请求取消：保持 cancelled，不覆盖
            if task.status == "cancelled":
                return

            task.status = "completed"
            task.result = result
            task.elapsed_ms = int((time.time() - start) * 1000)
            task.progress = 100.0
            task.updated_at = time.time()

            logger.info(
                f"[GenTask] 任务完成 | id={task_id} | "
                f"elapsed={task.elapsed_ms}ms"
            )

        except asyncio.TimeoutError:
            task.status = "failed"
            task.error = f"任务执行超时（>{self._task_timeout}s）"
            task.elapsed_ms = int((time.time() - start) * 1000)
            task.updated_at = time.time()
            logger.error(
                f"[GenTask] 任务超时 | id={task_id} | "
                f"timeout={self._task_timeout}s | elapsed={task.elapsed_ms}ms"
            )

        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.elapsed_ms = int((time.time() - start) * 1000)
            task.updated_at = time.time()
            logger.error(f"[GenTask] 任务失败 | id={task_id} | error={e}")

        finally:
            self._save_task_to_disk(task)
            await self._cleanup_expired_tasks()
            await self._notify(task_id)
            # 全局回调列表（如显存释放、WS 推送等）
            for cb in self._on_done_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(task)
                    else:
                        cb(task)
                except Exception as e:
                    logger.warning(f"[GenTask] on_done 回调失败: {cb.__name__ if hasattr(cb, '__name__') else cb}: {e}")

    def register_done_callback(self, cb: Callable) -> None:
        """注册任务完成回调（支持多个回调，避免互相覆盖）

        替代旧的 self._on_task_done = cb 直接赋值方式。
        """
        if cb not in self._on_done_callbacks:
            self._on_done_callbacks.append(cb)
            logger.info(f"[GenTask] 注册 on_done 回调 | cb={cb.__name__ if hasattr(cb, '__name__') else cb} | total={len(self._on_done_callbacks)}")

    def unregister_done_callback(self, cb: Callable) -> None:
        """取消注册任务完成回调"""
        if cb in self._on_done_callbacks:
            self._on_done_callbacks.remove(cb)
            logger.info(f"[GenTask] 取消注册 on_done 回调 | cb={cb.__name__ if hasattr(cb, '__name__') else cb} | total={len(self._on_done_callbacks)}")

    def get_task(self, task_id: str) -> Optional[GenTask]:
        """获取任务状态"""
        return self._tasks.get(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """取消任务（尽力而为）

        - 仅 pending / running 状态可取消
        - 置为 cancelled 后，后台执行协程在完成前会检查该状态并放弃写入结果
        - 已在 ComfyUI 侧发起的请求无法中断，但状态会保持 cancelled
        """
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status not in ("pending", "running"):
                return False
            task.status = "cancelled"
            task.error = "用户取消"
            task.updated_at = time.time()
        self._save_task_to_disk(task)
        logger.info(f"[GenTask] 取消任务 | id={task_id}")
        return True

    async def list_tasks(self, status: str = "") -> List[GenTask]:
        """列出任务"""
        async with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def subscribe(self, task_id: str, callback: Callable):
        """订阅任务状态变更"""
        if task_id not in self._callbacks:
            self._callbacks[task_id] = []
        self._callbacks[task_id].append(callback)

    async def _notify(self, task_id: str):
        """通知任务状态变更"""
        task = self._tasks.get(task_id)
        if not task:
            return
        callbacks = self._callbacks.get(task_id, [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(task)
                else:
                    cb(task)
            except Exception as e:
                logger.warning(f"[GenTask] 回调失败: {e}")

    @property
    def running_count(self) -> int:
        """当前运行中的任务数"""
        return sum(1 for t in self._tasks.values() if t.status == "running")

    @property
    def pending_count(self) -> int:
        """当前等待中的任务数"""
        return sum(1 for t in self._tasks.values() if t.status == "pending")


# ============================================================
# 单例
# ============================================================

_instance: Optional[GenTaskManager] = None


def get_gen_task_manager() -> GenTaskManager:
    global _instance
    if _instance is None:
        _timeout = float(os.environ.get("GEN_TASK_TIMEOUT", _DEFAULT_TASK_TIMEOUT))
        _instance = GenTaskManager(task_timeout=_timeout)
    return _instance
