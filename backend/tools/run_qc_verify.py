"""QC 验证：用本地 Qwen3-VL-8B 对成片跑完整质检（技术 + 语义）"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qc.qc_service import (  # noqa: E402
    MAIN_MODEL,
    MODEL_DISPLAY,
    run_qc_async,
)

VIDEO = r"D:\1\2\director\backend\output\output\export_85cbf5da.mp4"


async def main():
    if not os.path.exists(VIDEO):
        print(f"[FAIL] 成片不存在: {VIDEO}")
        return 1
    print(f"模型: {MODEL_DISPLAY}")
    print(f"成片: {VIDEO} ({os.path.getsize(VIDEO) // 1024} KB)")
    print("开始质检（技术 + 语义），可能需要几分钟...\n")

    result = await run_qc_async(VIDEO, caption="", threshold=60.0, use_semantic=True)

    d = result.to_dict() if hasattr(result, "to_dict") else result
    print("=" * 50)
    print(f"总分: {d.get('total_score')} / 100")
    print(f"通过: {'是' if d.get('passed') else '否'} (阈值 {d.get('threshold', 60)})")
    print(f"模型: {d.get('model_used')}")
    if d.get("blocked"):
        print(f"红线拦截: 是")
        for r in d.get("blocked_reasons") or []:
            print(f"  - {r}")
    print("=" * 50)
    for dim, v in (d.get("dimensions") or {}).items():
        print(f"  {dim}: {v}")
    if d.get("compliance_hits"):
        print(f"\n合规命中: {d['compliance_hits']}")
    if d.get("copyright_hits"):
        print(f"\n版权命中: {d['copyright_hits']}")
    if d.get("summary"):
        print(f"\nAI 总结: {d['summary']}")
    issues = d.get("issues") or []
    if issues:
        print("\n问题项:")
        for i in issues:
            print(f"  - {i}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
