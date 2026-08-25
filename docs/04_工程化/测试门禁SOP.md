# 测试门禁 SOP（P2 — T6）

> 状态：已落地 | 日期：2026-08-25 | 适用范围：所有改动 backend/ 的提交与发布
>
> 本文件是 `docs/工程化/综合治理方案.md` P2「3 份 SOP」之一，定义什么算"测过"、何时必须补测试。

## 1. 三级门禁（强制顺序）

| 级别 | 命令 | 通过标准 | 触发时机 |
|---|---|---|---|
| L0 本地自检 | `python director.py lint && python director.py test -q` | flake8 0 新增 + pytest 全绿 | 每次 commit（pre-commit ratchet） |
| L1 CI | GitHub Actions：backend-lint / backend-test(+coverage) / frontend-build | 3 job 全绿；覆盖率 ≥31%（ratchet baseline，2026-08-25） | 每次 push / PR |
| L2 发布门禁 | `python director.py gates --all` | G0-G6 按阶段通过 | 打 tag / 发版前 |

## 2. 测试分层与位置

```
backend/tests/
├── test_<module>.py        # 单测：一模块一文件，pytest-asyncio
├── conftest.py             # 共享 fixture（mock provider、tmp workspace）
└── （不放调试脚本 — 归 backend/scripts/debug/）
```

- **provider 改动** → mock 外部 API，断言 request/response shape + error path。
- **stage 改动** → 用 tmp_path 造最小素材，跑 stage 主路径 + 1 个失败分支。
- **API 改动** → TestClient 打真实路由，断言 status + body 关键字段。
- 禁止在 tests/ 里放需要真 key / 真网络的脚本（归 scripts/debug/）。

## 3. 覆盖率规则（T3 ratchet）

- Baseline：TOTAL **31%**（2026-08-25，`--cov-fail-under=31` in CI + G1）。
- **只许升不许降**：改了哪块就补哪块的用例；想降 baseline 必须改 CI 里的数字 + 在本文档记一笔。
- 目标路径：65%（治理方案 T3）→ 90%+ core services，通过分批补测推进，不一次性硬凑。

## 4. 发布前清单（G1-G6 速查）

```bash
python director.py gates --all    # G0 依赖 / G1 测试覆盖 / G2 E2E 回归 / G3 API contract / G4 lint+path / G5 frontend build / G6 changelog
```

| Gate | 阻塞级别 | 备注 |
|---|---|---|
| G0 deps | 硬 | requirements.lock 与 .env.example 存在且可装 |
| G1 tests | 软* | pytest 全绿硬；覆盖率 < baseline 硬，< 65% 记为 ratchet 缺口（不阻塞发布但必须登记） |
| G2 e2e | 软 | 一键成片回归，环境允许时必跑 |
| G3 api | 硬 | 路由契约 snapshot 不漂移 |
| G4 lint/path | 硬 | flake8 + cwd 路径正则零残留 |
| G5 frontend | 硬 | npm run build 通过 |
| G6 changelog | 硬 | CHANGELOG.md [Unreleased] 非空或显式跳过原因 |

\* 「软」= 发布前必须人工确认并记录，不是可忽略。

## 5. 失败处置

1. CI 红 → 24h 内修或 revert；不许 "will fix later" on master.
2. G2 E2E 环境性失败（GPU/网络）→ 记录原因 + 最近一次成功证据（commit），允许带条件发布。
3. 任何门禁被临时绕过 → 必须在 CHANGELOG [Unreleased] 写 bypass reason，48h 内闭环。

## 6. 新增测试 checklist

- [ ] 用例名 `test_<行为>_<场景>`，一用例一断言主题
- [ ] mock 边界在 provider 层，不在 stage 内部
- [ ] tmp_path 隔离文件系统副作用，不留 generated/ 垃圾
- [ ] 失败时输出能直接定位（assert msg / caplog）
