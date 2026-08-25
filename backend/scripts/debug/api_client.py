"""UTF-8 安全的 API 客户端（PowerShell Invoke-RestMethod 会损坏中文）"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path):
    req = urllib.request.Request(BASE + path)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def submit_stage(stage_id, input_ids, provider, params):
    return post("/api/director/stages/execute", {
        "stage_id": stage_id,
        "input_asset_ids": input_ids,
        "provider_id": provider,
        "params": params,
        "async_mode": True,
    })


def wait_task(task_id, timeout_s=600, interval=3):
    import time
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        t = get(f"/api/director/stages/task/{task_id}")
        if t["status"] not in ("running", "pending"):
            return t
        time.sleep(interval)
    return {"status": "timeout"}


if __name__ == "__main__":
    # 测试中文是否被正确接收
    r = submit_stage("tts", [], "comfyui", {
        "text": "测试中文字幕编码",
        "mode": "voice_design",
        "voice_description": "年轻女声",
    })
    print("submit:", r["task_id"])
