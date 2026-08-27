# 内容创作系统规划（CreativeOS）v1.4

> 日期：2026-08-26 ｜ 定位：三系统架构中的"创意引擎"
> v1.2 更新：M0–M4 全部完成、全链路真实闭环、M4.1 修复批次（云端优先/视觉表现力/平台适配）、质量对比经验沉淀
> v1.3 更新：**CineForge 7 项资产全部落地（P0/P1/P2）**——时长配额、台词估算、叙事蓝图、五维评估、A/B 实验、质量门禁、跨集连贯性；版本号对齐 §12.5
> v1.5 更新：**CineForge 四轮穷尽式挖掘总账（19 项）全部落地**（2026-08-27 完成 P3-P8 批次），CreativeOS 内容竞争力补齐闭环，正式为挖掘画句号
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

### 二次深度吸收（v1.2，2026-08-26 复核源码后新增）

> 复核结论：CineForge 不止是提示词模板，其**源码层已实现一整套"结构校验 + 质量闭环 + 生产规格"机制**，以下是 CreativeOS 规划中此前未吸收、但可直接迁移的服务化资产：

| CreativeOS 层 | CineForge 资产（源码） | 迁移价值 |
|---|---|---|
| **L2 结构约束** | `narrativeBlueprintService.ts` 叙事蓝图：规则表（前史呼应/动机视觉化/视觉递进，各标注必须出现的场景）+ 情感节拍（假希望/中点反转/镜像时刻/代价极限/收束仪式，required+anchorScene）+ 节奏预算（每场时长配额，总和=总时长） | **补 L2 最大缺口**：结构完整性机制化校验，直接对抗"循环冗余/套路化"（已知缺陷①③）；节奏预算天然支撑"任意时长配额" |
| **L2 时长配额** | `durationSpec.ts` 时长规格表：30s/1min/2min/3min/5min…每档给**字数范围+场景数+结构模板**（如 30秒=250-350字/1-2场景/设置→反转→钩子） | **"时长自由组合"（5s~3min）的核心依据**：每段目标时长→字数/场景配额→L2 按配额生成 |
| **L4 台词时长** | `speechDurationEstimator.ts`：台词字数÷语速(3.5字/秒，重情绪3.0) + 动作行×1.8s + 情绪停顿(重2.0/中1.0/轻0.5s) → 偏差% | 生成台词即预估算时长，**防止 TTS 台词溢出视频时长**（人声装不下） |
| **L5 评估维度** | `评价.txt` 五维评估：精确性/完整性/情感张力/**视听可执行性**/主题深度 + **多版本横向对比**（情感峰值对比表） | L5 加"视听可执行性"（场景数/特效成本/道具→director 好不好做）；多版本并排对比取代单点打分 |
| **L5 实验选优** | `abExperimentService.ts`：同一基础 prompt + 2 变体并行生成 → 按 compareMetric 判定胜者 | 钩子/角度/提示词 **A/B 实验选优**，替代"人工猜哪个好"；结果回灌模板库 |
| **质量门禁闭环** | `batchSeriesService`+`screenplayService`：生成→自检→autoFix→质量评分→**评分≥85+自检全 pass→跳过人工审批**；跨集 checkCoherence（LLM 对比上集钩子+本集事件，≥60 判定连贯） | director 门禁升级方向：不达标自动重生成、达标自动放行；矩阵化系列内容的连贯性保障 |

**建议吸收路径**：
1. **L2 结构模板**：将叙事蓝图（rules/beats/pacing）抽象为 `assets/templates/blueprint.yaml`，L2 生成前先加载蓝图约束
2. **时长配额表**：把 durationSpec 映射为 CreativeOS 的 `SEGMENT_QUOTA`（5s=15-25字、10s=30-50字、20s=60-100字、60s≈500-750字…），L2 按段配额生成台词
3. **L5 五维 + A/B**：quality.py 扩展为"结构校验（蓝图规则）+ 时长偏差（估算器）+ 视听可执行性 + LLM 内容分"；可选的 A/B 实验接口
4. **门禁闭环**：接 director——评分≥阈值自动放行，不达标回炉重生成（半自动→全自动）

> 版权与出处：以上机制源自 CineForge（作者 Work-Fisher）源码与《短剧.md》《自动化改造.md》《评价.txt》等实践文档，迁移时保留标注。

### 3.6 四轮挖掘总账（19 项，v1.4 穷尽）

> 2026-08-26 对 CineForge 做 **4 轮穷尽式深查**（初探 → 源码复核 → 方法论 → 工程/运营），累计挖出 19 项可迁移资产。标注状态：✅已落地 / 🟡待做（与当前目标契合）/ ⏸冻结（待命资产）。

