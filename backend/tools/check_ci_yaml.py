# -*- coding: utf-8 -*-
# flake8: noqa: E501  # 提示文案长行不可避免
"""CI YAML 本地校验门禁（G4 类，机器强制）。

背景：2026-08-27 CI 事故——workflow YAML 的 step `name:` 值里出现"半角冒号+空格"
被解析成 mapping，整个 workflow 无效，GitHub 直接创建 0 个 job（run 静默失败且无日志）。

本 hook 在 commit 前对 .github/workflows/*.yml 做 yaml.safe_load 解析，
任何解析失败立即拦截，杜绝"push 后才在 CI 上发现 workflow 无效"。

用法（pre-commit hook 调用，普通文件 hook）：
    python tools/check_ci_yaml.py <file1> [file2 ...]

校验规则：
    - 每个被检查的 .yml/.yaml 必须能被 PyYAML yaml.safe_load 成功解析
    - 解析失败：报错并退出码 1（含文件名 + YAML 定位）
"""
import glob
import os
import sys

try:
    import yaml
except ImportError:  # pragma: no cover - venv 缺依赖时的降级
    print("[ci-yaml] 依赖缺失: 未安装 PyYAML，跳过校验", file=sys.stderr)
    sys.exit(0)

# 半角冒号+空格 出现在标量里是最常见解析陷阱；解析失败时优先提示
_HINT = "常见原因：YAML 标量值里出现半角冒号+空格（如 name: xxx: yyy），应加双引号包裹或改用全角冒号"


def _check(path: str) -> list:
    """解析单个 YAML 文件，返回问题描述列表（空 = 通过）。"""
    problems = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            content = f.read()
    except OSError as e:
        return [f"{path}: 读取失败 {e}"]
    if not content.strip():
        return [f"{path}: 文件为空"]
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        where = ""
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            where = f" (line {mark.line + 1}, col {mark.column + 1})"
        problems.append(f"{path}{where}: YAML 解析失败: {e}\n  {_HINT}")
    return problems


def main() -> int:
    # 支持 glob 模式与具体文件混用（PowerShell 不会为原生命令展开通配符）
    paths = []
    for arg in sys.argv[1:]:
        if any(ch in arg for ch in "*?["):
            paths.extend(glob.glob(arg))
        elif os.path.isfile(arg):
            paths.append(arg)
    # 去重保序
    paths = list(dict.fromkeys(paths))
    if not paths:
        return 0
    problems = []
    for p in paths:
        problems.extend(_check(p))
    if problems:
        print("[ci-yaml] CI YAML 校验未通过（杜绝 workflow 解析失败事故）:", file=sys.stderr)
        for msg in problems:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    print(f"[ci-yaml] OK ({len(paths)} file(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
