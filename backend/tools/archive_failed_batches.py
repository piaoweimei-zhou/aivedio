# -*- coding: utf-8 -*-
"""归档历史 failed 批次任务：移到 data/batches/archive/（不删数据，可回溯）。

规则：status == failed 的全部归档。archive 子目录不被 _load 扫描（只扫根目录），
重启后任务表自动刷新，统计不再被历史失败污染。
"""
import json
import os
import shutil
import collections

BATCH_DIR = r"D:\1\2\director\backend\data\batches"
ARCHIVE_DIR = os.path.join(BATCH_DIR, "archive")


def main():
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    moved = 0
    by_status = collections.Counter()
    for fname in os.listdir(BATCH_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(BATCH_DIR, fname)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        status = data.get("status")
        by_status[status] += 1
        if status == "failed":
            dst = os.path.join(ARCHIVE_DIR, fname)
            shutil.move(path, dst)
            moved += 1
    print(f"归档 failed: {moved} 个 → {ARCHIVE_DIR}")
    print(f"归档后根目录状态分布: {dict(by_status)}")
    print(f"根目录剩余: {len([x for x in os.listdir(BATCH_DIR) if x.endswith('.json')])} 个")


if __name__ == "__main__":
    main()
