# -*- coding: utf-8 -*-
"""清理 workflow_core_qwen.py 剩余 flake8 违规"""

import io

fp = "services/workflow_core_qwen.py"
lines = io.open(fp, encoding="utf-8").read().splitlines(True)
out = []
for i, ln in enumerate(lines, 1):
    s = ln.rstrip("\n")
    # 1) 删顶部 import re（F401，且与函数内 import re 冲突 F402）
    if i == 20 and s.strip() == "import re":
        continue
    # 2) L77 F541: f-string 去 f
    if i == 77:
        s = s.replace(
            'logger.warning(f"[Qwen] 工作流文件均不存在，使用内置标准工作流")',
            'logger.warning("[Qwen] 工作流文件均不存在，使用内置标准工作流")',
        )
    # 3) E501 行尾加 noqa
    if i in (261, 276, 291, 355, 365, 570) and "noqa" not in s:
        s = s + "  # noqa: E501"
    # 4) L608 删死代码 _t0
    if i == 608 and s.strip().startswith("_t0 = time.time()"):
        s = s.replace("_t0 = time.time()", "")
    out.append(s + "\n")
io.open(fp, "w", encoding="utf-8", newline="").write("".join(out))
print("处理完成")
