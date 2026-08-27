# 测试门禁 SOP（P2 — T6）

> 状态：已落地 | 日期：2026-08-25 | 适用范围：所有改动 backend/ 的提交与发布
>
> 本文件是 `docs/工程化/综合治理方案.md` P2「3 份 SOP」之一，定义什么算"测过"、何时必须补测试。

## 1. 三级门禁（强制顺序）

| 级别 | 命令 | 通过标准 | 触发时机 |
|---|---|---|---|
| L0 本地自检 | `python director.py lint && python director.py test -q` | flake8 0 新增 + pytest 全绿 | 每次 commit（pre-commit ratchet） |
| L1 CI | GitHub Actions：backend-lint / backend-test(+coverage) / frontend-build / g2 / trafficos / docker | 6 job 全绿；覆盖率 ≥40%（见 §3 历史，2026-08-27 因 pm 测试暂缓调整） | 每次 push / PR |
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

- **基线历史**（本仓库原则：只许升不许降，降必须在此登记）：
  - 2026-08-25：**31%**（初建 CI）
  - 2026-08-26~27 上午：**44%**（补测试提升，全量 389 用例）
  - **2026-08-27：40%**（**process_manager 测试暂缓摘除**，见 §6-2 根因。排除 pm 后主片实测上限 ~42%，gate 定为 40% 留缓冲；**待 pm 修复重新启用后恢复 44%+**）
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

## 6. CI 实战踩坑经验（2026-08-27 沉淀）

> 来源：GitHub Actions 首次真实落地时连续多轮"卡死/失败"的排障全记录。三条独立根因叠加，逐一排掉后 CI 全绿（362 passed + 6 job success）。
> 提交链：1bb87a1（CREATE_NO_WINDOW 平台化）→ 86a352c → 6d74db9 → ea9002a → a9ce62f。

### 6-1. 【已修】`subprocess.CREATE_NO_WINDOW` 是 Windows 独有，Linux 直接 AttributeError → 静默返回 False

**现象**：`test_start_process_success` 在 CI Linux 上失败（断言 `_start_process() is True` 得 False），本地 Windows 全绿。
**根因**：`_start_process()` 里 `Popen(..., creationflags=subprocess.CREATE_NO_WINDOW)`——`CREATE_NO_WINDOW` 在 Linux 的 `subprocess` 里不存在，实参求值抛 AttributeError，被 `except Exception` 吞掉后返回 False。**生产代码在非 Windows 环境永远无法启动 ComfyUI**（不只是测试问题）。
**修复**：`_popen_kwargs` 平台化，`sys.platform == "win32"` 时才传 `creationflags`。涉及 `services/comfyui/process_manager.py` + `services/comfyui_lifecycle_process.py` 两处。
**回归门禁**：新增用例应覆盖"Popen 参数平台化"分支（或至少保证 `_start_process` 成功路径在 Linux 可跑）。

### 6-2. 【已定位·暂缓】pytest-asyncio 后台无限 task 泄漏 → Linux event loop 关闭卡死

**现象**：`tests/test_process_manager.py` 单独/全量在 CI Linux 上都卡在 `collected 27 items` 后（Windows 本地 27 用例 25s 全绿）。
**根因**：`_start_health_check()` 启动**无限 `while True` 后台 task**（`_health_check_loop`），测试结束未 cancel → pytest-asyncio 关闭 event loop 时等待残留 task → **Linux 永久卡死**。Windows 的 loop 关闭行为不同，故本地不触发。
**为什么难排查**：pytest-timeout 的 `signal`/`thread` 方法都**无法中断 asyncio 的 loop 关闭卡死**（实测加了 pytest-timeout 反而让单独跑也卡）——误导排查方向。
**当前处置**：`--ignore=tests/test_process_manager.py` 从 CI 摘除（用户决定暂缓），覆盖率 gate 降至 40%。
**已备好的根治**：`tests/test_process_manager.py` 的 `mgr` fixture 改为 **async fixture + teardown cancel 后台 task**（`_health_check_task` / `_idle_shutdown_task`），本地 27 用例全过且无卡死。**重新启用只需**：恢复 CI 的 `--ignore` 移除 + gate 回 44%，验证一次 Linux CI 即可。
**经验**：凡被测代码会启动**后台无限 task** 的测试，fixture teardown 必须 cancel + await；否则 CI Linux 是"定时炸弹"。

### 6-3. 【已修】GitHub Actions YAML：step `name:` 值里半角冒号+空格 → workflow 解析失败（job 0 静默失败）

**现象**：push 后 run 直接 `completed failure`，**job 列表为空（total_count=0）**，无任何日志可查。
**根因**：`name: Run tests (process_manager 暂缓: Linux ...)`——值里 `暂缓: Linux` 的**半角冒号+空格**被 YAML 解析成 mapping → 整个 workflow 无效，GitHub 不创建任何 job。
**排查关键**：job 0 是"workflow 解析失败"的标志（区别于 job 内失败）；本地用 `python -c "import yaml; yaml.safe_load(open(ci.yml))"` 秒级定位。
**修复**：step name 用双引号包裹 `name: "..."`，或避免在 name 值里用半角冒号。
**回归门禁**：CI YAML 改动本地必先过 `yaml.safe_load` 校验再 push（可加进 pre-commit 的 G4 类检查）。

---

## 7. 新增测试 checklist

- [ ] 用例名 `test_<行为>_<场景>`，一用例一断言主题
- [ ] mock 边界在 provider 层，不在 stage 内部
- [ ] tmp_path 隔离文件系统副作用，不留 generated/ 垃圾
- [ ] 失败时输出能直接定位（assert msg / caplog）
- [ ] **被测代码若启动后台 task**：fixture teardown 必须 cancel + await（见 §6-2）
