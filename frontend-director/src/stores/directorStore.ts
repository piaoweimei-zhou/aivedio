import { create } from 'zustand'
import directorApi from '../services/directorApi'

interface Asset {
  asset_id: string
  asset_type: string
  content_type: string
  name: string
  urls: string[]
  metadata: Record<string, any>
  parent_id: string | null
  project_id?: string | null
  version: number
  created_at: number
  updated_at: number
}

interface DirectorState {
  // 资产
  assets: Asset[]
  assetsLoading: boolean
  loadAssets: (filters?: { asset_type?: string; content_type?: string; category?: string; project_id?: string }) => Promise<void>
  createAsset: (data: { asset_type: string; content_type?: string; name: string; urls?: string[]; metadata?: Record<string, any>; parent_id?: string; project_id?: string }) => Promise<Asset | null>
  deleteAsset: (id: string) => Promise<boolean>

  // 供应商
  providers: any[]
  loadProviders: (capability?: string) => Promise<void>

  // 阶段
  stages: any[]
  loadStages: () => Promise<void>
  executeStage: (params: { stage_id: string; input_asset_ids: string[]; provider_id?: string; params?: Record<string, any> }) => Promise<any>

  // 选中
  selectedAssetIds: string[]
  toggleAssetSelection: (id: string) => void
  clearSelection: () => void

  // 批量任务实时状态（WebSocket 事件 → Store 单向数据流）
  batchProgress: Record<string, { completed: number; total: number; percent: number; lastEvent?: string }>
  updateBatchProgress: (batchId: string, progress: { completed: number; total: number; percent: number; lastEvent?: string }) => void
  clearBatchProgress: (batchId: string) => void
}

export const useDirectorStore = create<DirectorState>((set, get) => ({
  assets: [],
  assetsLoading: false,

  loadAssets: async (filters) => {
    set({ assetsLoading: true })
    try {
      const res = await directorApi.listAssets(filters)
      set({ assets: res.assets || [], assetsLoading: false })
    } catch {
      set({ assetsLoading: false })
    }
  },

  createAsset: async (data) => {
    try {
      const res = await directorApi.createAsset(data)
      if (res.success) {
        const newAssets = [...get().assets, res.asset]
        set({ assets: newAssets })
        return res.asset
      }
      return null
    } catch {
      return null
    }
  },

  deleteAsset: async (id) => {
    try {
      const res = await directorApi.deleteAsset(id)
      if (res.success) {
        set({ assets: get().assets.filter(a => a.asset_id !== id) })
        return true
      }
      return false
    } catch {
      return false
    }
  },

  providers: [],

  loadProviders: async (capability) => {
    try {
      const res = await directorApi.listProviders(capability)
      set({ providers: res.providers || [] })
    } catch { /* ignore */ }
  },

  stages: [],

  loadStages: async () => {
    try {
      const res = await directorApi.listStages()
      set({ stages: res.stages || [] })
    } catch { /* ignore */ }
  },

  executeStage: async (params) => {
    try {
      // 异步模式：提交任务 → 轮询状态
      const submitResult = await directorApi.executeStage({ ...params, async_mode: true })

      // 如果返回了 task_id，轮询等待结果
      if (submitResult?.task_id) {
        const taskId = submitResult.task_id
        const maxPollMs = 600000 // 最长轮询 10 分钟
        const pollIntervalMs = 2000 // 每 2 秒查询一次
        const startTime = Date.now()

        while (Date.now() - startTime < maxPollMs) {
          await new Promise(r => setTimeout(r, pollIntervalMs))
          const task = await directorApi.getStageTask(taskId)

          if (task?.status === 'completed') {
            return { success: true, asset: task.asset, elapsed_ms: task.elapsed_ms }
          }
          if (task?.status === 'failed') {
            return { success: false, error: task.error || '执行失败' }
          }
        }
        return { success: false, error: '任务超时' }
      }

      // 兼容：如果后端返回同步结果（async_mode=false）
      return submitResult
    } catch {
      return { success: false, error: '执行失败' }
    }
  },

  selectedAssetIds: [],

  toggleAssetSelection: (id) => {
    const current = get().selectedAssetIds
    set({
      selectedAssetIds: current.includes(id)
        ? current.filter(i => i !== id)
        : [...current, id],
    })
  },

  clearSelection: () => set({ selectedAssetIds: [] }),

  // 批量任务实时状态
  batchProgress: {},

  updateBatchProgress: (batchId, progress) => {
    set({
      batchProgress: {
        ...get().batchProgress,
        [batchId]: progress,
      },
    })
  },

  clearBatchProgress: (batchId) => {
    const next = { ...get().batchProgress }
    delete next[batchId]
    set({ batchProgress: next })
  },
}))
