# CHANGELOG

本文件记录 director 导演工作台的重要变更。格式对齐 G6 发布门禁要求。

## [Unreleased]

### Added
### P3 中间产物/临时文件治理
- **output/ 归类**：12 个历史中间产物目录（quick_video_test/script/quality_test/real_video_gen_v2/video_gen/video_v3/proto_gen/graphic/full_gen/batch_gen_2026/pipeline_gen/pipeline_scripts）+ qc_smoke_test.mp4 归档至 ackend/output/_archive_20260825/（42.3MB），output/ 根仅保留当前产物 output/、temp/
- **data/ 清理**：12 个调试脚本（_whisper/_vad/_tts 系列）+ 33 个历史 oneclick_baseline 报告 + baseline log/selftest_result/_tmp_cur 归档至 ackend/data/_archive_20260825/，保留 8-25 当前回归报告
- 归档目录带 _archive_<日期> 前缀，位于已被 .gitignore 覆盖的 output/、data/ 下，不入库；**移动优先、可逆**（非删除）
- G0-G6 门禁体系一键执行脚本 `backend/scripts/gates.py`（P2）
- 覆盖率门禁（coverage ≥ 65%，G1 子项）

## [v0.9-p1-final] - 2026-08-25

### P1 收尾
- **文档归类**：根目录 14 个 md/html 按分类映射 git mv 至 `docs/01_规划/02_分析/03_操作`，README 及被移动文档引用同步更新，删除运行时 debug.log
- **前端规范**：补 ESLint 8 + Prettier（`.eslintrc.cjs`/`.prettierrc` + lint/format script），npm 新增 134 依赖
- **workflows 索引**：`docs/02_分析/workflows索引.md` 登记 34 个模板用途；经评估维持扁平目录 + 中文名（后端 API 安全模型拒绝子路径）

## [v0.9-p1-paths] - 2026-08-25

### P1 路径收敛（T7 核心）
- 新建 `services/paths.py`：全部运行时目录根（data/output/assets/logs）单一来源
- 收敛 46 处路径引用（18 文件），消除 GENERATED_DIR 双定义
- output/output 嵌套定性：`/assets/output/` 死代码映射、`output/output/` 140 文件为无引用历史遗留（保留原位）
- lint 净变化 -2（1179→1177，无新增违规）；pytest 121 全绿

## [v0.9-fix-f821] - 2026-08-25

### Fix
- `workflow_storyboard.py` 补 import 4 个模板构建函数，修复 single/dual/local_multi/gpt_storyboard 模板路由 NameError（F821）

## [v0.9-p0-standards] - 2026-08-25

### P0 规范
- `director.py` 统一命令入口（status/test/lint/gates/health/start）
- flake8/black/isort 配置 + lint 基线冻结（1183）+ pre-commit ratchet 钩子

## [v0.9-p0-cleanup] - 2026-08-25

### P0 清理
- 调试脚本归档 `scripts/debug/`、`_selftest_runner` 规范移动、归档数据 gitignore

## [v0.9-baseline] - 2026-08-25

### P0 Step 0
- 基线入库：QC 新功能（asset_organizer/qc_stage/QcReportCard）、工具脚本、文档、22 处修改 + 工程化治理方案 v2.1
