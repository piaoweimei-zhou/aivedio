"""一次性验证：用 Qwen3-VL-8B 跑 export_85cbf5da.mp4 的完整 QC。
调用 qc_service.run_qc，复用已启动的 llama-server（若未起会自动起）。
"""

import sys
import json
import time

sys.path.insert(0, r"D:\1\2\director\backend")
from services.qc.qc_service import run_qc, run_technical_qc  # noqa: E402

VIDEO = r"D:\1\2\director\backend\output\output\export_85cbf5da.mp4"

print("=== 1. 技术质检 (cv2) ===", flush=True)
t0 = time.time()
tech = run_technical_qc(VIDEO)
print(json.dumps(tech, ensure_ascii=False, indent=2), flush=True)
print(f"tech done {time.time()-t0:.1f}s", flush=True)

print("=== 2. 完整 QC (技术 + Qwen3-VL-8B 语义) ===", flush=True)
t0 = time.time()
res = run_qc(VIDEO, caption="", threshold=60.0, use_semantic=True)
print(f"model_used: {res.model_used}", flush=True)
print(f"total_score: {res.total_score}  passed: {res.passed}", flush=True)
print("dimensions:", json.dumps(res.dimensions, ensure_ascii=False), flush=True)
print("compliance_hits:", res.compliance_hits, flush=True)
print("copyright_hits:", res.copyright_hits, flush=True)
print("summary:", res.summary, flush=True)
print(f"qc done {time.time()-t0:.1f}s", flush=True)
print("QC_VERIFY_DONE", flush=True)
