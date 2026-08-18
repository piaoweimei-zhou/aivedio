import axios from 'axios'

const api = axios.create({
  baseURL: '/api/director',
  timeout: 30000,
})

// ==================== 资产 API ====================

export const assetApi = {
  list: (params?: { asset_type?: string; content_type?: string; category?: string; parent_id?: string; project_id?: string }) =>
    api.get('/assets', { params }).then(r => r.data),

  get: (id: string) =>
    api.get(`/assets/${id}`).then(r => r.data),

  create: (data: { asset_type: string; content_type?: string; name: string; urls?: string[]; metadata?: Record<string, any>; parent_id?: string; project_id?: string }) =>
    api.post('/assets', data).then(r => r.data),

  update: (id: string, data: { name?: string; urls?: string[]; metadata?: Record<string, any> }) =>
    api.put(`/assets/${id}`, data).then(r => r.data),

  delete: (id: string) =>
    api.delete(`/assets/${id}`).then(r => r.data),

  lineage: (id: string) =>
    api.get(`/assets/${id}/lineage`).then(r => r.data),

  children: (id: string) =>
    api.get(`/assets/${id}/children`).then(r => r.data),

  stats: () =>
    api.get('/assets/stats/overview').then(r => r.data),

  types: () =>
    api.get('/assets/types').then(r => r.data),

  stageTypes: () =>
    api.get('/assets/stage-types').then(r => r.data),

  contentTypes: () =>
    api.get('/assets/content-types').then(r => r.data),

  upload: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/assets/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }).then(r => r.data)
  },
}

// ==================== 供应商 API ====================

export const providerApi = {
  list: (capability?: string) =>
    api.get('/providers', { params: capability ? { capability } : {} }).then(r => r.data),

  get: (id: string) =>
    api.get(`/providers/${id}`).then(r => r.data),

  // 健康检查
  health: () =>
    api.get('/providers/health/all').then(r => r.data),

  // 配置向导
  getConfigMeta: () =>
    api.get('/providers/config/meta').then(r => r.data),

  // 读取当前服务端配置（密钥由后端管理）
  getConfig: () =>
    api.get('/providers/config').then(r => r.data),

  saveConfig: (configs: Record<string, string>) =>
    api.post('/providers/config/save', configs).then(r => r.data),

  testConfig: (providerId: string, configs: Record<string, string>) =>
    api.post('/providers/config/test', configs, { params: { provider_id: providerId } }).then(r => r.data),
}

// ==================== 阶段 API ====================

export const stageApi = {
  list: () =>
    api.get('/stages').then(r => r.data),

  get: (id: string) =>
    api.get(`/stages/${id}`).then(r => r.data),

  execute: (data: { stage_id: string; input_asset_ids: string[]; provider_id?: string; params?: Record<string, any>; async_mode?: boolean }) =>
    api.post('/stages/execute', data, { timeout: 300000 }).then(r => r.data),

  getTask: (taskId: string) =>
    api.get(`/stages/task/${taskId}`).then(r => r.data),

  listTasks: (status?: string) =>
    api.get('/stages/tasks', { params: status ? { status } : {} }).then(r => r.data),

  resolve: (inputTypes: string[]) =>
    api.post('/stages/resolve', inputTypes).then(r => r.data),
}

// ==================== 新增阶段专用 API ====================

// AI 剧本视频类型
export interface VideoTypeOption {
  value: string
  label: string
  structure: string
  tone: string
}

// AI 剧本生成参数
export interface ScriptParams {
  topic: string
  video_type?: string  // problem_solving/efficiency_compare/review_tutorial/fun_drama/full_ai_short/image_story
  acts?: number
  duration_seconds?: number
  characters?: string[]
  tone_extra?: string
  target_audience?: string
  hook_style?: string  // comment_1/main_page/dm
  style_id?: string  // 网感风格
  model?: string
  temperature?: number
  max_tokens?: number
}

// 网感风格选项
export interface StyleOption {
  style_id: string
  name: string
  category: string
  description: string
  script_guidance?: string
  visual_prompt?: string
  params?: Record<string, any>
  tags?: string[]
  is_default?: boolean
}

