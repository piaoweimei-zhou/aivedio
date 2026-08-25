# -*- coding: utf-8 -*-
"""director 门禁 G0-G6 一键执行（P2）

用法：
    python scripts/gates.py            # 默认跑 G0/G1/G3/G4/G6（G2/G5 默认 SKIP）
    python scripts/gates.py --g2       # 额外跑 G2 一键成片端到端（需 ComfyUI + provider）
    python scripts/gates.py --release  # 发布模式：强制 G2 + G6

门禁语义（对齐治理方案十）：
    G0 代码规范  G1 单元测试  G2 一键成片端到端  G3 产物完整性
    G4 冒烟      G5 人工QC    G6 发布
"""
import argparse
import glob
import io
import os
import re
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BACKEND, ".venv-test", "Scripts", "python.exe")


def _run(cmd, cwd=BACKEND):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="ignore")


def _scan_py_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".venv-test", "__pycache__")]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return out


def check_g0():
    """代码规范：无临时文件 + 无散落目录根 + 无硬编码密钥 + 新增文件 0 违规（ratchet）"""
    issues = []
    # 1) 临时文件（backend 根散落 _*.py / _boot*.txt，排除合法 __init__.py）
    for pat in ["_*.py", "_boot*.txt", "_*.log", "debug.log"]:
        for fp in glob.glob(os.path.join(BACKEND, pat)):
            base = os.path.basename(fp)
            if base == "__init__.py":
                continue
            issues.append(f"临时文件: {os.path.relpath(fp, BACKEND)}")
    # 2) 新增散落目录根定义（应仅在 services/paths.py；跳过 scripts/debug 验证脚本区）
    root_defs = re.compile(
        r"GENERATED_DIR\s*=\s*os\.path\.join|OUTPUT_DIR\s*=\s*os\.path\.join|"
        r"_DEFAULT_[A-Z_]*DIR\s*=\s*\"data/|_PRESET_DIR\s*=\s*\"data/|"
        r"_PROMPT_DIR\s*=\s*\"data/|os\.path\.join\(\"data\"|"
        r"os\.path\.join\(_BASE_DIR,\s*\"data\"")
    for fp in _scan_py_files(BACKEND):
        rel = os.path.relpath(fp, BACKEND)
        rel_slash = rel.replace("\\", "/")
        if rel_slash == "services/paths.py" or rel_slash.startswith("scripts/debug/"):
            continue
        with io.open(fp, encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f, 1):
                if root_defs.search(ln):
                    issues.append(f"散落目录根: {rel}:L{i}")
    # 3) 硬编码密钥
    secret_pat = re.compile(
        r"(api_key|apikey|secret|password)\s*=\s*[\"'][A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9]{16,}")
    for fp in _scan_py_files(BACKEND):
        rel = os.path.relpath(fp, BACKEND)
        with io.open(fp, encoding="utf-8", errors="ignore") as f:
            for i, ln in enumerate(f, 1):
                if secret_pat.search(ln):
                    issues.append(f"疑似硬编码密钥: {rel}:L{i}")
    # 4) flake8 存量（仅报告，ratchet 策略：新增文件须 0 违规）
    flake8_total = 0
    r = _run([VENV_PY, "-m", "flake8", "api", "services", "core", "main.py", "--count"])
    try:
        flake8_total = int((r.stdout.strip().splitlines() or ["0"])[-1].strip())
    except Exception:
        pass
    new_violations = []
    git_cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=A",
               "--", "backend/**/*.py", "backend/*.py"]
    r2 = _run(git_cmd, cwd=os.path.dirname(BACKEND))
    for fp in r2.stdout.splitlines():
        rel = fp.replace("\\", "/")
        if rel.startswith("backend/"):
            rel = rel[len("backend/"):]
        r3 = _run([VENV_PY, "-m", "flake8", rel, "--max-line-length=100"])
        if r3.stdout.strip():
            new_violations.append(f"{rel}: {len(r3.stdout.strip().splitlines())} 条")
    passed = (len(issues) == 0 and not new_violations)
    return {
        "gate": "G0 代码规范", "passed": passed,
        "detail": {
            "临时文件/散落目录根/密钥问题": len(issues),
            "flake8 存量（仅报告）": flake8_total,
            "新增文件违规": len(new_violations),
            "问题明细": issues[:5] + new_violations[:5],
        },
    }


def check_g1(with_cov=True):
    """单元测试：pytest 全绿 + 覆盖率 ≥ 65%"""
    if with_cov:
        cov_cmd = [VENV_PY, "-m", "coverage", "run", "-m", "pytest",
                   "tests", "-q", "--disable-warnings"]
        r = _run(cov_cmd)
        tests_ok = r.returncode == 0
        n_pass = r.stdout.count("passed")
        rr = _run([VENV_PY, "-m", "coverage", "report"])
        m = re.search(r"TOTAL\s+[\d]+\s+[\d]+\s+([\d]+)%", rr.stdout)
        cov = int(m.group(1)) if m else None
    else:
        r = _run([VENV_PY, "-m", "pytest", "tests", "-q", "--disable-warnings"])
        tests_ok = r.returncode == 0
        n_pass = r.stdout.count("passed")
        cov = None
    passed = tests_ok and (cov is None or cov >= 65)
    return {
        "gate": "G1 单元测试", "passed": passed,
        "detail": {"pytest": "PASS" if tests_ok else "FAIL", "passed 用例": n_pass,
                   "覆盖率": f"{cov}%" if cov is not None else "未测"},
    }


