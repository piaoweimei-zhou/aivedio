# 内容创作系统规划（CreativeOS）v1.1

> 日期：2026-08-26 ｜ 定位：三系统架构中的"创意引擎"
> 一句话：**解决"管道里流的水"——让每条内容都有钩子、有结构、有表现力，替代静态模板，由 LLM 生成式创作 + 质量评估回灌。**

---

## 0. 战略判断

现有系统（director 生产管道 + TrafficOS 流量运营）已解决"**怎么造得稳、怎么发得准**"，但内容本身（创意/文案/表现力）仍是静态模板——观众唯一能感知的是内容质量，管道再顺，水是白开水，用户还是会走。

**结论**：内容创作是独立的"创意能力域"，与工程化生产、流量运营关注点完全不同。必须独立成第三套系统 CreativeOS，三系统各司其职：

| 系统 | 角色 | 回答的问题 |
|---|---|---|
| TrafficOS（流量侧） | 主编/运营总监 | 发什么方向、给谁看、怎么变现、哪些有效 |
| **CreativeOS（内容创作）** | **编剧/导演团队** | **怎么写才抓人、分镜怎么做、提示词怎么给** |
| director（生产管道） | 印刷厂/后期 | 怎么把剧本物理变成成片 |

---

## 1. 三系统架构与接口

```
┌─────────────────────────────────────────────────┐
│  CreativeOS · 内容创作系统（:8002，新）            │
│  L1 角度生成  L2 文案/台词  L3 分镜/提示词         │
│  L4 声音/节奏  L5 质量评估+回灌                    │
│  产物：Content Spec（剧本+分镜+提示词+文案包）     │
└───────┬──────────────────────────┬──────────────┘
        │ ① 选题+信号+ROI历史       │ ③ 效果反馈(角度/文案/提示词)
┌───────▼─────────┐   ┌───────────▼───────────────┐
│  TrafficOS :8001 │   │  director :8000            │
│  选题/数据/变现   │──▶│  本地 GPU 生产成片          │
│  发布/回灌       │ ② │  Content Spec → 5步成片     │
└─────────────────┘   └───────────────────────────┘
```

### 1.1 边界（关键，避免重叠）

| 环节 | TrafficOS | CreativeOS |
|---|---|---|
| 选题 | 该不该做：热度×变现×信号，打分排产 | 怎么做：同一话题切 5 种角度 |
| 文案 | 发布层：标题/caption/话题/封面（平台+人设+变现） | 内容层：剧本/台词/钩子/分镜/提示词 |
| 数据 | 播放/ROI/转化归因、选题权重回灌 | 质量分、好文案/提示词回灌模板库 |
| 发布 | 平台/排期/账号矩阵 | — |

### 1.2 四条数据流
1. **TrafficOS → CreativeOS**：选题（topic + dimension + platform + monetizer + 用户信号 + ROI 历史）
2. **CreativeOS → director**：Content Spec（经 contract API，schema 复用现有 script.acts）
3. **director → TrafficOS**：成片 → 发布 → 采集效果
4. **TrafficOS → CreativeOS**：效果反馈（哪个角度/钩子/提示词有效）→ 调权重/模板库

---

## 2. 核心产物：Content Spec（契约）

CreativeOS 的一切输出收敛为 Content Spec，director 只认它生产。**Schema 与现有 contract 兼容**（script.acts 直接映射）。

```json
{
  "content_id": "cs_xxx",
  "strategy": {
    "angle": "痛点|反差|故事|数据|悬念|教程|共鸣",
    "platform": "douyin|kuaishou|bilibili|xiaohongshu",
    "dimension": "pure_content|knowledge|soft_ad",
    "monetizer": "tool|adshare|netdisk",
    "topic_id": "topic_xxx",
    "persona": "轻松分享者"
  },
  "script": {
    "hook": "三秒解决去水印，别再求人了",
    "acts": [
      {"narration": "以前去水印要半小时…", "emotion": "共情", "duration": 3.0, "visual": "…"},
      {"narration": "现在这个工具三秒搞定", "emotion": "惊喜", "duration": 5.0, "visual": "…"}
    ],
    "cta": "评论区扣『工具』，我发你",
    "total_duration_s": 8.0
  },
  "storyboard": [
    {"shot": 1, "camera": "特写→推镜", "lighting": "暖光", "prompt": "专业英文分镜词"}
  ],
  "voice": {
    "tts_voice": "旁白_女声_温柔",
    "speed": 1.05,
    "emotion": "惊喜",
    "bgm": "轻快_upbeat",
    "bgm_volume": 0.25
  },
  "packaging": {
    "title": "3 秒解决去水印，亲测可用",
    "caption": "实测分享…\n\n#效率工具 #去水印",
    "cover_style": "大字标题_对比色"
  },
  "quality": {
    "content_score": 8.2,
    "visual_score": 7.5,
    "llm_provider": "cloud_doubao",
    "llm_model": "doubao-pro",
    "prompt_fingerprint": "hash_v1"
  }
}
```