// 剧本 JSON 结构
export interface ScriptData {
  title: string
  video_type?: string
  hook?: string
  characters?: Array<{ name: string; desc: string; role: string }>
  covers?: Array<{ title: string; subtitle: string; layout: string }>
  acts?: Array<{
    act: number
    scene: string
    narration?: string
    dialogues?: Array<{ character: string; line: string }>
    tts_texts?: string[]
    duration_seconds?: number
  }>
  raw_text?: string
  parse_error?: string
  meta?: Record<string, any>
}

// 录屏窗口
export interface RecordWindow {
  title: string
  process: string
}

// 录屏参数
export interface ScreenRecordParams {
  mode: 'record' | 'upload'
  duration?: number
  fps?: number
  name?: string
  // record 模式
  window_title?: string
  region?: string  // "x,y,w,h"
  display?: string  // Linux
  avfoundation_input?: string  // macOS
  ffmpeg_args?: string[]
}

// 分屏合成参数
export interface ComposeParams {
  layout: 'horizontal' | 'vertical' | 'grid' | 'split_compare'
  columns?: number  // grid 列数
  gap?: number  // 间隔像素
  labels?: string[]  // 每路标签
  name?: string
  size?: string  // "1920x1080"
  duration?: number  // 视频时长（图片转视频用）
  bg_color?: string  // 背景色
}

export const scriptApi = {
  // 列出 6 种视频类型
  listVideoTypes: (): Promise<{ video_types: VideoTypeOption[] }> =>
    api.get('/stages/script/video-types').then(r => r.data),

  // 获取剧本内容
  getScript: (assetId: string): Promise<{ script: ScriptData; asset: any }> =>
    api.get(`/stages/script/${assetId}`).then(r => r.data),

  // 执行剧本生成（异步）
  generate: (params: ScriptParams) =>
    api.post('/stages/execute', {
      stage_id: 'script',
      input_asset_ids: [],
      params,
      async_mode: true,
    }, { timeout: 300000 }).then(r => r.data),

  // 同步模式（短剧本）
  generateSync: (params: ScriptParams) =>
    api.post('/stages/execute', {
      stage_id: 'script',
      input_asset_ids: [],
      params,
      async_mode: false,
    }, { timeout: 300000 }).then(r => r.data),
}

// ==================== 网感风格 API ====================

export const styleApi = {
  // 列出全部风格预设
  list: (): Promise<{ styles: StyleOption[] }> =>
    api.get('/stages/styles').then(r => r.data),

  // 查询单个风格
  get: (styleId: string): Promise<{ style: StyleOption }> =>
    api.get(`/stages/styles/${styleId}`).then(r => r.data),
}

