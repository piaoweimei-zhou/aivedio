# 端到端一键成片 + QC 实测记录

> 真实跑通「概念图 → 三视图 → 图生视频 → 配音 → 字幕 → 钩子 → 导出 → 质量质检」完整链路，并修复 2 个断链 bug。本文档记录完整过程、可复现命令、遇到的问题与解法。

- 日期：2026-08-20
- 状态：✅ 全链路 7/7 通过，QC 增强 8 维度出分
- 关联：`tools/baseline_oneclick.py`（零侵入 HTTP 驱动）、`backend/services/qc/qc_service.py`、`backend/services/comfyui/file_handler.py`、`backend/services/providers/provider_utils.py`

---

## 1. 前置环境

| 服务 | 端口 | 位置 |
|------|------|------|
| 后端（FastAPI/uvicorn） | 8000 | `backend/.venv-test`，Python ≥ 3.13 |
| ComfyUI | 8188 | `D:\1\2\ComfyUI_windows_portable\ComfyUI` |
| llama-server（Qwen3-VL-8B） | 8082 | 常驻，供 QC 语义审核 |

**关键**：后端启动必须带 `COMFYUI_DIR`（项目已提供 `backend/start_backend.bat`，已正确设置 `COMFYUI_DIR` / `COMFYUI_PYTHON` / `COMFYUI_BASE_URL` / `PYTHONUTF8=1`，且用引号包裹赋值避免尾随空格）。

**推荐启动方式（标准，交互窗口前台跑）**：
```powershell
cd D:\1\2\director\backend
.\start_backend.bat
```

**如需后台启动**（落盘日志、不阻塞）：
```powershell
cmd /c "set "COMFYUI_DIR=D:\1\2\ComfyUI_windows_portable\ComfyUI" && set "COMFYUI_SEARCH_PATHS=D:\1\2\ComfyUI_windows_portable\ComfyUI" && set "PYTHONIOENCODING=utf-8" && start /b D:\1\2\director\backend\.venv-test\Scripts\python.exe -m uvicorn main:app --app-dir D:\1\2\director\backend --port 8000 --host 127.0.0.1 > D:\1\2\director\backend\_boot.txt 2>&1 & echo relaunched"
```

> ⚠️ 用 `set "VAR=值"`（带引号）而不是 `set VAR=值 &&`，可避免尾随空格（`&&` 前空格被吞进值）。重启后确认启动日志**无** `[ComfyUIConfig] 未检测到 ComfyUI 安装目录` 警告即成功。
> ⚠️ `start_backend.bat` 中 `PYTHONUTF8=1` 已解决中文 print 编码问题（等价于设 `PYTHONIOENCODING=utf-8`）。

---

## 2. 驱动方式：baseline_oneclick.py

`tools/baseline_oneclick.py` 是**零侵入**的 HTTP 客户端，复现前端 `OneClickVideoPage` 的 7 环节 DAG：

```
POST /api/director/batches          → 创建任务
POST /api/director/batches/{id}/dry-run → 预检（可用 --skip-dry-run 跳过）
POST /api/director/batches/{id}/start  → 启动
GET  /api/director/batches/{id}        → 轮询至终态
```

**7 个环节**（`build_oneclick_steps()`）：

| step | stage | provider | 说明 |
|------|-------|----------|------|
| s1/s2 | concept | comfyui | 角色 + 场景概念图 |
| s3 | angle | comfyui | 角色三视图 |
| s4 | video | minimax_h3 | 图生视频（720p/8s） |
| s5 | subtitle | local | 字幕叠加 |
| s6 | hook_overlay | local | 钩子文案 |
| s7 | export | local | 导出 1080×1920 |

### 运行命令

