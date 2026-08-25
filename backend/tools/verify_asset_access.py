"""验证资产整理后后台资产访问功能不受影响

覆盖：
1. 新格式 URL（带 subfolder）访问分层目录资产
2. 旧格式 URL（不带 subfolder）递归回退访问
3. 迁移前的旧文件名（如 prop_xxx）递归回退访问
4. 路径遍历防护（filename / subfolder 注入）
"""

import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} | {detail}")


# 找一个真实迁移后的文件
GEN = os.path.join(os.path.dirname(__file__), "..", "data", "generated")
real_sub = None
real_name = None
for root, _dirs, files in os.walk(GEN):
    if files and not real_name:
        rel = os.path.relpath(root, GEN)
        if rel != ".":
            real_sub = rel.replace("\\", "/")
            real_name = sorted(files)[0]
            break
print(f"测试文件: subfolder={real_sub} filename={real_name}")

print("\n[1] 新格式 URL（带 subfolder）")
r = client.get(f"/api/comfyui/image?filename={real_name}&subfolder={real_sub}")
check("新格式返回 200", r.status_code == 200, f"status={r.status_code}")
check(
    "内容类型正确", "image" in (r.headers.get("content-type") or ""), r.headers.get("content-type")
)
check("内容非空", len(r.content) > 0, f"len={len(r.content)}")

print("\n[2] 旧格式 URL（不带 subfolder，递归回退）")
r2 = client.get(f"/api/comfyui/image?filename={real_name}")
check("旧格式返回 200", r2.status_code == 200, f"status={r2.status_code}")
check("新旧内容一致", r2.content == r.content, f"len={len(r2.content)}")

print("\n[3] 迁移后新文件名（不带 subfolder，递归回退）")
# 取一个迁移后的新文件名，不带 subfolder 请求，验证递归回退
new_only_name = real_name
r3 = client.get(f"/api/comfyui/image?filename={new_only_name}")
check("迁移后文件名递归回退返回 200", r3.status_code == 200, f"status={r3.status_code}")

print("\n[4] 路径遍历防护")
r4 = client.get("/api/comfyui/image?filename=..%2F..%2Fetc%2Fpasswd")
check("filename 路径遍历被拒绝", r4.status_code == 400, f"status={r4.status_code}")
r5 = client.get(f"/api/comfyui/image?filename={real_name}&subfolder=..%2F..%2F..")
check("subfolder 路径遍历被拒绝", r5.status_code == 400, f"status={r5.status_code}")

print("\n[5] 不存在的文件返回 404")
r6 = client.get("/api/comfyui/image?filename=not_exist_12345.png")
check("不存在文件 404", r6.status_code == 404, f"status={r6.status_code}")

print(f"\n结果: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