export const screenRecordApi = {
  // 列出可录制窗口（Windows）
  listWindows: (): Promise<{ windows: RecordWindow[] }> =>
    api.get('/stages/screen/windows').then(r => r.data),

  // 执行录屏（异步）
  record: (params: ScreenRecordParams) =>
    api.post('/stages/execute', {
      stage_id: 'screen_record',
      input_asset_ids: [],
      params,
      async_mode: true,
    }, { timeout: 600000 }).then(r => r.data),  // 录屏可能很久

  // 上传模式：先上传文件，再以 input_asset_ids 调用 stage
  uploadAndRegister: async (file: File, name: string) => {
    // 1. 上传文件
    const formData = new FormData()
    formData.append('file', file)
    const uploadResp = await api.post('/assets/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    }).then(r => r.data)
    // 2. 创建 video 资产
    return api.post('/assets', {
      asset_type: 'video',
      name: name || file.name,
      urls: [uploadResp.url || uploadResp.urls?.[0] || ''],
      metadata: { source: 'screen_record_upload', mode: 'upload' },
    }).then(r => r.data)
  },
}

export const composeApi = {
  // 执行分屏合成（异步）
  compose: (inputAssetIds: string[], params: ComposeParams) =>
    api.post('/stages/execute', {
      stage_id: 'compose',
      input_asset_ids: inputAssetIds,
      params,
      async_mode: true,
    }, { timeout: 600000 }).then(r => r.data),
}

// ==================== 图文生成 API ====================

// 图文类型选项
export interface GraphicTypeOption {
  type: string
  label: string
  desc: string
}

// 图文生成参数
export interface GraphicParams {
  graphic_type: string  // infographic/comparison/tutorial/checklist/quote/data_chart
  topic: string
  title?: string
  style?: string  // modern/minimal/warm/tech
  model?: string
  temperature?: number
  max_tokens?: number
  width?: number
  height?: number
  extra_instructions?: string
}

export const graphicApi = {
  // 列出 6 种图文类型
  listGraphicTypes: (): Promise<{ graphic_types: GraphicTypeOption[] }> =>
    api.get('/stages/graphic/types').then(r => r.data),

  // 执行图文生成（异步）
  generate: (params: GraphicParams) =>
    api.post('/stages/execute', {
      stage_id: 'graphic',
      input_asset_ids: [],
      params,
      async_mode: true,
    }, { timeout: 300000 }).then(r => r.data),
}

// ==================== 视频 API ====================
// 复用导演工作台阶段体系：提交走 stages/execute（stage_id=video），
// 状态走 stages/task/{id}，列表走 stages/tasks，取消走 stages/task/{id}/cancel。

const videoStageApi = axios.create({
  baseURL: '/api/director/stages',
  timeout: 30000,
})

export const videoService = {
  submit: async (data: {
    provider_id: string; prompt: string; image_urls?: string[];
    model?: string; duration?: number; aspect_ratio?: string; resolution?: string;
    steps?: number; cfg?: number; fps?: number; seed?: number;
  }) => {
    // 阶段执行要求输入资产 ID；通用视频标签只提供 image_urls，先落库为资产
    const inputAssetIds: string[] = []
    for (const url of (data.image_urls || [])) {
      try {
        const asset = await assetApi.create({
          asset_type: 'image',
          name: url.split('/').pop() || 'video-input',
          urls: [url],
        })
        if (asset?.asset_id) inputAssetIds.push(asset.asset_id)
      } catch { /* 单个图源落库失败不阻断提交 */ }
    }
    const d = (await videoStageApi.post('/execute', {
      stage_id: 'video',
      input_asset_ids: inputAssetIds,
      provider_id: data.provider_id,
      params: {
        prompt: data.prompt,
        model: data.model,
        duration: data.duration,
        aspect_ratio: data.aspect_ratio,
        resolution: data.resolution,
        steps: data.steps,
        cfg: data.cfg,
        fps: data.fps,
        seed: data.seed,
      },
      async_mode: true,
    }, { timeout: 300000 })).data
    return { success: true, task_id: d.task_id, status: d.status }
  },

  listTasks: (status?: string) =>
    videoStageApi.get('/tasks', { params: status ? { status } : {} }).then(r => {
      const d = r.data
      return { success: true, tasks: d.tasks || [], running: d.running, pending: d.pending }
    }),

  getTask: (taskId: string) =>
    videoStageApi.get(`/task/${taskId}`).then(r => r.data),

  cancelTask: (taskId: string) =>
    videoStageApi.post(`/task/${taskId}/cancel`).then(r => r.data),
}

// ==================== MSR 视频 API ====================

const msrApi = axios.create({
  baseURL: '/api/canvas',
  timeout: 30000,
})

export const msrVideoService = {
  submit: (data: {
    ref1_image_url: string
    ref2_image_url: string
    ref3_image_url?: string
    ref4_image_url?: string
    bg_image_url?: string
    global_prompt: string
    local_prompts?: string
    width?: number
    height?: number
    frame_count?: number
    seed?: number
    // ⭐ 视频质量参数（与后端 MsrVideoRequest 对齐，注入节点 37/50/27）
    fps?: number
    cfg?: number
    steps?: number
    duration?: number
  }) => msrApi.post('/msr-video', data).then(r => r.data),

  getTask: (taskId: string) =>
    msrApi.get(`/msr-video/${taskId}`).then(r => r.data),
}

// ==================== 画布 API ====================

const canvasApi = axios.create({
  baseURL: '/api/canvas',
  timeout: 15000,
})

export const canvasService = {
  create: (name?: string) =>
    canvasApi.post('/', { name: name || '未命名画布' }).then(r => r.data),

  list: () =>
    canvasApi.get('/').then(r => r.data),

  get: (canvasId: string) =>
    canvasApi.get(`/${canvasId}`).then(r => r.data),

  update: (canvasId: string, data: { name?: string; nodes?: any[]; edges?: any[]; viewport?: any; base_updated_at?: number }) =>
    canvasApi.put(`/${canvasId}`, data).then(r => r.data),

  delete: (canvasId: string) =>
    canvasApi.delete(`/${canvasId}`).then(r => r.data),

  addNode: (canvasId: string, node: any) =>
    canvasApi.post(`/${canvasId}/nodes`, node).then(r => r.data),

  updateNode: (canvasId: string, nodeId: string, data: any) =>
    canvasApi.put(`/${canvasId}/nodes/${nodeId}`, data).then(r => r.data),

  removeNode: (canvasId: string, nodeId: string) =>
    canvasApi.delete(`/${canvasId}/nodes/${nodeId}`).then(r => r.data),
}

// ==================== 项目 API ====================

export interface Project {
  project_id: string
  name: string
  description: string
  status: string
  metadata: Record<string, any>
  created_at: number
  updated_at: number
}

export const projectService = {
  list: (status?: string) =>
    api.get('/projects', { params: status ? { status } : {} }).then(r => r.data),

  get: (projectId: string) =>
    api.get(`/projects/${projectId}`).then(r => r.data),

  create: (data: { name: string; description?: string; metadata?: Record<string, any> }) =>
    api.post('/projects', data).then(r => r.data),

  update: (projectId: string, data: { name?: string; description?: string; status?: string; metadata?: Record<string, any> }) =>
    api.put(`/projects/${projectId}`, data).then(r => r.data),

  delete: (projectId: string) =>
    api.delete(`/projects/${projectId}`).then(r => r.data),

  listAssets: (projectId: string, params?: { asset_type?: string; content_type?: string; category?: string }) =>
    api.get(`/projects/${projectId}/assets`, { params }).then(r => r.data),

  getStats: (projectId: string) =>
    api.get(`/projects/${projectId}/stats`).then(r => r.data),

  addAsset: (projectId: string, assetId: string) =>
    api.post(`/projects/${projectId}/assets/${assetId}`).then(r => r.data),

  removeAsset: (projectId: string, assetId: string) =>
    api.delete(`/projects/${projectId}/assets/${assetId}`).then(r => r.data),
}

// ==================== 批量任务 API ====================

export interface BatchStep {
  step_id?: string
  stage_id: string
  name?: string
  input_asset_ids?: string[]
  input_from_steps?: string[]
  provider_id?: string
  params?: Record<string, any>
  max_retries?: number
  // 运行时状态
  status?: string
  output_asset_id?: string
  error?: string
  elapsed_ms?: number
  retry_count?: number
}

export interface BatchTask {
  batch_id: string
  name: string
  project_id: string
  steps: BatchStep[]
  status: string
  current_step_index: number
  created_at: number
  updated_at: number
  started_at: number
  completed_at: number
  stop_on_failure: boolean
  auto_inherit_project: boolean
  error: string
  metadata: Record<string, any>
  progress?: number
  total_steps?: number
  completed_steps?: number
}

export const batchService = {
  create: (data: {
    name: string
    steps: BatchStep[]
    project_id?: string
    stop_on_failure?: boolean
    auto_inherit_project?: boolean
    metadata?: Record<string, any>
  }) => api.post('/batches', data).then(r => r.data),

  list: (params?: { status?: string; project_id?: string }) =>
    api.get('/batches', { params }).then(r => r.data),

  get: (batchId: string) =>
    api.get(`/batches/${batchId}`).then(r => r.data),

  start: (batchId: string, options?: { dry_run?: boolean; use_dag?: boolean }) =>
    api.post(`/batches/${batchId}/start`, null, {
      params: { dry_run: options?.dry_run || false, use_dag: options?.use_dag !== false },
    }).then(r => r.data),

  cancel: (batchId: string) =>
    api.post(`/batches/${batchId}/cancel`).then(r => r.data),

  retry: (batchId: string, fromStep?: string) =>
    api.post(`/batches/${batchId}/retry`, { from_step: fromStep || '' }).then(r => r.data),

  delete: (batchId: string) =>
    api.delete(`/batches/${batchId}`).then(r => r.data),

  // DAG 结构
  getDag: (batchId: string) =>
    api.get(`/batches/${batchId}/dag`).then(r => r.data),

  // 预检（DAG结构 + Provider可用性）
  dryRun: (batchId: string) =>
    api.post(`/batches/${batchId}/dry-run`).then(r => r.data),
}

// ==================== WebSocket 实时进度 ====================

export interface WsEvent {
  event: string
  batch_id: string
  step_id?: string
  stage_id?: string
  status?: string
  message?: string
  progress?: {
    completed: number
    total: number
    percent: number
    output_asset_id?: string
    elapsed_ms?: number
    failed_step?: string
  }
  timestamp: number
}

export class BatchWebSocket {
  private ws: WebSocket | null = null
  private batchId: string
  private listeners: Set<(event: WsEvent) => void> = new Set()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private pingTimer: ReturnType<typeof setInterval> | null = null

  constructor(batchId: string) {
    this.batchId = batchId
  }

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.hostname
    const port = import.meta.env.VITE_API_PORT || '8000'
    const url = `${protocol}//${host}:${port}/api/ws/batches/${this.batchId}`

    this.ws = new WebSocket(url)

    this.ws.onopen = () => {
      console.log(`[WS] 连接成功 | batch=${this.batchId}`)
      // 心跳保活
      this.pingTimer = setInterval(() => {
        if (this.ws?.readyState === WebSocket.OPEN) {
          this.ws.send('ping')
        }
      }, 30000)
    }

    this.ws.onmessage = (e) => {
      try {
        const event: WsEvent = JSON.parse(e.data)
        this.listeners.forEach(fn => fn(event))
      } catch (err) {
        console.error('[WS] 解析消息失败', err)
      }
    }

    this.ws.onclose = () => {
      console.log(`[WS] 连接关闭 | batch=${this.batchId}`)
      if (this.pingTimer) {
        clearInterval(this.pingTimer)
        this.pingTimer = null
      }
    }

    this.ws.onerror = (err) => {
      console.error('[WS] 连接错误', err)
      // 3秒后重连
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
      this.reconnectTimer = setTimeout(() => this.connect(), 3000)
    }
  }

  onEvent(fn: (event: WsEvent) => void) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  close() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
    this.listeners.clear()
  }
}

