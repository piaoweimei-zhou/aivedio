"""本地实测：对成片跑一次完整 QC，输出完整 JSON 结果"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qc.qc_service import run_qc_async, MODEL_DISPLAY  # noqa: E402

VIDEO = r"D:\1\2\director\backend\output\output\export_85cbf5da.mp4"
OUT = r"D:\1\2\director\backend\data\generated\qc\qc_run_latest.json"


async def main():
    if not os.path.exists(VIDEO):
        print(f"[FAIL] 成片不存在: {VIDEO}")
        return 1
    print(f"模型: {MODEL_DISPLAY}")
    print(f"成片: {VIDEO} ({os.path.getsize(VIDEO) // 1024} KB)")
    print("开始质检（技术 + 语义），可能需要几分钟...\n")

    result = await run_qc_async(VIDEO, caption="", threshold=60.0, use_semantic=True)
    d = result.to_dict()
    d["video_path"] = VIDEO
    d["video_size_kb"] = os.path.getsize(VIDEO) // 1024
    d["model_display"] = MODEL_DISPLAY

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {OUT}")
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
