#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
director 导演工作台 · 统一命令入口（工程化治理 P0 产物）

用法示例：
    python director.py status
    python director.py test
    python director.py lint
    python director.py gates
    python director.py health [--url http://127.0.0.1:8000]
    python director.py start

设计对齐 bupvideo/manage.py：单一入口封装所有操作，后续按阶段扩展
release / dashboard 等命令在 P2/P3 补充。
"""

import argparse
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
VENV_PY = os.path.join(BACKEND, ".venv-test", "Scripts", "python.exe")


def _py():
    """优先使用 backend/.venv-test 的解释器，否则用系统 python"""
    if os.path.isfile(VENV_PY):
        return VENV_PY
    return sys.executable


def _run(cmd, cwd=None):
    print("$", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd or ROOT)


def _count_files(path):
    n = 0
    if os.path.isdir(path):
        n = sum(len(files) for _, _, files in os.walk(path))
    return n


def cmd_status(args):
    print("== git 状态 ==")
    _run(["git", "status", "--short"])
    print("\n== 运行时目录（应集中在 backend/ 下）==")
    for rel in ["data", "output", "assets", "logs"]:
        p = os.path.join(BACKEND, rel)
        print(f"  backend/{rel}: {_count_files(p)} 文件")
    print("\n== 基线/阶段标签 ==")
    _run(["git", "tag"])
    return 0


def cmd_test(args):
    print("== G1 单元测试 ==")
    r = _run([_py(), "-m", "pytest", "tests", "-q", "--disable-warnings"], cwd=BACKEND)
    return 0 if r.returncode == 0 else 1


def cmd_lint(args):
    print("== G0 lint（flake8）==")
    r = _run([_py(), "-m", "flake8", "api", "services", "core", "main.py", "--count"],
             cwd=BACKEND)
    return 0 if r.returncode == 0 else 1


def cmd_gates(args):
    print("=" * 46)
    print("director 门禁 G0-G6")
    print("=" * 46)
    cmd = [_py(), os.path.join(BACKEND, "scripts", "gates.py")]
    if getattr(args, "g2", False):
        cmd.append("--g2")
    if getattr(args, "release", False):
        cmd.append("--release")
    if getattr(args, "no_cov", False):
        cmd.append("--no-cov")
    r = _run(cmd, cwd=BACKEND)
    return 0 if r.returncode == 0 else 1


def cmd_health(args):
    url = args.url.rstrip("/")
    print(f"== 健康检查 {url} ==")
    for probe in ["/", "/api/health", "/health"]:
        try:
            with urllib.request.urlopen(url + probe, timeout=5) as resp:
                body = resp.read().decode("utf-8", "ignore")
                print(f"  {probe} -> HTTP {resp.status}: {body[:120]}")
                return 0
        except Exception as e:
            print(f"  {probe} -> 不可达: {type(e).__name__}")
    return 1


def cmd_start(args):
    print("== 启动后端（start_backend.bat）==")
    bat = os.path.join(BACKEND, "start_backend.bat")
    if os.path.isfile(bat):
        r = _run([bat])
        return 0 if r.returncode == 0 else 1
    print("  未找到 start_backend.bat")
    return 1


def cmd_dashboard(args):
    print("== 治理看板 ==")
    r = _run([_py(), os.path.join(BACKEND, "scripts", "dashboard.py")], cwd=BACKEND)
    return 0 if r.returncode == 0 else 1


def cmd_regress_g2(args):
    print("== G2 一键成片回归 ==")
    cmd = [_py(), os.path.join(BACKEND, "tools", "g2_regression.py")]
    if getattr(args, "full", False):
        cmd.append("--full")
    if getattr(args, "runs", 3) != 3:
        cmd += ["--runs", str(args.runs)]
    r = _run(cmd, cwd=BACKEND)
    return 0 if r.returncode == 0 else 1


def main():
    p = argparse.ArgumentParser(description="director 统一命令入口")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status", help="git 状态 + 运行时目录概览")
    sub.add_parser("test", help="跑单元测试（G1）")
    sub.add_parser("lint", help="跑 flake8（G0）")
    sub.add_parser("gates", help="跑门禁 G0-G6").add_argument("--no-cov", action="store_true", dest="no_cov", help="跳过覆盖率")
    gp = sub.add_parser("gates-g2", help="跑门禁含 G2 一键成片")
    gp.add_argument("--g2", action="store_true", default=True, help=argparse.SUPPRESS)
    gp.add_argument("--release", action="store_true", help="发布模式")
    gp.add_argument("--no-cov", action="store_true", dest="no_cov", help="跳过覆盖率")
    h = sub.add_parser("health", help="后端健康检查")
    h.add_argument("--url", default="http://127.0.0.1:8000")
    sub.add_parser("dashboard", help="生成治理看板")
    gr = sub.add_parser("regress-g2", help="G2 一键成片回归（默认契约冒烟）")
    gr.add_argument("--full", action="store_true", help="真实回归（需后端+ComfyUI+key）")
    gr.add_argument("--runs", type=int, default=3)
    sub.add_parser("start", help="启动后端")
    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        return 0
    fns = {"status": cmd_status, "test": cmd_test, "lint": cmd_lint,
           "gates": cmd_gates, "health": cmd_health, "dashboard": cmd_dashboard,
           "regress-g2": cmd_regress_g2, "start": cmd_start}
    return fns[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
