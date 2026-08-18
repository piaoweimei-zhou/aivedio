# 导演工作台 (Director's Workbench)

短剧/视频创作者的全栈工具。独立子项目，与 AI-IDE-v2 并行开发。

## 快速启动

### 后端

```bash
cd backend
pip install -r requirements.txt
python main.py
```

服务启动在 `http://localhost:8000`。

### 前端

```bash
cd frontend-director
npm install
npm run dev
```

开发服务器运行在 `http://localhost:5173`。

## 核心能力

- **概念图/角色/场景生成** — 7 个供应商，统一抽象
- **三视图/360全景** — 角色资产多角度视图
- **分镜画布** — 网格视图 / React Flow 画布 / Infinite Canvas iframe
- **视频生成** — 即梦 / RunningHub / 火山引擎
- **视频剪辑** — 可视化时间线 + 裁剪/拼接
- **成片导出** — 多格式 / 编码 / 分辨率
- **资产库** — 统一管理，血缘追踪

## 技术栈

- **后端**: Python + FastAPI + httpx
- **前端**: React 18 + TypeScript + Zustand + @xyflow/react
- **UI**: Ant Design 5
- **AI**: ComfyUI(本地) / 即梦 / RunningHub / 火山引擎 / OpenAI / Gemini / ModelScope

## 项目结构

```
backend/
├── main.py                    # 后端入口
├── requirements.txt           # 依赖
├── .env                       # 环境变量
├── services/
│   ├── asset_service.py       # 资产注册表
│   ├── canvas_service.py      # 画布布局持久化
│   ├── stage_service.py       # 阶段路由
│   ├── provider_service.py    # 供应商抽象层
│   ├── video_service.py       # 视频生成服务
│   ├── comfyui_service.py     # ComfyUI 本地推理
│   ├── providers/             # 7 个供应商包装
│   └── stages/                # 11 个生产阶段
├── api/                       # 5 个 REST API
├── core/ws_manager.py         # WebSocket 管理
└── static/director/           # 静态资源
```
