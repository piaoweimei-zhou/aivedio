# QC 质检链路诊断报告

> 日期：2026-08-20　|　对象：一键成片链路质检（qc_stage）　|　模型：Qwen3-VL-8B-Instruct-Q4_K_M（本地 llama.cpp）

---

## 1. 背景与目标

一键成片链路（concept → storyboard → video → export）已能产出成片，但**成片质量缺乏客观评价**。为此引入本地视觉模型 Qwen3-VL-8B（Q4_K_M 主模型 + FP16 mmproj，约 6GB，16G 显存可跑满血），在 export 之后挂载 `qc_stage`，对成片做三件事：

1. **AI 看懂片子打分（100 分制）**：按敲定的维度权重打分，低于阈值判"翻车"不发布。
2. **平台规则校验**：文案/字幕/画面跑敏感词 + 违规项规则库，输出"通过/拦截 + 命中项"。
3. **版权风险提示**：检测受版权保护的 IP 形象/音乐/水印，输出风险等级 + 原因。

### 1.1 敲定的 100 分制权重

| 维度 | 权重 | 说明 | 评分来源 |
|------|------|------|----------|
| 画质清晰度 | 20 | 黑屏/模糊/压缩伪影/分辨率达标 | cv2 客观质检 |
| 人物一致性 | 20 | 多镜头角色脸/服装/体型是否同一人 | AI 语义 |
| 口型同步 | 15 | 人声与嘴型是否对得上（无口播给 80） | AI 语义 |
| 构图与美学 | 15 | 画面构图、景别、信息密度 | AI 语义 |
| 节奏与完播 | 15 | 前 3 秒钩子、节奏是否拖沓 | AI 语义 |
| 平台合规 | 15 | 低俗/政治/医疗/标题党/诱导加私信 | AI 语义 + 关键词兜底 |

**红线规则**（优先于分数）：
- 合规命中即拦截（AI `compliance_hits` 非空 → 直接判不通过）
- 版权高风险 IP 一票否决（命中 `COPYRIGHT_RISK_BRANDS` → 直接判不通过）

---

## 2. 实测结果

测试成片：`D:\1\2\director\backend\output\output\export_85cbf5da.mp4`（8.4 MB）

### 2.1 修改前（AI 命中不拦截）

| 项 | 值 |
|----|----|
| 总分 | **91.0 / 100** |
| 判定 | 通过（阈值 60） |
| 维度 | quality 100 / composition 90 / consistency 100 / lip_sync 80 / rhythm 85 / compliance 85 |
| 合规命中 | `['诱导加私信']` |
| AI 总结 | 画面构图美观，角色一致性高，无口播但节奏紧凑，结尾有诱导关注的合规风险 |

**问题**：AI 已明确识别"诱导加私信"合规风险，但红线**未触发**，成片被判"通过"。

### 2.2 修改后（AI 命中即拦截）

| 项 | 值 |
|----|----|
| 总分 | **87.8 / 100** |
| 判定 | **不通过（红线拦截）** |
| 红线拦截 | 合规红线命中: 诱导加私信 |
| 维度 | quality 100 / composition 85 / consistency 95 / lip_sync 80 / rhythm 75 / compliance 85 |
| 合规命中 | `['诱导加私信']` |

**结论**：红线逻辑修复后，AI 识别出的违规项能正确触发拦截，成片判"不通过"，符合"命中高危词直接拦截"的设计意图。

---

## 3. 问题根因

原红线逻辑只做**本地关键词匹配**：

```python
for kw in SENSITIVE_KEYWORDS:
    if kw in all_text:   # all_text = compliance_hits + summary
        res.blocked = True
```

而 `SENSITIVE_KEYWORDS` 只覆盖 `微信号/加微信/私聊` 等词，**缺"诱导/私信/加私信"**。AI 返回的 `compliance_hits=['诱导加私信']` 与关键词库无交集 → 红线未触发。

**根因**：AI 语义判断结果（`compliance_hits`）未直接作为红线依据，与"命中高危词直接拦截"的设计意图脱节。AI 语义理解能力远超关键词匹配，识别出的违规项理应直接拦截。

---

## 4. 已实施修改

### 4.1 `qc_service.py` 核心改动

