"""质检合规 stage（qc）。

挂在一键成片 export 之后，对成片做 100 分制质量 + 平台规则 + 版权质检。
零侵入：不修改任何既有 stage / DAG / 前端；仅注册为可选 stage_id="qc"。

行为：产出「质检报告资产」+「门禁结果资产」。默认不拦截视频发布（由人决策），
但会把 gate 决策（passed / blocked / forced_publish）落盘，供前端复核与强制发布留痕。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from services.paths import QC_DIR

from services.stage_service import (
    StagePlugin,
    StageDef,
    AssetRef,
    AssetProduceResult,
    get_asset_service,
)  # noqa: E501
from services.qc.qc_service import run_qc_async, QcResult


class QcStage(StagePlugin):
    stage_def = StageDef(
        stage_id="qc",
        name="质检合规",
        input_types=["video"],
        output_type="qc_report",
        default_provider="",
        supported_providers=["local"],
        description="对成片做 100 分制质量/平台规则/版权质检，产出可复核报告",
    )

    async def execute(
        self,
        input_assets: List[AssetRef],
        provider_id: str = "",
        params: Dict[str, Any] = None,
    ) -> AssetProduceResult:
        params = params or {}
        threshold = float(params.get("threshold", 60.0))
        use_semantic = bool(params.get("use_semantic", True))
        # 默认 False：调用【用户已起的常驻】llama-server (8082)，绝不拉起/杀掉它。
        manage_server = bool(params.get("manage_server", False))
        caption = params.get("caption", "") or ""

        if not input_assets:
            return self._error_result("qc 阶段缺少输入视频资产")

        video_asset = input_assets[0]
        video_path = self._resolve_local_path(video_asset)
        if not video_path or not os.path.exists(video_path):
            return self._error_result(f"qc 阶段无法定位本地视频文件: {video_asset.urls}")

        try:
            result: QcResult = await run_qc_async(
                video_path,
                caption=caption,
                threshold=threshold,
                use_semantic=use_semantic,
                manage_server=manage_server,
            )
        except Exception as e:
            return self._error_result(f"qc 执行失败: {e}")

        report = result.to_dict()

        # 门禁决策：默认不阻断发布，仅落盘 gate 结果供前端复核。
        # blocked=红线命中（版权/平台规则），passed=达到阈值且无红线。
        gate_result = {
            "passed": bool(report.get("passed", False)),
            "blocked": bool(report.get("blocked", False)),
            "score": float(report.get("total_score", 0.0)),
            "threshold": threshold,
            "forced_publish": False,
            "note": "质检不达标可强制发布，强制发布操作将在此留痕",
        }
        report["gate"] = gate_result

        # 把报告落盘为 json 资产
        asset_svc = get_asset_service()
        report_name = f"qc_report_{video_asset.asset_id}.json"
        report_dir = QC_DIR
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, report_name)
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 历史快照：每次 QC 追加一条带时间戳的归档，供趋势对比（#8）
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        snap_name = f"qc_report_{video_asset.asset_id}_{ts}.json"
        snap_path = os.path.join(report_dir, snap_name)
        with open(snap_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        history_path = os.path.join(report_dir, f"qc_history_{video_asset.asset_id}.json")
        _append_qc_history(
            history_path,
            {
                "ts": ts,
                "snap": snap_name,
                "total_score": float(report.get("total_score", 0.0)),
                "passed": bool(report.get("passed", False)),
                "blocked": bool(report.get("blocked", False)),
                "dimensions": report.get("dimensions", {}),
                "blocked_reasons": report.get("blocked_reasons", []),
            },
        )

        # 门禁结果单独落盘（供 API / 前端快速读取，无需解析整份报告）
        gate_name = f"qc_gate_{video_asset.asset_id}.json"
        gate_path = os.path.join(report_dir, gate_name)
        with open(gate_path, "w", encoding="utf-8") as f:
            json.dump(gate_result, f, ensure_ascii=False, indent=2)

        asset = await self._register_asset_direct(
            asset_svc,
            asset_type="qc_report",
            name=report_name,
            urls=[report_path, gate_path],
            input_assets=input_assets,
            extra_metadata={"qc": report, "qc_gate": gate_result},
            content_type="qc_report",
        )
        return AssetProduceResult(asset=asset, success=True)

    @staticmethod
    def _resolve_local_path(asset: AssetRef) -> Optional[str]:
        """从资产 urls 解析本地文件路径（兼容 file:// 与相对路径）。"""
        for u in asset.urls or []:
            p = u
            if p.startswith("file://"):
                p = p[len("file://") :]
            if os.path.exists(p):
                return p
            # 尝试相对 backend 根
            cand = os.path.join(os.getcwd(), p)
            if os.path.exists(cand):
                return cand
        return None


def _append_qc_history(history_path: str, entry: Dict[str, Any]) -> None:
    """追加一条 QC 历史记录（#8 趋势对比）。保留最近 50 条。"""
    import json as _json

    history: List[Dict[str, Any]] = []
    if os.path.exists(history_path):
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                history = _json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    history.append(entry)
    history = history[-50:]
    with open(history_path, "w", encoding="utf-8") as f:
        _json.dump(history, f, ensure_ascii=False, indent=2)


def register() -> None:
    """注册入口（供 stage_service 自动发现，可选）。"""
    from services.stage_service import register_stage

    register_stage(QcStage)
