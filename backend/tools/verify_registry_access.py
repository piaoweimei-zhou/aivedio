"""全量验证：资产注册表中所有 URL 均可访问（迁移后后台查看资产不受影响）"""

import json
import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)

REGISTRY = os.path.join(os.path.dirname(__file__), "..", "assets", "asset_registry.json")
data = json.load(open(REGISTRY, encoding="utf-8"))
assets = data.get("assets", data) if isinstance(data, dict) else data
items = list(assets.values()) if isinstance(assets, dict) else assets

total = 0
ok = 0
fail = []
checked_urls = set()

for a in items:
    for u in a.get("urls") or []:
        if "filename=" not in u:
            continue
        if u in checked_urls:
            continue
        checked_urls.add(u)
        total += 1
        r = client.get(u)
        if r.status_code == 200:
            ok += 1
        else:
            fail.append((u, r.status_code))

print(f"检查 URL 总数: {total}")
print(f"可访问: {ok} ({ok * 100 // total if total else 0}%)")
print(f"失败: {len(fail)}")
for u, s in fail[:20]:
    print(f"  [{s}] {u}")

# 资产级统计：有多少资产至少一个 URL 可访问
asset_ok = 0
asset_bad = []
for a in items:
    urls = a.get("urls") or []
    if not urls:
        continue
    accessible = False
    for u in urls:
        if "filename=" not in u:
            accessible = True
            break
        r = client.get(u)
        if r.status_code == 200:
            accessible = True
            break
    if accessible:
        asset_ok += 1
    else:
        asset_bad.append((a.get("asset_id"), a.get("name"), urls))

print(f"\n资产级: 可访问 {asset_ok}，完全不可访问 {len(asset_bad)}")
for aid, name, urls in asset_bad[:10]:
    print(f"  {aid} | {name} | {urls[:2]}")

sys.exit(1 if fail else 0)
