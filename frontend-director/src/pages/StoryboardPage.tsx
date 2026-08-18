import { useEffect, useState, useCallback, useRef } from 'react'
import { Typography, Select, Button, Space, message, Segmented } from 'antd'
import { PlayCircleOutlined, LayoutOutlined, GlobalOutlined } from '@ant-design/icons'
import { useDirectorStore } from '../stores/directorStore'
import { useCanvasStore } from '../stores/canvasStore'
import CanvasPanel from '../components/CanvasPanel'
import IframeEmbed from '../components/IframeEmbed'
import ProjectSelector from '../components/ProjectSelector'
import { initCanvasRealtime } from '../services/canvasRealtime'
import { styleApi, StyleOption } from '../services/directorApi'

const { Title, Text } = Typography

interface StoryboardFrame {
  asset_id: string
  name: string
  urls: string[]
  metadata: Record<string, any>
}

// 分镜工作流模板选项
const STORYBOARD_TEMPLATES = [
  { label: '默认分镜(换装)', value: '' },
  { label: '单人分镜', value: 'single_person' },
  { label: '双人融合分镜', value: 'dual_person' },
  { label: '本地多人分镜', value: 'local_multi' },
  { label: 'GPT分镜', value: 'gpt_storyboard' },
]

export default function StoryboardPage() {
  const { assets, loadAssets, selectedAssetIds, executeStage, stages, loadStages } = useDirectorStore()
  const canvasStore = useCanvasStore()
  const [frames, setFrames] = useState<StoryboardFrame[]>([])
  const [providerId, setProviderId] = useState('comfyui')
  const [templateType, setTemplateType] = useState('')
  const [styleId, setStyleId] = useState('')
  const [styles, setStyles] = useState<StyleOption[]>([])
  const [loading, setLoading] = useState(false)
  const [availableProviders, setAvailableProviders] = useState<string[]>([])
  const [viewMode, setViewMode] = useState<'canvas' | 'infinite'>('canvas')

  useEffect(() => {
    loadAssets({ asset_type: 'storyboard' })
    loadStages()
    // 建立画布实时订阅：接收其他客户端对当前画布的变更并刷新
    initCanvasRealtime()
    // 加载网感风格
    styleApi.list().then(res => {
      const list = res.styles || []
      setStyles(list)
      const def = list.find(s => s.is_default)
      if (def) setStyleId(def.style_id)
    }).catch(() => {})
  }, [])

  // 获取分镜阶段支持的供应商
  useEffect(() => {
    const storyboardStage = stages.find((s: any) => s.stage_id === 'storyboard')
    if (storyboardStage) {
      setAvailableProviders(storyboardStage.supported_providers || ['comfyui'])
    }
  }, [stages])

  // 从资产列表提取分镜帧
  useEffect(() => {
    const storyboardAssets = assets.filter(a => a.asset_type === 'storyboard')
    setFrames(storyboardAssets.map(a => ({
      asset_id: a.asset_id,
      name: a.name,
      urls: a.urls || [],
      metadata: a.metadata || {},
    })))
  }, [assets])

  // 切换到画布视图时自动初始化画布
  // 依赖 frames：资产异步加载完成后 frames 才有数据，需要重新执行以添加节点
  const canvasInitRef = useRef(false)
  useEffect(() => {
    if (viewMode !== 'canvas') {
      canvasInitRef.current = false
      return
    }
    // 仅在首次进入 canvas 模式或 frames 从空变为有数据时初始化
    if (canvasInitRef.current && frames.length === 0) return
    canvasInitRef.current = true

    const initCanvas = async () => {
      const store = useCanvasStore
      const state = store.getState()
      const { loadCanvases, createCanvas, openCanvas, addNode } = state

      await loadCanvases()
      const curState = store.getState()
      const canvases = curState.canvases
      let activeCanvasId = curState.activeCanvasId

      if (!activeCanvasId) {
        if (canvases.length > 0) {
          activeCanvasId = canvases[0].canvas_id
          await openCanvas(activeCanvasId)
        } else {
          const newCanvas = await createCanvas('分镜画布')
          if (newCanvas) activeCanvasId = newCanvas.canvas_id
        }
      }

      if (frames.length > 0 && activeCanvasId) {
        const afterState = store.getState()
        const currentNodes = afterState.activeCanvas?.nodes || []
        const existingAssetIds = new Set(currentNodes.map(n => n.asset_id))
        const newFrames = frames.filter(f => !existingAssetIds.has(f.asset_id))
        newFrames.forEach((frame, i) => {
          addNode({
            node_id: `sb_${frame.asset_id}`,
            asset_id: frame.asset_id,
            node_type: 'image',
            x: 50 + (currentNodes.length + i) * 280,
            y: 100,
            width: 240,
            height: 180,
            label: frame.name,
            metadata: { urls: frame.urls },
          })
        })
        if (newFrames.length > 0) {
          message.success(`已将 ${newFrames.length} 个分镜帧添加到画布`)
        }
      }
    }
    initCanvas()
  }, [viewMode, frames])

  const handleGenerate = useCallback(async () => {
    if (selectedAssetIds.length === 0) {
      message.warning('请先在资产库中选择角色和场景资产')
      return
    }
    setLoading(true)
    try {
      const params: Record<string, any> = { size: '1365x768' }
      if (templateType) {
        params.template = templateType
      }
      if (styleId) {
        params.style_id = styleId
      }
      const result = await executeStage({
        stage_id: 'storyboard',
        input_asset_ids: selectedAssetIds,
        provider_id: providerId,
        params,
      })
      if (result?.success) {
        message.success('分镜生成成功')
        loadAssets({ asset_type: 'storyboard' })
      } else {
        message.error(result?.error || '分镜生成失败')
      }
    } catch (e: any) {
      message.error(e.message || '分镜生成失败')
    } finally {
      setLoading(false)
    }
  }, [selectedAssetIds, providerId, templateType, executeStage, loadAssets])

  return (
    <div style={{ padding: 24, height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 工具栏 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Title level={3} style={{ margin: 0 }}>分镜画布</Title>
          <ProjectSelector />
        </Space>
        <Space>
          <Segmented
            value={viewMode}
            onChange={(v) => setViewMode(v as 'canvas' | 'infinite')}
            options={[
              { label: '画布', value: 'canvas', icon: <LayoutOutlined /> },
              { label: '无限画布', value: 'infinite', icon: <GlobalOutlined /> },
            ]}
          />
          <Text>工作流:</Text>
          <Select
            value={templateType}
            onChange={setTemplateType}
            style={{ width: 150 }}
            options={STORYBOARD_TEMPLATES}
          />
          <Text>风格:</Text>
          <Select
            value={styleId}
            onChange={setStyleId}
            style={{ width: 140 }}
            options={styles.map(s => ({ label: s.name, value: s.style_id }))}
            placeholder="网感风格"
          />
          <Text>供应商:</Text>
          <Select
            value={providerId}
            onChange={setProviderId}
            style={{ width: 140 }}
            options={availableProviders.map(p => ({ label: p, value: p }))}
          />
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleGenerate}
            loading={loading}
            disabled={selectedAssetIds.length === 0}
          >
            生成分镜
          </Button>
        </Space>
      </div>

      {/* 提示 */}
      {selectedAssetIds.length > 0 && (
        <div style={{ marginBottom: 12, padding: '8px 12px', background: '#e6f7ff', borderRadius: 6 }}>
          <Text type="secondary">已选择 {selectedAssetIds.length} 个资产作为分镜输入</Text>
        </div>
      )}

      {/* 分镜帧视图 */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', position: 'relative' }}>
        {/* 无限画布：始终挂载但隐藏，避免每次切换重建 */}
        <div
          style={{
            position: 'absolute', inset: 0,
            display: viewMode === 'infinite' ? 'block' : 'none',
            zIndex: viewMode === 'infinite' ? 1 : -1,
          }}
        >
          <IframeEmbed
            src="/static/director/canvas.html"
            title="Infinite Canvas"
            style={{ height: '100%' }}
            onMessage={(data) => {
              if (data.type === 'asset-created') {
                loadAssets()
              }
            }}
          />
        </div>
        {/* 画布视图 */}
        {viewMode === 'canvas' ? (
          <div style={{ height: '100%' }}>
            <CanvasPanel />
          </div>
        ) : null}
      </div>
    </div>
  )
}
