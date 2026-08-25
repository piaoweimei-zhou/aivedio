# 发布流程 SOP（P2 — T6）

> 状态：已落地 | 日期：2026-08-25 | 适用范围：所有打 tag / 发版动作
>
> 本文件是 `docs/工程化/综合治理方案.md` P2「3 份 SOP」之一。版本线现状：`v0.9-baseline → v0.9-p0-cleanup → v0.9-p0-standards → v0.9-p1-paths → v0.9-fix-f821 → v0.9-p1-final`。

## 1. 发布物定义

| 产物 | 位置 | 说明 |
|---|---|---|
| git tag `vX.Y[-stage]` | master | 唯一版本锚点，必须指向 CI-green commit |
| CHANGELOG.md | repo root | [Unreleased] → 新 version 段（Added/Changed/Fixed） |
| Docker image | `docker build -f backend/Dockerfile` / `frontend-director/Dockerfile` | 可选；CI docker-build job 验证可构建性 |

## 2. Pre-release checklist（按序执行，全过才打 tag）

```bash
# 0) clean tree, master up-to-date
git status && git pull --ff-only origin master

# 1) full gate suite
python director.py gates --all        # G0-G6；任何 HARD fail = stop and fix

# 2) E2E smoke（环境允许时）
python director.py e2e                # 一键成片 + QC，batch ≥3 成功记录入 CHANGELOG

# 3) changelog
#    - move [Unreleased] entries into ## vX.Y (date)
#    - add "发布说明" line: gate results, E2E batch result, known gaps

# 4) tag & push
git add CHANGELOG.md && git commit -m "release: vX.Y"
git tag v0.9-pX-final                 # stage naming below
git push origin master --tags
```

## 3. Tag 命名（沿用 P1 约定）

| Pattern | Meaning | Example |
|---|---|---|
| `vA.B-baseline` | pre-governance anchor | v0.9-baseline |
| `vA.B-pN-<stage>` | phase N stage checkpoint | v0.9-p1-paths, v0.9-fix-f821 |
| `vA.B-final` / `vA.B` | stable release | v0.9-p1-final → 下一版 v1.0 |

- Fix-only tags: `vX.Y-fix-<short>` (e.g. `v0.9-fix-f821`).
- Never delete or move a tag; cut a new one if wrong.

## 4. Rollback procedure

```bash
git checkout <last-good-tag>   # e.g. v0.9-p1-final
# for deployed env: rebuild image from that commit, redeploy
```

- Rollback decision SLA: P0 incident → rollback first, diagnose after.
- After rollback, file a fix branch; do not hotfix on the old tag's tree directly.

## 5. Post-release (within 24h)

1. `docs/03_操作/start.md` — update "current version" line if it exists.
2. LEARNINGS.md entry if release surfaced any incident/near-miss.
3. Update 治理执行手册 execution log with tag + gate summary.

## 6. Known gaps at v0.9-p1-final (carry-forward)

- Coverage 31% < 65% target — ratchet in CI, tracked as T3 gap (does not block release per G1 soft rule).
- `backend/api/infinite_canvas_api.py` >40KB — registered exception, split scheduled.
