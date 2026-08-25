> ⚠️ **规划文档**：本文为历史规划，其中大量引用的 `pipeline_executor.py` 已被 `workflow_builder / dag_executor / gen_task_manager` 重构替代，磁盘上已不存在。仅供参考，勿作为现状依据。

## 问题

当前 `asset_type` 是一个扁平字段，同时承担了两个不同维度的分类职责：

1. **生产阶段类型（生产过程）**：概念图(concept)、精修(edit)、分镜帧(storyboard)、视频(video)、姿态(pose)、线稿(lineart)、深度图(depth)
2. **内容类型（描绘对象）**：角色(character)、场景(scene)、道具(prop)

当用户生成一张"角色概念图"时，只能选 `asset_type=concept` 或 `asset_type=character`，无法同时表达。精修后输出永远是 `asset_type=edit`，丢失了"这是角色的精修还是场景的精修"信息。

用户原始描述："我们有概念，精修，等类型的图，但是角色，场景，道具包含在这些类型中"

## 目标

引入两级分类体系，将"生产阶段类型"和"内容类型"分开：

- **类型 (asset_type)**：概念图、精修、分镜帧、视频、姿态、线稿、深度图（生产阶段维度）
- **内容 (content_type)**：角色、场景、道具（空=无分类/抽象内容）（描绘对象维度）

每个资产同时拥有类型和内容两个属性，例如：

- 类型=概念图, 内容=角色  -> 角色概念图
- 类型=精修, 内容=角色    -> 角色精修图
- 类型=概念图, 内容=场景  -> 场景概念图

## 核心功能

1. 后端 AssetRef 数据模型新增 content_type 字段
2. 阶段插件（concept_stage、refine_stage 等）自动继承/传递 content_type
3. 后端 API 支持按 content_type 过滤
4. 前端 Tab 使用两级导航：一级=类型（概念图/精修/分镜/视频），二级=内容筛选（全部/角色/场景/道具）
5. 新建资产/生成资产弹窗支持分别选择类型和内容

## 技术栈

- 后端：Python 3.10+ / FastAPI / Pydantic / dataclass
- 前端：React 18 + TypeScript + Ant Design 5 + Zustand
- 持久化：JSON 文件（asset_registry.json）

## 实现方案

### 1. 数据模型变更（asset_service.py）

在 `AssetRef` 中新增 `content_type` 字段：

```python
@dataclass
class AssetRef:
    asset_id: str
    asset_type: str              # concept/edit/storyboard/video/pose/lineart/depth
    content_type: str = ""       # character/scene/prop/"" (空=无内容分类)
    name: str
    ...
```

将 `ASSET_TYPES` 拆分为两个独立字典：

- `STAGE_TYPES`：生产阶段类型（concept/edit/storyboard/video/pose/lineart/depth）
- `CONTENT_TYPES`：内容类型（character/scene/prop），增加一个空选项代表"无内容"

对已有 JSON 存储中缺少 `content_type` 的资产，`__post_init__` 中默认填充为空字符串，保证向后兼容。

### 2. 阶段插件变更

**concept_stage.py**（第 41 行）：

- 当前：`asset_type = params.get("asset_type", "concept")`  ->  混合了阶段类型和内容类型
- 改为：`content_type = params.get("content_type", "")`，`asset_type` 固定为 `"concept"`
- 新建资产时同步传入 `content_type`

**refine_stage.py**（第 85-100 行）：

- 当前：`asset_type="edit"` 固定写死
- 改为：从源资产 `source.content_type` 继承，`asset_type="edit"` 保持不变
- 避免精修后丢失内容分类信息

其他阶段（pose_extraction、lineart_extraction、depth_map 等）按类似方式处理。

### 3. 后端 API 变更（director_asset_api.py）

- `CreateAssetRequest` 新增 `content_type: str = ""` 字段
- `GET /assets` 查询增加 `content_type` 可选参数
- `_asset_dict()` 返回值增加 `content_type`
- 新增 `GET /api/director/assets/content-types` 端点，返回 CONTENT_TYPES 字典

### 4. 前端 Store + API 变更

- `directorStore.ts`：`Asset` 接口新增 `content_type` 字段；`createAsset` 参数新增 `content_type`
- `directorApi.ts`：`stageApi.execute` 请求体兼容 `params.content_type` 字段

### 5. 前端 AssetsPage 重构

**Tab 系统重设计**：

