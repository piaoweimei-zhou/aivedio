import { useCanvasStore } from '../stores/canvasStore'

export interface CanvasUpdateEvent {
  type: string
  canvas_id: string
  action: string
  data?: Record<string, any>
}

/** 影响当前画布内容的动作，触发重新拉取；列表级动作（created/deleted）单独处理 */
const CONTENT_ACTIONS = new Set([
  'node_added',
  'node_updated',
  'node_removed',
  'edge_added',
  'edge_removed',
  'layout_updated',
])

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let refreshTimer: ReturnType<typeof setTimeout> | null = null

function getWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.hostname
  const port = import.meta.env.VITE_API_PORT || '8000'
  return `${protocol}//${host}:${port}/api/ws/canvas`
}

function connect(): void {
  ws = new WebSocket(getWsUrl())

  ws.onopen = () => {
    console.log('[CanvasWS] 连接成功')
  }

  ws.onmessage = (e) => {
    try {
      const evt: CanvasUpdateEvent = JSON.parse(e.data)
      handleEvent(evt)
    } catch (err) {
      console.error('[CanvasWS] 解析消息失败', err)
    }
  }

  ws.onerror = () => {
    ws?.close()
  }

  ws.onclose = () => {
    console.log('[CanvasWS] 连接关闭，10s 后重连')
    scheduleReconnect()
  }
}

function scheduleReconnect(): void {
  if (reconnectTimer) return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect()
  }, 10000)
}

function handleEvent(evt: CanvasUpdateEvent): void {
  if (evt.type !== 'canvas_update') return
  const { activeCanvasId, openCanvas, loadCanvases } = useCanvasStore.getState()

  // 列表级变更：刷新画布列表
  if (evt.action === 'created' || evt.action === 'deleted') {
    loadCanvases()
    return
  }
  // 仅关注影响当前打开画布内容的变更
  if (!CONTENT_ACTIONS.has(evt.action)) return
  if (!evt.canvas_id || evt.canvas_id !== activeCanvasId) return

  // 合并短时间内的连续事件，避免拖动过程高频刷新
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    openCanvas(evt.canvas_id)
  }, 300)
}

/** 建立画布实时订阅（幂等：已连接或连接中则跳过） */
export function initCanvasRealtime(): void {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return
  connect()
}