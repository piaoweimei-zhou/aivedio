"""ROI 轮询器（S3 · B 线）：对自产视频 URL 定时轮询真实数据，沉淀按视频的时间序列。

- 复用 collector.parse_one（4 平台单条解析：B站/抖音/快手/小红书）
- 每次轮询把 {plays,likes,comments,shares,collects,ts} 追加到 data/roi/{video_id}.json
- ROI 序列是"选题→生产→发布→数据回流"飞轮的最后一环：可算增速/互动率/命中率

用法:
  python -m app.collectors.roi_poller --urls "https://...url1,url2"
  python -m app.collectors.roi_poller --jobs        # 从 publish_jobs 读 published 且 url 非空
  python -m app.collectors.roi_poller --urls ... --roi-dir data/roi
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.collectors.collector import _bvid_of, parse_one  # noqa: E402

logger = logging.getLogger("roi_poller")


def _video_id(rec: dict) -> str:
    """视频唯一 id：优先 bvid（B 站），否则 url 的 md5 前 12 位。"""
    bvid = _bvid_of(rec)
    if bvid and not bvid.startswith("http"):
        return bvid
    return hashlib.md5(rec.get("url", "").encode("utf-8")).hexdigest()[:12]


def _load(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def append_sample(roi_dir: str, rec: dict) -> str:
    """把一次轮询的真实数据追加进视频 ROI 时间序列。返回序列文件路径。"""
    os.makedirs(roi_dir, exist_ok=True)
    vid = _video_id(rec)
    path = os.path.join(roi_dir, f"{vid}.json")

    seq = _load(path)
    seq["video_id"] = vid
    seq.setdefault("url", rec.get("url", ""))
    seq.setdefault("platform", rec.get("platform", ""))
    seq.setdefault("title", rec.get("title", ""))
    samples = seq.setdefault("samples", [])
    ts = int(time.time())
    samples.append({
        "ts": ts,
        "plays": rec.get("plays", 0),
        "likes": rec.get("likes", 0),
        "comments": rec.get("comments", 0),
        "shares": rec.get("shares", 0),
        "collects": rec.get("collects", 0),
    })
    seq["first_seen"] = seq.get("first_seen", ts)
    seq["last_seen"] = ts
    seq["sample_count"] = len(samples)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(seq, f, ensure_ascii=False, indent=2)
    logger.info("ROI 追加: %s | play=%s 累计样本=%d", vid, rec.get("plays", 0), len(samples))
    return path


def load_published_urls(store_file: str) -> list:
    """从 publish_jobs.json 读取 status=published 且 url 非空的自产视频 URL。"""
    out: list = []
    if not os.path.exists(store_file):
        return out
    data = json.load(open(store_file, encoding="utf-8"))
    for job in data.values() if isinstance(data, dict) else data:
        if isinstance(job, dict) and job.get("status") == "published" and job.get("url"):
            out.append(job["url"])
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="TrafficOS ROI 轮询器（自产视频数据序列）")
    ap.add_argument("--urls", default="", help="逗号分隔的自产视频 URL 列表")
    ap.add_argument("--jobs", action="store_true",
                    help="从 publish_jobs.json 读 published 且 url 非空的视频")
    ap.add_argument("--store", default="", help="publish_jobs.json 路径（配合 --jobs）")
    ap.add_argument("--roi-dir", default="data/roi", help="ROI 序列目录")
    args = ap.parse_args()

    urls: list = []
    if args.urls:
        urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    if args.jobs:
        store = args.store or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "data", "publish_jobs.json")
        urls += load_published_urls(store)

    if not urls:
        logger.error("无轮询 URL（--urls 或 --jobs 必填，且需有 published+url 非空的自产视频）")
        return 2

    ok = 0
    for i, u in enumerate(urls, 1):
        rec = parse_one(u)
        if rec.get("error"):
            logger.warning("[%d/%d] 失败 %s -> %s", i, len(urls), u, rec["error"])
            continue
        append_sample(args.roi_dir, rec)
        ok += 1
        time.sleep(0.5)
    logger.info("ROI 轮询完成: %d/%d 成功", ok, len(urls))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
