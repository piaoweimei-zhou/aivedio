# -*- coding: utf-8 -*-
"""P1 回归：静态挂载端点验证（/output、/static/director/uploads、/assets 等）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.chdir(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi.testclient import TestClient  # noqa: E402
from services.paths import (  # noqa: E402
    OUTPUT_DIR, GENERATED_DIR, DATA_DIR, LOGS_DIR,
)

print("=== paths.py 常量解析 ===")
for name in ["BACKEND_DIR", "DATA_DIR", "OUTPUT_DIR", "ASSETS_DIR", "LOGS_DIR",
             "GENERATED_DIR", "UPLOADS_DIR"]:
    print(f"  {name} = {eval(name)}")
assert os.path.isdir(DATA_DIR), f"DATA_DIR 不存在: {DATA_DIR}"
assert os.path.isdir(GENERATED_DIR), f"GENERATED_DIR 不存在: {GENERATED_DIR}"
assert os.path.isdir(OUTPUT_DIR), f"OUTPUT_DIR 不存在: {OUTPUT_DIR}"
assert os.path.isdir(LOGS_DIR), f"LOGS_DIR 不存在: {LOGS_DIR}"
print("✅ 所有运行时目录存在")

import main  # noqa: E402

with TestClient(main.app) as client:
    checks = [
        ("/output/", 200, "output 目录挂载"),
        ("/static/director/uploads/", 200, "uploads 挂载"),
        ("/assets/", 200, "assets 挂载"),
        ("/api/health", 200, "health 端点"),
    ]
    for path, want, desc in checks:
        try:
            r = client.get(path)
            status = "✅" if r.status_code == want else "⚠️"
            print(f"  {status} {desc}: GET {path} → {r.status_code}")
        except Exception as e:
            print(f"  ❌ {desc}: GET {path} 异常: {type(e).__name__}: {e}")

    # 挂载目录内实际文件可访问性抽查
    import glob
    out_files = glob.glob(os.path.join(OUTPUT_DIR, "*"))[:5]
    if out_files:
        fn = os.path.basename(out_files[0])
        r = client.get(f"/output/{fn}")
        print(f"  {'✅' if r.status_code == 200 else '⚠️'} output 真实文件可访问: "
              f"/output/{fn} → {r.status_code}")
    gen_files = glob.glob(os.path.join(GENERATED_DIR, "*"))[:5]
    if gen_files:
        fn = os.path.basename(gen_files[0])
        r = client.get(f"/data/generated/{fn}")
        print(f"  {'✅' if r.status_code == 200 else '⚠️'} generated 文件经 "
              f"/data/generated 访问: → {r.status_code}")

print("✅ 挂载端点回归完成")
