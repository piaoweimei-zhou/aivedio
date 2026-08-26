"""TrafficOS 工具传感器 SDK（B8）：供产品工具（去水印等）接入埋点。

用法（去水印工具接入）：
    from tool_tracker import ToolTracker
    tracker = ToolTracker(base_url="http://127.0.0.1:8001", tool_name="watermark-remover")
    tracker.track(action="download", title="某明星采访视频", url="https://...")

- 仅上报脱敏聚合数据（领域/关键词/动作），不采集个人信息
- 用标准库 urllib，无第三方依赖
"""
from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional


class ToolTracker:
    def __init__(
        self,
        base_url: str,
        tool_name: str,
        api_key: Optional[str] = None,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tool_name = tool_name
        self.api_key = api_key
        self.timeout = timeout

    # ---------- 单条上报 ----------

    def track(
        self,
        action: str,
        title: str = "",
        url: str = "",
        field: str = "",
        keyword: str = "",
        extra: Optional[Dict[str, object]] = None,
    ) -> bool:
        """上报一条工具行为事件。成功返回 True，失败返回 False（不抛异常，不阻塞业务）。"""
        payload = {
            "tool_name": self.tool_name,
            "action": action,
            "title": title,
            "url": url,
            "field": field,
            "keyword": keyword,
            "extra": extra or {},
        }
        return self._post("/api/traffic/signals/tool-event", payload)

    # ---------- 批量上报 ----------

    def track_many(self, events: List[Dict[str, object]]) -> int:
        """批量上报。返回成功条数（失败不抛异常）。"""
        ok = 0
        for evt in events:
            if self.track(**evt):
                ok += 1
        return ok

    # ---------- 内部 ----------

    def _post(self, path: str, payload: Dict[str, object]) -> bool:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False
