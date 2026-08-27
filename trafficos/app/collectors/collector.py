# -*- coding: utf-8 -*-
"""TrafficOS 采集器（主线第一阶段：工具传感器闭环）

把平台真实视频解析为结构化数据（播放/互动/发布时间），用于：
  - A 线：真实热点采集（替代榜单，供选题）
  - B 线：自产视频 ROI 数据轮询
解析核心（parser_base + bilibili parser）迁自 bupvideo 去水印工具。

用法（在 trafficos 目录，venv 需含 requests）：
    python -m app.collectors.collector --urls "https://www.bilibili.com/video/BV1xx411c7mD"
    python -m app.collectors.collector --file app/collectors/seeds.txt --report
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
import urllib.request
import urllib.error

from .parsers.bilibili import BilibiliParser
from .parsers.douyin import DouyinParser
from .parsers.kuaishou import KuaishouParser
from .parsers.xiaohongshu import XiaohongshuParser

logger = logging.getLogger("collectors")

_PARSERS = {
    "bilibili": BilibiliParser,
    "douyin": DouyinParser,
    "kuaishou": KuaishouParser,
    "xiaohongshu": XiaohongshuParser,
}


def _platform_of(url: str) -> str:
    u = url.lower()
    for name in ("bilibili", "douyin", "kuaishou", "xiaohongshu"):
        if name in u:
            return name
    return "unknown"


def _heat(rec: dict) -> float:
    """热度打分（可解释、可复算）：播放量 + 互动率 + 时效。"""
    plays = rec.get("plays", 0) or 0
    likes = rec.get("likes", 0) or 0
    comments = rec.get("comments", 0) or 0
    shares = rec.get("shares", 0) or 0
    collects = rec.get("collects", 0) or 0
    create_time = rec.get("create_time", 0) or 0

    heat_plays = min(100.0, (math.log10(plays + 1) / 6.0) * 100.0) if plays > 0 else 0.0
    engage = (likes + 3 * comments + 5 * shares + 2 * collects) / max(plays, 1)
    heat_engage = min(50.0, engage * 1000.0)
    age_days = max(0.0, (time.time() - create_time) / 86400.0) if create_time else 365.0
    heat_recency = max(0.0, 100.0 - age_days / 30.0 * 100.0)

    return round(0.5 * heat_plays + 0.3 * heat_engage + 0.2 * heat_recency, 1)


def parse_one(url: str) -> dict:
    """解析单条视频，返回结构化记录（失败返回 error 字段）。"""
    plat = _platform_of(url)
    if plat == "unknown":
        return {"url": url, "error": "unsupported_platform"}
    try:
        r = _PARSERS[plat]().parse(url)
    except Exception as e:  # noqa: BLE001 - 逐条容错
        return {"url": url, "platform": plat, "error": f"{type(e).__name__}: {e}"}
    if r.error:
        return {"url": url, "platform": plat, "error": r.error}
    return {
        "url": r.url,
        "platform": plat,
        "video_url": r.video_url,
        "title": r.title,
        "author": r.author,
        "duration": r.duration,
        "plays": r.plays,
        "likes": r.likes,
        "comments": r.comments,
        "shares": r.shares,
        "collects": r.collects,
        "create_time": r.create_time,
        "collected_at": int(time.time()),
    }


def report_tool_events(base_url: str, recs: list, tool_name: str = "hotspot-collector") -> int:
    """上报成功解析的记录为 ToolEvent（extra 携带全量真实数据）。返回成功条数。"""
    ok = 0
    for rec in recs:
        if rec.get("error"):
            continue
        payload = {
            "tool_name": tool_name,
            "action": "analyze",
            "url": rec.get("url", ""),
            "title": rec.get("title", ""),
            "field": "general",
            "keyword": "",
            "extra": {
                "platform": rec.get("platform", ""),
                "author": rec.get("author", ""),
                "plays": rec.get("plays", 0),
                "likes": rec.get("likes", 0),
                "comments": rec.get("comments", 0),
                "shares": rec.get("shares", 0),
                "collects": rec.get("collects", 0),
                "create_time": rec.get("create_time", 0),
                "duration": rec.get("duration", ""),
                "heat": rec.get("heat", 0.0),
            },
        }
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/traffic/signals/tool-event",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    ok += 1
        except (urllib.error.URLError, OSError) as e:
            logger.warning("上报失败 %s: %s", rec.get("url"), e)
    return ok


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="TrafficOS 热点采集器")
    ap.add_argument("--urls", default="", help="逗号分隔的 URL 列表")
    ap.add_argument("--file", default="", help="每行一个 URL 的种子文件")
    ap.add_argument("--out", default="", help="排行 JSON 输出路径")
    ap.add_argument("--report", action="store_true", help="上报 TrafficOS")
    ap.add_argument("--trafficos", default="http://127.0.0.1:8001", help="TrafficOS 地址")
    args = ap.parse_args()

    urls: list = []
    if args.urls:
        urls += [u.strip() for u in args.urls.split(",") if u.strip()]
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            urls += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]

    if not urls:
        logger.error("无种子 URL（--urls 或 --file 必填）")
        return 2

    logger.info("解析 %d 条种子视频...", len(urls))
    recs = []
    for i, u in enumerate(urls, 1):
        rec = parse_one(u)
        if rec.get("error"):
            logger.warning("[%d/%d] 失败 %s -> %s", i, len(urls), u, rec["error"])
        else:
            rec["heat"] = _heat(rec)
            logger.info("[%d/%d] OK %s | %s | play=%s heat=%.1f",
                        i, len(urls), rec["platform"], (rec.get("title") or "")[:30],
                        rec.get("plays"), rec["heat"])
        recs.append(rec)
        time.sleep(0.5)

    recs.sort(key=lambda x: x.get("heat", -1), reverse=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(recs, f, ensure_ascii=False, indent=2)
        logger.info("排行已落盘: %s", args.out)

    if args.report:
        ok = report_tool_events(args.trafficos, recs)
        logger.info("上报 TrafficOS: %d/%d 成功", ok, sum(1 for r in recs if not r.get("error")))

    print("\n===== 热度排行 =====")
    for r in recs:
        if r.get("error"):
            print(f"  [ERR] {r['url']}: {r['error']}")
        else:
            print(f"  {r.get('heat', 0):6.1f} | {r.get('platform'):12s} | "
                  f"play={r.get('plays', 0):>9d} | {r.get('title', '')[:34]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
