# workflows 工作流索引
> 状态：现状 ｜ 维护：2026-08-25
> 本索引登记 `workflows/` 目录全部工作流模板的用途与分类，供新增/复用/治理时对照。
> 说明：`workflows/templates/` 为模板素材（图/CSV/manifest），不在此表。

## 技术约束（命名规范化的边界）
- 后端 `infinite_canvas_api.py` **动态扫描** `workflows/` 顶层目录（`os.listdir` 非递归），仅列 `.json`。
- `GET /api/workflows/{name}` 的**安全校验拒绝路径分隔符**（防路径遍历），当前**不支持子目录访问**。
- 因此维持「扁平目录 + 中文名」：中文名对用户直接友好，前端按文件名显示；拆分/英文化需先改造 API 安全模型，收益低、风险高，暂不执行。

## 工作流清单
| 文件名 | 用途 | 分类 | 带 config ||--------|------|------|-----------|| Z-Image.json | 文生图（Z-Image 基础） | 文生图/图生图 | ✅ || Z-Image-Enhance.json | 文生图增强 | 文生图/图生图 | — || Flux2-Klein.json | Flux2 Klein 模型文生图 | 文生图/图生图 | — || 文生图.json | 通用文生图 | 文生图/图生图 | — || 文生图影视级.json | 影视级质感文生图 | 文生图/图生图 | — || 最终文生图.json | 定稿文生图 | 文生图/图生图 | ✅ || 真正全景图.json | 全景图生成 | 文生图/图生图 | — || 精修优化.json | 成图精修优化 | 文生图/图生图 | — || LTX-2.3_video_only.json | LTX-2.3 文生视频 | 视频生成 | — || LTX-2.3_MSR_sample_workflow_V2.json | LTX-2.3 MSR 采样视频 | 视频生成 | — || LTX2.3导演2.json | LTX2.3 导演版 | 视频生成 | — || LTXDirectorv2-API.json | LTX Director v2（API 版） | 视频生成 | ✅ || seed视频放大工作流.json | 视频放大（seed 版） | 视频生成 | — || 1人分镜.json | 单人分镜（正/侧/背三视图 → 分镜） | 分镜/故事板 | ✅ || 2人分镜.json | 双人分镜 | 分镜/故事板 | ✅ || GPT分镜.json | GPT 辅助分镜 | 分镜/故事板 | ✅ || 多人分镜三元约束.json | 多人分镜（三元约束） | 分镜/故事板 | ✅ || 本地多人分镜.json | 本地多人分镜 | 分镜/故事板 | ✅ || 多场景.json | 多场景分镜 | 分镜/故事板 | — || 三个骨架图.json | 三视图骨架图 | 姿态/骨架 | — || 姿态迁移骨骼图.json | 姿态迁移骨骼图 | 姿态/骨架 | — || pose_extraction.json | 姿态提取（Pose Extraction） | 姿态/骨架 | — || lineart_extraction.json | 线稿提取（Lineart） | 姿态/骨架 | — || depth_map.json | 深度图提取 | 姿态/骨架 | — || 模板Pose优化.json | 模板姿态优化 | 姿态/骨架 | — || 模板清场+蒙版.json | 模板清场 + 蒙版处理 | 姿态/骨架 | — || 放大工作流.json | 通用放大 | 放大/增强 | — || 图像放大.json | 图像放大 | 放大/增强 | — || upscale.json | Upscale 放大 | 放大/增强 | — || 最终道具工作流.json | 道具生成 | 道具/场景 | — || 分层渲染.json | 分层渲染 | 道具/场景 | — || Qwen3+TTS+音色设计.json | TTS 音色设计 | 语音/TTS | — || Qwen3+TTS+音频克隆.json | TTS 音频克隆 | 语音/TTS | — || 提示词ltx2.3.md | LTX2.3 提示词参考 | 提示词 | — |
## 统计
- 工作流模板（json）：33 个；其中带 .config.json 参数面板：8 个
- 分类分布：文生图 8 / 视频 5 / 分镜 6 / 姿态 7 / 放大 3 / 道具 2 / TTS 2 / 提示词 1