| # | 层 | 资产 | 来源 | 状态 |
|---|---|---|---|---|
| 1 | 内容创意 | 时长配额表 `SEGMENT_QUOTA`（5s=14-20字…60s=180-215字） | durationSpec.ts | ✅ `8329552` |
| 2 | 内容创意 | 台词时长估算（字数/3.8+情绪停顿） | speechDurationEstimator.ts | ✅ `8329552` |
| 3 | 内容创意 | 叙事蓝图（功能/情绪节拍/信息增量防重复） | narrativeBlueprintService.ts | ✅ `ec5c6d0` |
| 4 | 内容创意 | L5 五维评估（+视听可执行性+结构去重） | 评价.txt 五维 | ✅ `ec5c6d0` |
| 5 | 内容创意 | A/B 实验选优 | abExperimentService.ts | ✅ `ec5c6d0` |
| 6 | 内容创意 | 质量门禁闭环（不达标回炉） | batchSeriesService | ✅ `0c08773` |
| 7 | 内容创意 | 跨集连贯性检查（0-100 <60 断裂） | checkCoherence | ✅ `0c08773` |
| 8 | 生产路由/账本 | **成本预估面板**（调用次数→Token→$2.5/M→$50红标） | 自动化改造.md §13.1 | ✅ `b238ed7` |
| 9 | 生产路由/账本 | **Provider 混合路由表**（MediaProviderConfig+default+enabled） | videoCompositionService.ts | ✅ `b238ed7` |
| 10 | 记忆/一致性 | EpisodeMemory 剧集记忆（keyEvents/剧情线状态/道具）注入下集 | batchSeriesService | ✅ `e049690` |
| 11 | 记忆/一致性 | VisualFeatures 参考图特征（色彩/光影/风格/构图/质感→prompt） | visualFeatureService.ts | ✅ `e049690` |
| 12 | 方法论 | **分镜方法论 V3**（逐镜16字段/起幅落幅焊接点/T1-T19镜头模板库） | 提示词/分镜模板V3 | ✅ `6bbac7d` |
| 13 | 方法论 | 剧本 8 步渐进生成（每步自检+批准） | screenplayStepParser + 短剧.md | ✅ `58139a6` |
| 14 | 方法论 | 题材 SKILL 方法论库（触发词+公式蒸馏+跨模型适配） | 提示词/剧本SKILL | ✅ `30d5a80` |
| 15 | 方法论 | 分镜批量预览（缩略图省无效视频成本） | storyboardPreviewService | ✅ `30d5a80` |
| 16 | 方法论 | prompt 资产库（图像生成器/反推/全资产大师/MJ模板） | 提示词/ 目录 | ✅ `30d5a80` |
| 17 | 工程/运营 | **LLM 解析健壮性经验**（思考前缀→两阶段法/单点脆弱/双路径架空） | 自查.md | ✅ `6bbac7d` |
| 18 | 工程/运营 | 知识图谱提示词工程（prompt 实体+维度排列组合抽卡+测试集） | 思路.md | ✅ `e4ab0aa` |
| 19 | 工程/运营 | 平台合规/质量经验（半身照过审/图像决定视频/不油腻） | 思路.md | ✅ `e4ab0aa` |

**已确认无价值（封存）**：`skills-main/`（anthropics/skills 官方克隆，非原创）；`build/dist/final/release/node_modules`（构建产物）；`2.0界面*.md`（Electron 前端还原文档，本系统不做该前端）。

**落地优先级建议**：
1. 🥇 **#12 分镜方法论** → 升级 L3 分镜 prompt（直接提升成片视觉质量，半天）
2. 🥈 **#8+#9 合并为混合策略配置面**（成本模型 + 路由表，服务"线上+本地"）
3. 🥉 **#17 解析健壮性** → 顺手加固 `call_llm_json`（防踩 CineForge 同坑）
4. ✅ 19 项全部落地（2026-08-27）：P0-P2（#1-7）→ P3（#12/17）→ P4（#8/9）→ P5（#13）→ P6（#10/11）→ P7（#14/15/16）→ P8（#18/19）

> 版权与出处：19 项均源自 CineForge（作者 Work-Fisher）源码与实践文档，吸收方法论结构，保留标注；分镜模板 V3 含版权声明，仅借鉴框架不复制原文。

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

### 4.4 实测与修复（v1.2，M4.1）
> **关键教训：服务进程必须显式加载 .env，否则 auto 策略恒降级本地。**
> 根因：`app/main.py` 未调用 `load_dotenv` → 服务进程读不到 `.env` → `CloudProvider.available()` 恒为 False → **永远走 local 兜底**，云端配置形同虚设。修复：`main.py` 顶部 `load_dotenv(_BASE / ".env")` + `load_dotenv()` 兜底。

