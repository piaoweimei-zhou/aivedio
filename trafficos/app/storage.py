"""TrafficOS 存储层：JSON 文件集合存储（可审计、免外部依赖）。

对齐 director batch_task_service 的持久化模式：
每个集合一个 JSON 文件，线程安全（锁），原子写（先写临时文件再替换）。
P0 阶段无并发写压力，JSON 足够；后续可无痛换 sqlite/DB。
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


class JsonCollection:
    """一个集合 = 一个 JSON 文件，提供通用 CRUD。

    record 必须是 pydantic 模型（有 id 字段），持久化为 dict。
    """

    def __init__(self, name: str, data_dir: str = _DEFAULT_DATA_DIR):
        self.name = name
        self.path = os.path.join(data_dir, f"{name}.json")
        self._lock = threading.RLock()
        self._records: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with io.open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._records = data
        except Exception as exc:  # noqa: BLE001
            logger.warning("[TrafficOS] 集合 %s 加载失败（%s），以空启动", self.name, exc)
            self._records = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = f"{self.path}.tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._records, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---------- CRUD ----------

    def insert(self, record: Any) -> Any:
        with self._lock:
            record.touch()
            self._records[record.id] = record.model_dump()
            self._save()
            return record

    def get(self, record_id: str) -> Optional[dict]:
        with self._lock:
            return self._records.get(record_id)

    def list(self) -> List[dict]:
        with self._lock:
            return list(self._records.values())

    def update(self, record_id: str, patch: dict) -> Optional[dict]:
        with self._lock:
            cur = self._records.get(record_id)
            if cur is None:
                return None
            cur.update(patch)
            cur["updated_at"] = _ts()
            self._save()
            return cur

    def delete(self, record_id: str) -> bool:
        with self._lock:
            if record_id not in self._records:
                return False
            del self._records[record_id]
            self._save()
            return True

    def find(self, predicate) -> List[dict]:
        with self._lock:
            return [r for r in self._records.values() if predicate(r)]

    def clear(self) -> None:
        with self._lock:
            self._records = {}
            self._save()


def _ts() -> float:
    import time
    return time.time()


# 运行时集合单例（惰性创建）
_store: Dict[str, JsonCollection] = {}
_store_lock = threading.Lock()


def get_collection(name: str, data_dir: str = _DEFAULT_DATA_DIR) -> JsonCollection:
    """获取（惰性创建）命名集合。"""
    with _store_lock:
        if name not in _store:
            _store[name] = JsonCollection(name, data_dir)
        return _store[name]
