import { create } from 'zustand'
import { canvasService } from '../services/directorApi'

export interface CanvasNode {
  node_id: string
  asset_id: string
  node_type: 'image' | 'video' | 'text' | 'group'
  x: number
  y: number
  width: number
  height: number
  label: string
  metadata: Record<string, any>
  // 后端 to_dict() 展开的额外字段
  url?: string
  [key: string]: any
}

export interface CanvasEdge {
  edge_id: string
  source_id: string
  target_id: string
  source_port: string
  target_port: string
  label: string
}

export interface CanvasViewport {
  x: number
  y: number
  zoom: number
}

export interface CanvasLayout {
  canvas_id: string
  name: string
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  viewport: CanvasViewport
  created_at: number
  updated_at: number
}

interface CanvasState {
  // 画布列表
  canvases: Array<{ canvas_id: string; name: string; node_count: number; updated_at: number }>
  // 当前画布
  activeCanvasId: string | null
  activeCanvas: CanvasLayout | null
  loading: boolean
  // 保存错误状态（供组件层提示用户）
  saveError: string | null

  // 操作
  loadCanvases: () => Promise<void>
  createCanvas: (name?: string) => Promise<CanvasLayout | null>
  openCanvas: (canvasId: string) => Promise<void>
  saveCanvas: () => Promise<void>
  deleteCanvas: (canvasId: string) => Promise<void>

  // 节点操作（本地优先 + 自动保存）
  addNode: (node: CanvasNode) => void
  updateNode: (nodeId: string, data: Partial<CanvasNode>) => void
  removeNode: (nodeId: string) => void
  addEdge: (edge: CanvasEdge) => void
  removeEdge: (edgeId: string) => void
  setViewport: (viewport: CanvasViewport) => void
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  canvases: [],
  activeCanvasId: null,
  activeCanvas: null,
  loading: false,
  saveError: null,

  loadCanvases: async () => {
    set({ loading: true })
    try {
      const res = await canvasService.list()
      set({ canvases: res.canvases || [] })
    } catch {
      set({ canvases: [] })
    } finally {
      set({ loading: false })
    }
  },

  createCanvas: async (name?: string) => {
    try {
      const res = await canvasService.create(name)
      const canvas = res.canvas as CanvasLayout
      set(state => ({
        canvases: [{ canvas_id: canvas.canvas_id, name: canvas.name, node_count: 0, updated_at: canvas.updated_at }, ...state.canvases],
        activeCanvasId: canvas.canvas_id,
        activeCanvas: canvas,
      }))
      return canvas
    } catch {
      return null
    }
  },

  openCanvas: async (canvasId: string) => {
    try {
      const res = await canvasService.get(canvasId)
      set({ activeCanvasId: canvasId, activeCanvas: res.canvas })
    } catch {
      // ignore
    }
  },

  saveCanvas: async () => {
    const { activeCanvasId, activeCanvas } = get()
    if (!activeCanvasId || !activeCanvas) return
    try {
      const resp = await canvasService.update(activeCanvasId, {
        nodes: activeCanvas.nodes,
        edges: activeCanvas.edges,
        viewport: activeCanvas.viewport,
        // 传递 base_updated_at 实现乐观锁，防止并发覆盖
        base_updated_at: activeCanvas.updated_at,
      })
      // 后端返回最新 canvas（含新的 updated_at），同步到本地状态
      // 否则下次保存时 base_updated_at 仍是旧值，会触发误报冲突
      const serverCanvas = resp?.canvas
      if (serverCanvas && typeof serverCanvas.updated_at === 'number') {
        set({
          activeCanvas: { ...activeCanvas, updated_at: serverCanvas.updated_at },
          saveError: null,
        })
      } else {
        set({ saveError: null })
      }
    } catch (err: any) {
      // 记录错误状态，供组件层提示用户
      const status = err?.response?.status
      let msg = '画布保存失败'
      if (status === 409) {
        msg = '画布已被其他客户端修改，请刷新后重试'
        // 409 响应体中包含服务端最新 canvas，自动同步以避免用户手动刷新
        const serverCanvas = err?.response?.data?.detail?.canvas
        if (serverCanvas) {
          set({
            activeCanvas: serverCanvas,
            saveError: msg,
          })
          return
        }
      } else if (err?.message) {
        msg = `画布保存失败: ${err.message}`
      }
      set({ saveError: msg })
      console.error('[CanvasStore] saveCanvas 失败:', err)
    }
  },

  deleteCanvas: async (canvasId: string) => {
    try {
      await canvasService.delete(canvasId)
      set(state => ({
        canvases: state.canvases.filter(c => c.canvas_id !== canvasId),
        activeCanvasId: state.activeCanvasId === canvasId ? null : state.activeCanvasId,
        activeCanvas: state.activeCanvasId === canvasId ? null : state.activeCanvas,
      }))
    } catch {
      // ignore
    }
  },

  addNode: (node: CanvasNode) => {
    set(state => {
      if (!state.activeCanvas) return state
      const canvas = { ...state.activeCanvas, nodes: [...state.activeCanvas.nodes, node] }
      return { activeCanvas: canvas }
    })
    // 自动保存（防抖）
    debounceSave(get)
  },

  updateNode: (nodeId: string, data: Partial<CanvasNode>) => {
    set(state => {
      if (!state.activeCanvas) return state
      const nodes = state.activeCanvas.nodes.map(n =>
        n.node_id === nodeId ? { ...n, ...data } : n
      )
      return { activeCanvas: { ...state.activeCanvas, nodes } }
    })
    debounceSave(get)
  },

  removeNode: (nodeId: string) => {
    set(state => {
      if (!state.activeCanvas) return state
      return {
        activeCanvas: {
          ...state.activeCanvas,
          nodes: state.activeCanvas.nodes.filter(n => n.node_id !== nodeId),
          edges: state.activeCanvas.edges.filter(e => e.source_id !== nodeId && e.target_id !== nodeId),
        },
      }
    })
    debounceSave(get)
  },

  addEdge: (edge: CanvasEdge) => {
    set(state => {
      if (!state.activeCanvas) return state
      return { activeCanvas: { ...state.activeCanvas, edges: [...state.activeCanvas.edges, edge] } }
    })
    debounceSave(get)
  },

  removeEdge: (edgeId: string) => {
    set(state => {
      if (!state.activeCanvas) return state
      return { activeCanvas: { ...state.activeCanvas, edges: state.activeCanvas.edges.filter(e => e.edge_id !== edgeId) } }
    })
    debounceSave(get)
  },

  setViewport: (viewport: CanvasViewport) => {
    set(state => {
      if (!state.activeCanvas) return state
      return { activeCanvas: { ...state.activeCanvas, viewport } }
    })
    debounceSave(get)
  },
}))

// 防抖保存
let _saveTimer: ReturnType<typeof setTimeout> | null = null
function debounceSave(get: () => CanvasState) {
  if (_saveTimer) clearTimeout(_saveTimer)
  // 捕获当前画布 ID 快照，防止切换画布后把新画布数据保存到旧画布 ID
  const targetCanvasId = get().activeCanvasId
  _saveTimer = setTimeout(() => {
    _saveTimer = null
    const state = get()
    // 验证当前激活画布仍是 debounce 时捕获的画布，避免竞态条件
    if (state.activeCanvasId !== targetCanvasId) {
      // 画布已切换，放弃本次保存（新画布的修改会触发新的 debounce）
      return
    }
    state.saveCanvas()
  }, 1000)
}
