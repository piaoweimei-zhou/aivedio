import { useEffect, useState, useRef, useMemo } from 'react'
import {
  Typography, Form, Select, Input, InputNumber, Button, Space, Card, Progress,
  message, Tag, Steps, Tooltip, Alert, Divider, Input as AntInput, Modal,
  Radio, Empty, Switch, Row, Col,
} from 'antd'
import {
  PlayCircleOutlined, ReloadOutlined, ThunderboltOutlined,
  InfoCircleOutlined, FileTextOutlined, ImportOutlined,
  PlusOutlined, DeleteOutlined, UserOutlined, AppstoreOutlined,
  StarOutlined, SoundOutlined, DownloadOutlined, ExportOutlined,
} from '@ant-design/icons'
import {
  batchService, BatchTask, BatchStep, BatchWebSocket, WsEvent, providerApi, assetApi,
  stageApi, scriptApi, styleApi,
} from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'
import { useDirectorStore } from '../stores/directorStore'
import ProjectSelector from '../components/ProjectSelector'
import JSZip from 'jszip'

// 按给定最大宽度把文本折成多行（用于 canvas 封面标题换行）
function wrapText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const out: string[] = []
  let line = ''
  for (const ch of text) {
    if (ctx.measureText(line + ch).width > maxWidth && line) {
      out.push(line)
      line = ch
    } else {
      line += ch
    }
  }
  if (line) out.push(line)
  return out
}

const { Title, Text, Paragraph } = Typography
const { TextArea } = AntInput

// 视频参数默认值
const DEFAULT_VIDEO_PARAMS = {
  duration: 60,
  frame_rate: 24,
  width: 1280,
  height: 720,
  aspect_ratio: '16:9',
  resolution: '720p',
  model: 'LTX-2.3_MSR_sample_workflow_V2.json',
  segment_seconds: 15,
}

// 默认 4 段独立故事情节
const DEFAULT_SEGMENT_PROMPTS = [
  '石板小路上，女人从远处缓缓走来，双手轻轻提着裙摆，衣裙飘飘，镜头从背影跟拍',
  '男人从街道另一端出现，两人迎面相遇，女人抬头与男人对视，表情从冷淡转为微笑',
  '男人侧身做出邀请手势，女人点头回应，两人开始交谈，镜头环绕拍摄',
  '两人并肩向街道深处走去，镜头缓缓跟拍，消失在烟雨朦胧的石板小路尽头',
]

// 默认提示词模板
const DEFAULT_PROMPTS = {
  char1: 'a beautiful young woman in traditional hanfu dress, walking on a stone path in jiangnan water town, gentle expression, long black hair, cinematic lighting, high detail, 8k',
  char2: 'a handsome young man in traditional chinese scholar robe, standing on a stone path in jiangnan water town, warm smile, cinematic lighting, high detail, 8k',
  char3: 'an elegant elderly scholar in grey robe, long white beard, kind expression, cinematic lighting, high detail, 8k',
  char4: 'a young girl in pink hanfu, lively smile, holding a flower, cinematic lighting, high detail, 8k',
  scene: 'jiangnan water town stone path, misty rain, traditional chinese architecture, willow trees, cinematic atmosphere, high detail, 8k',
  prop1: 'a traditional chinese paper umbrella, bamboo handle, oil paper, intricate painting, cinematic lighting, high detail, 8k',
  prop2: 'a wooden flute (dizi), carved bamboo, traditional chinese instrument, cinematic lighting, high detail, 8k',
}

// ============ 预设模板 ============
// 用户只需修改提示词和视频分段提示词即可，其余参数由模板预置
interface PresetTemplate {
  key: string
  label: string
  desc: string
  videoParams: typeof DEFAULT_VIDEO_PARAMS
  videoPrompt: string
  segmentPrompts: string[]
  materials: MaterialState[]
}