- 父级 Tabs：全部、概念图、精修、分镜、视频、姿态、线稿、深度图（按 asset_type 过滤）
- 子级 Segmented/Radio：全部内容、角色、场景、道具（按 content_type 二次过滤）
- 当父 Tab 为"全部"时隐藏子级筛选
- 过滤逻辑：`a.asset_type === activeTab && (!contentTab || a.content_type === contentTab)`

**卡片展示**：

- 原 Tag 显示 asset_type 标签（蓝色"概念图"）
- 新增小号 Tag 显示 content_type（若有），如：`[概念图] [角色] 名称`
- content_type 为空时不显示

**新建/生成弹窗**：

- 资产类型选择区展示 STAGE_TYPES（概念图/精修/分镜/视频/姿态/线稿/深度图）
- 内容类型选择区展示 CONTENT_TYPES（无/角色/场景/道具），默认"无"
- 两行独立选择，用户可自由组合

### 6. 持久化兼容

`_save()` 和 `_load()` 自动处理 `content_type`，旧 JSON 数据加载时缺失字段默认 `content_type=""`。

## 目录结构变更

修改 7 个文件，无需新增文件：

- `backend/services/asset_service.py` — [MODIFY] AssetRef +content_type; 拆分 ASSET_TYPES
- `backend/services/stages/concept_stage.py` — [MODIFY] content_type 来自 params，asset_type 固定
- `backend/services/stages/refine_stage.py` — [MODIFY] 从源资产继承 content_type
- `backend/api/director_asset_api.py` — [MODIFY] CreateAssetRequest +content_type; 新增 content-types 端点; 过滤扩展
- `frontend-director/src/stores/directorStore.ts` — [MODIFY] Asset 接口+createAsset 参数增加 content_type
- `frontend-director/src/services/directorApi.ts` — [MODIFY] 兼容 content_type 参数
- `frontend-director/src/pages/AssetsPage.tsx` — [MODIFY] 二级 Tab、卡片展示、弹窗两级选择

## 风险评估

### 风险分布总览

| 风险层级 | 影响范围 | 等级 |
|----------|----------|------|
| 数据层 | AssetRef 模型 + asset_registry.json | 🟢 低 |
| 定义层 | ASSET_TYPES 字典 + /types API 端点 | 🟡 中 |
| 阶段路由层 | StageDef.input_types / output_type | 🔴 高 |
| 阶段插件层 | concept/refine/storyboard/video 等 | 🟡 中 |
| 校验逻辑层 | StagePlugin.validate_inputs() | 🔴 高 |
| 管线执行层 | pipeline_executor.py（36+ 处引用） | 🔴 高 |
| 兼容层 | results_access.py 旧格式回退键 | 🟡 中 |
| 前端 Tab 层 | AssetsPage.tsx | 🟡 中 |
| 其他前端页 | StoryboardPage/EditPage/CanvasPanel | 🟢 低 |

### 🔴 高风险项

#### 1. `StagePlugin.validate_inputs()` — 校验逻辑断裂

```python
# stage_service.py:76-79
required_types = set(self.stage_def.input_types)
provided_types = set(a.asset_type for a in input_assets)  # ← 仅匹配 asset_type
missing = required_types - provided_types
```

**问题：** `input_types` 中既有阶段类型（`concept`、`storyboard`、`edit`）又有内容类型（`character`、`scene`、`prop`）。拆分后 `a.asset_type` 不再包含 `"character"`，校验永远认为"缺少 character 输入"。

**影响：** RefineStage 的 `input_types=["concept", "character", "scene", "storyboard"]` 全部校验失败，精修流程不可用。

**缓解方案：** 需要将 `validate_inputs` 改为同时检查 `asset_type` 和 `content_type`，或为 `StageDef` 增加 `input_content_types` 字段。

#### 2. `pipeline_executor.py` — 管线核心流程重度依赖扁平 `asset_type`

**关键路径（共 36+ 处引用）：**

```
- 概念阶段同步创建资产（L637-642）：asset_type = character/scene/prop → 需切到 content_type
- 构图和尺寸选择（L744-749）：asset_type == character → 竖图，scene → 横图 → 需切到 content_type
- 评估结果写入（L691/818）：ev["asset_type"] → 需切到 content_type
- 最佳变体选择（L932）：ev.get("asset_type") == "character" → 需切到 content_type
- WebSocket 进度推送（L836）：asset_type → 需拆为两个字段
- VisualCritic 设置角色参考（L932）：== "character" → 需切到 content_type
- _sync_concept_to_assets（L3007-3022）：创建旧版资产时 asset_type → 需切到 content_type
```