// ==================== 工作流模板 API ====================

export interface WorkflowStepTemplate {
  stage_id: string
  name?: string
  input_mode?: 'auto' | 'fixed' | 'user_select'
  input_from_steps?: string[]
  input_asset_ids?: string[]
  provider_id?: string
  params?: Record<string, any>
  max_retries?: number
  description?: string
}

export interface WorkflowTemplate {
  template_id: string
  name: string
  description: string
  category: 'preset' | 'custom'
  steps: WorkflowStepTemplate[]
  required_inputs: Array<{
    key: string
    label: string
    asset_type: string
    content_type: string
  }>
  created_at: number
  updated_at: number
  metadata?: Record<string, any>
}

export const workflowTemplateService = {
  list: (category?: string) =>
    api.get('/workflow-templates', { params: { category } }).then(r => r.data),

  get: (templateId: string) =>
    api.get(`/workflow-templates/${templateId}`).then(r => r.data),

  create: (data: {
    name: string
    description?: string
    steps: WorkflowStepTemplate[]
    required_inputs?: any[]
    metadata?: Record<string, any>
  }) => api.post('/workflow-templates', data).then(r => r.data),

  update: (templateId: string, data: {
    name?: string
    description?: string
    steps?: WorkflowStepTemplate[]
    required_inputs?: any[]
  }) => api.put(`/workflow-templates/${templateId}`, data).then(r => r.data),

  delete: (templateId: string) =>
    api.delete(`/workflow-templates/${templateId}`).then(r => r.data),

  createBatch: (templateId: string, data: {
    name: string
    project_id?: string
    input_assets?: Record<string, string[]>
    step_params?: Record<string, Record<string, any>>
    stop_on_failure?: boolean
    auto_start?: boolean
  }) => api.post(`/workflow-templates/${templateId}/create-batch`, data).then(r => r.data),
}

