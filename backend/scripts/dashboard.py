# -*- coding: utf-8 -*-
"""director dashboard 看板（P2，T5 透明可观测）

数据源：asset_registry.json / logs/*.log / git tags / 代码度量
用法：
    python scripts/dashboard.py            # 控制台摘要 + 生成 HTML 看板
    python scripts/dashboard.py --html-only
    python scripts/dashboard.py --json     # 输出 JSON（供集成）
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BACKEND)
OUT_HTML = os.path.join(ROOT, "docs", "工程化", "dashboard.html")


def _load_json(p):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def collect_assets():
    reg = _load_json(os.path.join(BACKEND, "assets", "asset_registry.json"))
    if not reg:
        return {"total": 0, "by_type": {}, "chain_depth": 0, "source": "缺失"}
    assets = reg.get("assets", [])
    by_type = {}
    for a in assets:
        t = a.get("asset_type", "?")
        by_type[t] = by_type.get(t, 0) + 1
    # 血缘链深度：沿 parent_id 上溯
    max_depth = 0
    for a in assets:
        depth = 1
        cur = a
        seen = set()
        while cur.get("parent_id") and cur["parent_id"] not in seen:
            seen.add(cur["parent_id"])
            par = next((x for x in assets if x.get("asset_id") == cur["parent_id"]), None)
            if not par:
                break
            cur = par
            depth += 1
        max_depth = max(max_depth, depth)
    return {"total": len(assets), "by_type": by_type, "chain_depth": max_depth,
            "source": "asset_registry.json"}


def collect_logs():
    err_file = os.path.join(BACKEND, "logs", "error.log")
    # 扫描全部日志的 ERROR 级别
    err_total = 0
    warn_total = 0
    info_total = 0
    if os.path.isdir(os.path.join(BACKEND, "logs")):
        for fn in os.listdir(os.path.join(BACKEND, "logs")):
            fp = os.path.join(BACKEND, "logs", fn)
            if not (fn.endswith(".log") or ".log." in fn):
                continue
            with io.open(fp, encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    if '"level": "ERROR"' in ln:
                        err_total += 1
                    elif '"level": "WARNING"' in ln:
                        warn_total += 1
                    elif '"level": "INFO"' in ln:
                        info_total += 1
    total = err_total + warn_total + info_total
    success_rate = round((1 - err_total / max(total, 1)) * 100, 1)
    return {"errors": err_total, "warns": warn_total, "infos": info_total,
            "total": total, "success_rate": success_rate,
            "err_file_exists": os.path.isfile(err_file)}


def collect_releases():
    r = subprocess.run(["git", "tag", "-l", "v*"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="ignore")
    tags = [t for t in r.stdout.splitlines() if t]
    # 版本号一致性
    ver_m = re.search(r'"version"\s*:\s*"([^"]+)"',
                      io.open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read())
    return {"tags": tags, "version": ver_m.group(1) if ver_m else "?"}


def collect_big_files():
    """工程化度量：services/ 大文件清单（P2 拆分目标 <40KB）"""
    big = []
    svc = os.path.join(BACKEND, "services")
    for root, _, files in os.walk(svc):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(root, fn)
            size = os.path.getsize(fp)
            if size >= 40000:
                rel = os.path.relpath(fp, BACKEND)
                big.append((rel, size))
    return sorted(big, key=lambda x: -x[1])


def build_html(metrics, ts):
    assets, logs, rel, big = metrics
    btype_items = sorted(assets["by_type"].items(), key=lambda x: -x[1])
    btype = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in btype_items)
    bigrows = "".join(f"<tr><td>{rel}</td><td>{size/1024:.1f} KB</td></tr>" for rel, size in big)
    tags = " ".join(f"<span class='tag'>{t}</span>" for t in rel["tags"])
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>director 治理看板</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;margin:24px;background:#f5f6fa;color:#1f2937}}
h1{{font-size:22px}} .card{{background:#fff;border-radius:10px;padding:16px 20px;
margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.metric{{display:inline-block;background:#eef2ff;border-radius:8px;padding:10px 18px;
margin:6px;text-align:center}}
.metric b{{display:block;font-size:26px;color:#4f46e5}} table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #e5e7eb;padding:6px 10px;text-align:left;font-size:14px}}
.tag{{background:#eef2ff;color:#4338ca;border-radius:6px;padding:2px 8px;margin:2px;font-size:12px}}
.foot{{color:#9ca3af;font-size:12px}}
</style></head><body>
<h1>director 导演工作台 · 治理看板</h1>
<div class="foot">生成时间 {ts}</div>
<div class="card"><h3>资产血缘</h3>
<div class="metric"><b>{assets['total']}</b>资产总数</div>
<div class="metric"><b>{assets['chain_depth']}</b>最大血缘深度</div>
<div class="metric"><b>asset_registry.json</b>数据源</div>
<table><tr><th>资产类型</th><th>数量</th></tr>{btype}</table></div>
<div class="card"><h3>日志健康度（全链路可观测）</h3>
<div class="metric"><b>{logs['success_rate']}%</b>非ERROR占比</div>
<div class="metric"><b>{logs['errors']}</b>ERROR</div>
<div class="metric"><b>{logs['warns']}</b>WARNING</div>
<div class="metric"><b>{logs['infos']}</b>INFO</div>
<div class="foot">说明：供应商成本埋点待 P3 指标埋点落地，当前仅日志级可观测。</div></div>
<div class="card"><h3>发布历史（G6）</h3>
<div class="metric"><b>{rel['version']}</b>服务版本</div>
<div>{tags}</div></div>
<div class="card"><h3>工程化度量：services/ 大文件（目标 &lt;40KB）</h3>
<table><tr><th>文件</th><th>大小</th></tr>{bigrows}</table></div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="director dashboard 看板")
    ap.add_argument("--html-only", action="store_true")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args()
    metrics = (collect_assets(), collect_logs(), collect_releases(), collect_big_files())
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    if args.as_json:
        print(json.dumps({k: v for k, v in zip(
            ["assets", "logs", "releases", "big_files"], metrics)}, ensure_ascii=False, indent=2))
        return 0
    print("== director 治理看板 ==")
    a, l, r, b = metrics
    print(f"  资产血缘: {a['total']} 资产, {a['by_type']}, 最大链深 {a['chain_depth']}")
    print(f"  日志健康: 非ERROR占比 {l['success_rate']}% "
          f"(ERROR {l['errors']}/WARN {l['warns']}/INFO {l['infos']})")
    print(f"  发布历史: 服务 v{r['version']}, {len(r['tags'])} 个 tag")
    print(f"  大文件: {len(b)} 个 ≥40KB")
    if not args.html_only:
        os.makedirs(os.path.dirname(OUT_HTML), exist_ok=True)
        with io.open(OUT_HTML, "w", encoding="utf-8") as f:
            f.write(build_html(metrics, ts))
        print(f"  HTML 看板: {OUT_HTML}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
