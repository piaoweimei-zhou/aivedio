# -*- coding: utf-8 -*-
"""nightly 回归报告生成器（C 项：回归可追踪）。

在 CI backend-test job 跑完测试后调用，生成结构化报告 JSON 并输出 job summary。

用法：
    python tools/nightly_report.py --junitxml test-results.xml --coverage cov.json \
        [--out nightly-report.json]

输入：
    --junitxml : pytest --junitxml 输出（tests/failures/errors/skipped）
    --coverage : pytest --cov-report=json 输出（coverage.json 含 total.percent）
    --out      : 报告输出路径（默认 nightly-report.json）

输出：
    1. JSON 报告文件（可上传 artifact 归档追踪）
    2. Markdown summary（写入 $GITHUB_STEP_SUMMARY，run 页可见）
    3. stdout 打印一行摘要
"""
import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description="nightly 回归报告生成器")
    ap.add_argument("--junitxml", required=True, help="pytest junitxml 路径")
    ap.add_argument("--coverage", default="", help="pytest coverage.json 路径")
    ap.add_argument("--out", default="nightly-report.json", help="报告输出路径")
    args = ap.parse_args()

    # 1) junitxml → 测试统计（pytest 根为 <testsuites>，统计在 <testsuite> 子节点）
    tests = failures = errors = skipped = 0
    try:
        root = ET.parse(args.junitxml).getroot()
        suites = root.findall(".//testsuite")
        for suite in suites:
            tests += int(suite.get("tests", 0))
            failures += int(suite.get("failures", 0))
            errors += int(suite.get("errors", 0))
            skipped += int(suite.get("skipped", 0))
    except Exception as e:
        print(f"[nightly] 解析 junitxml 失败: {e}", file=sys.stderr)
        return 1

    # 2) coverage.json → 覆盖率（字段 percent_covered）
    coverage = 0.0
    if args.coverage and os.path.exists(args.coverage):
        try:
            cov = json.load(open(args.coverage, encoding="utf-8"))
            totals = cov.get("totals", {})
            coverage = float(totals.get("percent_covered", totals.get("percent", 0.0)))
        except Exception as e:
            print(f"[nightly] 解析 coverage.json 失败: {e}", file=sys.stderr)

    passed = tests - failures - errors - skipped
    status = "PASS" if (failures == 0 and errors == 0) else "FAIL"

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "commit": _git_sha(),
        "coverage": round(coverage, 2),
        "tests": tests,
        "passed": passed,
        "failed": failures + errors,
        "skipped": skipped,
        "status": status,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 3) job summary（GITHUB_STEP_SUMMARY 存在时写入）
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        md = (
            "## Nightly 回归报告\n\n"
            f"| 项 | 值 |\n|---|---|\n"
            f"| 状态 | {status} |\n"
            f"| 提交 | {report['commit']} |\n"
            f"| 覆盖率 | {report['coverage']}% |\n"
            f"| 测试总数 | {tests} |\n"
            f"| 通过 | {passed} |\n"
            f"| 失败 | {failures + errors} |\n"
            f"| 跳过 | {skipped} |\n"
            f"| 时间 | {report['timestamp']} |\n"
        )
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)

    print(f"[nightly] {status} cov={report['coverage']}% tests={tests} "
          f"pass={passed} fail={failures + errors} skip={skipped} -> {args.out}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
