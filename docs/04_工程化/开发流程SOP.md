# 开发流程 SOP（P2 — T6）

> 状态：已落地 | 日期：2026-08-25 | 适用范围：director 全仓改动
>
> 本文件是 `docs/工程化/综合治理方案.md` P2「3 份 SOP」之一，定义从改代码到提交的强制路径。

## 1. 日常提交（必须）

```bash
# 1) 改动后自检（本地 ratchet pre-commit 也会自动跑这两条）
python director.py lint        # flake8 + eslint --max-warnings=0，只挡新增违规
python director.py test -q     # pytest 全绿；失败 = 不许提交

# 2) 提交
git add <files> && git commit -m "<type>: <一句话描述>"   # type: feat/fix/chore/docs/test/refactor
```

- 提交信息前缀与类型对齐 CHANGELOG.md 的 Added/Changed/Fixed。
- **禁止**：`--no-verify` 绕过 pre-commit；commit 里夹带 node_modules / `.pyc` / `input/`（已 .gitignore，出现即说明 ignore 规则被改）。

## 2. 分支策略

| 分支 | 用途 |
|---|---|
| `master` | 可发布主干，永远保持 CI 绿 + G0-G6 可跑 |
| `feat/<name>` | 新功能，开发中不要求全门禁（但 lint/test 应绿） |
| `fix/<name>` | bug 修复，必须附回归测试 |

- PR 合并前：CI 3 job（lint / pytest+coverage / build）全绿。
- 禁止 force-push master；历史重写需团队确认。

## 3. 新增代码规范

1. **路径**：一律 `from services.paths import ...`，禁 cwd 相对字符串（G4 正则兜底）。
2. **provider**：新供应商放 `backend/services/providers/`，实现 `ProviderPlugin` 接口 + `is_available()`。
3. **stage**：新管线阶段放 `backend/services/stages/`，继承现有 stage 基类，注册进 pipeline。
4. **API**：路由放 `backend/api/`，handler ≤200 行；超过先拆 helper 再写。
5. **密钥**：只从 env 读（模板见 `backend/.env.example`），禁硬编码。
6. **日志**：`logging.getLogger(__name__)`，禁 print（调试脚本除外）。

## 4. 大文件红线（T2）

- 单 .py >40KB → 必须拆或登记例外到「治理执行手册」T2 表。
- `backend/services/workflows/` 为唯一 workflow 模块位置；新代码不进 services 根目录。

## 5. 文档同步（每次结构性改动必做）

| 改了什么 | 同步哪份文档 |
|---|---|
| 新增/删除 provider、stage、API | `README.md` + `docs/03_操作/系统功能.md` |
| 路径/目录变更 | `docs/04_工程化/资产命名与目录规范.md` |
| 修 bug / 踩坑 | `docs/02_分析/LEARNINGS.md` |
| 发布动作 | `CHANGELOG.md`（[Unreleased] → 版本段） |

## 6. 事故响应

1. 线上/本机坏 → 先回滚到最近 tag：`git checkout v0.9-p1-final && git reset --hard`
2. 复现 + 最小修复 on `fix/` branch with regression test
3. 记录 LEARNINGS.md（现象 / root cause / fix / prevention）
