import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  Node,
  Edge,
  NodeTypes,
  BackgroundVariant,
  Handle,
  Position,
  NodeProps,
  useReactFlow,
  ReactFlowProvider,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Image, Typography, Button, Modal, List, Tag, Input, Space, message, Alert } from 'antd'
import {
  PictureOutlined,
  VideoCameraOutlined,
  FileTextOutlined,
  AppstoreOutlined,
  PlusOutlined,
  SearchOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { downloadImage } from '../utils/download'
import { useCanvasStore, CanvasNode as CNode } from '../stores/canvasStore'
import { useDirectorStore } from '../stores/directorStore'

const { Text } = Typography

// ==================== 自定义节点 ====================

const typeIcons: Record<string, React.ReactNode> = {
  image: <PictureOutlined />,
  video: <VideoCameraOutlined />,
  text: <FileTextOutlined />,
  group: <AppstoreOutlined />,
}

const typeColors: Record<string, string> = {
  image: '#1677ff',
  video: '#ff4d4f',
  text: '#52c41a',
  group: '#722ed1',
}

function AssetNode({ data }: NodeProps) {
  const nodeType = data.nodeType as string || 'image'
  const label = data.label as string || ''
  const url = data.url as string || ''
  const color = typeColors[nodeType] || '#999'

  return (
    <div style={{
      background: '#fff',
      borderRadius: 8,
      border: `2px solid ${color}`,
      width: data.width as number || 240,
      minHeight: 160,
      overflow: 'hidden',
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
    }}>
      <Handle type="target" position={Position.Left} style={{ background: color }} />
      {/* 头部 */}
      <div style={{
        padding: '4px 8px',
        background: color,
        color: '#fff',
        fontSize: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 4,
      }}>
        {typeIcons[nodeType]}
        <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {label || nodeType}
        </span>
      </div>
      {/* 内容 */}
      <div style={{ padding: 8, textAlign: 'center' }}>
        {url && nodeType === 'image' ? (
          <div style={{ position: 'relative', display: 'inline-block' }}>
            <Image
              src={url}
              alt={label}
              style={{ maxHeight: 100, objectFit: 'contain' }}
              preview={false}
            />
            <Button
              type="text"
              icon={<DownloadOutlined />}
              size="small"
              style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', width: 20, height: 20, fontSize: 11, padding: 0, lineHeight: '20px', zIndex: 1 }}
              onClick={(e) => { e.stopPropagation(); downloadImage(url, label || 'image') }}
            />
          </div>
        ) : url && nodeType === 'video' ? (
          <video src={url} style={{ maxHeight: 100, width: '100%' }} muted />
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>{label || '空节点'}</Text>
        )}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: color }} />
    </div>
  )
}

const nodeTypes: NodeTypes = {
  asset: AssetNode,
}

// ==================== 资产选择器 ====================