---

## 3. 五层详设

### L1 内容策略层 —— 选题差异化角度生成
- **输入**：TrafficOS 选题 + 平台 + 维度 + 用户信号 + ROI 历史
- **角度库**：痛点 / 反差 / 故事 / 数据 / 悬念 / 教程 / 共鸣 / 争议
- **输出**：同话题生成 5 种切入角度 + 按平台调性推荐 Top1
  - douyin：情绪化、快节奏、强钩子
  - bilibili：深度、知识增量
  - xiaohongshu：种草、生活方式、真诚人设
  - kuaishou：真实、接地气、口播感
- **落地**：`app/strategy.py`（角度 prompt 模板 × LLM 生成）
- **验收**：1 topic → 5 角度，命中平台调性规则，可人工改选

### L2 文案创作层 —— 生成式写作框架（最大杠杆）
- **结构模板**：钩子(0-3s) → 冲突/展开(3-8s) → CTA 收尾；多段按"情绪曲线"排
- **台词**：口语化、节奏感、情绪标注（共情/惊讶/紧迫/治愈）
- **标题公式库**：悬念式/数字式/反差式/痛点式（LLM 按公式生成多版）
- **caption**：分平台写作 + 话题自动生成（复用 TrafficOS 话题规则但内容生成化）
- **落地**：`app/copywriting.py` + `app/api/creative.py`
- **验收**：topic → 完整 script（hook+acts+cta）+ 3 版标题，风格可配置

### L3 提示词工程层 —— 镜头语言组件库
- **组件库**（结构化，非散文）：
  - 景别：特写/近景/中景/全景/远景
  - 运镜：推/拉/摇/移/跟/升/降/环绕
  - 光影：暖光/冷调/逆光/丁达尔/霓虹
  - 构图：三分法/中心/对称/留白/前景遮挡
  - 材质：玻璃/金属/布料/皮肤质感
  - 风格：电影感/赛博/极简/治愈/复古
- **组装**：分镜设计 → 中文分镜描述 → 英文 ComfyUI 兼容 prompt（`build_en_prompt()`）
- **角色卡**：角色外貌/服饰/风格锚点，保证跨镜一致性（`app/character.py`）
- **落地**：`app/storyboard.py` + `app/prompt_engine.py`
- **验收**：剧本 → 1~N 分镜，每镜含镜头语言，可被 director 消费

### L4 表现力层 —— 声音/节奏设计
- **声音**：TTS 多音色库（男/女/少年/旁白）+ 语气/语速/停顿；按 emotion 选音色
- **BGM**：情绪曲线（开场轻 → 高潮强 → 收尾柔）→ BGM 标签 + 音量
- **节奏**：钩子前置、卡点设计、单段时长按台词密度分配
- **对接**：输出 `voice` 字段 → director 的 tts_mode/tts_volume/bgm 参数
- **落地**：`app/audio_design.py`
- **验收**：script → voice 设计（音色+语速+BGM+情绪），可被 director 消费

### L5 质量评估层 —— 量化反馈 + 回灌
- **内容分**：钩子强度/结构完整/口语化/平台匹配（规则分 + LLM 评估分）
- **视觉分**：构图/曝光/一致性（复用 director qc_service 的 CV2 探针思路）
- **回灌**：
  - 高分文案/提示词 → `templates/` 库（带权重，优先复用）
  - TrafficOS 效果反馈（完播/点赞/转化）→ 调角度权重、模板权重
- **落地**：`app/quality.py` + `data/feedback/`
- **验收**：生成即出分；feedback 落库可审计；模板权重可被 L2/L3 读取