**实测可用配置（2026-08-26）**：
| Provider | 配置 | 实测结论 |
|---|---|---|
| 云端豆包 | `CREATIVEOS_CLOUD_MODEL=doubao-1-5-pro-32k-250115`（ARK API） | ✅ **唯一实测可用云端模型**；`doubao-seed-2-0-pro`/`1-6` 系列 404 死路 |
| 本地 Ollama | `qwen2.5:7b` | ⚠️ 可跑，但多段生成质量差（台词重复、画面空），仅作兜底 |
| DeepSeek | `deepseek-v4-flash` | ❌ 402 欠费死路 |

**云端 vs 本地质量对比（同 topic"去水印工具"4 段）**：
| 维度 | local qwen2.5:7b（修复前） | cloud doubao（修复后） |
|---|---|---|
| 台词 | 模板化、段2/3 重复（"关键步骤来了"×2） | 真实故事线：烦→坑→惊喜→呼吁，每段不重复 |
| 画面 visual | **全部为空 ''** | 每段有具体画面（皱眉看手机/摊手/展示工具/邀请手势） |
| 分镜 content | **全部为空 ''**（只有镜头语言标签，画面泛化） | 具体内容（用户皱眉看手机/摊手/展示手机工具/向镜头做邀请手势） |
| 钩子 | 总踩坑？三秒给你答案 | 想取视频水印，又怕收费？（痛点切入） |
| 内容分 | — | 7.52 |

> **结论**：CreativeOS 质量上限由 LLM 决定，**必须确保云端优先 + 显式加载配置 + 降级时明确告警**，否则"空画面成片"（观众视角直接劝退）会静默产生。此经验已固化为 §8 风险应对 + 门禁关注点。

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

> **状态更新（v1.2）**：M0–M4 已于 2026-08-26 全部完成并验收，三系统真实闭环打通（TrafficOS→CreativeOS→director→成片）。下表标记为 ✅ 已完成。

| 阶段 | 内容 | 交付 | 状态 |
|---|---|---|---|
| M0 | 骨架：目录 + Content Spec schema(Pydantic) + LLM 抽象层(auto降级) | `app/` 骨架可启动 | ✅ 完成 |
| M1 | **L2 文案层 + L1 角度生成**（最大杠杆） | topic → 完整 script + 3 版标题 | ✅ 完成 |
| M2 | L3 分镜/提示词（组件库 + 英文 prompt 组装 + 角色卡） | script → 分镜 spec | ✅ 完成 |
| M3 | L4 声音/节奏设计 | spec 含 voice 字段 | ✅ 完成 |
| M4 | L5 评估 + 回灌 + TrafficOS adapter 对接 | 全闭环 + 端到端对比成片 | ✅ 完成 |
| M4.1 | 修复批次：云端优先 / 视觉表现力 / 平台标题限长 | 详见 §12.2 | ✅ 完成 |
| **P0–P2** | **CineForge 7 项资产吸收落地** | 详见 §12.5 | ✅ 完成 |

**已交付验收基准**：`scripts/m4_acceptance.py`（可重复跑 generate→templates→feedback）；pytest **63 passed** + flake8 **0 error**（含 scripts）。

### 7.1 验收标准（一条 M1 端到端）——已达成
1 topic → CreativeOS 生成 Content Spec（角度+钩子+分镜+提示词+标题）
→ director 生产成片 → 与旧模板成片**并排对比**，主观质量显著提升
→ TrafficOS 发布 → 效果数据回灌 CreativeOS → 模板权重更新（可审计）

**真实闭环样本（v1.2）**：
- TrafficOS 选题"去水印工具" → CreativeOS LLM 生成（钩子"想取视频水印，又怕收费？"）→ director `/contract/produce` → 5 步流水线（concept→video→subtitle→hook_overlay→export）跑通
- 成片交付 2 条：`global_export_006_d093e0.mp4`（单段 11.4MB）、`global_export_007_5103f0.mp4`（LLM 全链路 4.9MB）
- Content Spec 样本：`creativeos/data/specs/cs_去水印工具_1787741713221.json`（五层结构 + LLM 溯源 cloud/doubao）

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

**状态更新（v1.2）**：以下约束已全部落地并纳入 CI：
- 入 `trafficos/.flake8` 同标准：flake8 0-error、pre-commit 门禁 ✅
- 单测：每层 ≥3 条（schema 校验 / LLM mock / 模板命中）✅（pytest 63 passed）
- 目录命名与 data 规范对齐 director/trafficos ✅
- 经验沉淀：P0闭环经验文档风格，按里程碑记录 ✅（见 §12）

---

## 12. 实施记录与经验沉淀（v1.2）

### 12.1 M4 收尾交付
- **L5 LLM 质量评估**（`app/quality.py`）：规则×0.4 + LLM×0.6 加权 + 逐条 suggestions
- **模板回灌**（`app/template_lib.py`）：content_score≥8.0 入库（权重 0.5）、feedback ±0.1 调权
- **TrafficOS adapter**（`trafficos/app/creative_adapter.py`）：失败兜底回原模板
- 提交：creativeos `f26879d`（M4）｜ director `07221b8`（adapter）

