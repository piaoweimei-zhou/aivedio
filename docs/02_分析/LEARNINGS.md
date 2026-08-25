# 导演工作台开发记录 (LEARNINGS)

> 本项目记录了导演工作台（Director's Workbench）从架构设计到实施过程中的经验教训。

## 架构决策记录

### ADR-1：从线性管线到资产网络
- **问题**：传统 A→B→C 管线无法承载短剧创作的灵活需求
- **决策**：采用 AssetService + StageRegistry + ProviderService 三层解耦
- **效果**：新增能力只需注册一个新 Stage + 配一个新 Provider，不改核心架构

### ADR-2：前端分治 vs 单一前端
- **问题**：AI-IDE-v2 已有完整前端，直接叠加会使其更臃肿
- **决策**：frontend-director 独立子项目，共享 backend，各自独立 store
- **效果**：开发互不干扰，AI-IDE 保持稳定

### ADR-3：Infinite-Canvas 集成策略
- **问题**：Infinite-Canvas main.py 13,188 行，耦合度高，不能直接引用
- **决策**：提取关键函数（provider/gen/image/output）到独立文件，做包装隔离
- **经验**：提取前必须做依赖分析（code-explorer），优先提取低依赖函数

## 实施经验

### Phase 0：基础设施
- **成功经验**：Python ABC 基类 + dataclass 的设计模式适合插件系统
- **教训**：ABC 基类的方法签名要足够通用，避免后期新 Stage 不兼容

### Phase 1：Provider 提取
- **问题**：Infinite-Canvas 的 7 个供应商函数共享全局 API 配置字典
- **解决**：将 `load_api_providers()` 先行提取，让所有 Provider 共享同一个配置源
- **教训**：提取前先用 code-explorer 做依赖分析

### Phase 2：前端页面
- **成功经验**：通用的 `stageApi.execute()` 设计优雅，前端不关心后端具体路由
- **问题**：EditPage 原始实现无可视化时间轴
- **修复**：用 Ant Design 组件构建可视化轨道 UI，避免直接集成 ltx-director-timeline.js
- **教训**：静态 JS 库通过 Web Component 包装后集成在 React 中更稳定

### Phase 3：ComfyUI 工作流
- **问题**：pose_extraction/lineart_extraction/depth_map 工作流最初缺失
- **解决**：创建 3 个独立的 ComfyUI JSON 工作流文件，注册到 Backend 工作流注册器
- **格式规范**：遵循现有 workflows/ 目录的 JSON 格式（LoadImage → Preprocessor → SaveImage）

### Phase 4：前端验证
- **问题**：Canvas 和 Timeline 组件目录为空，`canvasStore.ts` 不存在
- **修复**：创建 canvasStore.ts（Zustand）+ CanvasPanel.tsx（react-flow）+ CanvasNode.tsx
- **问题**：SettingsPage 的 API Key 配置为只读占位
- **修复**：改为可编辑 Input.Password + localStorage 保存 + 测试按钮
- **问题**：AssetsPage 缺少资产详情面板
- **修复**：添加双击 Drawer 展示元数据/血缘/多图

## 常见问题

### 前端编译
- 确保 `package.json` 依赖包含 `@xyflow/react`（react-flow 的 v12 包名）
- `verbatimModuleSyntax` 设为 `false` 避免类型导入错误

### 后端导入
- `asset_service.py` / `provider_service.py` / `stage_service.py` 使用单例模式
- 各 Provider 通过 `_register_all()` 自动注册，无需手动 import

## 待办
- [ ] 完整端到端测试（概念图→三视图→分镜→视频→剪辑→成片）
- [ ] 性能测试（大资产库下的列表渲染优化）
- [ ] SettingsPage API Key 增加后端持久化接口