---

## 3.5 参考资产吸收（CineForge 方法论原型）

> 项目：`D:\项目\视频与内容创作\chuangzuo`（CineForge V2，Electron 桌面短剧 AI 创作工具，**已实践验证**）
> 判断：**CreativeOS 不从零发明——它已在这个桌面工具里验证了 L2/L3/L5 三层核心方法论。CreativeOS 做的是"服务化迁移"：把给人用的 GUI 流程 + 提示词模板，改造成机器可调用的 API + 结构化模板库。**

### 可移植资产映射

| CreativeOS 层 | CineForge 现成资产 | 迁移方式 |
|---|---|---|
| **L2 剧本/文案** | 9 步剧本 Wizard（设定→概念→梗概→人物→前史→结构→场景→写作→审阅）；剧本 SKILL（eye-blink-life 等：一句话设定 → 完整剧本+镜头表，含 protagonist-card / shot-card-pov 等 references） | 流程服务化为 `app/copywriting.py` 的多步生成；剧本 SKILL 结构化进模板库 |
| **L3 分镜/提示词** | 分镜提示词模板 V3（Step 0.3 剧本强化锁方向 / 角色 ID 卡 / **16 字段逐镜输出**）；全资产大师（场景+角色+道具）；反推图像提示词（空间构图/光影/五大维度方法论） | 模板→组件库 → `app/storyboard.py` / `app/prompt_engine.py` 组装；资产库→角色卡 |
| **L5 质量评估** | 评价.txt 三维打分：**精确性/完整性/情感张力**（量化评分+扣分点+改进建议，且已做多版本对比） | 直接作为 `app/quality.py` 评分维度基础（内容分=三维持 + 平台匹配） |

### 已知缺陷（CineForge 暴露，CreativeOS 必须解决）
1. **内容循环冗余**：剧本场景高度重复（多次"硬币掉包-修复-真相泄露"循环）→ L5 加"结构去重/信息增量"检查 + L2 结构模板约束每幕必须有新信息增量
2. **时长不匹配**：5 分钟内容仅 11 个单元、时长压缩 → L2 按时长配额生成（如 60s/8 段 vs 15s/1 段）
3. **情感张力依赖特定结构**（如"不可挽回的损失"）→ L1 角度库 + L5 情感峰值检查，防止套路化

### 落地
- M0 起将 CineForge 提示词模板库**直接复制为 `creativeos/assets/prompts/` seeds**（结构化解析，非散文本）
- 剧本/分镜模板先冻结为 schema（`assets/templates/*.yaml`），由 L2/L3 按 schema 加载组装
- 保留版权与出处标注（CineForge 作者 Work-Fisher）

---

## 4. LLM 混合接入（云端为主 + 本地兜底）

### 4.1 抽象层
```
LLMProvider 接口（app/llm/base.py）
 ├── CloudProvider  （app/llm/cloud.py）  豆包/通义 API
 └── LocalProvider  （app/llm/local.py）  Qwen 开源（复用 GPU/ollama）
   选择策略：CREATIVEOS_LLM_PROVIDER=auto|cloud|local
   auto = 云端可用用云端，失败/超时自动降级本地
```

### 4.2 分层策略
| 层 | 主 | 兜底 | 理由 |
|---|---|---|---|
| L1 角度 | 云端 | 规则+本地 | 角度需多样生成 |
| L2 文案 | 云端 | 本地 + 模板库 | 质量要求高 |
| L3 提示词 | 本地优先 | 云端 | 组件库可规则组装，少用 LLM 省钱 |
| L4 声音 | 规则 | 规则 | 音色库映射，无需 LLM |
| L5 评估 | 云端 | 本地 + 规则 | 评估需要理解力 |

### 4.3 成本控制
- 模板库命中优先（高分模板直接复用，不调 LLM）
- prompt_fingerprint 缓存（相同输入不重复调用）
- 本地模型做批量/离线生成，云端做质量敏感生成

---

## 5. 数据模型与 API

### 5.1 数据目录
```
creativeos/
  data/
    specs/          # Content Spec 产物（json）
    templates/      # 高分文案/提示词模板库（带权重）
    feedback/       # 效果反馈（TrafficOS 回灌）
    characters/     # 角色卡
```

