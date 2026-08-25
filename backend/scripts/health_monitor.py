# -*- coding: utf-8 -*-
"""director 定时 health 巡检脚本（P2）

可被计划任务/Windows 任务计划程序周期性调用。
用法：
    python scripts/health_monitor.py            # 单次巡检，非零退出码=异常
    python scripts/health_monitor.py --loop 60  # 循环模式：每 60 秒巡检一次
退出码：
    0 = 全部正常  1 = 后端不可达  2 = ComfyUI 不可达  3 = 多异常
"""
import argparse
import json
import os
import sys
import time
import urllib.request

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEALTH_URL = "http://127.0.0.1:8000/health"
COMFYUI_URL = "http://127.0.0.1:8188/system_stats"
LOG_FILE = os.path.join(BACKEND, "logs", "health_monitor.jsonl")


def _probe(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return True, resp.status, resp.read().decode("utf-8", "ignore")[:200]
    except Exception as e:
        return False, 0, f"{type(e).__name__}: {e}"


def _append_log(entry):
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def check_once(verbose=True):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    be_ok, be_code, be_body = _probe(HEALTH_URL)
    cq_ok, cq_code, cq_body = _probe(COMFYUI_URL)
    entry = {
        "ts": ts, "backend_ok": be_ok, "backend_http": be_code,
        "backend_body": be_body if be_ok else be_body,
        "comfyui_ok": cq_ok, "comfyui_http": cq_code,
    }
    _append_log(entry)
    if verbose:
        print(f"[{ts}] backend={'OK' if be_ok else 'DOWN'}({be_code}) "
              f"comfyui={'OK' if cq_ok else 'DOWN'}({cq_code})")
        if be_ok:
            print(f"   backend body: {be_body[:120]}")
    if not be_ok and not cq_ok:
        return 3
    if not be_ok:
        return 1
    if not cq_ok:
        return 2
    return 0


def main():
    ap = argparse.ArgumentParser(description="director 定时 health 巡检")
    ap.add_argument("--loop", type=int, default=0, help="循环间隔秒数（0=单次）")
    args = ap.parse_args()
    if args.loop > 0:
        while True:
            rc = check_once()
            if rc:
                print(f"[monitor] 异常退出码 {rc}，写入 {LOG_FILE}")
            time.sleep(args.loop)
    return check_once()


if __name__ == "__main__":
    sys.exit(main())