// ==================== 预设 API ====================

export interface Preset {
  preset_id: string
  name: string
  stage_id: string
  project_id: string
  provider_id: string
  params: Record<string, any>
  reference_asset_ids: string[]
  is_default: boolean
  description: string
  created_at: number
  updated_at: number
  metadata?: Record<string, any>
}

export const presetService = {
  list: (params?: { project_id?: string; stage_id?: string }) =>
    api.get('/presets', { params }).then(r => r.data),

  getDefault: (projectId: string, stageId?: string) =>
    api.get('/presets/default', { params: { project_id: projectId, stage_id: stageId } }).then(r => r.data),

  get: (presetId: string) =>
    api.get(`/presets/${presetId}`).then(r => r.data),

  create: (data: {
    name: string
    stage_id: string
    project_id?: string
    provider_id?: string
    params?: Record<string, any>
    reference_asset_ids?: string[]
    description?: string
    is_default?: boolean
    metadata?: Record<string, any>
  }) => api.post('/presets', data).then(r => r.data),

  update: (presetId: string, data: {
    name?: string
    description?: string
    stage_id?: string
    provider_id?: string
    params?: Record<string, any>
    reference_asset_ids?: string[]
    project_id?: string
  }) => api.put(`/presets/${presetId}`, data).then(r => r.data),

  delete: (presetId: string) =>
    api.delete(`/presets/${presetId}`).then(r => r.data),

  apply: (presetId: string) =>
    api.post(`/presets/${presetId}/apply`).then(r => r.data),

  setDefault: (presetId: string, projectId: string) =>
    api.post(`/presets/${presetId}/set-default`, { project_id: projectId }).then(r => r.data),
}