```powershell
# 实时跑 N 次（默认 10 次，主题池轮换）
d:\1\2\director\backend\.venv-test\Scripts\python.exe d:\1\2\director\backend\tools\baseline_oneclick.py --runs 3 --topic "一只会做饭的猫"

# 单次（跳过预检，后台跑落日志）
cmd /c "set PYTHONIOENCODING=utf-8 && start /b D:\1\2\director\backend\.venv-test\Scripts\python.exe D:\1\2\director\backend\tools\baseline_oneclick.py --runs 1 --topic 一只会做饭的猫 --skip-dry-run --host http://127.0.0.1:8000 > D:\1\2\director\backend\_baseline.txt 2> D:\1\2\director\backend\_baseline_err.txt & echo started"

# 从磁盘已有 batch 聚合（不重跑）
d:\1\2\director\backend\.venv-test\Scripts\python.exe d:\1\2\director\backend\tools\baseline_oneclick.py --from-disk --only-completed
```

> ⚠️ **必须设 `PYTHONIOENCODING=utf-8`**，否则脚本内中文 `print`（如主题 `!r` 含非 GBK 字符）会抛 `UnicodeEncodeError` 崩在 GBK stdout。
> ⚠️ `--host` 建议显式 `http://127.0.0.1:8000`，避免 `localhost` 解析到 IPv6 `::1` 而连接拒绝。

---

## 3. 全链路实测结果

### 3.1 修复前（首跑）

| 环节 | 成功率 | 说明 |
|------|--------|------|
| concept | 100% | P50≈161s |
| angle | **0%** | 3.7s 秒挂，`ComfyUI Invalid image file` |
| video 及下游 | 0% | 依赖 angle 输出，cascade 失败 |

### 3.2 修复后（7/7 100%）

| 环节 | 成功率 | P50(s) |
|------|--------|--------|
| concept 概念图 | 100% | 170 |
| angle 三视图 | 100% | 107 |
| video 图生视频 | 100% | 632 |
| subtitle 字幕 | 100% | 1.7 |
| hook_overlay 钩子 | 100% | 1.4 |
| export 导出 | 100% | 3.1 |

产物落盘：`backend/data/generated/baseline/{concept,angle,video,subtitle,hook_overlay,export}/`。

---

## 4. 遇到的问题与修复

### 问题 1：angle 三视图失败（`Invalid image file`）

**现象**：concept 成功，angle 3.7s 秒挂，`ComfyUI Custom validation failed: image - Invalid image file`。

**排查链**：
1. `baseline_oneclick` 报 `create_failed` → 实为 `--host localhost` 解析问题，改 `127.0.0.1` 解决。
2. 读 batch step 错误 → `Invalid image file: baseline...`（存储时被截断，实际 ComfyUI 收到完整文件名）。
3. 用 `ensure_image_in_input_dir` 复现 → `FileNotFoundError: ...\ComfyUI \input`（**路径带尾随空格**）。
4. 根因分两层：
   - **COMFYUI_DIR 带尾随空格**：`set COMFYUI_DIR=... &&` 吞空格 → output/input 路径全错。
   - **concept 图持久化到后端 `data/generated/baseline/concept/`，但取图逻辑不认持久化目录**：`ensure_image_in_input_dir` 只搜 ComfyUI output/input/HTTP/项目目录，找不到 → ComfyUI LoadImage 拿到文件名但文件不在 input → `Invalid image file`。

**修复**：
- 后端带正确 `COMFYUI_DIR`（引号包裹）启动。
- `backend/services/comfyui/file_handler.py` → `ensure_image_in_input_dir` 末尾加「从 `GENERATED_DIR`（`backend/data/generated`）持久化目录兜底搜索」，按 `subfolder` + 扁平 + `os.walk` 递归三种结构找图，复制到 ComfyUI input 目录。

**验证**：日志出现 `从持久化目录复制 | baseline_concept_character_006_fd93df.png` → `LoadImage节点68 注入图片: baseline_concept_character_006_fd93df.png`，angle 通过。

### 问题 2：subtitle 字幕失败（`视频文件不存在`）

**现象**：video 成功后，subtitle 0.04s 秒挂，`视频文件不存在: /api/comfyui/image?filename=baseline_video_character_001_90068a.mp4&subfolder=baseline/video`。