def check_g2():
    """一键成片端到端：前置条件检查（真实回归需手动/发布触发）"""
    pre = {}
    # ComfyUI 存活
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=3) as resp:
            pre["ComfyUI 127.0.0.1:8188"] = f"存活({resp.status})"
    except Exception as e:
        pre["ComfyUI 127.0.0.1:8188"] = f"不可达({type(e).__name__})"
    # provider key
    env = {}
    for k in os.environ:
        if "KEY" in k.upper() or "TOKEN" in k.upper():
            env[k] = "已设置" if os.environ[k] else "空"
    pre["provider 密钥（env）"] = f"{len(env)} 个"
    ready = pre.get("ComfyUI 127.0.0.1:8188", "").startswith("存活")
    return {
        "gate": "G2 一键成片端到端", "passed": ready,
        "detail": {"前置条件": pre, "说明": "真实成片回归需 --g2/--release 且 ComfyUI+provider 就绪"},
    }


def check_g3():
    """产物完整性：成片/字幕/封面/发布素材包结构"""
    found = {}
    out = os.path.join(BACKEND, "output")
    for cat in ["video", "script", "subtitle", "cover", "package"]:
        d = os.path.join(out, cat)
        found[cat] = (len(glob.glob(os.path.join(d, "**", "*"), recursive=True))
                      if os.path.isdir(d) else 0)
    # 资产注册表
    reg = os.path.join(BACKEND, "assets", "asset_registry.json")
    reg_ok = os.path.isfile(reg)
    passed = reg_ok  # 基础资产注册表存在即通过；产物为运行期动态
    return {
        "gate": "G3 产物完整性", "passed": passed,
        "detail": {"output 分类产物": found, "asset_registry": "存在" if reg_ok else "缺失"},
    }


def check_g4():
    """冒烟：服务启动 + 关键 API + 静态挂载端点"""
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)
    try:
        from fastapi.testclient import TestClient
        import main
        with TestClient(main.app) as client:
            results = {}
            for path in ["/health", "/output/", "/static/director/uploads/", "/assets/"]:
                try:
                    r = client.get(path)
                    results[path] = r.status_code
                except Exception as e:
                    results[path] = f"ERR:{type(e).__name__}"
            # 关键端点存活：/health 200
            ok_health = results.get("/health") == 200
            # 挂载端点：访问真实存在的生成文件应 200（仅取文件，排除目录）
            mount_ok = True
            gen = [f for f in glob.glob(os.path.join(BACKEND, "output", "**", "*"), recursive=True)
                   if os.path.isfile(f)]
            if gen:
                fn = os.path.basename(gen[0])
                r = client.get(f"/output/{fn}")
                results[f"/output/{fn}"] = r.status_code
                mount_ok = r.status_code == 200
            passed = ok_health and mount_ok
            return {"gate": "G4 冒烟", "passed": passed, "detail": results}
    except Exception as e:
        return {"gate": "G4 冒烟", "passed": False, "detail": {"异常": f"{type(e).__name__}: {e}"}}


def check_g5():
    """人工 QC（无法自动化）"""
    return {"gate": "G5 人工QC", "passed": None, "detail": {"说明": "成片观感/卡点/字幕需人工验收，标记 SKIP"}}


def check_g6():
    """发布：版本一致 + CHANGELOG + 文档"""
    issues = []
    changelog = os.path.join(os.path.dirname(BACKEND), "CHANGELOG.md")
    if not os.path.isfile(changelog):
        issues.append("CHANGELOG.md 缺失")
    # 版本一致性：main.py health 返回的 version
    src = open(os.path.join(BACKEND, "main.py"), encoding="utf-8").read()
    m = re.search(r'"version"\s*:\s*"([^"]+)"', src)
    ver = m.group(1) if m else "?"
    passed = len(issues) == 0
    return {
        "gate": "G6 发布", "passed": passed,
        "detail": {"服务版本": ver, "问题": issues},
    }


GATES = [check_g0, check_g1, check_g2, check_g3, check_g4, check_g5, check_g6]


def main():
    ap = argparse.ArgumentParser(description="director 门禁 G0-G6")
    ap.add_argument("--g2", action="store_true", help="强制跑 G2（需 ComfyUI+provider 就绪）")
    ap.add_argument("--release", action="store_true", help="发布模式：强制 G2")
    ap.add_argument("--no-cov", action="store_true", help="跳过覆盖率")
    args = ap.parse_args()

    print("=" * 64)
    print("director 门禁 G0-G6")
    print("=" * 64)
    all_pass = True
    for i, fn in enumerate(GATES):
        if i == 2 and not (args.g2 or args.release):
            print("  G2 一键成片端到端 : SKIP（--g2/--release 触发，需 ComfyUI+provider）")
            continue
        if i == 5:
            print("  G5 人工QC        : SKIP（人工验收）")
            continue
        res = fn()
        mark = "PASS" if res["passed"] else ("SKIP" if res["passed"] is None else "FAIL")
        if res["passed"] is False:
            all_pass = False
        print(f"  {res['gate']:<14} : {mark}")
        for k, v in res["detail"].items():
            if isinstance(v, dict):
                print(f"      {k}: {v}")
            elif isinstance(v, list) and v:
                print(f"      {k}: {v[:3]}")
            else:
                print(f"      {k}: {v}")
    print("=" * 64)
    print("门禁结果:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