**影响：** 遗漏任一处改动就可能导致管线中断、尺寸错误或数据丢失。

**缓解方案：** 建议统一提取 `_get_content_type(asset)` 辅助函数收口所有读取点。

#### 3. `StageDef.input_types` — 设计层面需重新定义

当前五种阶段使用了混合语义的 `input_types`：

| Stage | input_types | 问题 |
|-------|-------------|------|
| refine | `[concept, character, scene, storyboard]` | concept/storyboard=阶段类型, character/scene=内容类型 |
| angle | `[concept, character]` | concept=阶段类型, character=内容类型 |
| pano | `[scene]` | scene=内容类型 |
| storyboard | `[character, scene]` | 均为内容类型 |

拆分后需明确每个 `input_types` 条目是属于 `asset_type` 约束还是 `content_type` 约束。建议 `StageDef` 增加 `input_content_types: List[str] = []` 字段。

### 🟡 中风险项

#### 4. `refine_stage.py` — 硬编码 `asset_type="edit"`

```python
# refine_stage.py:86
new_asset = await asset_svc.create(
    asset_type="edit",  # ← 硬编码，丢失内容类型
)
```

需要改为从源资产继承 `content_type`，例如 `content_type=source.content_type`。

同样问题存在于：`video_stage.py:67`（`asset_type="video"`）、`storyboard_stage.py:87`（`asset_type="storyboard"`）等所有阶段插件。

#### 5. `list_assets()` 过滤需增加 `content_type` 参数

当前 `list_assets` 仅支持 `asset_type` 和 `category` 过滤。拆分后前端需要按 `content_type` 筛选，API 层和 Service 层需新增参数。

#### 6. 前端 AssetsPage 三层结构需同步改造

- **Tab 系统：** 从单层变为两层（阶段 Tab + 内容类型子筛选）
- **卡片标签：** 从单 Tag 变为双 Tag，如 `[概念图] [角色] ...名称`
- **新建弹窗：** 资产类型选择器需拆为"阶段类型"和"内容类型"两行
- **过滤逻辑：** L103 `a.asset_type !== activeTab` 需改为同时匹配 `asset_type` 和 `content_type`
- **详情面板：** L661/694 展示标签需改为双标签

#### 7. `results_access.py` — 旧格式回退机制

```python
# results_access.py:29
or standardize_dict.get(asset_type)  # ← 旧数据用 asset_type 值作字典键
```

旧 pipeline 项目数据可能以 `asset_type` 字符串（如 `"character"`）为键存储标准化/精修结果。拆分后 `asset_type` 的值变化，旧数据的回退读取会失效。

### 🟢 低风险项

#### 8. `asset_registry.json` 为空

当前文件内容为 `{"assets": []}`，无存量数据，模型迁移成本为零。

#### 9. 多数阶段插件只需增加 content_type 传递

`pose_extraction_stage.py`、`lineart_extraction_stage.py`、`depth_map_stage.py`、`pano_stage.py`、`edit_stage.py`、`export_stage.py`：
- 当前都硬编码自己的阶段类型为 `asset_type`
- 只需在创建资产时额外传入 `content_type`（可从源资产继承或留空）

#### 10. 其他前端页面不受影响

- `StoryboardPage.tsx`：按 `a.asset_type === 'storyboard'` 筛选 → `storyboard` 仍是阶段类型值，不变
- `EditPage.tsx`：按 `a.asset_type === 'video'` 筛选 → 同上
- `CanvasPanel.tsx`：按 `asset.asset_type` 判读节点类型 → 同上

### 建议的实施策略

**第一阶段（向后兼容）：**
1. AssetRef 新增 `content_type` 字段（默认 `""`）
2. ASSET_TYPES 保持原结构 + 新增 CONTENT_TYPES
3. `validate_inputs` 同时检查 asset_type 和 content_type（或为 StageDef 增加 input_content_types）
4. `list_assets` 新增 content_type 过滤参数
5. 所有阶段插件继承源资产的 content_type（写入 metadata 兜底）
6. 在 `pipeline_executor.py` 提取 `_get_content_type()` 辅助函数收口所有读取

**第二阶段（前端升级）：**
1. AssetsPage Tab 重构为二级筛选
2. 卡片展示双标签
3. 新建弹窗增加内容类型选择行
4. `/types` API 返回兼容两种格式

**第三阶段（旧数据清理）：**
1. 确认 `results_access.py` 旧格式数据是否存在
2. 如存在，编写迁移脚本统一写入新格式