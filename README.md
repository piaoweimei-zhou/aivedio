# 导演工作台 (Director's Workbench)

短剧/视频创作者的全栈工具。独立子项目，与 AI-IDE-v2 并行开发。

## 快速启动

### 后端

```bash
cd backend
# 运行时依赖（生产/CI 可复现安装用 requirements.lock；开发含测试依赖用 requirements-dev.txt）
pip install -r requirements.txt
# 推荐：用固化脚本启动（自动设置 ComfyUI 环境变量，要求 Python 3.13）
start_backend.bat
# 或手动启动
python main.py
```

服务启动在 `http://localhost:8000`。

> ComfyUI 为宿主机 GPU 进程，后端 `ensure_running` 会自动拉起（需 `COMFYUI_DIR` / `COMFYUI_PYTHON` 环境变量，`start_backend.bat` 已固化）。也可手动运行 `start_comfyui.bat`。

### 前端

```bash
cd frontend-director
npm install
npm run dev
```

开发服务器运行在 `http://localhost:5174`（`/api`、`/ws` 已代理到后端 8000）。

### 测试

```bash
cd backend
python -m pytest        # 34 个单元测试（gen_task_manager / stage_service / workflow_builder）
```

### Docker / CI

```bash
docker compose up -d    # 后端 :8000 + 前端 :5174
```

CI 配置见 `.github/workflows/ci.yml`（后端 pytest → 前端构建 → 镜像构建）。

## 核心能力

- **概念图/角色/场景生成** — 7 个供应商，统一抽象
- **三视图/360全景** — 角色资产多角度视图
- **分镜画布** — 网格视图 / React Flow 画布 / Infinite Canvas iframe（WebSocket 实时同步）
- **视频生成** — 即梦 / RunningHub / 火山引擎 / ComfyUI
- **视频剪辑** — 可视化时间线 + 裁剪/拼接
- **成片导出** — 多格式 / 编码 / 分辨率
- **资产库** — 统一管理，血缘追踪

## 技术栈

- **后端**: Python 3.13 + FastAPI + httpx + aiohttp
- **前端**: React 18 + TypeScript + Zustand + @xyflow/react
- **UI**: Ant Design 5
- **AI**: ComfyUI(本地) / 即梦 / RunningHub / 火山引擎 / OpenAI / Gemini / ModelScope

## 项目结构

```
backend/
├── main.py                    # 后端入口（路由注册、CORS、静态文件、图片代理）
├── requirements.txt           # 运行时依赖（语义下限，生产镜像用）
├── requirements-dev.txt       # 开发/测试依赖（含 requirements.txt + pytest）
├── requirements.lock          # 全量锁定版本（CI/本地可复现安装）
├── .env                       # 环境变量 / Provider 密钥（由配置向导写入）
├── start_backend.bat          # 后端启动脚本（固化 ComfyUI 环境变量）
├── start_comfyui.bat          # ComfyUI 启动脚本
├── api/                       # 10 个 REST API 路由
│   ├── director_stage_api.py  # 阶段执行 + 异步任务队列
│   ├── director_asset_api.py  # 资产 CRUD + 上传
│   ├── director_provider_api.py # 供应商发现 + 密钥管理（服务端 .env）
│   ├── director_canvas_api.py # 画布 CRUD + WebSocket 广播
│   ├── director_batch_api.py  # 批量任务
│   ├── infinite_canvas_api.py # 无限画布桥接
│   └── ...                    # project / preset / prompt / workflow_template
├── services/
│   ├── stage_service.py       # 阶段路由（23 个阶段注册、执行调度）
│   ├── provider_service.py    # 供应商抽象层（7 个供应商统一接口）
│   ├── comfyui_service.py     # ComfyUI 本地推理（自动拉起、离线回读）
│   ├── gen_task_manager.py    # 异步任务队列（持久化 / TTL / 取消 / 超时兜底）
│   ├── workflow_builder.py    # ComfyUI 工作流构建器（标准版/影视级/精修）
│   ├── canvas_service.py      # 画布布局持久化
│   ├── providers/             # 7 个供应商插件
│   └── stages/                # 23 个生产阶段插件
├── core/ws_manager.py         # WebSocket 连接管理
├── tests/                     # pytest 单元测试
└── static/director/           # 静态资源
```

## 密钥管理

Provider API Key 统一由**后端 .env 管理**（前端「供应商设置」页或「配置向导」写入，服务端保存并立即生效），**不存储在浏览器 localStorage**。

## 文档分级

| 类型 | 文档 | 说明 |
|------|------|------|
| 现状 | `README.md`、`docs/03_操作/系统功能.md` | 反映当前实现 |
| 规划 | `docs/01_规划/plan.md`、`docs/01_规划/导演工作台架构方案.md`、`docs/01_规划/优化集成.md`、`工业化/` | 目标规划，部分引用已过时的 `pipeline_executor / video_service`，仅供参考 |
| 分析 | `docs/02_分析/项目分析报告.html`、`docs/02_分析/comfyui对接诊断报告.md` | 历史诊断与治理建议 |