### 12.2 M4.1 修复批次（12 缺陷诊断中的 CreativeOS 项）
| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| #4 | auto 恒降级 local | main.py 未 load_dotenv，服务进程读不到 .env | 显式 load_dotenv → 实测走 cloud/doubao ✅ |
| #5 | 视觉表现力弱（visual 空） | fallback 路径 visual='' → prompt 无内容 → 成片泛化 | `copywriting.py` 按情绪生成画面（_VISUAL_BY_EMOTION 6 模板）+ `storyboard.py` content 空时回退剧本 visual ✅ |
| #11 | 多平台封面标题溢出 | 标题无平台限长 | `orchestrator.py` 按平台限长（抖音/快手 14 字、小红书 18、B站 26）+ director 封面换行按画面比例自适应 ✅ |
| #6 | 内容体系早期（多段连贯性） | 本地模型多段质量差 | 云端已启用 + 模板回灌复利，标记为持续演进项 📌 |

### 12.3 已确认的关键链路事实
- **全链路 5 步**：concept（概念图 I2V 输入）→ video（H3 分段）→ subtitle → hook_overlay → export，`/contract/produce`（X-API-Key: `dev-contract-key-not-for-prod`）驱动
- **4 段 20s 完整结构**（共情→冲突→解决→惊喜）：验证 L1 角度 + L2 情绪曲线体系能力（成片 `global_export_008`，22.4MB）
- **脚本时长自由组合**：contract 层 `acts[N]` → 逐段视频，任意段数 × 任意时长（5s~3min），时长 100% 来自契约输入

### 12.4 遗留与后续（不阻塞）
1. **多段剧情连贯性提升**：云端下继续打磨 L1 角度差异化与 L5 结构去重检查（CineForge 已知缺陷①的对抗）
2. **模板库冷启动**：高分样本积累中，随真实发布数据回灌升温
3. **quality 视觉分**：目前以规则/LLM 文本评估为主，可对接 director qc_service 的 CV2 真实画面探针
4. **成本曲线**：云端用量随内容量上升，需持续跟踪 §9 度量（调用成本/降级率）

### 12.5 CineForge 7 项资产落地记录（v1.3）

> 2026-08-26 深度复核 CineForge 源码后，将 §3.5"二次深度吸收"的 7 项资产全部服务化落地，分 P0/P1/P2 三批。

| 优先级 | 资产 | 落地实现 | 提交 | 实测 |
|---|---|---|---|---|
| P0-1 | 时长配额表 | `app/duration.py` `SEGMENT_QUOTA`（5s=14-20字…60s=180-215字，插值）+ `word_quota()`；LLM prompt 注入每段字数要求 | `8329552` | 4 段台词 11-14 字全在配额内 |
| P0-2 | 台词时长估算 | `estimate_narration_sec`（字数÷3.8+情绪停顿）+ `check_duration_fit` 偏差校验 + `truncate_to_quota` 自动截断；orchestrator 生成后拟合校验入 quality 建议 | `8329552` | 拟合校验接入 |
| P1-1 | 叙事蓝图 | `app/blueprint.py` 段数→功能+情绪蓝图 + 段间信息增量校验；LLM prompt 注入蓝图；重复段自动 LLM 二次精修 `_dedupe_script` | `ec5c6d0` | 4 段严格按功能递进、无重复 |
| P1-2 | L5 五维评估 | `quality.py` 加视觉可执行性（visual 具体性评分）+ 结构去重维度；规则建议（重复/visual 空）并入 suggestions | `ec5c6d0` | 无硬问题建议、内容分 7.68 |
| P1-3 | A/B 实验 | `app/abtest.py` + `POST /api/creative/ab-test`：两角度变体并行生成→L5 打分选胜 | `ec5c6d0` | 痛点 7.92 vs 故事 7.92 |
| P2-1 | 质量门禁闭环 | `generate` 重构 `_generate_once` + `quality_gate`（内容分<6.5 自动回炉，max_retries=1，重试记录进 quality 建议） | `0c08773` | 4 门禁测试 |
| P2-2 | 跨集连贯性 | `app/coherence.py` + `POST /api/creative/coherence`：LLM 校验主题/人设/钩子衔接，0-100 <60 断裂，失败规则兜底 | `0c08773` | 4 测试（规则/LLM/兜底） |

**质量基线**：pytest **92 passed**（+24 新测试）/ flake8 **0 error** / CreativeOS :8002 已重启生效

**新增 API 面**：`/api/creative/generate`（quality_gate 参数）、`/api/creative/ab-test`、`/api/creative/coherence`
