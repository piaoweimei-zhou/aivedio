"""MiniMax H3 builder 真机校验：提交到 ComfyUI 确认结构被接受，随后取消队列。
用法: python verify_minimax.py [duration_seconds]
"""
import sys  # noqa: E402
import uuid  # noqa: E402
import urllib.request  # noqa: E402
import json  # noqa: E402

sys.path.insert(0, r"D:\1\2\director\backend")
from services.workflow_minimax import build_minimax_h3_video_workflow  # noqa: E402

BASE = "http://127.0.0.1:8188"
duration = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


wf = build_minimax_h3_video_workflow(
    prompt="夕阳下平静的湖面有只白鹭掠过",
    width=480, height=864, duration_seconds=duration,
    seed=7, audio_mode="native", filename_prefix="mmh3_verify",
)
status, body = post("/prompt", {"prompt": wf, "client_id": str(uuid.uuid4())})
print("[submit] status =", status)
if status == 200:
    pid = body["prompt_id"]
    print("[submit] OK prompt_id =", pid)
    # 取消队列，避免真正长跑
    c = post(f"/prompt/{pid}/cancel", {})
    print("[cancel] status =", c[0])
else:
    print("[submit] 服务端拒绝:", json.dumps(body, ensure_ascii=False)[:1500])
