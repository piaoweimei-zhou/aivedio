# -*- coding: utf-8 -*-
# flake8: noqa: E501  # 提示文案长行不可避免
"""commit-msg 门禁：强制 Conventional Commits 格式（B 项，机器强制提交规范）。

用法（pre-commit commit-msg hook 调用）：
    python tools/commit_msg_check.py <commit_msg_file>

校验规则：
    格式  : type(scope): subject  或  type: subject
    type  : feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert
    scope : 可选，小写字母/数字/下划线/连字符
    subject: 非空

参考：https://www.conventionalcommits.org/
"""
import re
import sys

_PATTERN = re.compile(
    r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(\([a-z0-9_-]+\))?!?:\s+.+$"
)

_ALLOWED_TYPES = {
    "feat", "fix", "docs", "style", "refactor",
    "perf", "test", "build", "ci", "chore", "revert",
}


def main() -> int:
    if len(sys.argv) < 2:
        print("[commit-msg] 缺少 commit-msg 文件参数", file=sys.stderr)
        return 1
    path = sys.argv[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # 兼容 UTF-8 BOM（Windows 工具写入常见）
        text = text.lstrip("\ufeff")
        lines = text.splitlines()
    except OSError as e:
        print(f"[commit-msg] 读取失败: {e}", file=sys.stderr)
        return 1

    # 跳过空行与注释，取第一条有效 subject
    subject = ""
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        subject = line
        break
    if not subject:
        print("[commit-msg] 提交信息为空", file=sys.stderr)
        return 1

    if not _PATTERN.match(subject):
        print(
            f"[commit-msg] 提交信息不符合 Conventional Commits 规范:\n"
            f"  当前  : {subject}\n"
            f"  期望  : type(scope): subject  (type ∈ {sorted(_ALLOWED_TYPES)})\n"
            f"  示例  : fix(ci): 修复 YAML 解析失败\n"
            f"          test(coverage): 补充 provider_utils 单测",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