// ==================== 提示词中心 ====================

export interface PromptVariable {
  name: string
  default?: string
  description?: string
  required?: boolean
}

export interface PromptEntry {
  prompt_id: string
  name: string
  content: string
  category: string
  stage_id: string
  variables: PromptVariable[]
  tags: string[]
  project_id: string
  quality_score: number
  usage_count: number
  description: string
  is_default: boolean
  version: number
  created_at: number
  updated_at: number
  metadata?: Record<string, any>
  // ⭐ Phase 5：关联的工作流参数（"预设风格 = 提示词 + 参数"）
  params?: Record<string, any>
}

export const promptService = {
  list: (params?: {
    project_id?: string
    stage_id?: string
    category?: string
    tag?: string
    keyword?: string
  }) => api.get('/prompts', { params }).then(r => r.data),

  get: (promptId: string) =>
    api.get(`/prompts/${promptId}`).then(r => r.data),

  create: (data: {
    name: string
    content: string
    category?: string
    stage_id?: string
    variables?: PromptVariable[]
    tags?: string[]
    project_id?: string
    description?: string
    quality_score?: number
    metadata?: Record<string, any>
    // ⭐ Phase 5：关联工作流参数
    params?: Record<string, any>
  }) => api.post('/prompts', data).then(r => r.data),

  update: (promptId: string, data: {
    name?: string
    content?: string
    category?: string
    stage_id?: string
    variables?: PromptVariable[]
    tags?: string[]
    project_id?: string
    description?: string
    quality_score?: number
    // ⭐ Phase 5：关联工作流参数
    params?: Record<string, any>
  }) => api.put(`/prompts/${promptId}`, data).then(r => r.data),

  delete: (promptId: string) =>
    api.delete(`/prompts/${promptId}`).then(r => r.data),

  resolve: (promptId: string, variables: Record<string, string>) =>
    api.post(`/prompts/${promptId}/resolve`, { variables }).then(r => r.data),

  resolveContent: (content: string, variables: Record<string, string>) =>
    api.post('/prompts/resolve', { content, variables }).then(r => r.data),

  // 阶段 C：项目默认提示词
  setDefault: (promptId: string, projectId: string, stageId: string = '') =>
    api.post(`/prompts/${promptId}/set-default`, { project_id: projectId, stage_id: stageId }).then(r => r.data),

  unsetDefault: (promptId: string) =>
    api.post(`/prompts/${promptId}/unset-default`).then(r => r.data),

  getDefault: (projectId: string, stageId: string = '') =>
    api.get(`/prompts/defaults/${projectId}`, { params: { stage_id: stageId } }).then(r => r.data),

  // 阶段 C：版本历史
  history: (promptId: string) =>
    api.get(`/prompts/${promptId}/history`).then(r => r.data),

  rollback: (promptId: string, version: number) =>
    api.post(`/prompts/${promptId}/rollback`, { version }).then(r => r.data),

  categories: () => api.get('/prompts/categories').then(r => r.data),
  tags: () => api.get('/prompts/tags').then(r => r.data),
  stats: (projectId?: string) =>
    api.get('/prompts/stats', { params: { project_id: projectId || '' } }).then(r => r.data),
}