### 5.2 API（:8002）
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/creative/generate | 一键：topic → Content Spec（含全部层） |
| POST | /api/creative/angles | L1 角度生成 |
| POST | /api/creative/script | L2 剧本生成 |
| POST | /api/creative/storyboard | L3 分镜+提示词 |
| POST | /api/creative/voice | L4 声音设计 |
| POST | /api/creative/assess | L5 质量评估 |
| GET | /api/creative/specs/{content_id} | 取产物 |
| GET | /api/creative/templates | 模板库 |
| POST | /api/creative/feedback | TrafficOS 效果回灌 |

---

## 6. 与现有系统衔接

### 6.1 TrafficOS 改造（最小侵入）
- `orchestrator.build_script_from_topic`（现硬编码 `f"{core}，快看这里"`）→ 改为调 CreativeOS `/generate`，失败兜底回原模板
- 保持 TrafficOS 所有现有 API/测试不变，新增一个 adapter

### 6.2 director 对接
- Content Spec.script 与现有 contract schema **天然兼容**（acts.narration/visual/duration 已支持）
- voice/packaging 字段映射到现有 contract params

### 6.3 数据回灌闭环
- TrafficOS metrics（完播/点赞/转化）→ POST /api/creative/feedback → L5 更新模板权重

---

## 7. 优先级与路线图

| 阶段 | 内容 | 交付 | 估时 |
|---|---|---|---|
| M0 | 骨架：目录 + Content Spec schema(Pydantic) + LLM 抽象层(auto降级) | `app/` 骨架可启动 | 0.5 天 |
| M1 | **L2 文案层 + L1 角度生成**（最大杠杆） | topic → 完整 script + 3 版标题 | 1 天 |
| M2 | L3 分镜/提示词（组件库 + 英文 prompt 组装 + 角色卡） | script → 分镜 spec | 1-1.5 天 |
| M3 | L4 声音/节奏设计 | spec 含 voice 字段 | 0.5 天 |
| M4 | L5 评估 + 回灌 + TrafficOS adapter 对接 | 全闭环 + 端到端对比成片 | 1-1.5 天 |

**建议顺序**：M0 → M1 → M2 → M3 → M4（合计约 4-5 天，可并行压）

### 7.1 验收标准（一条 M1 端到端）
1 topic → CreativeOS 生成 Content Spec（角度+钩子+分镜+提示词+标题）
→ director 生产成片 → 与旧模板成片**并排对比**，主观质量显著提升
→ TrafficOS 发布 → 效果数据回灌 CreativeOS → 模板权重更新（可审计）

---

## 8. 风险与决策

| 风险 | 应对 |
|---|---|
| LLM 生成质量不稳定 | L5 评分 + 模板回灌 + 高分锁定；人工精选入口 |
| LLM 成本 | 混合策略 + 模板命中优先 + fingerprint 缓存 |
| 本地模型效果/速度 | 仅作兜底降级，不阻塞主链路 |
| Content Spec 与 director 兼容 | 复用现有 script schema，M0 先冻结 schema 并对拍 |
| 文案"套话化" | 角度库 + 情绪标注 + 反馈调权，持续对抗同质化 |

## 9. 度量体系
- 内容分/视觉分趋势（周）
- 钩子完播率提升（对比旧模板基线）
- 模板库复用率 / LLM 调用成本 / 成功率 / 降级率
- 反馈回灌覆盖率（多少内容有效果数据）

## 10. 立即行动项（本周）
1. 建 `D:\1\2\creativeos` 骨架（app/ data/ tests/ assets/）
2. Content Spec Pydantic schema（对齐 contract schema）
3. LLM 抽象层（cloud + local + auto 降级，读环境变量）
4. **从 CineForge 移植结构化提示词模板 → `assets/prompts/` seeds**（分镜 V3 / 资产库 / 剧本 SKILL）
5. L2 最小可跑：topic → script 的 LLM 调用 + 落盘
6. 写 M1 端到端验收脚本（调 CreativeOS → director 对比成片）

---

## 11. 工程化约束（对齐现有治理）
- 入 `trafficos/.flake8` 同标准：flake8 0-error、pre-commit 门禁
- 单测：每层 ≥3 条（schema 校验 / LLM mock / 模板命中）
- 目录命名与 data 规范对齐 director/trafficos
- 经验沉淀：P0闭环经验文档风格，按里程碑记录