function AssetPickerModal({
  open,
  onClose,
  onPick,
}: {
  open: boolean
  onClose: () => void
  onPick: (asset: any) => void
}) {
  const { assets, loadAssets } = useDirectorStore()
  const [search, setSearch] = useState('')

  useEffect(() => {
    if (open) loadAssets()
  }, [open, loadAssets])

  const filtered = assets.filter((a: any) =>
    !search || a.name?.toLowerCase().includes(search.toLowerCase()) || a.asset_type?.includes(search)
  )

  return (
    <Modal
      title="从资产库添加到画布"
      open={open}
      onCancel={onClose}
      footer={null}
      width={600}
    >
      <Input
        prefix={<SearchOutlined />}
        placeholder="搜索资产名称或类型..."
        value={search}
        onChange={e => setSearch(e.target.value)}
        style={{ marginBottom: 12 }}
        allowClear
      />
      <List
        dataSource={filtered}
        style={{ maxHeight: 400, overflow: 'auto' }}
        renderItem={(asset: any) => (
          <List.Item
            actions={[
              <Button key="add" type="link" size="small" onClick={() => onPick(asset)}>
                添加
              </Button>,
            ]}
          >
            <List.Item.Meta
              avatar={
                asset.urls?.[0] ? (
                  <Image src={asset.urls[0]} width={48} height={48} style={{ objectFit: 'cover', borderRadius: 4 }} preview={false} />
                ) : (
                  <div style={{ width: 48, height: 48, background: '#f0f0f0', borderRadius: 4, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <PictureOutlined style={{ color: '#999' }} />
                  </div>
                )
              }
              title={asset.name || asset.asset_id?.slice(0, 8)}
              description={
                <Space size={4}>
                  <Tag>{asset.asset_type}</Tag>
                  {asset.urls?.length > 1 && <Text type="secondary" style={{ fontSize: 11 }}>{asset.urls.length} 图</Text>}
                </Space>
              }
            />
          </List.Item>
        )}
      />
    </Modal>
  )
}

// ==================== 画布内部（需要 ReactFlowProvider） ====================

function CanvasInner() {
  const canvasStore = useCanvasStore()
  const { activeCanvas, addNode, updateNode, removeNode, addEdge: storeAddEdge, removeEdge, setViewport, saveError } = canvasStore
  const [pickerOpen, setPickerOpen] = useState(false)
  const screenToFlowPosition = useReactFlow().screenToFlowPosition

  // 将 CanvasNode 转为 ReactFlow Node
  // P2 修复：优先从 metadata.urls 取，再取 metadata.url/image_url，最后取 asset 的 urls[]
  const initialNodes: Node[] = useMemo(() =>
    (activeCanvas?.nodes || []).map(n => ({
      id: n.node_id,
      type: 'asset',
      position: { x: n.x, y: n.y },
      data: {
        nodeType: n.node_type,
        label: n.label,
        url: n.url || n.metadata?.urls?.[0] || n.metadata?.url || n.metadata?.image_url || n.metadata?.asset_urls?.[0] || '',
        width: n.width,
        height: n.height,
        assetId: n.asset_id,
      },
    })),
    [activeCanvas?.nodes]
  )

  const initialEdges: Edge[] = useMemo(() =>
    (activeCanvas?.edges || []).map(e => ({
      id: e.edge_id,
      source: e.source_id,
      target: e.target_id,
      sourceHandle: e.source_port,
      targetHandle: e.target_port,
      label: e.label,
      animated: true,
      style: { stroke: '#1677ff' },
    })),
    [activeCanvas?.edges]
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)

  useEffect(() => { setNodes(initialNodes) }, [initialNodes, setNodes])
  useEffect(() => { setEdges(initialEdges) }, [initialEdges, setEdges])

  const onConnect = useCallback((connection: Connection) => {
    setEdges(eds => addEdge({ ...connection, animated: true, style: { stroke: '#1677ff' } }, eds))
    if (connection.source && connection.target) {
      storeAddEdge({
        edge_id: `e_${Date.now()}`,
        source_id: connection.source,
        target_id: connection.target,
        source_port: connection.sourceHandle || 'output',
        target_port: connection.targetHandle || 'input',
        label: '',
      })
    }
  }, [setEdges, storeAddEdge])

  const onNodeDragStop = useCallback((_event: MouseEvent | TouchEvent, node: Node) => {
    updateNode(node.id, { x: node.position.x, y: node.position.y })
  }, [updateNode])

  const onNodesDelete = useCallback((deleted: Node[]) => {
    deleted.forEach(n => removeNode(n.id))
  }, [removeNode])

  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    deleted.forEach(e => removeEdge(e.id))
  }, [removeEdge])

  const onMoveEnd = useCallback((_event: unknown, viewport: { x: number; y: number; zoom: number }) => {
    setViewport(viewport)
  }, [setViewport])

  // 从资产选择器添加节点
  const handlePickAsset = useCallback((asset: any) => {
    const nodeType = asset.asset_type === 'video' ? 'video' : 'image'
    const url = asset.urls?.[0] || ''
    // 放置在画布中心偏移随机位置
    const offset = activeCanvas?.nodes?.length || 0
    addNode({
      node_id: `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      asset_id: asset.asset_id,
      node_type: nodeType,
      x: 100 + offset * 60,
      y: 100 + offset * 40,
      width: 240,
      height: 180,
      label: asset.name || asset.asset_id?.slice(0, 8),
      metadata: { url, asset_urls: asset.urls || [] },
    })
    setPickerOpen(false)
  }, [addNode, activeCanvas])

  if (!activeCanvas) {
    return (
      <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: '#999', gap: 16 }}>
        <AppstoreOutlined style={{ fontSize: 48, color: '#d9d9d9' }} />
        <Text type="secondary">暂无画布内容</Text>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => {
          canvasStore.createCanvas('新画布')
        }}>
          创建画布
        </Button>
      </div>
    )
  }

  return (
    <div style={{ height: '100%', width: '100%', position: 'relative' }}>
      {/* 保存错误提示 */}
      {saveError && (
        <Alert
          message={saveError}
          type="error"
          closable
          showIcon
          style={{ position: 'absolute', top: 12, left: '50%', transform: 'translateX(-50%)', zIndex: 20, maxWidth: 400 }}
          onClose={() => useCanvasStore.setState({ saveError: null })}
        />
      )}
      {/* 添加资产按钮 */}
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={() => setPickerOpen(true)}
        style={{ position: 'absolute', top: 12, left: 12, zIndex: 10 }}
      >
        添加资产
      </Button>

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeDragStop={onNodeDragStop}
        onNodesDelete={onNodesDelete}
        onEdgesDelete={onEdgesDelete}
        onMoveEnd={onMoveEnd}
        nodeTypes={nodeTypes}
        fitView
        deleteKeyCode="Delete"
        style={{ background: '#f5f5f5' }}
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls />
        <MiniMap
          nodeColor={(n) => typeColors[n.data?.nodeType as string] || '#999'}
          style={{ background: '#fafafa' }}
        />
      </ReactFlow>

      <AssetPickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={handlePickAsset}
      />
    </div>
  )
}

// ==================== 导出：包裹 ReactFlowProvider ====================

export default function CanvasPanel() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  )
}
