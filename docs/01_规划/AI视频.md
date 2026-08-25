# 导演工作台 — AI 视频生产系统全景

> ⚠️ **规划文档**：本文为 2026-06 的全景规划，其中引用的 `video_service.py` 在磁盘上已不存在（视频生成已由 `video_stage` + `gen_task_manager` 承担）。仅供参考，现状以 `README.md` / `docs/系统功能.md` 为准。

> 版本：v2.0 | 2026-06-27  
> 定位：面向短剧/视频创作者的 AI 辅助全栈生产平台  
> 覆盖：概念设计 → 资产生成 → 模板提取 → 分镜制作 → 视频生成 → 剪辑导出

---

## 目录

1. [系统架构](#一系统架构)
2. [核心功能矩阵](#二核心功能矩阵)
3. [关键亮点](#三关键亮点)
4. [商业差距与进阶路径](#四商业差距与进阶路径)

---

## 一、系统架构

### 1.1 核心原则

**资产网络，而非线性管线**。传统管线是 A→B→C→D 固定顺序，真实的短剧创作是**网状结构**：

```
角色概念图 ───→ 三视图 ───→ 分镜换装 ───→ 视频生成
场景图 ───────→ 360全景 ──→ 分镜背景 ──→ 
物体图 ───────→ 线稿提取 → 深度图 ───→ 模板约束生成
任意资产 ─────→ 精修/超分 → 回资产库（非破坏性迭代）
```

**开源闭源结合**：ComfyUI 本地推理 + 云端 API（7 个供应商）混合驱动，通过统一抽象层解耦。

**不重复造轮子**：复用 Infinite-Canvas 成熟模块（供应商/资产库/画布）、V6.0 分镜模板系统、即梦 CLI 等。

### 1.2 三层解耦架构

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: StageRegistry（阶段路由层）                          │
│                                                             │
│   17 个 Stage 插件，每个声明 input_type[] / output_type       │
│   所有 Stage 通过输入资产类型自动匹配路由                       │
│                                                             │
│   核心：concept / refine / angle / pano / storyboard /       │
│         video / edit / export                                │
│   提取：pose_extraction / lineart / depth_map / extract_all  │
│   模板：template_batch_extract / template_clean / template_pose│
│   进阶：multi_person / layered_render / batch_storyboard      │
│   新增：script / tts / screen_record / compose / graphic      │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ Layer 2: ProviderService（供应商抽象层）                       │
│                                                             │
│  ComfyUI(本地) / 即梦CLI(视频) / RunningHub(图+视频)           │
│  火山API(图+视频) / OpenAI(图) / Gemini(图) / ModelScope(三视图)│
│                                                             │
│  所有供应商统一 `generate_image` / `generate_video` 接口      │
│  上层业务无感知切换                                           │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│ Layer 1: AssetService（资产注册表）                            │
│                                                             │
│  类型体系（两级分类）：                                        │
│    阶段类型: concept / edit / storyboard / video / pose /    │
│              lineart / depth / depth_clean / mask / script   │
│    内容类型: character / scene / prop                        │
│                                                             │
│  功能：CRUD + WebSocket 广播 + 版本化 + 血缘追踪（parent_id）  │
└─────────────────────────────────────────────────────────────┘
```

### 1.3 异步任务队列

```
用户请求 → GenTaskManager.submit()
  ├─ 立即返回 task_id + 初始状态
  ├─ 后台执行（并发控制，默认 2 个）
  ├─ 前端轮询 GET /task/{id}
  └─ 完成 → 注册资产到 AssetService → WebSocket 通知前端
```

- 任务状态持久化到磁盘（服务重启不丢失）
- 超时自动失败
- 适合：精修（Qwen Image Edit 55s）、视频生成（即梦 3-10 分钟）、MSR 多角色视频（5-15 分钟）

### 1.4 模板工业化生产管线

```
参考构图图 ──→ Phase 1: 批量提取 ──→ Phase 2: 清场+蒙版 ──→ Phase 3: Pose优化
                  │                      │                      │
                  ├─ pose.png            ├─ depth_clean.png     ├─ pose_simplified.png
                  ├─ depth_raw.png       └─ mask.png            └─ pose_corrected.png(手动)
                  └─ lineart.png
```

**Manifest 状态流转**：`pending → partial → extracted → cleaned → ready`

### 1.5 技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 后端 | Python 3.12 + FastAPI + httpx | REST API + WebSocket |
| 前端 | React 18 + TypeScript + Vite | Ant Design 5 + Zustand |
| 画布 | @xyflow/react + Infinite Canvas iframe | 节点拖拽 + 无限画布 |
| 本地 AI | ComfyUI（SAM2 / Qwen / DWPose / MBDepth / Z-Image / Flux） | 本地 GPU 推理 |
| 云端 AI | 即梦 / RunningHub / 火山引擎 / ModelScope / OpenAI / Gemini | API 调用 |
| 数据 | JSON 文件（asset_registry.json）+ 文件系统持久化 | 轻量无数据库 |

---

## 二、核心功能矩阵

### 2.1 生产阶段（10 个核心）

| 阶段 | 功能 | 输入 | 输出 | 默认供应商 |
|------|------|------|------|-----------|
| **concept** | 概念图（文生图） | 文本 | concept | ComfyUI |
| **refine** | 精修/超分 | concept/storyboard | edit | ComfyUI |
| **angle** | 三视图 | concept | multi_view | ModelScope |
| **pano** | 360 全景图 | 文本 | pano | ComfyUI |
| **storyboard** | 分镜换装 | character+scene | storyboard | ComfyUI |
| **video** | 视频生成 | storyboard | video | 即梦 |
| **edit** | 视频剪辑 | video | video | FFmpeg |
| **export** | 成片导出 | video | video | FFmpeg |
| **script** | 剧本生成 | 文本 | script | LLM |
| **tts** | 语音合成 | script | audio | TTS |

### 2.2 辅助提取阶段（7 个）

| 阶段 | 功能 | 技术方案 |
|------|------|---------|
| **pose_extraction** | DWPose 骨架提取 | ComfyUI DWPreprocessor |
| **lineart_extraction** | 线稿提取 | ComfyUI LineArtPreprocessor |
| **depth_map** | 深度图提取 | ComfyUI DepthAnythingV2 |
| **extract_all** | 三合一提取 | 单次推理同时产出 Pose+深度+线稿 |
| **layered_render** | 分层渲染 | A/B 组分别生成后合成 |
| **batch_storyboard** | CSV 批量分镜 | 按镜头号+模板编号批量生成 |
| **multi_person** | 多人分镜 | 三元约束（4-5 人场景） |

### 2.3 模板制作阶段（3 个）

| 阶段 | 功能 | 技术细节 |
|------|------|---------|
| **template_batch_extract** | Phase1: 三件套提取 | DWPose + DepthAnythingV2 + LineArt，最长边 1920px |
| **template_clean** | Phase2: 清场+蒙版 | SAM2 检测 → PIL MinFilter+GaussianBlur 生成蒙版 → cv2.inpaint 深度填充 + 梯度模拟，~7s |
| **template_pose** | Phase3: Pose 简化 | 完整 100+ 节点 → 7 个核心节点（头/肩/肘/胯/膝/脚），支持手动 PoseEditor 修正 |

### 2.4 前端页面（16 个路由页面）

| 页面 | 核心功能 | 文件大小 |
|------|---------|---------|
| **AssetsPage** | 资产库（二级Tab筛选、文生图、右键精修/提取/超分） | ~110KB |
| **StoryboardPage** | 分镜画布（react-flow + 无限画布 iframe） | |
| **VideoPage** | 视频生成（标准 + MSR 多角色） | ~27KB |
| **OneClickVideoPage** | 一键成片（全自动管线编排） | ~72KB |
| **EditPage** | 视频剪辑（拼接/裁剪/转场） | |
| **ExportPage** | 成片导出（编码/格式/分辨率） | |
| **ScriptPage** | 剧本创作编辑 | ~16KB |
| **GraphicPage** | 图形化编排画布 | ~33KB |
| **PromptsPage** | 提示词模板管理 | ~27KB |
| **SettingsPage** | 供应商 API Key 配置 | |
| **ComposePage** | 合成编排 | |
| **ScreenRecordPage** | 屏幕录制 | |
| **BatchesPage** | 批量任务管理 | ~28KB |
| **PresetsPage** | 预设管理 | |
| **ProjectsPage** | 项目管理 | |
| **WorkflowTemplatesPage** | 工作流模板库 | |

### 2.5 ComfyUI 工作流模板（41 个）

| 类别 | 数量 | 代表性工作流 |
|------|------|-------------|
| 文生图 | 5 | `最终文生图.json`（25步/AuraFlow/影视级）、`最终道具工作流.json`（+SeedVR2超分） |
| 分镜 | 6 | `1人分镜.json`、`2人分镜.json`、`本地多人分镜.json`、`GPT分镜.json`、`多人分镜三元约束.json` |
| 视频 | 5 | `LTX-2.3_MSR_sample_workflow_V2.json`（35节点）、`LTXDirectorv2-API.json` |
| 提取 | 4 | `pose_extraction.json`、`lineart_extraction.json`、`depth_map.json`、`三个骨架图.json` |
| 模板 | 3 | `模板清场+蒙版.json`、`模板Pose优化.json` |
| 精修/超分 | 5 | `Z-Image.json`、`Z-Image-Enhance.json`、`upscale.json`、`精修优化.json`、`图像放大.json` |
| 全景/分层 | 2 | `真正全景图.json`、`分层渲染.json` |
| 其他 | 11 | `Qwen3+TTS+音色设计.json`、`Flux2-Klein.json`、`多场景.json` 等 |

### 2.6 供应商集成（7 个）

| 供应商 | 能力 | 特点 |
|--------|------|------|
| **ComfyUI**（本地） | 文生图、精修、超分、分镜、MSR 视频、骨架/线稿/深度提取 | 免费、本地运行、无 API 调用成本 |
| **即梦 Jimeng**（云端） | 视频生成（图生视频） | 国内可用、短视频风格 |
| **RunningHub**（云端） | 图+视频工作流执行 | 灵活的工作流编排 |
| **火山引擎**（云端） | 图+视频生成 | 字节跳动系、高并发 |
| **ModelScope**（云端） | 三视图生成 | 角色三视图专用 |
| **OpenAI 兼容**（云端） | 文生图 | 通用性强、质量高 |
| **Gemini**（云端） | 文生图 | Google 系、免费额度 |

### 2.7 内容类型驱动差异化参数

| 维度 | character（角色） | scene（场景） | prop（道具） |
|------|-----------------|-------------|-------------|
| 工作流模板 | 最终文生图.json（影视级） | 最终道具工作流.json | 最终道具工作流.json（专用） |
| 默认尺寸 | 1080×1920（竖版） | 1920×1080（横屏） | 1920×1080（方形道具特写） |
| LoRA 强度 | 1.0（锁死五官一致性） | 0.6（允许光影突变） | 0.4（允许材质完全推翻） |
| 正向触发词 | 全身站立从头到脚 | 广角视角宏大场景 | 纯黑背景环形灯微距 |
| Seed 策略 | 固定 | 随机 | 固定 |
| 精修约束 | 保持脸部特征不变 | 保持核心结构 | 保留轮廓，仅改材质颜色 |

---

## 三、关键亮点

### 3.1 架构亮点

**1. 三层解耦 + 插件体系**

新增能力 = 注册一个新 `StagePlugin` + 配一个新 `ProviderPlugin`，不改核心架构。17 个阶段插件 + 7 个供应商插件各自独立，通过 `StageService` / `ProviderService` 统一调度。

**2. 异步任务队列 + 持久化**

`GenTaskManager` 管理所有长时间任务，支持：
- 立即返回 `task_id`，前端轮询进度
- 任务状态持久化到磁盘，服务重启不丢失
- 并发数控制（默认 2），超时自动失败
- 适合精修（~55s）、视频生成（数分钟）、MSR 视频（~15 分钟）

**3. 两级资产分类体系**

| asset_type（阶段类型） | content_type（内容类型） |
|----------------------|----------------------|
| concept / edit / storyboard / video / pose / lineart / depth / script | character / scene / prop |

- 精修后自动继承源资产的内容类型
- 前端二级 Tab 筛选（阶段 Tab + 内容子筛选）
- 血缘追踪（parent_id 链接上下游）

**4. 工作流构建器（workflow_builder, ~117KB）**

- 根据内容类型自动选择工作流模板
- 动态注入提示词、LoRA、尺寸、种子
- Qwen/Z-Image 双路径支持
- 自定义工作流解析（`.config.json` 自动生成 UI 表单）

**5. 模板工业化三阶段管线**

```
Phase1 (5.8s): ComfyUI SAM2+DINO → 人物检测 → mask_raw
Phase2 (0.02s): PIL MinFilter(3)收缩1px → GaussianBlur(3)羽化3px → mask
               + cv2.inpaint(INPAINT_NS)深度填充 + gradient模拟 → depth_clean
Phase3 (PoseEditor): 100+节点 → 7节点简化 → 手动修正
```

避开 Qwen Image Edit（55s/9GB VRAM），使用后端 PIL+OpenCV 在 0.02s 内完成。

**6. 无限画布 API 桥接**

将 canvas.js 期望的 API 端点映射到现有后端服务，无需重新实现画布前端，通过 iframe 嵌入 + WebSocket 桥接。

**7. 前/后端分离独立子项目**

`frontend-director` 与 `frontend-react`（AI-IDE-v2）独立开发，共享同一个 backend，互不干扰。

### 3.2 技术亮点

**1. ComfyUI 深度集成**

- 41 个工作流模板，覆盖文生图、视频、提取、精修、全景、TTS 等
- 自动解析 `.config.json` → 动态生成输入 UI（图片/文本框/滑块）
- 工作流参数动态注入（prompt / seed / LoRA strength / reference images）
- 输出图片自动注册到资产库 + 血缘追踪

**2. MSR 多角色视频生成**

基于 LTX-2.3 模型，支持 4 个角色 + 1 个背景的多人交互视频生成。
- 35 节点复杂工作流
- 异步提交（`_queue_prompt_with_retry`）→ 轮询（`_wait_for_completion`）→ 下载注册
- 前端进度显示 + 播放器

**3. 模板深度图清场优化**

从 Qwen Image Edit 55s/9GB VRAM → 后端 OpenCV 7s 解决方案：
- SAM2 人物检测（ComfyUI）
- PIL 蒙版收缩+羽化
- `cv2.inpaint(INPAINT_NS)` Navier-Stokes 深度修复
- Numpy 中心→边缘深度渐变模拟（max 10 灰度级）

**4. 精修 content_type 差异化**

精修阶段自动根据源资产类型（角色/场景/道具）切换：
- LoRA 一致性强度（1.0 / 0.6 / 0.4）
- 缩放尺寸（1344 / 1024）
- 前端 prompt 提示动态切换

**5. 前端大页面性能**

`AssetsPage.tsx` 达 110KB，采用二级 Tab 筛选 + 虚拟滚动 + 右键菜单 + Drawer 详情面板。

### 3.3 用户体验亮点

- **右键菜单**：资产库中右键任意资产直接选择"精修/超分/三视图/线稿/骨架/深度图"
- **一键成片**：OneClickVideoPage 全自动管线编排，从文本到视频一步完成
- **Pose 手动编辑**：基于 Canvas 的骨架编辑器，支持画笔修正、撤销/重做
- **自定义工作流**：上传 JSON 到 Infinite Canvas，自动解析输入字段

---

## 四、商业差距与进阶路径

### 4.1 与专业商业分镜的差距全景

| 对比维度 | 本方案（当前水平） | 专业商业分镜（头部标准） | 差距等级 |
|---------|-----------------|----------------------|---------|
| **叙事目的性** | 单张画面美观，无明确叙事权重 | 每个像素服务于"讲好故事" | ★★★★★ |
| **镜头语言专业性** | 固定机位模板，基础景别 | 动态镜头设计 + 电影语法全覆盖 | ★★★★☆ |
| **表演颗粒度** | 宏观姿势 + 基本表情 | 微表情 + 肢体细节 + 情绪节奏 | ★★★★☆ |
| **拍摄可行性** | 忽略物理限制与制作成本 | 完全贴合实际拍摄流程 | ★★★☆☆ |
| **后期衔接性** | 仅提供静态画面 | 包含完整后期制作指令（转场/特效/音效/调色） | ★★★☆☆ |
| **导演意图传达** | 依赖提示词描述 | 精准可视化导演创作意图 + 多方案备选 | ★★★★☆ |

### 4.2 六类差距深度解读

**1. 叙事目的性（最大差距）**

本方案生成的是"好看的画面"，专业分镜画的是"故事的瞬间"：
- 专业分镜：每个元素有明确权重（主角70% / 次要20% / 背景10%），精确控制视线
- AI 现状：平均分配注意力，无法理解"这个镜头为什么存在"，无时间轴概念

**2. 镜头语言专业性**

本方案仅覆盖约 30% 的基础镜头类型，缺失：
- 运动镜头（推/拉/摇/移/跟/升/降）— 完全不支持静态机位
- 主观镜头 — 透视经常错误
- 景深镜头 — 只能提示词模糊描述
- 情绪-镜头映射 — 紧张→低角度+特写+硬光，悬疑→倾斜构图+阴影

**3. 拍摄可行性**

AI 经常生成"反物理"镜头（摄像机穿墙、演员悬浮、超出人体极限），完全忽略场地和成本约束。

**4. 后期衔接性**

专业分镜是"施工图纸"，包含：转场方式、特效提示、音效提示、剪辑点、调色提示。
本方案仅有静态画面，无任何后期标注。

### 4.3 当前优势定位

```
工具级 ───→ 目前位置 ───→ 专业级 ───→ 院线级
   ↑                        ↑
  中小成本短剧             头部网剧/电影
（单集成本 5 万以下）     （单张分镜 500-2000 元）
```

**判断：** 本方案处于 **"工具级"向"专业级"过渡阶段**，已解决 AI 短剧生产中最痛的"一致性"和"效率"问题，完全可支撑中小成本短剧的工业化量产。

**核心优势：** 本地运行 + 高效率 + 低成本，这是任何云端商业分镜工具无法比拟的。

### 4.4 进阶路线图

#### 第一阶段（30天）：合格商业分镜
| 改进项 | 方案 |
|--------|------|
| **叙事权重注入** | 模板增加"视觉重心"参数（蒙版标记核心区域）+ 强制视觉引导提示词 + 镜头时长字段 |
| **基础镜头语言库** | 20 个运动镜头静态等效模板 + 情绪-镜头语言映射表 |
| **拍摄可行性校验** | 物理约束模块（机位/人体极限/场地限制）+ 拍摄难度+预估成本标注 |
| **后期标注自动生成** | 后期标注模块（转场/特效/音效）+ 标准分镜表（PDF/Excel）+ Premiere XML 导出 |

#### 第二阶段（90天）：优秀商业分镜
| 改进项 | 方案 |
|--------|------|
| **专业分镜知识库** | 10000 张标注分镜数据训练专用提示词模型 |
| **表演细节增强** | 100 个微表情/肢体细节 LoRA + 动作分解（多关键帧） |
| **多方案生成** | 同时 3-5 个备选方案 + 叙事效果自动评估 + 方案融合 |
| **团队协作** | 在线批注 + 版本控制 + 角色权限管理 |

#### 第三阶段（长期）：院线级
| 改进项 | 方案 |
|--------|------|
| **动态分镜** | 视频生成模型直接生成 animatic（含镜头运动/角色动画/音效） |
| **剧本自动分镜** | LLM 自动拆解完整剧本为分镜脚本 |
| **全流程整合** | 与 Blender / Premiere / After Effects 无缝对接 |

### 4.5 优先级建议

| 优先级 | 改进项 | 投入产出 |
|--------|--------|---------|
| 🔴 **最高** | 后期标注自动生成 + 标准分镜表输出 | 最影响实用性的短板 |
| 🟡 **高** | 叙事权重注入 + 基础镜头语言库 | 提升分镜质量的关键 |
| 🟢 **中** | 拍摄可行性校验 + 多方案生成 | 专业团队最需要的功能 |
| ⚪ **低** | 表演细节增强 + 动态分镜 | 长期目标，逐步完善 |

---

## 附录 A：项目目录结构

```
d:\director/
├── backend/                           # Python FastAPI 后端
│   ├── main.py                        # 后端入口（路由注册、CORS、静态文件）
│   ├── requirements.txt
│   ├── api/                           # REST API 路由
│   │   ├── director_asset_api.py      # 资产 CRUD + 模板 manifest
│   │   ├── director_stage_api.py      # 阶段执行 + 异步任务
│   │   ├── director_provider_api.py   # 供应商发现
│   │   ├── video_api.py               # 视频任务提交/查询/取消
│   │   ├── canvas_api.py              # 画布 CRUD + MSR 视频
│   │   └── infinite_canvas_api.py     # 无限画布 API 桥接
│   ├── services/                      # 业务逻辑层（29个.py）
│   │   ├── asset_service.py           # 资产注册表
│   │   ├── stage_service.py           # 阶段路由（17个阶段）
│   │   ├── provider_service.py        # 供应商抽象
│   │   ├── workflow_builder.py        # ComfyUI 工作流构建器（~117KB）
│   │   ├── comfyui_service.py         # ComfyUI 本地推理服务（~244KB）
│   │   ├── gen_task_manager.py        # 异步任务队列
│   │   ├── template_utils.py          # 模板工具
│   │   ├── prompt_service.py          # 提示词服务
│   │   ├── providers/                 # 7 个供应商插件
│   │   └── stages/                    # 27 个阶段插件
│   └── data/                          # 持久化数据
├── frontend-director/                 # React 前端（16 个页面）
│   └── src/
│       ├── pages/                     # 16 页面路由
│       ├── components/                # InlineEmbed, CanvasPanel, PoseEditor 等
│       ├── services/directorApi.ts    # API 封装
│       ├── stores/                    # Zustand 状态管理
│       └── utils/
└── workflows/                         # 41 个 ComfyUI 工作流 JSON
    ├── 最终文生图.json                # 影视级文生图（AuraFlow 25步）
    ├── LTX-2.3_MSR_sample_workflow_V2.json  # MSR 多角色视频（35节点）
    ├── 模板清场+蒙版.json             # SAM2+Inpaint（6节点优化版）
    ├── 三个骨架图.json                # DWPose+Depth+LineArt 三合一
    └── templates/                     # 模板资产
```

## 附录 B：数据流完整示例

```
用户操作：
  ① 资产库选中角色图 → 右键"提取线稿"
数据流：
  AssetsPage → POST /api/director/stage/execute
    → stage_service.execute("lineart_extraction")
    → comfyui_provider.generate(workflow=lineart_extraction.json)
    → ComfyUI 本地推理（AIO_Preprocessor LineArt）
    → SaveImage → 文件持久化
    → 后端扫描 ComfyUI 输出 → 注册到 AssetService
    → WebSocket 广播资产库更新
    → 前端 AssetsPage 刷新显示线稿缩略图

用户操作：
  ② 资产库选中分镜帧 → 点击"生成视频"
数据流：
  VideoPage → POST /api/video/generate
    → video_service.generate(provider="jimeng")
    → 即梦 CLI 提交任务 → 返回 task_id
    → 前端轮询 GET /api/video/task/{id}
    → 即梦生成完成 → 下载视频到持久化目录
    → asset_svc.create(asset_type="video", content_type="character")
    → 前端播放器显示视频
```

## 附录 C：模板 Manifest 结构

```json
{
  "templates": {
    "T01": {
      "name": "古装站立对话",
      "status": "ready",
      "files": {
        "pose": "T01_pose.png",
        "depth_raw": "T01_depth_raw.png",
        "lineart": "T01_lineart.png",
        "depth_clean": "T01_depth_clean.png",
        "mask": "T01_mask.png",
        "pose_simplified": "T01_pose_simplified.png"
      },
      "recommended_params": {
        "depth_weight": 0.8,
        "pose_weight": 0.45,
        "mask_blur": 2
      }
    }
  }
}
```