// ==================== 统一导出 ====================

const directorApi = {
  listAssets: assetApi.list,
  getAsset: assetApi.get,
  createAsset: assetApi.create,
  updateAsset: assetApi.update,
  deleteAsset: assetApi.delete,
  assetLineage: assetApi.lineage,
  assetChildren: assetApi.children,
  assetStats: assetApi.stats,
  assetTypes: assetApi.types,
  assetStageTypes: assetApi.stageTypes,
  assetContentTypes: assetApi.contentTypes,
  uploadAssetFile: assetApi.upload,

  listProviders: providerApi.list,
  getProvider: providerApi.get,

  listStages: stageApi.list,
  getStage: stageApi.get,
  executeStage: stageApi.execute,
  getStageTask: stageApi.getTask,
  listStageTasks: stageApi.listTasks,
  resolveStages: stageApi.resolve,

  updateTemplateManifest: (templateId: string, updates: Record<string, any>) =>
    api.post(`/assets/templates/manifest/${templateId}`, updates).then(r => r.data),

  // 批量任务
  batchCreate: batchService.create,
  batchList: batchService.list,
  batchGet: batchService.get,
  batchStart: batchService.start,
  batchCancel: batchService.cancel,
  batchRetry: batchService.retry,
  batchDelete: batchService.delete,

  // 工作流模板
  workflowTemplateList: workflowTemplateService.list,
  workflowTemplateGet: workflowTemplateService.get,
  workflowTemplateCreate: workflowTemplateService.create,
  workflowTemplateUpdate: workflowTemplateService.update,
  workflowTemplateDelete: workflowTemplateService.delete,
  workflowTemplateCreateBatch: workflowTemplateService.createBatch,

  // 预设
  presetList: presetService.list,
  presetGetDefault: presetService.getDefault,
  presetGet: presetService.get,
  presetCreate: presetService.create,
  presetUpdate: presetService.update,
  presetDelete: presetService.delete,
  presetApply: presetService.apply,
  presetSetDefault: presetService.setDefault,

  // 提示词中心
  promptList: promptService.list,
  promptGet: promptService.get,
  promptCreate: promptService.create,
  promptUpdate: promptService.update,
  promptDelete: promptService.delete,
  promptResolve: promptService.resolve,
  promptResolveContent: promptService.resolveContent,
  promptSetDefault: promptService.setDefault,
  promptUnsetDefault: promptService.unsetDefault,
  promptGetDefault: promptService.getDefault,
  promptHistory: promptService.history,
  promptRollback: promptService.rollback,
  promptCategories: promptService.categories,
  promptTags: promptService.tags,
  promptStats: promptService.stats,
}

export default directorApi