const PRESET_TEMPLATES: PresetTemplate[] = [
  {
    key: 'single-60s-720p',
    label: '单人 60s 720p',
    desc: '1 角色 + 1 场景，60 秒 720p 横屏',
    videoParams: { duration: 60, frame_rate: 24, width: 1280, height: 720, aspect_ratio: '16:9', resolution: '720p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 15 },
    videoPrompt: 'single character cinematic scene, jiangnan water town, cinematic, misty rain',
    segmentPrompts: [
      '角色登场，从远处缓缓走来，镜头从背影跟拍',
      '角色近景特写，表情细腻，光影变化',
      '角色与环境互动，转身环视四周',
      '角色远去，镜头拉远，消失在烟雾中',
    ],
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: 'single-20s-576x1024',
    label: '单人 20s 竖屏',
    desc: '1 角色 + 1 场景，20 秒 576×1024 竖屏',
    videoParams: { duration: 20, frame_rate: 24, width: 576, height: 1024, aspect_ratio: '9:16', resolution: '480p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 10 },
    videoPrompt: 'vertical short video, single character with background, cinematic',
    segmentPrompts: [
      '角色正面特写，目光看向镜头',
      '角色侧身转身，环境展示',
    ],
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: 'duo-60s-720p',
    label: '双人 60s 720p',
    desc: '2 角色 + 1 场景，60 秒 720p 横屏',
    videoParams: { duration: 60, frame_rate: 24, width: 1280, height: 720, aspect_ratio: '16:9', resolution: '720p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 15 },
    videoPrompt: 'two characters meet on stone path, cinematic, misty rain',
    segmentPrompts: DEFAULT_SEGMENT_PROMPTS,
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: 'duo-prop-60s-720p',
    label: '双人+道具 60s',
    desc: '2 角色 + 1 道具 + 1 场景，60 秒 720p 横屏',
    videoParams: { duration: 60, frame_rate: 24, width: 1280, height: 720, aspect_ratio: '16:9', resolution: '720p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 15 },
    videoPrompt: 'two characters with prop on stone path, cinematic, misty rain',
    segmentPrompts: DEFAULT_SEGMENT_PROMPTS,
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 3, kind: 'prop', mode: 'generate', outputForm: 'concept', prompt: DEFAULT_PROMPTS.prop1, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: 'trio-prop-60s-1080p',
    label: '三人+道具 60s 1080p',
    desc: '3 角色 + 1 道具 + 1 场景，60 秒 1080p 横屏',
    videoParams: { duration: 60, frame_rate: 24, width: 1920, height: 1080, aspect_ratio: '16:9', resolution: '1080p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 15 },
    videoPrompt: 'three characters with prop, cinematic story, jiangnan',
    segmentPrompts: [
      '三人登场，镜头从远处推近，人物站位清晰',
      '三人互动，主角与配角交谈，第三人在旁观望',
      '冲突升级，道具出现，三人围绕道具展开情节',
      '结局收尾，三人各自离去，镜头拉远',
    ],
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 3, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char3, asset_id: '' },
      { slotId: 4, kind: 'prop', mode: 'generate', outputForm: 'concept', prompt: DEFAULT_PROMPTS.prop1, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: 'quad-2prop-90s-1080p',
    label: '四人+2道具 90s',
    desc: '4 角色 + 2 道具 + 1 场景，90 秒 1080p 横屏',
    videoParams: { duration: 90, frame_rate: 24, width: 1920, height: 1080, aspect_ratio: '16:9', resolution: '1080p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 15 },
    videoPrompt: 'four characters with two props, group story, cinematic',
    segmentPrompts: [
      '四人登场，镜头扫过每个人物站位',
      '分组互动，A 与 B 交谈，C 与 D 在另一边',
      '全员聚拢，道具出现，集体讨论',
      '冲突高潮，两组对抗，氛围紧张',
      '和解结局，四人和解，共同离去',
      '镜头拉远，众人消失在街道尽头',
    ],
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 3, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char3, asset_id: '' },
      { slotId: 4, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char4, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
  {
    key: '40s-480p',
    label: '40s 480p 短片',
    desc: '2 角色 + 1 场景，40 秒 480p 快速预览',
    videoParams: { duration: 40, frame_rate: 24, width: 854, height: 480, aspect_ratio: '16:9', resolution: '480p', model: 'LTX-2.3_MSR_sample_workflow_V2.json', segment_seconds: 10 },
    videoPrompt: 'fast preview short clip, two characters, cinematic',
    segmentPrompts: [
      '两人登场，快速对视',
      '互动情节，道具展示',
      '高潮冲突',
      '结尾收场',
    ],
    materials: [
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ],
  },
]

// ============ 提示词模板库 ============
// 用户可以不输入提示词，从模板库直接选择，或调用 AI 生成
interface PromptTemplate {
  label: string         // 简短标签
  desc?: string         // 风格描述
  prompt: string        // 完整提示词
}

interface PromptLibCategory {
  category: string      // 分类名（如"古风角色"）
  items: PromptTemplate[]
}

const PROMPT_LIBRARY: Record<string, PromptLibCategory[]> = {
  character: [
    {
      category: '古风角色',
      items: [
        { label: '汉服少女', desc: '江南水乡', prompt: 'a beautiful young woman in traditional hanfu dress, walking on a stone path in jiangnan water town, gentle expression, long black hair, cinematic lighting, high detail, 8k' },
        { label: '书生', desc: '温文尔雅', prompt: 'a handsome young man in traditional chinese scholar robe, standing on a stone path in jiangnan water town, warm smile, cinematic lighting, high detail, 8k' },
        { label: '老学者', desc: '仙风道骨', prompt: 'an elegant elderly scholar in grey robe, long white beard, kind expression, cinematic lighting, high detail, 8k' },
        { label: '粉衣少女', desc: '活泼可爱', prompt: 'a young girl in pink hanfu, lively smile, holding a flower, cinematic lighting, high detail, 8k' },
        { label: '剑客', desc: '江湖侠客', prompt: 'a chinese swordsman in dark robe, holding a long sword, cold expression, wind blowing hair, cinematic lighting, high detail, 8k' },
        { label: '宫装贵妇', desc: '华贵端庄', prompt: 'a noble woman in elaborate tang dynasty palace attire, gold hairpin, serene expression, cinematic lighting, high detail, 8k' },
      ],
    },
    {
      category: '现代角色',
      items: [
        { label: '都市青年', desc: '商务休闲', prompt: 'a young urban professional man, smart casual outfit, confident smile, city street background, soft daylight, photorealistic, 8k' },
        { label: '都市女郎', desc: '时尚优雅', prompt: 'a stylish young woman in modern fashion, holding a coffee cup, walking on city street, soft daylight, photorealistic, 8k' },
        { label: '学生', desc: '清新校园', prompt: 'a teenage student in casual outfit, backpack, natural smile, school campus background, soft sunlight, photorealistic, 8k' },
        { label: '老人', desc: '沧桑慈祥', prompt: 'an elderly man with grey beard, wrinkles, warm eyes, simple clothes, soft indoor light, photorealistic, 8k' },
      ],
    },
    {
      category: '科幻角色',
      items: [
        { label: '宇航员', desc: '太空探索', prompt: 'a futuristic astronaut in sleek white spacesuit, helmet under arm, starry space background, cinematic lighting, sci-fi, high detail, 8k' },
        { label: '赛博朋克', desc: '霓虹都市', prompt: 'a cyberpunk character in leather jacket with neon accents, augmented eyes, rainy neon city background, cinematic lighting, high detail, 8k' },
        { label: '机器人', desc: '人形AI', prompt: 'a humanoid android with metallic skin, glowing blue eyes, futuristic outfit, lab background, cinematic lighting, high detail, 8k' },
      ],
    },
    {
      category: '动漫角色',
      items: [
        { label: '少女', desc: '日系动漫', prompt: 'a cute anime girl with long pink hair, big eyes, school uniform, cherry blossom background, soft lighting, studio ghibli style, 8k' },
        { label: '少年', desc: '热血动漫', prompt: 'a young anime hero with spiky black hair, determined eyes, casual outfit, sunset background, vibrant colors, 8k' },
        { label: '魔法师', desc: '奇幻动漫', prompt: 'a magical anime character with flowing robe, glowing staff, mystical aura, fantasy background, vibrant colors, 8k' },
      ],
    },
  ],
  scene: [
    {
      category: '古镇场景',
      items: [
        { label: '江南水乡', desc: '烟雨朦胧', prompt: 'jiangnan water town stone path, misty rain, traditional chinese architecture, willow trees, cinematic atmosphere, high detail, 8k' },
        { label: '京都街道', desc: '日式古街', prompt: 'kyoto traditional street, wooden houses, lanterns, cherry blossoms, soft afternoon light, cinematic atmosphere, high detail, 8k' },
        { label: '徽派村落', desc: '白墙黛瓦', prompt: 'huizhou ancient village, white walls dark tiles, horse-head gables, misty mountains background, cinematic atmosphere, high detail, 8k' },
        { label: '长安街道', desc: '盛唐气象', prompt: 'tang dynasty changan street, grand architecture, red pillars, busy crowd, golden hour, cinematic atmosphere, high detail, 8k' },
      ],
    },
    {
      category: '自然场景',
      items: [
        { label: '竹林', desc: '清幽雅致', prompt: 'lush green bamboo forest, soft sunlight filtering through, misty atmosphere, stone path, cinematic, high detail, 8k' },
        { label: '雪山', desc: '壮阔苍茫', prompt: 'majestic snow mountain landscape, blue sky, crisp air, distant peaks, cinematic atmosphere, high detail, 8k' },
        { label: '海边', desc: '碧海蓝天', prompt: 'tropical beach scene, turquoise water, white sand, palm trees, blue sky, golden hour, cinematic, high detail, 8k' },
        { label: '森林', desc: '神秘幽深', prompt: 'mystical enchanted forest, ancient trees, fog, rays of light, mossy ground, cinematic atmosphere, high detail, 8k' },
      ],
    },
    {
      category: '城市场景',
      items: [
        { label: '霓虹夜街', desc: '赛博朋克', prompt: 'cyberpunk neon city street at night, rain reflections, holographic ads, busy crowd, cinematic atmosphere, high detail, 8k' },
        { label: '现代都市', desc: '玻璃幕墙', prompt: 'modern city business district, glass skyscrapers, blue sky, busy street, golden hour, cinematic, high detail, 8k' },
        { label: '咖啡店', desc: '温馨室内', prompt: 'cozy modern coffee shop interior, warm lighting, wooden furniture, plants, large windows, cinematic atmosphere, high detail, 8k' },
        { label: '地铁车厢', desc: '都市通勤', prompt: 'modern subway car interior, fluorescent light, passengers, city tunnel view through window, cinematic, high detail, 8k' },
      ],
    },
    {
      category: '奇幻场景',
      items: [
        { label: '魔法森林', desc: '奇幻童话', prompt: 'magical fantasy forest, glowing mushrooms, fairy lights, ancient ruins, mystical atmosphere, cinematic, high detail, 8k' },
        { label: '天空之城', desc: '云端秘境', prompt: 'floating sky city above clouds, grand temples, golden light, waterfalls cascading into sky, cinematic, high detail, 8k' },
        { label: '地下宫殿', desc: '神秘幽深', prompt: 'underground palace hall, glowing crystals, ancient columns, mysterious light, cinematic atmosphere, high detail, 8k' },
      ],
    },
  ],
  prop: [
    {
      category: '传统道具',
      items: [
        { label: '油纸伞', desc: '古典雅致', prompt: 'a traditional chinese paper umbrella, bamboo handle, oil paper, intricate painting, cinematic lighting, high detail, 8k' },
        { label: '笛子', desc: '竹制乐器', prompt: 'a wooden flute (dizi), carved bamboo, traditional chinese instrument, cinematic lighting, high detail, 8k' },
        { label: '古剑', desc: '寒光凛冽', prompt: 'a traditional chinese sword (jian), ornate hilt, polished blade, engraved scabbard, cinematic lighting, high detail, 8k' },
        { label: '茶具', desc: '紫砂陶艺', prompt: 'a traditional chinese tea set, purple clay teapot, small cups, bamboo tray, cinematic lighting, high detail, 8k' },
        { label: '灯笼', desc: '红绸宫灯', prompt: 'a traditional chinese red lantern, golden tassels, ornate frame, warm glow, cinematic lighting, high detail, 8k' },
      ],
    },
    {
      category: '现代道具',
      items: [
        { label: '手机', desc: '智能科技', prompt: 'a modern smartphone, sleek design, glowing screen, minimalist background, cinematic lighting, high detail, 8k' },
        { label: '相机', desc: '复古单反', prompt: 'a vintage style camera, leather strap, metal body, lens cap, cinematic lighting, high detail, 8k' },
        { label: '咖啡杯', desc: '陶瓷温润', prompt: 'a ceramic coffee cup with saucer, latte art, steam rising, wooden table, cinematic lighting, high detail, 8k' },
        { label: '书本', desc: '精装古籍', prompt: 'a hardcover book, leather binding, gold lettering, ribbon bookmark, cinematic lighting, high detail, 8k' },
      ],
    },
    {
      category: '奇幻道具',
      items: [
        { label: '魔法杖', desc: '神秘符文', prompt: 'a magical wizard staff, carved wood, glowing blue crystal on top, runes, mystical aura, cinematic lighting, high detail, 8k' },
        { label: '宝箱', desc: '黄金宝藏', prompt: 'an ornate treasure chest, golden decorations, jewels, open lid with glowing light inside, cinematic lighting, high detail, 8k' },
        { label: '卷轴', desc: '古老文献', prompt: 'an ancient scroll, yellowed parchment, golden seal, calligraphy, wooden case, cinematic lighting, high detail, 8k' },
      ],
    },
  ],
}

// ============ AI 生成提示词配置 ============
// 用户可配置任意 OpenAI 兼容的 LLM 接口来生成提示词
interface LlmConfig {
  base_url: string    // 例如 https://api.openai.com/v1 或 https://dashscope.aliyuncs.com/compatible-mode/v1
  api_key: string
  model: string       // 例如 gpt-4o-mini 或 qwen-plus
}

const DEFAULT_LLM_CONFIG: LlmConfig = {
  base_url: '',
  api_key: '',
  model: '',
}

// 从后端读取 LLM 配置（密钥由服务端 .env 管理，不落浏览器）
async function loadLlmConfig(): Promise<LlmConfig> {
  try {
    const res = await providerApi.getConfig()
    const c = res?.config || {}
    return {
      base_url: c.OPENAI_BASE_URL || '',
      api_key: c.OPENAI_API_KEY || '',
      model: c.OPENAI_TEXT_MODEL || '',
    }
  } catch {
    return { ...DEFAULT_LLM_CONFIG }
  }
}

// 保存 LLM 配置到后端 .env
async function saveLlmConfig(cfg: LlmConfig) {
  await providerApi.saveConfig({
    OPENAI_BASE_URL: cfg.base_url,
    OPENAI_API_KEY: cfg.api_key,
    OPENAI_TEXT_MODEL: cfg.model,
  })
}

// 调用 LLM 生成提示词（OpenAI 兼容协议）
async function generatePromptViaLlm(
  cfg: LlmConfig,
  kind: 'character' | 'scene' | 'prop',
  userDesc: string,
): Promise<string> {
  if (!cfg.base_url || !cfg.api_key || !cfg.model) {
    throw new Error('请先配置 LLM 接口（base_url / api_key / model）')
  }
  const kindLabel = kind === 'character' ? '角色' : kind === 'scene' ? '场景' : '道具'
  const systemPrompt = `你是一位专业的 AI 绘画提示词工程师。根据用户的简短描述，生成一段高质量的英文 Stable Diffusion / ComfyUI 文生图提示词。
要求：
1. 输出纯英文提示词，不要任何解释或前后缀
2. 包含主体描述、服装/材质、表情/动作、环境、光照、画质等要素
3. 末尾加上 ", cinematic lighting, high detail, 8k"
4. 长度 50-100 个英文单词

类型：${kindLabel}
用户描述：${userDesc || '（无，请自由发挥）'}`

  const resp = await fetch(`${cfg.base_url.replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${cfg.api_key}`,
    },
    body: JSON.stringify({
      model: cfg.model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: `请为${kindLabel}生成英文提示词` },
      ],
      temperature: 0.8,
      max_tokens: 300,
    }),
  })
  if (!resp.ok) {
    const errText = await resp.text()
    throw new Error(`LLM 接口错误 ${resp.status}: ${errText.slice(0, 200)}`)
  }
  const data = await resp.json()
  const content = data?.choices?.[0]?.message?.content || ''
  // 去除可能的 markdown 包裹
  return content.replace(/^```[a-z]*\n?/i, '').replace(/\n?```$/i, '').trim()
}

// 4 个动态槽位（除场景外），对应工作流的 LoadImage 节点
// 节点29(2.jpg), 节点40(1.jpg), 节点33(3 (4).png), 节点95(4.png)
interface SlotDef {
  slotId: number            // 1-4
  origFile: string          // 工作流原文件名
  defaultPromptKey: string  // 默认提示词key
}

const DYNAMIC_SLOTS: SlotDef[] = [
  { slotId: 1, origFile: '2.jpg', defaultPromptKey: 'char1' },
  { slotId: 2, origFile: '1.jpg', defaultPromptKey: 'char2' },
  { slotId: 3, origFile: '3 (4).png', defaultPromptKey: 'char3' },
  { slotId: 4, origFile: '4.png', defaultPromptKey: 'char4' },
]

// 场景固定槽位
const SCENE_SLOT = { origFile: 'bg.png', defaultPromptKey: 'scene' }

const ASPECT_RATIOS = [
  { label: '16:9', value: '16:9' },
  { label: '9:16', value: '9:16' },
  { label: '1:1', value: '1:1' },
  { label: '4:3', value: '4:3' },
]

const RESOLUTIONS = [
  { label: '480p', value: '480p' },
  { label: '720p', value: '720p' },
  { label: '1080p', value: '1080p' },
]

// 单个素材状态
interface MaterialState {
  slotId: number                  // 1-4 动态槽位 / 0 场景
  kind: 'character' | 'scene' | 'prop'  // 类型
  mode: 'generate' | 'existing'   // 新生成 / 使用已有
  outputForm: 'concept' | 'angle' | 'pano'  // 输出形态：概念图/三视图/全景图
  prompt: string
  asset_id: string
}

const ASPECT_RATIOS_FOR_CONTENT: Record<string, { w: number; h: number }> = {
  character: { w: 768, h: 1024 },
  scene: { w: 1024, h: 768 },
  prop: { w: 768, h: 768 },
}

export default function OneClickVideoPage() {
  const { currentProjectId } = useProject()
  const { assets, loadAssets } = useDirectorStore()
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [batch, setBatch] = useState<BatchTask | null>(null)
  const [wsProgress, setWsProgress] = useState<WsEvent | null>(null)
  // 成片结果：批量完成后取最终视频资产用于预览/下载
  const [finalVideo, setFinalVideo] = useState<{ url: string; name: string; asset_id: string } | null>(null)
  const [segmentPrompts, setSegmentPrompts] = useState<string[]>(DEFAULT_SEGMENT_PROMPTS)
  // TTS 配音配置
  const [ttsEnabled, setTtsEnabled] = useState(false)
  const [ttsMode, setTtsMode] = useState<'voice_design' | 'voice_clone'>('voice_design')
  const [ttsVoiceDesc, setTtsVoiceDesc] = useState('成年女性，温柔亲切，语速适中，咬字清晰')
  const [ttsRefAudio, setTtsRefAudio] = useState('')
  const [ttsMixMode, setTtsMixMode] = useState<'replace' | 'overlay'>('replace')
  const [ttsVolume, setTtsVolume] = useState(1.0)
  const [bgmUrl, setBgmUrl] = useState('')
  const [bgmVolume, setBgmVolume] = useState(0.2)
  // 每段台词/旁白
  const [segmentTexts, setSegmentTexts] = useState<string[]>(['', '', '', ''])
  // 提示词模板选择器
  const [tplPickerOpen, setTplPickerOpen] = useState(false)
  const [tplPickerIdx, setTplPickerIdx] = useState<number>(-1)  // 当前选择模板的素材索引
  // AI 生成提示词
  const [llmCfgOpen, setLlmCfgOpen] = useState(false)
  const [llmCfg, setLlmCfg] = useState<LlmConfig>({ ...DEFAULT_LLM_CONFIG })
  const [llmCfgDraft, setLlmCfgDraft] = useState<LlmConfig>({ ...DEFAULT_LLM_CONFIG })
  const [aiGenLoadingIdx, setAiGenLoadingIdx] = useState<number>(-1)
  const [aiDescOpen, setAiDescOpen] = useState(false)
  const [aiDescIdx, setAiDescIdx] = useState<number>(-1)
  const [aiDescText, setAiDescText] = useState('')
  // 分镜图直接输入模式（跳过素材生成，直接用已有分镜图做视频）
  const [useStoryboard, setUseStoryboard] = useState(false)
  const [storyboardAssetId, setStoryboardAssetId] = useState<string>('')
  const [storyboardAssets, setStoryboardAssets] = useState<any[]>([])
  const [jsonInputOpen, setJsonInputOpen] = useState(false)
  const [jsonText, setJsonText] = useState('')

  // ===== AI 一键出片（主题 → 剧本 → 填入分故事情节/台词）=====
  const [aiTopic, setAiTopic] = useState('')
  const [aiVideoType, setAiVideoType] = useState('')
  const [videoTypes, setVideoTypes] = useState<Array<{ value: string; label: string }>>([])
  const [aiLoading, setAiLoading] = useState(false)
  const [aiScriptTitle, setAiScriptTitle] = useState('')
  const [aiScriptAssetId, setAiScriptAssetId] = useState('')
  // 剧本生成的封面文案（供发布素材包复用）
  const [scriptCovers, setScriptCovers] = useState<Array<{ title: string; subtitle: string }>>([])
  const [scriptHook, setScriptHook] = useState('')
  // 发布素材包：可编辑的标题/标签/封面副文案（默认取自剧本）
  const [packTitle, setPackTitle] = useState('')
  const [packTags, setPackTags] = useState('')
  const [packSubtitle, setPackSubtitle] = useState('')
  const [coverDataUrl, setCoverDataUrl] = useState('')
  const [packBundling, setPackBundling] = useState(false)
  // 首次拿到封面文案时预填发布素材包的默认值
  useEffect(() => {
    if (!scriptCovers.length) return
    const c0 = scriptCovers[0]
    if (c0.title && !packTitle) setPackTitle(c0.title)
    if (c0.subtitle && !packSubtitle) setPackSubtitle(c0.subtitle)
    if (!packTags) setPackTags((scriptCovers.map(c => c.title).filter(Boolean).slice(0, 3)).join(','))
  }, [scriptCovers])

  // 加载视频类型选项
  useEffect(() => {
    scriptApi.listVideoTypes().then(res => {
      setVideoTypes((res.video_types || []).map(vt => ({ value: vt.value, label: vt.label })))
    }).catch(() => {/* 静默 */})
  }, [])

  // 主题直出：异步生成剧本，轮询完成并填回分段故事情节/台词
  const handleAiGenerate = async () => {
    if (!aiTopic.trim()) { message.warning('请输入主题词，才能 AI 生成剧本'); return }
    if (!aiVideoType) { message.warning('请选择视频类型'); return }
    setAiLoading(true)
    message.loading({ content: 'AI 生成剧本中…', key: 'aiscr', duration: 0 })
    const pollRef: { current: number | null } = { current: null }
    try {
      const res = await scriptApi.generate({
        topic: aiTopic.trim(),
        video_type: aiVideoType,
        acts: 3,
        duration_seconds: 30,
        hook_style: 'comment_1',
      })
      const taskId = res.task_id
      // 轮询直到完成
      for (let i = 0; i < 120; i++) {
        const t = await stageApi.getTask(taskId)
        if (t.status === 'completed' && t.success && t.asset) {
          const targetAssetId = t.asset.asset_id || (typeof t.asset === 'string' ? t.asset : '')
          if (!targetAssetId) { message.success('剧本已生成（无资产）', 2); break }
          const sd = await scriptApi.getScript(targetAssetId)
          const script = sd.script
          fillScriptIntoPage(script)
          setAiScriptAssetId(targetAssetId)
          message.success({ content: `剧本「${script.title || 'AI成片'}」已生成并填入`, key: 'aiscr' })
          break
        }
        if (t.status === 'failed') { message.error({ content: `剧本生成失败: ${t.error || ''}`, key: 'aiscr' }); break }
        await new Promise(r2 => setTimeout(r2, 2000))
      }
    } catch (e: any) {
      message.error({ content: `剧本生成异常: ${e.message}`, key: 'aiscr' })
    } finally {
      if (pollRef.current) clearInterval(pollRef.current)
      setAiLoading(false)
    }
  }

  // 把剧本 JSON 映射进本页分段故事情节(segmentPrompts)+台词(segmentTexts)+封面文案
  const fillScriptIntoPage = (script: any) => {
    const acts: any[] = script.acts || []
    const n = Math.max(1, acts.length)
    // 让分段数与剧本幕数对齐：计算 segment_seconds 使 duration/seg = n
    const cur = form.getFieldsValue() as any
    const dur = Number(cur.duration) || 30
    const sat = Math.max(1, n)
    form.setFieldsValue({ segment_seconds: Math.round(dur / sat) })
    // 故事情节 ← narration / scene；台词 ← tts_texts
    const newSegPrompts = acts.map(a => (a as any).narration || (a as any).scene || '')
    const newSegTexts = acts.map(a => Array.isArray((a as any).tts_texts) ? (a as any).tts_texts.join(' ') : '')
    setSegmentPrompts(newSegPrompts.length ? newSegPrompts : DEFAULT_SEGMENT_PROMPTS)
    setSegmentTexts(newSegTexts.length ? newSegTexts : ['', '', '', ''])
    // 封面文案
    setScriptCovers(Array.isArray(script.covers) ? script.covers.map((c: any) => ({ title: c.title || '', subtitle: c.subtitle || '' })) : [])
    setScriptHook(script.hook || '')
    setAiScriptTitle(script.title || '')
  }

  // ===== 发布素材包（封面图 + 标题/标签/文案 + 成片，浏览器打包下载）=====
  // 构建发布文案：标题 + 分段亮点 + 结尾钩子 + 标签
  const buildPackCopy = () => {
    const title = packTitle.trim() || '未命名成片'
    const subtitles = scriptCovers.map(c => c.subtitle).filter(Boolean)
    const tags = packTags.split(/[,，\s]+/).filter(Boolean).map(t => `#${t.replace(/^#/, '')}`)
    const lines = [
      `【成片标题】`,
      title,
      '',
      `【内容亮点】`,
      subtitles.length ? subtitles.map((s, i) => `${i + 1}. ${s}`).join('\n') : '无',
      '',
      `【结尾钩子】`,
      scriptHook || '评论区扣1领工具',
      '',
      `【推荐标签】`,
      tags.join(' ') || '#短视频',
      '',
    ]
    return lines.join('\n')
  }

  // 用 canvas 生成竖版封面图（1080x1920，9:16），返回 dataURL
  const renderCover = (): Promise<string> => {
    return new Promise((resolve) => {
      const w = 1080, h = 1920
      const canvas = document.createElement('canvas')
      canvas.width = w; canvas.height = h
      const ctx = canvas.getContext('2d')
      if (!ctx) { resolve(''); return }
      // 深色渐变底 + 顶部标签 + 居中主标题 + 副标题
      const t = ctx.createLinearGradient(0, 0, 0, h)
      t.addColorStop(0, '#1a1a2e'); t.addColorStop(0.55, '#16213e'); t.addColorStop(1, '#0f3460')
      ctx.fillStyle = t; ctx.fillRect(0, 0, w, h)
      // 装饰圆环
      ctx.strokeStyle = 'rgba(255,255,255,0.12)'; ctx.lineWidth = 2
      ctx.beginPath(); ctx.arc(w / 2, h * 0.40, 260, 0, Math.PI * 2); ctx.stroke()
      // 顶部小标签
      const title = packTitle.trim() || 'AI 成片'
      const subtitle = packSubtitle.trim() || ''
      ctx.fillStyle = 'rgba(255,255,255,0.6)'; ctx.font = 'bold 44px "Microsoft YaHei", sans-serif'; ctx.textAlign = 'center'
      ctx.fillText('· 短 视 频 成 片 ·', w / 2, 150)
      // 主标题（自动换行，最多两行）
      ctx.font = 'bold 96px "Microsoft YaHei", sans-serif'
      ctx.fillStyle = '#ffffff'
      const lines = wrapText(ctx, title, w * 0.82)
      lines.slice(0, 2).forEach((line, idx) => {
        ctx.fillText(line, w / 2, 600 + idx * 130)
      })
      // 副标题
      ctx.font = '56px "Microsoft YaHei", sans-serif'
      ctx.fillStyle = 'rgba(255,255,255,0.85)'
      ctx.fillText(subtitle || '私信领取完整工具教程', w / 2, 900)
      // 底部钩子
      ctx.font = 'bold 48px "Microsoft YaHei", sans-serif'
      ctx.fillStyle = '#ffd166'
      ctx.fillText(scriptHook || '评论区扣1领工具', w / 2, h - 220)
      resolve(canvas.toDataURL('image/png'))
    })
  }

  // 生成封面并显示预览
  const handlePreviewCover = async () => {
    const du = await renderCover()
    setCoverDataUrl(du)
    if (!du) message.warning('封面生成失败（当前浏览器不支持 canvas）')
  }

  // 通用文本下载
  const downloadText = (name: string, content: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = name
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  }

  // 下载封面 PNG
  const handleDownloadCover = async () => {
    const du = coverDataUrl || await renderCover()
    if (!du) { message.warning('请先点击「生成封面图」'); return }
    const a = document.createElement('a')
    a.href = du; a.download = `封面_${(packTitle || '成片').replace(/[\\/:*?"<>|]/g, '_')}.png`
    a.click()
  }

  // 下载发布文案 txt
  const handleDownloadCopy = () => {
    downloadText(`发布文案_${(packTitle || '成片').replace(/[\\/:*?"<>|]/g, '_')}.txt`, buildPackCopy())
  }

  // 打包下载 zip（封面图 + 发布文案 + 成片视频，视频尽力内嵌）
  const handleDownloadZip = async () => {
    const zip = new JSZip()
    setPackBundling(true)
    try {
      // 1. 封面图
      const du = coverDataUrl || await renderCover()
      if (du) {
        const base64 = du.split(',')[1]
        zip.file('01_封面.png', base64, { base64: true })
      }
      // 2. 发布文案
      zip.file('02_发布文案.txt', buildPackCopy())
      // 3. 成片视频（尽力内嵌，CORS 失败则跳过）
      let videoEmbedded = false
      if (finalVideo?.url) {
        try {
          const resp = await fetch(finalVideo.url)
          if (resp.ok) {
            const buf = await resp.arrayBuffer()
            zip.file(`03_成片_${finalVideo.name || 'video'}.mp4`, buf)
            videoEmbedded = true
          }
        } catch { /* CORS 或网络不可达：跳过视频内嵌 */ }
      }
      const blob = await zip.generateAsync({ type: 'blob' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `发布素材包_${(packTitle || '成片').replace(/[\\/:*?"<>|]/g, '_')}.zip`
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 1000)
      message.success(videoEmbedded ? '发布素材包已打包下载' : '素材包已生成（成片较大或跨域，请单独下载成片）')
    } catch (e: any) {
      message.error(`打包失败: ${e.message || e}`)
    } finally {
      setPackBundling(false)
    }
  }

  // ===== 成片后期（全链路：字幕→钩子→平台导出）=====
  const [postEnabled, setPostEnabled] = useState(true)          // 是否启用后期阶段
  const [subEnabled, setSubEnabled] = useState(true)            // 字幕烧录
  const [subKeywords, setSubKeywords] = useState('')            // 字幕高亮关键词(逗号分隔)
  const [hookEnabled, setHookEnabled] = useState(true)          // 结尾钩子引导框
  const [hookText, setHookText] = useState('评论区扣1领工具')     // 钩子主文案
  const [hookSubText, setHookSubText] = useState('免费链接私发你') // 钩子副文案
  const [exportEnabled, setExportEnabled] = useState(true)      // 平台规格导出
  const [exportPlatform, setExportPlatform] = useState('抖音')    // 导出平台规格
  // 平台导出规格映射
  const PLATFORM_EXPORT_SPECS: Record<string, { resolution: string; desc: string }> = {
    抖音: { resolution: '1080x1920', desc: '9:16 全屏' },
    快手: { resolution: '1080x1920', desc: '9:16 全屏' },
    视频号: { resolution: '1080x1920', desc: '9:16 全屏' },
    小红书: { resolution: '1080x1440', desc: '3:4 信息流' },
  }

  // 动态素材列表：默认2角色（三视图）+1场景（全景图）
  const [materials, setMaterials] = useState<MaterialState[]>([
    { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
    { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
    { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
  ])

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const wsRef = useRef<BatchWebSocket | null>(null)

  useEffect(() => {
    if (currentProjectId) loadAssets({ project_id: currentProjectId })
  }, [currentProjectId, loadAssets])

  // 从后端加载 LLM 配置（密钥由服务端管理）
  useEffect(() => {
    loadLlmConfig().then(cfg => {
      setLlmCfg(cfg)
      setLlmCfgDraft(cfg)
    })
  }, [])

  // 加载分镜图资产（asset_type=storyboard 或 multi_view）
  useEffect(() => {
    if (!useStoryboard) return
    loadAssets({ asset_type: 'storyboard', project_id: currentProjectId || undefined })
      .then(() => {
        const sb = assets.filter(a =>
          a.asset_type === 'storyboard' ||
          a.asset_type === 'multi_view' ||
          a.content_type === 'storyboard'
        )
        setStoryboardAssets(sb)
      })
      .catch(() => {})
  }, [useStoryboard, currentProjectId])

  // 按类型分组的资产
  const assetsByType = useMemo(() => {
    const m: Record<string, typeof assets> = { character: [], scene: [], prop: [] }
    assets.forEach(a => {
      const t = a.content_type
      if (t && m[t]) m[t].push(a)
    })
    return m
  }, [assets])

  // 实时计算分镜图列表
  const storyboardList = useMemo(() => {
    return assets.filter(a =>
      a.asset_type === 'storyboard' ||
      a.asset_type === 'multi_view' ||
      a.content_type === 'storyboard'
    )
  }, [assets])

  // 统计：当前角色数、道具数
  const stats = useMemo(() => {
    let chars = 0, props = 0, scenes = 0
    materials.forEach(m => {
      if (m.kind === 'character') chars++
      else if (m.kind === 'prop') props++
      else if (m.kind === 'scene') scenes++
    })
    return { chars, props, scenes }
  }, [materials])

  // 已使用的槽位ID
  const usedSlotIds = useMemo(() => new Set(materials.map(m => m.slotId)), [materials])

  // 下一个可用的动态槽位
  const nextAvailableSlot = useMemo(() => {
    return DYNAMIC_SLOTS.find(s => !usedSlotIds.has(s.slotId))
  }, [usedSlotIds])

  // 更新某个素材
  const updateMaterial = (idx: number, patch: Partial<MaterialState>) => {
    setMaterials(prev => prev.map((m, i) => i === idx ? { ...m, ...patch } : m))
  }

  // 添加角色
  const addCharacter = () => {
    if (!nextAvailableSlot) {
      message.warning('已达到最大槽位数（4个动态素材）')
      return
    }
    if (stats.chars >= 4) {
      message.warning('最多 4 个角色')
      return
    }
    const promptKey = `char${stats.chars + 1}`
    setMaterials(prev => [...prev, {
      slotId: nextAvailableSlot.slotId,
      kind: 'character',
      mode: 'generate',
      outputForm: 'angle',  // 默认三视图
      prompt: (DEFAULT_PROMPTS as any)[promptKey] || '',
      asset_id: '',
    }])
  }

  // 添加道具
  const addProp = () => {
    if (!nextAvailableSlot) {
      message.warning('已达到最大槽位数（4个动态素材）')
      return
    }
    const promptKey = `prop${stats.props + 1}`
    setMaterials(prev => [...prev, {
      slotId: nextAvailableSlot.slotId,
      kind: 'prop',
      mode: 'generate',
      outputForm: 'concept',  // 道具默认概念图
      prompt: (DEFAULT_PROMPTS as any)[promptKey] || '',
      asset_id: '',
    }])
  }

  // 删除某个素材
  const removeMaterial = (idx: number) => {
    if (materials[idx].kind === 'scene') {
      message.warning('场景必须保留')
      return
    }
    setMaterials(prev => prev.filter((_, i) => i !== idx))
  }

  // 轮询批量任务进度
  const startPolling = (batchId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const resp = await batchService.get(batchId)
        const b = resp.batch || resp
        setBatch(b)
        if (b.status === 'completed' || b.status === 'failed' || b.status === 'cancelled') {
          if (pollRef.current) clearInterval(pollRef.current)
          if (wsRef.current) wsRef.current.close()
        }
      } catch (e) { /* ignore */ }
    }, 3000)
  }

  const startWs = (batchId: string) => {
    if (wsRef.current) wsRef.current.close()
    const ws = new BatchWebSocket(batchId)
    ws.onEvent((evt: WsEvent) => setWsProgress(evt))
    ws.connect()
    wsRef.current = ws
  }

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (wsRef.current) wsRef.current.close()
    }
  }, [])

  // 批量全部完成后，取最终输出视频资产用于预览/下载
  useEffect(() => {
    if (!batch || batch.status !== 'completed' || finalVideo) return
    const doneSteps = (batch.steps || []).filter(s => s.status === 'completed' && s.output_asset_id)
    const lastStep = doneSteps[doneSteps.length - 1]
    const targetId = lastStep?.output_asset_id
    if (!targetId) return
    ;(async () => {
      try {
        const resp = await assetApi.get(targetId)
        const a = resp.asset || resp
        const url = a.metadata?.video_url || (a.urls || [])[0]
        if (url) setFinalVideo({ url, name: a.name || '成片', asset_id: a.asset_id || targetId })
      } catch (e) { /* 忽略预览加载失败 */ }
    })()
  }, [batch, finalVideo])

  // 构造 TTS 参数（如果启用）
  const buildTtsParams = (segCount: number) => {
    if (!ttsEnabled) return {}
    const texts = segmentTexts.slice(0, segCount)
    // 补齐到 segCount 长度
    while (texts.length < segCount) texts.push('')
    const params: any = {
      tts_enabled: true,
      tts_texts: texts,
      tts_mode: ttsMode,
      tts_mix_mode: ttsMixMode,
      tts_volume: ttsVolume,
      bgm_url: bgmUrl,
      bgm_volume: bgmVolume,
    }
    if (ttsMode === 'voice_design') {
      params.tts_voice_desc = ttsVoiceDesc
    } else {
      params.tts_ref_audio = ttsRefAudio
    }
    return params
  }

  // 追加成片后期阶段（可选：字幕 → 钩子 → 平台导出）
  const buildPostStages = (steps: BatchStep[], videoStepId: string, subtitleTexts: string[]): BatchStep[] => {
    if (!postEnabled) return steps
    let lastId = videoStepId
    // 字幕烧录：将 TTS 台词作为字幕时间轴
    if (subEnabled && subtitleTexts.length > 0) {
      steps.push({
        step_id: 's_subtitle',
        stage_id: 'subtitle',
        name: '字幕烧录',
        provider_id: 'local',
        params: {
          subtitle_texts: subtitleTexts.map((t, i) => ({ text: t, start: i * 5, end: (i + 1) * 5 })),
          keywords: subKeywords.split(/[,，]/).map(s => s.trim()).filter(Boolean),
          margin_v: '0.13',
        },
        input_asset_ids: [],
        input_from_steps: [lastId],
      })
      lastId = 's_subtitle'
    }
    // 结尾钩子引导框
    if (hookEnabled) {
      steps.push({
        step_id: 's_hook',
        stage_id: 'hook_overlay',
        name: '结尾钩子引导框',
        provider_id: 'local',
        params: {
          hook_text: hookText,
          sub_text: hookSubText,
          duration: 4,
          position: 'bottom',
          margin: null as any, // 使用默认安全边距 10%
        },
        input_asset_ids: [],
        input_from_steps: [lastId],
      })
      lastId = 's_hook'
    }
    // 平台规格导出
    if (exportEnabled) {
      const spec = PLATFORM_EXPORT_SPECS[exportPlatform] || PLATFORM_EXPORT_SPECS.抖音
      steps.push({
        step_id: 's_export',
        stage_id: 'export',
        name: `导出成片 ${exportPlatform}规格 (${spec.resolution})`,
        provider_id: 'local',
        params: {
          resolution: spec.resolution,
          format: 'mp4',
          codec: 'libx264',
          bitrate: '8M',
          name: `成片_${exportPlatform}_${spec.resolution}`,
        },
        input_asset_ids: [],
        input_from_steps: [lastId],
      })
      lastId = 's_export'
    }
    return steps
  }

  // 构建批量任务步骤
  // 模式 A（分镜图直接输入）：仅 1 个 video 步骤，输入为已有分镜图资产
  // 模式 B（正常多素材）：DAG 3 层 concept → angle/pano → video
  const buildSteps = (values: any): BatchStep[] => {
    const segCount = Math.floor(values.duration / values.segment_seconds) || 4
    const segs = segmentPrompts.slice(0, segCount)
    const steps: BatchStep[] = []

    // ===== 模式 A：分镜图直接输入 =====
    if (useStoryboard && storyboardAssetId) {
      steps.push({
        step_id: 's_video',
        stage_id: 'video',
        name: '基于分镜图生成长视频（分段+拼接）',
        provider_id: 'comfyui',
        params: {
          prompt: values.video_prompt,
          duration: values.duration,
          aspect_ratio: values.aspect_ratio,
          resolution: values.resolution,
          model: values.model,
          frame_rate: values.frame_rate,
          width: values.width,
          height: values.height,
          segment_seconds: values.segment_seconds,
          segment_prompts: segs,
          ...buildTtsParams(segCount),
        },
        input_asset_ids: [storyboardAssetId],
        input_from_steps: [],
      })
      return buildPostStages(steps, 's_video', segmentTexts.filter(t => t && t.trim()))
    }

    // ===== 模式 B：正常多素材生成 =====
    const videoDeps: string[] = []
    const reference_image_files: string[] = []
    const input_asset_ids_existing: string[] = []

    materials.forEach((m, idx) => {
      const origFile = m.slotId === 0
        ? SCENE_SLOT.origFile
        : DYNAMIC_SLOTS.find(s => s.slotId === m.slotId)?.origFile || ''
      reference_image_files.push(origFile)

      if (m.mode === 'existing') {
        // 使用已有资产：直接作为 video 步骤输入
        if (m.asset_id) input_asset_ids_existing.push(m.asset_id)
        return
      }

      // 新生成模式
      const dims = ASPECT_RATIOS_FOR_CONTENT[m.kind]
      const conceptStepId = `s${idx + 1}_concept_${m.kind}${m.slotId}`
      const kindLabel = m.kind === 'character' ? '角色' : m.kind === 'scene' ? '场景' : '道具'
      const slotLabel = m.slotId === 0 ? '' : ` ${m.slotId}`

      // Layer 0: concept 步骤
      steps.push({
        step_id: conceptStepId,
        stage_id: 'concept',
        name: `生成${kindLabel}${slotLabel}概念图`,
        provider_id: 'comfyui',
        params: {
          prompt: m.prompt,
          negative_prompt: 'low quality, blurry, deformed, ugly',
          content_type: m.kind,
          width: dims.w,
          height: dims.h,
        },
        input_asset_ids: [],
        input_from_steps: [],
      })

      // Layer 1: 根据 outputForm 决定是否追加 angle/pano 步骤
      if (m.outputForm === 'angle' && m.kind === 'character') {
        const angleStepId = `s${idx + 1}_angle_${m.slotId}`
        steps.push({
          step_id: angleStepId,
          stage_id: 'angle',
          name: `生成${kindLabel}${slotLabel}三视图`,
          provider_id: 'comfyui',
          params: {
            prompt: `Multi-angle views of: ${m.prompt}`,
            seed: 0,
          },
          input_asset_ids: [],
          input_from_steps: [conceptStepId],
        })
        videoDeps.push(angleStepId)
      } else if (m.outputForm === 'pano' && m.kind === 'scene') {
        const panoStepId = `s${idx + 1}_pano`
        steps.push({
          step_id: panoStepId,
          stage_id: 'pano',
          name: `生成${kindLabel}${slotLabel}全景图`,
          provider_id: 'comfyui',
          params: {
            prompt: `360 degree panoramic view: ${m.prompt}`,
            size: '2048x1024',
            template: 'panorama',
          },
          input_asset_ids: [],
          input_from_steps: [conceptStepId],
        })
        videoDeps.push(panoStepId)
      } else {
        // 直接用 concept 作为 video 输入
        videoDeps.push(conceptStepId)
      }
    })

    // Layer 2: video 步骤
    steps.push({
      step_id: 's_video',
      stage_id: 'video',
      name: '基于多素材生成长视频（分段+拼接）',
      provider_id: 'comfyui',
      params: {
        prompt: values.video_prompt,
        duration: values.duration,
        aspect_ratio: values.aspect_ratio,
        resolution: values.resolution,
        model: values.model,
        frame_rate: values.frame_rate,
        width: values.width,
        height: values.height,
        segment_seconds: values.segment_seconds,
        reference_image_files,
        segment_prompts: segs,
        ...buildTtsParams(segCount),
      },
      input_asset_ids: input_asset_ids_existing,
      input_from_steps: videoDeps,
    })

    return buildPostStages(steps, 's_video', segmentTexts.filter(t => t && t.trim()))
  }

  const handleSubmit = async () => {
    if (!currentProjectId) {
      message.warning('请先选择项目')
      return
    }
    // 分镜图模式校验
    if (useStoryboard) {
      if (!storyboardAssetId) {
        message.warning('请选择已有的分镜图资产')
        return
      }
    } else {
      // 正常模式校验
      for (let i = 0; i < materials.length; i++) {
        const m = materials[i]
        if (m.mode === 'existing' && !m.asset_id) {
          message.warning(`第 ${i + 1} 个素材：选择了"使用已有"但未选择资产`)
          return
        }
        if (m.mode === 'generate' && !m.prompt.trim()) {
          message.warning(`第 ${i + 1} 个素材：提示词不能为空`)
          return
        }
      }
    }
    // TTS 校验
    if (ttsEnabled) {
      const hasText = segmentTexts.some(t => t && t.trim())
      if (!hasText) {
        message.warning('启用了 TTS 但所有段台词为空，请至少为一段输入台词')
        return
      }
      if (ttsMode === 'voice_clone' && !ttsRefAudio.trim()) {
        message.warning('音色克隆模式必须提供参考音频 URL')
        return
      }
      if (ttsMode === 'voice_design' && !ttsVoiceDesc.trim()) {
        message.warning('音色设计模式必须提供音色描述')
        return
      }
    }
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      const steps = buildSteps(values)
      const segCount = steps[steps.length - 1].params!.segment_prompts!.length
      const createResp = await batchService.create({
        name: `一键成片-${values.title || '多角色长视频'}`,
        project_id: currentProjectId,
        stop_on_failure: true,
        auto_inherit_project: true,
        steps,
      })
      const batchId = createResp.batch?.batch_id || createResp.batch_id
      message.success(`批量任务已创建 | ID: ${batchId} | 角色:${stats.chars} 道具:${stats.props} 场景:${stats.scenes} | 分段:${segCount}`)
      await batchService.start(batchId, { use_dag: true })
      message.success('已启动执行（DAG 引擎）')
      setBatch({ ...createResp.batch, batch_id: batchId, status: 'running' } as BatchTask)
      startPolling(batchId)
      startWs(batchId)
    } catch (e: any) {
      message.error(e?.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleReset = () => {
    form.resetFields()
    setSegmentPrompts([...DEFAULT_SEGMENT_PROMPTS])
    setMaterials([
      { slotId: 1, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char1, asset_id: '' },
      { slotId: 2, kind: 'character', mode: 'generate', outputForm: 'angle', prompt: DEFAULT_PROMPTS.char2, asset_id: '' },
      { slotId: 0, kind: 'scene', mode: 'generate', outputForm: 'pano', prompt: DEFAULT_PROMPTS.scene, asset_id: '' },
    ])
    setBatch(null)
    setWsProgress(null)
    setFinalVideo(null)
    message.info('已重置为默认参数')
  }

  // 应用预设模板：覆盖视频参数/视频提示词/分段提示词/素材列表
  // 用户后续只需修改提示词即可
  const applyPreset = (tpl: PresetTemplate) => {
    form.setFieldsValue({
      ...tpl.videoParams,
      video_prompt: tpl.videoPrompt,
      title: tpl.label,
    })
    setSegmentPrompts([...tpl.segmentPrompts])
    setMaterials(tpl.materials.map(m => ({ ...m })))
    setBatch(null)
    setWsProgress(null)
    message.success(`已应用模板：${tpl.label}（${tpl.desc}）`)
  }

  // 打开提示词模板选择器
  const openTplPicker = (idx: number) => {
    setTplPickerIdx(idx)
    setTplPickerOpen(true)
  }
  // 应用提示词模板
  const applyPromptTemplate = (tpl: PromptTemplate) => {
    if (tplPickerIdx >= 0 && tplPickerIdx < materials.length) {
      updateMaterial(tplPickerIdx, { prompt: tpl.prompt })
      message.success(`已应用模板：${tpl.label}`)
    }
    setTplPickerOpen(false)
  }

  // 打开 AI 生成描述输入框
  const openAiDesc = (idx: number) => {
    if (!llmCfg.base_url || !llmCfg.api_key || !llmCfg.model) {
      setLlmCfgDraft({ ...llmCfg })
      setLlmCfgOpen(true)
      message.warning('请先配置 LLM 接口')
      return
    }
    setAiDescIdx(idx)
    setAiDescText(materials[idx]?.prompt || '')
    setAiDescOpen(true)
  }
  // 执行 AI 生成
  const runAiGenerate = async () => {
    if (aiDescIdx < 0) return
    const m = materials[aiDescIdx]
    if (!m) return
    setAiGenLoadingIdx(aiDescIdx)
    try {
      const newPrompt = await generatePromptViaLlm(llmCfg, m.kind, aiDescText)
      updateMaterial(aiDescIdx, { prompt: newPrompt })
      message.success('AI 生成提示词完成')
      setAiDescOpen(false)
    } catch (e: any) {
      message.error(`AI 生成失败：${e?.message || e}`)
    } finally {
      setAiGenLoadingIdx(-1)
    }
  }
  // 保存 LLM 配置
  const saveLlmCfg = async () => {
    try {
      await saveLlmConfig(llmCfgDraft)
      setLlmCfg({ ...llmCfgDraft })
      setLlmCfgOpen(false)
      message.success('LLM 配置已保存到服务端')
    } catch {
      message.error('LLM 配置保存失败')
    }
  }

  // 导入提示词 JSON
  const handleImportJson = () => {
    try {
      const parsed = JSON.parse(jsonText)
      const segs = Array.isArray(parsed.segment_prompts) ? parsed.segment_prompts : null
      if (segs) setSegmentPrompts(segs)
      // 支持两种格式：
      // 1. {materials: [{kind, prompt, ...}]} 完整覆盖
      // 2. {char1, char2, scene, ...} 按key匹配
      if (Array.isArray(parsed.materials)) {
        const next: MaterialState[] = parsed.materials.map((m: any, idx: number) => ({
          slotId: m.slotId ?? (idx + 1),
          kind: m.kind || 'character',
          mode: m.mode || 'generate',
          outputForm: m.outputForm || (m.kind === 'scene' ? 'pano' : m.kind === 'character' ? 'angle' : 'concept'),
          prompt: m.prompt || '',
          asset_id: m.asset_id || '',
        }))
        setMaterials(next)
      } else {
        // 按 key 匹配
        const next = materials.map(m => {
          const key = m.kind === 'scene' ? 'scene' : `${m.kind}${m.slotId}`
          const v = parsed[key] || parsed[`${m.kind}${m.slotId}_prompt`]
          return v ? { ...m, prompt: v } : m
        })
        setMaterials(next)
      }
      if (parsed.video_prompt) form.setFieldValue('video_prompt', parsed.video_prompt)
      if (parsed.title) form.setFieldValue('title', parsed.title)
      message.success('JSON 已导入')
      setJsonInputOpen(false)
      setJsonText('')
    } catch (e: any) {
      message.error('JSON 格式错误: ' + e.message)
    }
  }

  // 导出当前配置为JSON
  const handleExportJson = () => {
    const values = form.getFieldsValue()
    const exportData = {
      title: values.title,
      video_prompt: values.video_prompt,
      materials: materials.map(m => ({
        slotId: m.slotId,
        kind: m.kind,
        mode: m.mode,
        outputForm: m.outputForm,
        prompt: m.prompt,
        asset_id: m.asset_id,
      })),
      segment_prompts: segmentPrompts,
      video_params: {
        duration: values.duration,
        segment_seconds: values.segment_seconds,
        frame_rate: values.frame_rate,
        width: values.width,
        height: values.height,
        aspect_ratio: values.aspect_ratio,
        resolution: values.resolution,
        model: values.model,
      },
    }
    setJsonText(JSON.stringify(exportData, null, 2))
    setJsonInputOpen(true)
  }

  const isRunning = batch?.status === 'running'

  // 槽位 → 工作流原文件名映射
  const getOrigFile = (slotId: number) => {
    if (slotId === 0) return SCENE_SLOT.origFile
    return DYNAMIC_SLOTS.find(s => s.slotId === slotId)?.origFile || ''
  }

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }} align="center">
        <Space>
          <ThunderboltOutlined style={{ fontSize: 28, color: '#1677ff' }} />
          <Title level={3} style={{ margin: 0 }}>一键成片</Title>
          <Tag color="blue">多步 DAG</Tag>
          <Tag color="purple">{stats.chars} 角色</Tag>
          <Tag color="cyan">{stats.props} 道具</Tag>
          <Tag color="green">{stats.scenes} 场景</Tag>
          <Tag color="orange">分段故事</Tag>
        </Space>
        <ProjectSelector />
      </Space>

      <Alert
        type="info"
        showIcon
        icon={<InfoCircleOutlined />}
        style={{ marginBottom: 16 }}
        message="一键成片工作流（动态多角色 + 三视图 + 全景图）"
        description={
          <span>
            可配置 <b>1-4 个角色</b>（默认三视图）+ <b>0-2 个道具</b> + <b>1 个场景</b>（默认全景图）。
            DAG 流程：<b>Layer 0</b> 概念图 → <b>Layer 1</b> 三视图/全景图 → <b>Layer 2</b> LTX-2.3 长视频分段拼接。
            5 个 LoadImage 节点对应 5 个素材槽位（2.jpg/1.jpg/3 (4).png/4.png/bg.png），每段独立故事情节。
          </span>
        }
      />

      {/* AI 一键出片：主题直出剧本并自动填回分段故事情节/台词 */}
      <Card
        variant="outlined"
        style={{ marginBottom: 16 }}
        title={<Space><ThunderboltOutlined style={{ color: '#fa8c16' }} /> AI 一键出片（主题直出）</Space>}
        extra={aiScriptTitle && <Tag color="green">已填入：{aiScriptTitle}</Tag>}
      >
        <Row gutter={12} align="middle">
          <Col xs={24} sm={7}>
            <Input
              addonBefore="主题"
              placeholder="例如：批量重命名工具-古今穿越剧"
              value={aiTopic}
              onChange={e => setAiTopic(e.target.value)}
              onPressEnter={handleAiGenerate}
            />
          </Col>
          <Col xs={24} sm={8}>
            <Select
              placeholder="选择视频类型"
              style={{ width: '100%' }}
              value={aiVideoType}
              onChange={setAiVideoType}
              options={videoTypes.map(vt => ({ value: vt.value, label: vt.label }))}
              showSearch
              optionFilterProp="label"
            />
          </Col>
          <Col xs={24} sm={9}>
            <Space>
              <Button
                type="primary"
                icon={<FileTextOutlined />}
                loading={aiLoading}
                onClick={handleAiGenerate}
              >生成剧本并填入</Button>
              {aiScriptAssetId && (
                <Text type="secondary" style={{ fontSize: 12 }}>剧本资产 {aiScriptAssetId.slice(0, 8)}</Text>
              )}
            </Space>
          </Col>
        </Row>
        <Paragraph type="secondary" style={{ marginBottom: 0, marginTop: 8, fontSize: 12 }}>
          输入主题后点击生成：自动产出 3 幕剧本，并把每幕的故事情节、TTS 台词、封面文案自动填入下方表单，随后即可一键跑完整条成片管线。
        </Paragraph>
      </Card>

      {/* 预设模板快速选择 */}
      <Card
        size="small"
        title={<Space><StarOutlined />预设模板（一键加载全套参数）</Space>}
        style={{ marginBottom: 16, background: '#fafafa' }}
        extra={<Text type="secondary" style={{ fontSize: 12 }}>点击后仅修改提示词即可</Text>}
      >
        <Space wrap>
          {PRESET_TEMPLATES.map(tpl => (
            <Tooltip key={tpl.key} title={tpl.desc}>
              <Button
                size="middle"
                icon={<ThunderboltOutlined />}
                onClick={() => applyPreset(tpl)}
              >
                {tpl.label}
              </Button>
            </Tooltip>
          ))}
        </Space>
      </Card>

      <Card
        title={
          <Space>
            <FileTextOutlined />
            <span>素材配置</span>
            <Tooltip title="工作流共有5个LoadImage节点：4个动态槽位（可分配为角色或道具）+ 1个场景槽位">
              <InfoCircleOutlined style={{ color: '#1677ff' }} />
            </Tooltip>
          </Space>
        }
        extra={
          <Space>
            <Tooltip title="开启后跳过角色/场景/道具生成，直接用已有分镜图做视频">
              <Text type="secondary" style={{ fontSize: 12 }}>分镜图直通</Text>
              <Switch
                size="small"
                checked={useStoryboard}
                onChange={setUseStoryboard}
              />
            </Tooltip>
            {!useStoryboard && (
              <>
                <Button size="small" icon={<PlusOutlined />} onClick={addCharacter} disabled={stats.chars >= 4 || !nextAvailableSlot}>
                  添加角色
                </Button>
                <Button size="small" icon={<PlusOutlined />} onClick={addProp} disabled={!nextAvailableSlot}>
                  添加道具
                </Button>
              </>
            )}
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        {/* 分镜图直通模式 */}
        {useStoryboard ? (
          <div>
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 12 }}
              message="分镜图直通模式"
              description="跳过角色/场景/道具概念图、三视图、全景图生成，直接用已有分镜图作为视频输入。DAG 仅含 1 个 video 步骤。"
            />
            <Form.Item label="选择已有分镜图" required>
              <Select
                style={{ width: '100%' }}
                placeholder="选择分镜图 / 三视图 / 多视角资产"
                value={storyboardAssetId || undefined}
                onChange={setStoryboardAssetId}
                showSearch
                optionFilterProp="label"
                options={storyboardList.map(a => ({
                  value: a.asset_id,
                  label: (
                    <Space>
                      {a.urls?.[0] && (
                        <img src={a.urls[0]} alt={a.name} style={{ width: 40, height: 40, objectFit: 'cover', borderRadius: 4 }} />
                      )}
                      <span>{a.name} ({a.asset_type})</span>
                    </Space>
                  ),
                }))}
                notFoundContent={
                  <Empty
                    description="没有分镜图资产。请先在其他页面生成分镜图，或关闭此模式。"
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                  />
                }
              />
            </Form.Item>
            {storyboardAssetId && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                已选资产 ID: {storyboardAssetId.slice(0, 16)}... · 将直接作为 video 步骤的输入
              </Text>
            )}
          </div>
        ) : (
          /* 正常多素材模式 */
          materials.map((m, idx) => {
          const origFile = getOrigFile(m.slotId)
          const assetList = assetsByType[m.kind] || []
          const isScene = m.kind === 'scene'
          const kindLabel = m.kind === 'character' ? '角色' : m.kind === 'scene' ? '场景' : '道具'
          const kindColor = m.kind === 'character' ? 'purple' : m.kind === 'scene' ? 'green' : 'cyan'
          return (
            <Card
              key={`${m.slotId}-${idx}`}
              size="small"
              type="inner"
              title={
                <Space>
                  {m.kind === 'character' && <UserOutlined style={{ color: '#722ed1' }} />}
                  {m.kind === 'scene' && <AppstoreOutlined style={{ color: '#52c41a' }} />}
                  {m.kind === 'prop' && <AppstoreOutlined style={{ color: '#13c2c2' }} />}
                  <Text strong>{kindLabel}{isScene ? '' : ` ${m.slotId}`}</Text>
                  <Tag color={kindColor}>{m.kind}</Tag>
                  <Tooltip title={`工作流节点原文件名: ${origFile}`}>
                    <Tag color="blue">{origFile}</Tag>
                  </Tooltip>
                </Space>
              }
              extra={
                !isScene && (
                  <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeMaterial(idx)} />
                )
              }
              style={{ marginBottom: 8 }}
            >
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Space wrap>
                  <Radio.Group
                    value={m.mode}
                    onChange={e => updateMaterial(idx, { mode: e.target.value })}
                    optionType="button"
                    buttonStyle="solid"
                    size="small"
                  >
                    <Radio.Button value="generate">新生成</Radio.Button>
                    <Radio.Button value="existing">使用已有</Radio.Button>
                  </Radio.Group>

                  {/* 输出形态选择：仅"新生成"模式可用 */}
                  {m.mode === 'generate' && (
                    <Select
                      size="small"
                      value={m.outputForm}
                      onChange={v => updateMaterial(idx, { outputForm: v })}
                      style={{ width: 140 }}
                      options={
                        m.kind === 'character'
                          ? [
                              { value: 'concept', label: '概念图' },
                              { value: 'angle', label: '三视图' },
                            ]
                          : m.kind === 'scene'
                          ? [
                              { value: 'concept', label: '概念图' },
                              { value: 'pano', label: '全景图' },
                            ]
                          : [{ value: 'concept', label: '概念图' }]
                      }
                    />
                  )}
                </Space>

                {m.mode === 'generate' ? (
                  <Space direction="vertical" style={{ width: '100%' }} size={6}>
                    <Space size={4} wrap>
                      <Tooltip title="从内置提示词模板库选择">
                        <Button
                          size="small"
                          type="link"
                          icon={<AppstoreOutlined />}
                          onClick={() => openTplPicker(idx)}
                          style={{ padding: '0 4px' }}
                        >选模板</Button>
                      </Tooltip>
                      <Tooltip title="调用 LLM 生成提示词">
                        <Button
                          size="small"
                          type="link"
                          icon={<ThunderboltOutlined />}
                          onClick={() => openAiDesc(idx)}
                          loading={aiGenLoadingIdx === idx}
                          style={{ padding: '0 4px' }}
                        >AI生成</Button>
                      </Tooltip>
                      {llmCfg.model && (
                        <Text type="secondary" style={{ fontSize: 11 }}>
                          ({llmCfg.model})
                        </Text>
                      )}
                    </Space>
                    <TextArea
                      rows={2}
                      value={m.prompt}
                      onChange={e => updateMaterial(idx, { prompt: e.target.value })}
                      placeholder={`${kindLabel}的提示词（可不填，用模板或AI生成）`}
                    />
                  </Space>
                ) : (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Select
                      style={{ width: '100%' }}
                      placeholder={`选择已有的 ${m.kind} 资产`}
                      value={m.asset_id || undefined}
                      onChange={v => updateMaterial(idx, { asset_id: v })}
                      showSearch
                      optionFilterProp="label"
                      options={assetList.map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_id.slice(0, 8)})` }))}
                      notFoundContent={assetList.length === 0 ? <Empty description={`没有 ${m.kind} 类型资产`} image={Empty.PRESENTED_IMAGE_SIMPLE} /> : null}
                    />
                    {assetList.length === 0 && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        提示：项目内暂无 {m.kind} 类型资产，可切换到"新生成"模式
                      </Text>
                    )}
                  </Space>
                )}
              </Space>
            </Card>
          )
        })
        )}
        <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12, marginBottom: 0 }}>
          已用 {materials.length}/5 个槽位 · 剩余 {5 - materials.length} 个可添加
        </Paragraph>
      </Card>

      <Card title={<Space><FileTextOutlined />视频参数</Space>} style={{ marginBottom: 16 }}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            title: '多角色长视频',
            video_prompt: 'jiangnan water town story, characters meet on stone path, cinematic, misty rain',
            ...DEFAULT_VIDEO_PARAMS,
          }}
        >
          <Form.Item label="任务名称" name="title" rules={[{ required: true }]}>
            <Input placeholder="给这次一键成片起个名字" />
          </Form.Item>

          <Space wrap size="middle">
            <Form.Item label="视频总时长（秒）" name="duration" rules={[{ required: true }]} tooltip=">15 秒自动触发分段模式">
              <InputNumber min={15} max={300} step={15} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item label="单段时长（秒）" name="segment_seconds" rules={[{ required: true }]} tooltip="LTX-2.3 单次最大 15 秒">
              <InputNumber min={5} max={15} step={1} style={{ width: 120 }} />
            </Form.Item>
            <Form.Item label="帧率（fps）" name="frame_rate" rules={[{ required: true }]}>
              <InputNumber min={8} max={60} step={1} style={{ width: 100 }} />
            </Form.Item>
            <Form.Item label="宽度" name="width" rules={[{ required: true }]}>
              <InputNumber min={320} max={1920} step={64} style={{ width: 100 }} />
            </Form.Item>
            <Form.Item label="高度" name="height" rules={[{ required: true }]}>
              <InputNumber min={320} max={1920} step={64} style={{ width: 100 }} />
            </Form.Item>
            <Form.Item label="宽高比" name="aspect_ratio">
              <Select style={{ width: 100 }} options={ASPECT_RATIOS} />
            </Form.Item>
            <Form.Item label="分辨率" name="resolution">
              <Select style={{ width: 100 }} options={RESOLUTIONS} />
            </Form.Item>
            <Form.Item label="工作流文件" name="model" rules={[{ required: true }]} style={{ minWidth: 350 }}>
              <Input placeholder="LTX-2.3_MSR_sample_workflow_V2.json" />
            </Form.Item>
          </Space>

          <Form.Item label="视频全局提示词" name="video_prompt" rules={[{ required: true }]} tooltip="LTX-2.3 工作流的 global prompt">
            <TextArea rows={2} />
          </Form.Item>

          <Divider orientation="left">
            <Space>
              <span>分段故事情节（local_prompts）</span>
              <Tooltip title="从 JSON 导入所有提示词">
                <Button size="small" icon={<ImportOutlined />} onClick={() => setJsonInputOpen(true)}>导入JSON</Button>
              </Tooltip>
              <Tooltip title="导出当前配置为JSON">
                <Button size="small" onClick={handleExportJson}>导出JSON</Button>
              </Tooltip>
              <Tooltip title="配置 AI 生成提示词的 LLM 接口">
                <Button
                  size="small"
                  icon={<ThunderboltOutlined />}
                  onClick={() => { setLlmCfgDraft({ ...llmCfg }); setLlmCfgOpen(true) }}
                >
                  LLM配置 {llmCfg.model ? `(${llmCfg.model})` : ''}
                </Button>
              </Tooltip>
            </Space>
          </Divider>
          {segmentPrompts.map((p, i) => (
            <Form.Item key={i} label={`片段 ${i + 1} 故事情节`}>
              <Space.Compact style={{ width: '100%' }}>
                <TextArea
                  value={p}
                  onChange={e => {
                    const next = [...segmentPrompts]
                    next[i] = e.target.value
                    setSegmentPrompts(next)
                  }}
                  rows={2}
                  style={{ flex: 1 }}
                />
                <Button
                  danger
                  disabled={segmentPrompts.length <= 1}
                  onClick={() => {
                    setSegmentPrompts(segmentPrompts.filter((_, idx) => idx !== i))
                    setSegmentTexts(segmentTexts.filter((_, idx) => idx !== i))
                  }}
                >删除</Button>
              </Space.Compact>
              {ttsEnabled && (
                <Input.TextArea
                  value={segmentTexts[i] || ''}
                  onChange={e => {
                    const next = [...segmentTexts]
                    while (next.length <= i) next.push('')
                    next[i] = e.target.value
                    setSegmentTexts(next)
                  }}
                  rows={2}
                  placeholder={`片段 ${i+1} 台词/旁白（留空则该段无配音）`}
                  style={{ marginTop: 6 }}
                />
              )}
            </Form.Item>
          ))}
          <Button
            type="dashed"
            block
            onClick={() => {
              setSegmentPrompts([...segmentPrompts, ''])
              setSegmentTexts([...segmentTexts, ''])
            }}
            disabled={segmentPrompts.length >= 20}
          >+ 添加一段故事</Button>
        </Form>
      </Card>

      {/* TTS 配音配置 */}
      <Card
        title={
          <Space>
            <SoundOutlined />
            <span>TTS 配音</span>
            <Tooltip title="开启后为每段视频生成 AI 配音，自动混入最终视频">
              <InfoCircleOutlined style={{ color: '#1677ff' }} />
            </Tooltip>
          </Space>
        }
        extra={
          <Space>
            <Text type="secondary" style={{ fontSize: 12 }}>启用 TTS</Text>
            <Switch checked={ttsEnabled} onChange={setTtsEnabled} />
          </Space>
        }
        style={{ marginBottom: 16 }}
      >
        {ttsEnabled ? (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="Qwen3-TTS 配音"
              description="基于已有的 Qwen3+TTS+音色设计 / Qwen3+TTS+音频克隆 工作流。在上方「分段故事情节」中为每段输入台词/旁白，系统会按段生成音频并混入视频。"
            />
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item label="TTS 模式">
                  <Radio.Group value={ttsMode} onChange={e => setTtsMode(e.target.value)}>
                    <Radio value="voice_design">音色设计（文字描述音色）</Radio>
                    <Radio value="voice_clone">音色克隆（参考音频复刻）</Radio>
                  </Radio.Group>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="混音模式">
                  <Radio.Group value={ttsMixMode} onChange={e => setTtsMixMode(e.target.value)}>
                    <Radio value="replace">替代原音频</Radio>
                    <Radio value="overlay">叠加原音频</Radio>
                  </Radio.Group>
                </Form.Item>
              </Col>
            </Row>

            {ttsMode === 'voice_design' ? (
              <Form.Item
                label="音色描述"
                tooltip="用自然语言描述想要的音色，如年龄、性别、情绪、语速、参考角色等"
              >
                <TextArea
                  rows={3}
                  value={ttsVoiceDesc}
                  onChange={e => setTtsVoiceDesc(e.target.value)}
                  placeholder="如：清脆童声，8岁女童音色，语速偏快，咬字带稚气"
                />
                <Space wrap style={{ marginTop: 6 }}>
                  {[
                    '成年女性，温柔亲切，语速适中，咬字清晰',
                    '成年男性，沉稳磁性，播音腔，语速偏慢',
                    '清脆童声，8岁女童音色，语速偏快，咬字带稚气',
                    '老年男性，沧桑缓慢，沙哑，带方言口音',
                    '年轻女性，活泼俏皮，语速快，情绪外放',
                  ].map(desc => (
                    <Button key={desc} size="small" type="link" onClick={() => setTtsVoiceDesc(desc)}>
                      {desc.slice(0, 12)}...
                    </Button>
                  ))}
                </Space>
              </Form.Item>
            ) : (
              <Form.Item
                label="参考音频 URL"
                tooltip="提供一段参考音频（5-10秒），TTS 将复刻该音色"
                required
              >
                <Input
                  value={ttsRefAudio}
                  onChange={e => setTtsRefAudio(e.target.value)}
                  placeholder="https://... 或 ComfyUI /view?filename=xxx.flac"
                />
              </Form.Item>
            )}

            <Row gutter={16}>
              <Col span={8}>
                <Form.Item label={`TTS 音量: ${ttsVolume.toFixed(2)}`}>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={ttsVolume}
                    onChange={e => setTtsVolume(parseFloat(e.target.value))}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label="BGM URL" tooltip="可选背景音乐，将循环混入整个视频">
                  <Input
                    value={bgmUrl}
                    onChange={e => setBgmUrl(e.target.value)}
                    placeholder="https://...bgm.mp3"
                  />
                </Form.Item>
              </Col>
              <Col span={8}>
                <Form.Item label={`BGM 音量: ${bgmVolume.toFixed(2)}`}>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    value={bgmVolume}
                    onChange={e => setBgmVolume(parseFloat(e.target.value))}
                    style={{ width: '100%' }}
                  />
                </Form.Item>
              </Col>
            </Row>
          </>
        ) : (
          <Text type="secondary">未启用 TTS 配音，最终视频将无音频或保留原视频音频</Text>
        )}
      </Card>

      {/* 成片后期：字幕 → 钩子 → 平台导出 */}
      <Card title="成片后期（全链路）" style={{ marginBottom: 16 }}
        extra={
          <Switch checked={postEnabled} onChange={setPostEnabled} checkedChildren="启用" unCheckedChildren="关闭" />
        }
      >
        {!postEnabled ? (
          <Text type="secondary">后期阶段已关闭：仅生成原始长视频，不做字幕/钩子/导出。</Text>
        ) : (
          <Row gutter={24}>
            {/* 字幕 */}
            <Col xs={24} lg={8}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Switch checked={subEnabled} onChange={setSubEnabled} checkedChildren="字幕" unCheckedChildren="字幕" />
                <Text type="secondary" style={{ fontSize: 12 }}>烧录 TTS 台词为竖版大字幕</Text>
                <div>
                  <Text style={{ fontSize: 12 }}>高亮关键词(逗号分隔):</Text>
                  <Input
                    value={subKeywords}
                    onChange={e => setSubKeywords(e.target.value)}
                    placeholder="例如：AI, 3秒, 免费"
                    style={{ marginTop: 4 }}
                  />
                </div>
              </Space>
            </Col>
            {/* 钩子 */}
            <Col xs={24} lg={8}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Switch checked={hookEnabled} onChange={setHookEnabled} checkedChildren="钩子" unCheckedChildren="钩子" />
                <Text type="secondary" style={{ fontSize: 12 }}>结尾 4 秒叠加转化引导框</Text>
                <div>
                  <Text style={{ fontSize: 12 }}>钩子主文案:</Text>
                  <Input value={hookText} onChange={e => setHookText(e.target.value)} style={{ marginTop: 4 }} />
                </div>
                <div>
                  <Text style={{ fontSize: 12 }}>钩子副文案:</Text>
                  <Input value={hookSubText} onChange={e => setHookSubText(e.target.value)} style={{ marginTop: 4 }} />
                </div>
              </Space>
            </Col>
            {/* 平台导出 */}
            <Col xs={24} lg={8}>
              <Space direction="vertical" style={{ width: '100%' }}>
                <Switch checked={exportEnabled} onChange={setExportEnabled} checkedChildren="平台导出" unCheckedChildren="平台导出" />
                <Text type="secondary" style={{ fontSize: 12 }}>导出为平台发布专用竖版规格</Text>
                <div>
                  <Text style={{ fontSize: 12 }}>发布平台:</Text>
                  <Select
                    value={exportPlatform}
                    onChange={setExportPlatform}
                    style={{ width: '100%', marginTop: 4 }}
                    options={Object.keys(PLATFORM_EXPORT_SPECS).map(p => ({
                      value: p,
                      label: `${p} (${PLATFORM_EXPORT_SPECS[p].resolution} ${PLATFORM_EXPORT_SPECS[p].desc})`,
                    }))}
                  />
                </div>
              </Space>
            </Col>
          </Row>
        )}
      </Card>

      <Space style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          size="large"
          icon={<PlayCircleOutlined />}
          loading={submitting || isRunning}
          onClick={handleSubmit}
          disabled={!currentProjectId || isRunning}
        >一键生成长视频</Button>
        <Button size="large" icon={<ReloadOutlined />} onClick={handleReset} disabled={isRunning}>重置默认</Button>
        {!currentProjectId && <Text type="warning">请先在右上角选择项目</Text>}
      </Space>

      {batch && (
        <Card title="执行进度" style={{ marginBottom: 16 }}>
          <Space style={{ marginBottom: 12 }} wrap>
            <Tag color={batch.status === 'completed' ? 'success' : batch.status === 'failed' ? 'error' : 'processing'}>
              {batch.status}
            </Tag>
            <Text type="secondary">批次 ID: {batch.batch_id}</Text>
            {batch.error && <Text type="danger">错误: {batch.error}</Text>}
          </Space>
          <Steps
            current={batch.current_step_index}
            status={batch.status === 'failed' ? 'error' : batch.status === 'completed' ? 'finish' : 'process'}
            size="small"
            items={batch.steps?.map(s => ({
              title: s.name,
              description: `${s.status} (${((s.elapsed_ms || 0) / 1000).toFixed(0)}s)`,
              status: s.status === 'completed' ? 'finish' : s.status === 'running' ? 'process' : s.status === 'failed' ? 'error' : 'wait',
            }))}
          />
          {wsProgress?.progress && (
            <Progress
              percent={wsProgress.progress.percent}
              status={batch.status === 'failed' ? 'exception' : batch.status === 'completed' ? 'success' : 'active'}
              style={{ marginTop: 12 }}
              format={() => `${wsProgress.progress!.completed}/${wsProgress.progress!.total}`}
            />
          )}
          {wsProgress?.message && (
            <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>{wsProgress.message}</Paragraph>
          )}
        </Card>
      )}

      {finalVideo && (
        <Card title="成片结果" style={{ marginBottom: 16 }}>
          <Row gutter={16} align="middle">
            <Col xs={24} sm={10}>
              <video
                src={finalVideo.url}
                controls
                style={{ width: '100%', maxHeight: 320, background: '#000', borderRadius: 8 }}
              />
            </Col>
            <Col xs={24} sm={14}>
              <Paragraph style={{ marginBottom: 8 }}>
                <Text strong>名称：</Text><Text>{finalVideo.name}</Text>
              </Paragraph>
              <Paragraph style={{ marginBottom: 16 }}>
                <Text strong>资产ID：</Text>
                <Text copyable style={{ fontSize: 12 }}>{finalVideo.asset_id}</Text>
              </Paragraph>
              <Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
                当前为 {exportPlatform} 规格成片（{PLATFORM_EXPORT_SPECS[exportPlatform]?.desc}）。如需多平台尺寸，请到「成片导出」页一键生成。
              </Text>
              <Space>
                <Button type="primary" icon={<DownloadOutlined />} href={finalVideo.url} target="_blank">下载成片</Button>
                <Button icon={<ExportOutlined />} onClick={() => message.info('已完成平台规格导出（本页）或前往「成片导出」页')}>
                  前往成片导出
                </Button>
              </Space>
            </Col>
          </Row>
        </Card>
      )}

      {finalVideo && (
        <Card title="发布素材包" style={{ marginBottom: 16 }}>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message="一键打包：封面图 + 发布文案 + 成片视频（zip），或逐项单独下载。标题/标签/封面副文案已按剧本预填，可修改后重新生成。"
          />
          <Row gutter={16}>
            <Col xs={24} sm={14}>
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <div>
                  <Text strong>成片标题：</Text>
                  <Input
                    value={packTitle}
                    onChange={e => setPackTitle(e.target.value)}
                    placeholder="发布标题"
                    style={{ marginTop: 4 }}
                  />
                </div>
                <div>
                  <Text strong>封面副文案：</Text>
                  <Input
                    value={packSubtitle}
                    onChange={e => setPackSubtitle(e.target.value)}
                    placeholder="封面副标题（吸睛短句）"
                    style={{ marginTop: 4 }}
                  />
                </div>
                <div>
                  <Text strong>推荐标签：</Text>
                  <Input
                    value={packTags}
                    onChange={e => setPackTags(e.target.value)}
                    placeholder="逗号分隔，如：短剧,AI工具,教程"
                    style={{ marginTop: 4 }}
                  />
                </div>
                <Space>
                  <Button icon={<PlayCircleOutlined />} loading={packBundling} type="primary" onClick={handleDownloadZip}>
                    打包下载 (zip)
                  </Button>
                  <Button icon={<FileTextOutlined />} onClick={handleDownloadCopy}>下载文案</Button>
                  <Button icon={<DownloadOutlined />} onClick={handleDownloadCover}>下载封面</Button>
                </Space>
              </Space>
            </Col>
            <Col xs={24} sm={10}>
              <div style={{ position: 'relative' }}>
                {coverDataUrl ? (
                  <img
                    src={coverDataUrl}
                    alt="封面预览"
                    style={{ width: '100%', maxHeight: 320, objectFit: 'contain', borderRadius: 8, background: '#000' }}
                  />
                ) : (
                  <div style={{ width: '100%', height: 180, background: '#f5f5f5', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999' }}>
                    未生成封面（点击下方按钮）
                  </div>
                )}
                <Button size="small" style={{ position: 'absolute', top: 8, right: 8 }} onClick={handlePreviewCover}>
                  生成/刷新封面
                </Button>
              </div>
            </Col>
          </Row>
        </Card>
      )}

      <Modal
        title="提示词 JSON 导入/导出"
        open={jsonInputOpen}
        onCancel={() => setJsonInputOpen(false)}
        footer={[
          <Button key="cancel" onClick={() => setJsonInputOpen(false)}>取消</Button>,
          <Button key="import" type="primary" onClick={handleImportJson}>导入</Button>,
        ]}
        width={720}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          导入支持两种格式：<br/>
          1) 完整格式：<code>{'{materials:[{slotId,kind,mode,outputForm,prompt,asset_id}], segment_prompts:[], video_prompt, title}'}</code><br/>
          2) 简易格式：<code>{'{char1:"...", char2:"...", scene:"...", prop1:"...", segment_prompts:[], video_prompt:"..."}'}</code><br/>
          <Text type="warning">outputForm 可选：character→concept/angle；scene→concept/pano；prop→concept</Text>
        </Paragraph>
        <TextArea
          rows={16}
          value={jsonText}
          onChange={e => setJsonText(e.target.value)}
          placeholder={`{
  "title": "江南相遇",
  "video_prompt": "视频全局提示词...",
  "materials": [
    {"slotId": 1, "kind": "character", "mode": "generate", "outputForm": "angle", "prompt": "主角描述（生成三视图）"},
    {"slotId": 2, "kind": "character", "mode": "generate", "outputForm": "angle", "prompt": "配角描述（生成三视图）"},
    {"slotId": 3, "kind": "character", "mode": "generate", "outputForm": "angle", "prompt": "第三个角色..."},
    {"slotId": 0, "kind": "scene", "mode": "generate", "outputForm": "pano", "prompt": "场景描述（生成全景图）"}
  ],
  "segment_prompts": [
    "片段1故事...",
    "片段2故事...",
    "片段3故事...",
    "片段4故事..."
  ]
}`}
        />
      </Modal>

      {/* 提示词模板选择器 */}
      <Modal
        open={tplPickerOpen}
        title={`选择${tplPickerIdx >= 0 ? materials[tplPickerIdx]?.kind : ''}提示词模板`}
        onCancel={() => setTplPickerOpen(false)}
        footer={null}
        width={720}
      >
        {tplPickerIdx >= 0 && materials[tplPickerIdx] && (
          <div style={{ maxHeight: 480, overflowY: 'auto' }}>
            {(PROMPT_LIBRARY[materials[tplPickerIdx].kind] || []).map(cat => (
              <div key={cat.category} style={{ marginBottom: 16 }}>
                <Title level={5} style={{ marginTop: 8 }}>{cat.category}</Title>
                <Space wrap>
                  {cat.items.map(item => (
                    <Tooltip key={item.label} title={
                      <div>
                        <div>{item.desc}</div>
                        <div style={{ maxWidth: 360, marginTop: 4, fontSize: 11, opacity: 0.85 }}>{item.prompt}</div>
                      </div>
                    }>
                      <Button onClick={() => applyPromptTemplate(item)}>{item.label}</Button>
                    </Tooltip>
                  ))}
                </Space>
              </div>
            ))}
          </div>
        )}
      </Modal>

      {/* AI 生成提示词输入框 */}
      <Modal
        open={aiDescOpen}
        title={`AI 生成${aiDescIdx >= 0 ? materials[aiDescIdx]?.kind : ''}提示词`}
        onCancel={() => setAiDescOpen(false)}
        onOk={runAiGenerate}
        okText="生成"
        cancelText="取消"
        confirmLoading={aiGenLoadingIdx >= 0}
        width={560}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          输入简短中文/英文描述，LLM 将自动生成完整的英文提示词。留空则由 LLM 自由发挥。
          当前模型：<Text code>{llmCfg.model || '未配置'}</Text>
        </Paragraph>
        <TextArea
          rows={4}
          value={aiDescText}
          onChange={e => setAiDescText(e.target.value)}
          placeholder="例如：一个穿汉服的少女，在江南水乡石板路上行走，温柔表情，长发"
        />
        <Space style={{ marginTop: 8 }}>
          <Button size="small" type="link" onClick={() => setLlmCfgOpen(true)}>配置 LLM 接口</Button>
        </Space>
      </Modal>

      {/* LLM 接口配置 */}
      <Modal
        open={llmCfgOpen}
        title="配置 LLM 接口（OpenAI 兼容）"
        onCancel={() => setLlmCfgOpen(false)}
        onOk={saveLlmCfg}
        okText="保存"
        cancelText="取消"
        width={520}
      >
        <Paragraph type="secondary" style={{ fontSize: 12 }}>
          支持任意 OpenAI 兼容协议（OpenAI / 通义千问 / DeepSeek / Moonshot / 本地 vLLM 等）。
          配置保存到后端 .env 文件，由服务端统一管理，不存储在浏览器本地。
        </Paragraph>
        <Form layout="vertical">
          <Form.Item label="Base URL" tooltip="如 https://api.openai.com/v1 或 https://dashscope.aliyuncs.com/compatible-mode/v1">
            <Input
              value={llmCfgDraft.base_url}
              onChange={e => setLlmCfgDraft({ ...llmCfgDraft, base_url: e.target.value })}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
            />
          </Form.Item>
          <Form.Item label="API Key">
            <Input.Password
              value={llmCfgDraft.api_key}
              onChange={e => setLlmCfgDraft({ ...llmCfgDraft, api_key: e.target.value })}
              placeholder="sk-..."
            />
          </Form.Item>
          <Form.Item label="Model" tooltip="如 gpt-4o-mini / qwen-plus / deepseek-chat">
            <Input
              value={llmCfgDraft.model}
              onChange={e => setLlmCfgDraft({ ...llmCfgDraft, model: e.target.value })}
              placeholder="qwen-plus"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