**根因**：video 输出资产 url 是 `/api/comfyui/image?filename=...&subfolder=...`，`output_file_from_url` 对这种格式（`clean == "/api/comfyui/image"`）**直接 `return None`** → `resolve_local_video` 拿不到本地路径 → subtitle 抛"视频文件不存在"。

**修复**：`backend/services/providers/provider_utils.py` → `output_file_from_url` 增加 `/api/comfyui/image` 分支，用 `urlparse`/`parse_qs` 提取 `filename`+`subfolder`，依次到 `data/generated`（含 subfolder / 扁平 / 递归）与 ComfyUI output 目录查找真实文件。

**验证**：`resolve_local_video` 正确返回 `...\data\generated\baseline/video\baseline_video_character_001_90068a.mp4`（exists=True），subtitle 通过。

---

## 5. 增强 QC：每环节质量

`backend/services/qc/qc_service.py` 此前已新增两个**不依赖模型**的客观维度：

| 维度 | 来源 | 算法 |
|------|------|------|
| `voice`（配音质量） | ffmpeg | 抽 PCM 算 RMS 响度 / 静音占比 / 削波率 / 采样率 → 0-100 |
| `composition_cv`（构图客观） | cv2 | 关键帧三分法对齐 / 主体亮度分布 / 边缘锐度 → 0-100 |

**8 维度权重**：`quality:18, consistency:16, lip_sync:12, composition:14, composition_cv:8, rhythm:10, voice:12, compliance:10`

### 对真实成片跑 QC

```powershell
# 脚本内：run_qc_async(成片路径, caption=真实文案, threshold=60, use_semantic=True, manage_server=False)
```

**最终成片 `baseline_export_001_b2b1dd.mp4`（8.2MB，1080×1920）QC 结果**：

| 维度 | 分 | 来源 |
|------|-----|------|
| quality 画质 | 100 | cv2 客观 |
| voice 配音 | 100 | ffmpeg 客观（RMS=0.186/静音22%/无削波） |
| composition_cv 构图客观 | 72 | cv2 三分法 |
| composition 构图美学 | 95 | 语义（"画面构图精美"） |
| consistency 一致性 | 90 | 语义 |
| lip_sync 口型 | 80 | 语义 |
| rhythm 节奏 | 85 | 语义 |
| compliance 合规 | 45 | 语义+本地关键词（**命中"诱导关注"** → medium 扣分） |

**total_score = 86.1，passed=True**（阈值 60；合规命中属 medium 级扣分不拦截，符合设计）。

---

## 6. 产物与改动清单

**改动文件**：
- `backend/services/comfyui/file_handler.py`：持久化目录兜底搜索
- `backend/services/providers/provider_utils.py`：`/api/comfyui/image` URL → 本地路径解析
- `backend/services/qc/qc_service.py`：`voice` / `composition_cv` 客观维度 + 8 维度权重

**产物**（`backend/data/generated/baseline/`）：
- `concept/`：角色、场景概念图
- `angle/`：角色三视图
- `video/`：720p 图生视频（带配音音轨）
- `subtitle/`：字幕叠加后视频
- `hook_overlay/`：钩子文案叠加后视频
- `export/`：最终 1080×1920 竖版成片

---

## 7. 注意事项 / 踩坑清单

1. **COMFYUI_DIR 必须引号包裹赋值**，防尾随空格；否则 angle/video/subtitle 全断。
2. **后端重启后**要确认启动日志无 `[ComfyUIConfig] 未检测到 ComfyUI` 警告。
3. **`PYTHONIOENCODING=utf-8`** 跑 baseline，否则中文 print 崩 GBK。
4. **`--host 127.0.0.1:8000`** 显式指定，防 localhost IPv6 解析问题。
5. ComfyUI output 有 6h 定时清理，concept/video 图由后端持久化到 `data/generated`；取图/取视频逻辑**必须认持久化目录**，不能只搜 ComfyUI output。
6. QC 语义审核打 8082 常驻 llama-server（`manage_server=False`，避免误杀常驻进程）。