| 位置 | 改动 |
|------|------|
| `WEIGHTS` | 更新为 6 维度 100 分制（quality 20 / consistency 20 / lip_sync 15 / composition 15 / rhythm 15 / compliance 15） |
| `SYSTEM_PROMPT` | 新增"节奏与完播"维度；版权改为"检测不评分"，只输出 `copyright_hits` |
| `QcResult` | 新增 `blocked` / `blocked_reasons` 字段，报告可区分"分数不达标"与"红线拦截" |
| `_SEM_MAP` | 去掉 copyright 分数映射，新增 rhythm |
| `aggregate` | 画质分由 cv2 提供；**合规 `compliance_hits` 非空 → 直接拦截**；版权高风险一票否决 |
| `run_qc` | 文案本地关键词兜底（命中注入 `compliance_hits`，不依赖模型） |
| `_build_messages` | 视频改**抽帧转 base64 多图**（llama.cpp OpenAI 兼容接口不支持 `video_url`，实测返回 400） |

### 4.2 红线逻辑（修改后）

```python
# 红线 1：合规命中 → 直接拦截（AI compliance_hits 非空即拦，本地关键词兜底已注入同列表）
if res.compliance_hits:
    res.blocked = True
    res.blocked_reasons.append(f"合规红线命中: {', '.join(res.compliance_hits)}")

# 红线 2：版权高风险 IP 一票否决
for brand in COPYRIGHT_RISK_BRANDS:
    if any(brand in h for h in res.copyright_hits):
        res.blocked = True
        res.blocked_reasons.append(f"版权高风险命中: {brand}")

res.passed = (not res.blocked) and res.total_score >= threshold
```

---

## 5. 修改建议（后续优化方向）

1. **分级拦截**：区分硬红线（低俗/政治/医疗夸大 → 必拦）与软红线（诱导关注/加私信 → 可配置为提示或拦截），避免一刀切误伤。
2. **误报监控**：AI 可能过度敏感，建议把拦截样本落盘，定期人工复核后调优 prompt 与词库。
3. **阈值按平台差异化**：`threshold` 已支持参数化，建议抖音/小红书/视频号使用不同阈值与红线库。
4. **版权库扩充**：`COPYRIGHT_RISK_BRANDS` 目前约 20 个，建议按内容行业（影视/动漫/游戏/品牌）扩充并支持配置。
5. **口型同步增强**：本地模型对短片口型判断能力有限，可接入 ASR 时间戳对齐做客观校验（可选）。
6. **报告可视化**：`qc_stage` 已产出 JSON 报告，建议前端用雷达图展示 6 维度得分，便于人工复核。

---

## 6. 风险与注意事项

| 风险 | 说明 | 建议 |
|------|------|------|
| AI 评分波动 | 两次实测总分 91.0 → 87.8，AI 主观评分有波动 | 多次采样取均值，或输出分数区间 |
| 误拦截 | AI 命中即拦截可能误伤合规内容 | 保留人工复核入口（报告可复核） |
| 显存竞争 | llama-server 与 ComfyUI 共用 16GB 显存 | QC 阶段建议在 ComfyUI 空闲时执行 |
| 服务器生命周期 | `run_semantic_qc` 的 `finally` 会 `_stop_server()`；外部启动的服务器（`_SERVER_PROC=None`）不受影响 | 确认服务器由谁管理，避免误停 |
| 抽帧信息损失 | 视频转 6 帧图片，动态信息（口型/节奏）判断受限 | 可增加帧数或分段抽帧 |

---

## 7. 结论

QC 质检链路已按敲定方案落地并通过端到端实测：
- ✅ 100 分制 6 维度打分（画质走 cv2，其余 AI）
- ✅ 合规红线拦截（AI 命中即拦 + 本地关键词兜底）
- ✅ 版权高风险一票否决
- ✅ 本地 Qwen3-VL-8B 抽帧多图推理（解决 llama.cpp 不支持 video_url 的问题）
- ✅ `qc_stage` 产出可复核 JSON 报告，零侵入挂载在 export 之后

**下一步建议**：接入一键成片 DAG（export 后追加 qc 节点），并做一次多成片批量实测校准阈值。
