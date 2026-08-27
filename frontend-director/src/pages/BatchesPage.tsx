import { useEffect, useState, useCallback, useRef } from 'react'
import {
  Typography, Button, Space, Table, Modal, Form, Select, Input, InputNumber,
  message, Tag, Progress, Card, Steps, Popconfirm, Empty, Switch, Tooltip,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, PlayCircleOutlined, StopOutlined,
  DeleteOutlined, RedoOutlined, ThunderboltOutlined, EyeOutlined,
  ApartmentOutlined, ApiOutlined,
} from '@ant-design/icons'
import {
  batchService, BatchTask, BatchStep, BatchWebSocket, WsEvent,
} from '../services/directorApi'
import { useDirectorStore } from '../stores/directorStore'
import { useProject } from '../contexts/ProjectContext'
import ProjectSelector from '../components/ProjectSelector'

const { Title, Text, Paragraph } = Typography

// 步骤状态颜色映射
const STEP_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  skipped: 'warning',
}

const BATCH_STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
}

const BATCH_STATUS_TEXT: Record<string, string> = {
  pending: '待执行',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

// DAG 节点状态颜色
const DAG_NODE_COLOR: Record<string, string> = {
  pending: '#d9d9d9',
  running: '#1677ff',
  completed: '#52c41a',
  failed: '#ff4d4f',
  skipped: '#faad14',
}

// Stage 中文标签
const STAGE_LABEL: Record<string, string> = {
  concept: '概念图',
  angle: '三视图',
  pano: '全景图',
  storyboard: '分镜',
  batch_storyboard: '批量分镜',
  multi_person: '多人分镜',
  video: '视频',
  edit: '剪辑',
  export: '导出',
  refine: '精修',
}

export default function BatchesPage() {
  const { currentProjectId, currentProject } = useProject()
  const { stages, loadStages, assets, loadAssets } = useDirectorStore()
  const [batches, setBatches] = useState<BatchTask[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [detailBatch, setDetailBatch] = useState<BatchTask | null>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [form] = Form.useForm()
  const [steps, setSteps] = useState<BatchStep[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // DAG 可视化状态
  const [dagOpen, setDagOpen] = useState(false)
  const [dagData, setDagData] = useState<any>(null)
  const [dagLoading, setDagLoading] = useState(false)

  // WebSocket 实时进度
  const [wsEvents, setWsEvents] = useState<WsEvent[]>([])
  const [wsProgress, setWsProgress] = useState<{ completed: number; total: number; percent: number } | null>(null)
  const wsRef = useRef<BatchWebSocket | null>(null)

  // 加载批量任务列表
  const loadBatches = useCallback(async () => {
    setLoading(true)
    try {
      const res = await batchService.list({
        project_id: currentProjectId || undefined,
      })
      if (res.success) {
        setBatches(res.batches || [])
      }
    } catch (e) {
      // 忽略
    } finally {
      setLoading(false)
    }
  }, [currentProjectId])

  // 初始化加载
  useEffect(() => {
    loadBatches()
    loadStages()
    if (currentProjectId) {
      loadAssets({ project_id: currentProjectId })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [currentProjectId])

  // 轮询运行中的任务（WebSocket 降级方案，保留兼容）
  useEffect(() => {
    const hasRunning = batches.some(b => b.status === 'running')
    if (hasRunning) {
      pollRef.current = setInterval(loadBatches, 3000)
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [batches])

  // WebSocket 实时进度订阅（详情页打开时）
  useEffect(() => {
    if (!detailOpen || !detailBatch) return
    // 只对运行中的任务建立 WebSocket
    if (detailBatch.status !== 'running') return

    const ws = new BatchWebSocket(detailBatch.batch_id)
    wsRef.current = ws
    setWsEvents([])
    setWsProgress(null)

    ws.onEvent((event) => {
      setWsEvents(prev => [...prev.slice(-50), event])  // 保留最近50条
      if (event.progress) {
        setWsProgress({
          completed: event.progress.completed,
          total: event.progress.total,
          percent: event.progress.percent,
        })
        // 同步到 Zustand Store（建立 WebSocket → Store 单向数据流）
        useDirectorStore.getState().updateBatchProgress(detailBatch.batch_id, {
          completed: event.progress.completed,
          total: event.progress.total,
          percent: event.progress.percent,
          lastEvent: event.event,
        })
      }
      // 收到完成/失败事件时刷新列表
      if (['batch_completed', 'batch_failed'].includes(event.event)) {
        setTimeout(loadBatches, 500)
        if (detailBatch) {
          setTimeout(() => batchService.get(detailBatch.batch_id).then(r => setDetailBatch(r)), 500)
        }
        // 清理 Store 中的进度状态
        setTimeout(() => useDirectorStore.getState().clearBatchProgress(detailBatch.batch_id), 5000)
      }
    })

    ws.connect()

    return () => {
      ws.close()
      wsRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [detailOpen, detailBatch?.batch_id, detailBatch?.status])

  // 查看 DAG 结构
  const handleViewDag = async (batchId: string) => {
    setDagOpen(true)
    setDagLoading(true)
    setDagData(null)
    try {
      const res = await batchService.getDag(batchId)
      setDagData(res.dag)
    } catch (e) {
      message.error('获取 DAG 结构失败')
    } finally {
      setDagLoading(false)
    }
  }

  // 预检（dry-run）
  const handleDryRun = async (batchId: string) => {
    try {
      const res = await batchService.dryRun(batchId)
      if (res.success) {
        message.success('预检通过：DAG 结构合法，Provider 全部可用')
      }
    } catch (e: any) {
      const msg = e?.response?.data?.detail || '预检失败'
      message.error(msg)
    }
  }

  // 创建批量任务
  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      if (!steps.length) {
        message.warning('请至少添加一个步骤')
        return
      }
      const res = await batchService.create({
        name: values.name,
        steps: steps.map((s, i) => ({
          ...s,
          step_id: s.step_id || `step_${i + 1}`,
        })),
        project_id: currentProjectId || '',
        stop_on_failure: values.stop_on_failure ?? true,
        auto_inherit_project: true,
      })
      if (res.success) {
        message.success('批量任务创建成功')
        setCreateOpen(false)
        form.resetFields()
        setSteps([])
        loadBatches()
        // 询问是否立即启动
        Modal.confirm({
          title: '是否立即启动？',
          content: '批量任务已创建，是否立即开始执行？',
          okText: '启动',
          cancelText: '稍后',
          onOk: async () => {
            await batchService.start(res.batch.batch_id)
            message.success('批量任务已启动')
            loadBatches()
          },
        })
      }
    } catch (e) {
      // 表单校验失败
    }
  }

  // 启动
  const handleStart = async (batchId: string) => {
    try {
      await batchService.start(batchId)
      message.success('批量任务已启动')
      loadBatches()
    } catch {
      message.error('启动失败')
    }
  }

  // 取消
  const handleCancel = async (batchId: string) => {
    try {
      await batchService.cancel(batchId)
      message.success('批量任务已取消')
      loadBatches()
    } catch {
      message.error('取消失败')
    }
  }

  // 重试
  const handleRetry = async (batchId: string) => {
    try {
      await batchService.retry(batchId)
      message.success('批量任务已重新启动')
      loadBatches()
    } catch {
      message.error('重试失败')
    }
  }

  // 删除
  const handleDelete = async (batchId: string) => {
    try {
      await batchService.delete(batchId)
      message.success('批量任务已删除')
      loadBatches()
    } catch {
      message.error('删除失败')
    }
  }

  // 查看详情
  const handleDetail = async (batch: BatchTask) => {
    try {
      const res = await batchService.get(batch.batch_id)
      if (res.success) {
        setDetailBatch(res.batch)
        setDetailOpen(true)
      }
    } catch {
      message.error('加载详情失败')
    }
  }

  // 添加步骤
  const addStep = () => {
    setSteps(prev => [...prev, {
      step_id: `step_${prev.length + 1}`,
      stage_id: '',
      name: '',
      input_asset_ids: [],
      input_from_steps: [],
      provider_id: '',
      params: {},
      max_retries: 0,
    }])
  }

  // 更新步骤
  const updateStep = (index: number, updates: Partial<BatchStep>) => {
    setSteps(prev => prev.map((s, i) => i === index ? { ...s, ...updates } : s))
  }

  // 删除步骤
  const removeStep = (index: number) => {
    setSteps(prev => prev.filter((_, i) => i !== index))
  }

  // 表格列定义
  const columns = [
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: BatchTask) => (
        <Space>
          <ThunderboltOutlined style={{ color: '#1677ff' }} />
          <a onClick={() => handleDetail(record)}>{name}</a>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: (status: string) => (
        <Tag color={BATCH_STATUS_COLOR[status]}>{BATCH_STATUS_TEXT[status] || status}</Tag>
      ),
    },
    {
      title: '进度',
      key: 'progress',
      render: (_: any, record: BatchTask) => {
        const completed = record.steps?.filter(s => s.status === 'completed').length || 0
        const total = record.steps?.length || 0
        const percent = total > 0 ? Math.round((completed / total) * 100) : 0
        return (
          <Space direction="vertical" style={{ width: 120 }}>
            <Progress percent={percent} size="small" />
            <Text type="secondary" style={{ fontSize: 12 }}>{completed}/{total} 步骤</Text>
          </Space>
        )
      },
    },
    {
      title: '步骤数',
      dataIndex: 'steps',
      key: 'steps',
      render: (steps: BatchStep[]) => steps?.length || 0,
      width: 80,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (t: number) => t ? new Date(t * 1000).toLocaleString('zh-CN') : '-',
      width: 180,
    },
    {
      title: '操作',
      key: 'action',
      width: 320,
      render: (_: any, record: BatchTask) => (
        <Space size="small">
          <Tooltip title="详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => handleDetail(record)} />
          </Tooltip>
          <Tooltip title="DAG 图谱">
            <Button size="small" icon={<ApartmentOutlined />} onClick={() => handleViewDag(record.batch_id)} />
          </Tooltip>
          <Tooltip title="预检（DAG+Provider）">
            <Button size="small" icon={<ApiOutlined />} onClick={() => handleDryRun(record.batch_id)} />
          </Tooltip>
          {record.status === 'pending' && (
            <Tooltip title="启动">
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => handleStart(record.batch_id)} />
            </Tooltip>
          )}
          {record.status === 'running' && (
            <Popconfirm title="确定取消？" onConfirm={() => handleCancel(record.batch_id)}>
              <Tooltip title="取消">
                <Button size="small" danger icon={<StopOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {(record.status === 'failed' || record.status === 'cancelled') && (
            <Tooltip title="重试（断点续跑）">
              <Button size="small" icon={<RedoOutlined />} onClick={() => handleRetry(record.batch_id)} />
            </Tooltip>
          )}
          {record.status !== 'running' && (
            <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record.batch_id)}>
              <Tooltip title="删除">
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      {/* 头部 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Title level={3} style={{ margin: 0 }}>批量任务</Title>
          <ProjectSelector />
        </Space>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建批量任务
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadBatches}>刷新</Button>
        </Space>
      </div>

      {currentProject && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space>
            <Text type="secondary">当前项目：</Text>
            <Tag color="blue">{currentProject.name}</Tag>
            <Text type="secondary">新建批量任务将自动归属此项目，步骤产物自动继承项目归属。</Text>
          </Space>
        </Card>
      )}

      {/* 任务列表 */}
      <Table
        columns={columns}
        dataSource={batches}
        rowKey="batch_id"
        loading={loading}
        pagination={{ pageSize: 10 }}
        locale={{ emptyText: <Empty description="暂无批量任务" /> }}
      />

      {/* 创建批量任务弹窗 */}
      <Modal
        title="新建批量任务"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => { setCreateOpen(false); form.resetFields(); setSteps([]) }}
        width={800}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" initialValues={{ stop_on_failure: true }}>
          <Form.Item name="name" label="任务名称" rules={[{ required: true, message: '请输入任务名称' }]}>
            <Input placeholder="例如：角色A的分镜到视频流水线" />
          </Form.Item>
          <Form.Item name="stop_on_failure" label="失败时停止" valuePropName="checked">
            <Switch />
          </Form.Item>

          <div style={{ marginBottom: 8 }}>
            <Space style={{ width: '100%', justifyContent: 'space-between' }}>
              <Text strong>步骤编排</Text>
              <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>
            </Space>
          </div>

          {steps.length === 0 && (
            <Empty description={'点击"添加步骤"开始编排'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
          )}

          {steps.map((step, index) => (
            <Card
              key={index}
              size="small"
              style={{ marginBottom: 8 }}
              title={`步骤 ${index + 1}${step.name ? '：' + step.name : ''}`}
              extra={
                <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => removeStep(index)} />
              }
            >
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Space style={{ width: '100%' }}>
                  <Input
                    placeholder="步骤名称（可选）"
                    style={{ width: 200 }}
                    value={step.name}
                    onChange={e => updateStep(index, { name: e.target.value })}
                  />
                  <Select
                    placeholder="选择阶段"
                    style={{ width: 200 }}
                    value={step.stage_id || undefined}
                    onChange={v => updateStep(index, { stage_id: v })}
                    options={stages.map((s: any) => ({ value: s.stage_id, label: s.name }))}
                  />
                  <InputNumber
                    placeholder="重试次数"
                    min={0}
                    max={5}
                    style={{ width: 100 }}
                    value={step.max_retries}
                    onChange={v => updateStep(index, { max_retries: v || 0 })}
                  />
                </Space>
                <Space style={{ width: '100%' }} wrap>
                  <Text type="secondary" style={{ fontSize: 12 }}>输入资产：</Text>
                  <Select
                    mode="multiple"
                    placeholder="选择固定资产（可选）"
                    style={{ minWidth: 300 }}
                    value={step.input_asset_ids}
                    onChange={v => updateStep(index, { input_asset_ids: v })}
                    options={assets.map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
                    showSearch
                    optionFilterProp="label"
                  />
                </Space>
                {index > 0 && (
                  <Space style={{ width: '100%' }} wrap>
                    <Text type="secondary" style={{ fontSize: 12 }}>引用前序步骤输出：</Text>
                    <Select
                      mode="multiple"
                      placeholder="选择依赖的步骤（输出将作为本步骤输入）"
                      style={{ minWidth: 400 }}
                      value={step.input_from_steps}
                      onChange={v => updateStep(index, { input_from_steps: v })}
                      options={steps.slice(0, index).map((s, i) => ({
                        value: s.step_id || `step_${i + 1}`,
                        label: `步骤 ${i + 1}${s.name ? '：' + s.name : ''}`,
                      }))}
                    />
                  </Space>
                )}
              </Space>
            </Card>
          ))}

          {steps.length > 0 && (
            <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 12 }}>
              提示：步骤将按顺序执行。引用前序步骤输出的资产会自动作为当前步骤的输入。所有产物自动归属当前项目。
            </Paragraph>
          )}
        </Form>
      </Modal>

      {/* 详情弹窗 */}
      <Modal
        title={detailBatch ? `批量任务详情：${detailBatch.name}` : '详情'}
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={760}
      >
        {detailBatch && (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            <Space wrap>
              <Tag color={BATCH_STATUS_COLOR[detailBatch.status]}>
                {BATCH_STATUS_TEXT[detailBatch.status] || detailBatch.status}
              </Tag>
              <Text type="secondary">ID: {detailBatch.batch_id}</Text>
              {detailBatch.error && <Text type="danger">错误: {detailBatch.error}</Text>}
            </Space>

            {/* WebSocket 实时进度 */}
            {wsProgress && (
              <Card size="small" title="实时进度（WebSocket）" style={{ background: '#f6ffed' }}>
                <Progress percent={wsProgress.percent} status="active" />
                <Text type="secondary">
                  {wsProgress.completed}/{wsProgress.total} 步骤完成
                </Text>
              </Card>
            )}

            {/* WebSocket 事件流 */}
            {wsEvents.length > 0 && (
              <Card size="small" title={`实时事件（${wsEvents.length}）`} style={{ maxHeight: 200, overflow: 'auto' }}>
                {wsEvents.slice(-8).reverse().map((e, i) => (
                  <div key={i} style={{ fontSize: 12, padding: '2px 0' }}>
                    <Tag color={
                      e.event === 'step_completed' ? 'success' :
                      e.event === 'step_failed' || e.event === 'batch_failed' ? 'error' :
                      e.event === 'step_skipped' ? 'warning' :
                      'processing'
                    } style={{ fontSize: 11 }}>
                      {e.event.replace(/_/g, ' ')}
                    </Tag>
                    <Text type="secondary">{e.message}</Text>
                  </div>
                ))}
              </Card>
            )}

            <Steps
              direction="vertical"
              size="small"
              current={detailBatch.current_step_index}
              items={detailBatch.steps.map((s, i) => ({
                title: `步骤 ${i + 1}：${s.name || s.stage_id}`,
                description: (
                  <Space direction="vertical" size="small">
                    <Text type="secondary">阶段: {STAGE_LABEL[s.stage_id] || s.stage_id}</Text>
                    <Tag color={STEP_STATUS_COLOR[s.status || 'pending']}>
                      {s.status === 'completed' ? '已完成' :
                       s.status === 'running' ? '运行中' :
                       s.status === 'failed' ? '失败' :
                       s.status === 'skipped' ? '已跳过' : '待执行'}
                    </Tag>
                    {s.output_asset_id && (
                      <Text type="success" style={{ fontSize: 12 }}>
                        输出资产: {s.output_asset_id}
                      </Text>
                    )}
                    {s.error && <Text type="danger" style={{ fontSize: 12 }}>{s.error}</Text>}
                    {(s.elapsed_ms || 0) > 0 && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        耗时: {((s.elapsed_ms || 0) / 1000).toFixed(1)}s
                        {(s.retry_count || 0) > 0 && ` · 重试 ${s.retry_count} 次`}
                      </Text>
                    )}
                  </Space>
                ),
                status: s.status === 'completed' ? 'finish' :
                        s.status === 'running' ? 'process' :
                        s.status === 'failed' ? 'error' :
                        s.status === 'skipped' ? 'wait' : 'wait',
              }))}
            />

            {detailBatch.status === 'running' && (
              <Button danger icon={<StopOutlined />} onClick={() => handleCancel(detailBatch.batch_id)}>
                取消任务
              </Button>
            )}
            {(detailBatch.status === 'failed' || detailBatch.status === 'cancelled') && (
              <Button type="primary" icon={<RedoOutlined />} onClick={() => handleRetry(detailBatch.batch_id)}>
                重试任务（断点续跑）
              </Button>
            )}
          </Space>
        )}
      </Modal>

      {/* DAG 可视化弹窗 */}
      <Modal
        title="DAG 任务图谱"
        open={dagOpen}
        onCancel={() => setDagOpen(false)}
        footer={null}
        width={900}
      >
        {dagLoading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>
        ) : dagData ? (
          <DagVisualization dag={dagData} />
        ) : (
          <Empty description="无 DAG 数据" />
        )}
      </Modal>
    </div>
  )
}

// ============================================================
// DAG 可视化组件（纯 SVG 实现，无需第三方依赖）
// ============================================================
function DagVisualization({ dag }: { dag: any }) {
  if (!dag || !dag.layers) {
    return <Empty description="无 DAG 数据" />
  }

  if (dag.error) {
    return <Text type="danger">DAG 错误: {dag.error}</Text>
  }

  const { layers, edges, nodes } = dag

  // 布局参数
  const nodeWidth = 140
  const nodeHeight = 60
  const layerGap = 120
  const nodeGap = 20
  const padding = 40

  // 计算每层节点的位置
  const positions = new Map<string, { x: number; y: number }>()
  layers.forEach((layer: any) => {
    const layerNodes = layer.steps
    const totalWidth = layerNodes.length * (nodeWidth + nodeGap) - nodeGap
    const startX = padding + (Math.max(...layers.map((l: any) => l.steps.length)) * (nodeWidth + nodeGap) - totalWidth) / 2
    layerNodes.forEach((stepId: string, i: number) => {
      positions.set(stepId, {
        x: startX + i * (nodeWidth + nodeGap),
        y: padding + layer.layer * layerGap,
      })
    })
  })

  const maxNodesInLayer = Math.max(...layers.map((l: any) => l.steps.length))
  const svgWidth = padding * 2 + maxNodesInLayer * (nodeWidth + nodeGap)
  const svgHeight = padding * 2 + layers.length * layerGap

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Tag color="blue">{layers.length} 层</Tag>
        <Tag color="blue">{nodes.length} 节点</Tag>
        <Tag color="blue">{edges.length} 依赖关系</Tag>
      </Space>

      <div style={{ overflow: 'auto', maxHeight: 500, border: '1px solid #f0f0f0', borderRadius: 4 }}>
        <svg width={svgWidth} height={svgHeight} style={{ background: '#fafafa' }}>
          {/* 绘制边（依赖关系） */}
          {edges.map((edge: any, i: number) => {
            const from = positions.get(edge.from)
            const to = positions.get(edge.to)
            if (!from || !to) return null
            const fromX = from.x + nodeWidth / 2
            const fromY = from.y + nodeHeight
            const toX = to.x + nodeWidth / 2
            const toY = to.y
            const midY = (fromY + toY) / 2
            return (
              <path
                key={`edge-${i}`}
                d={`M ${fromX} ${fromY} C ${fromX} ${midY}, ${toX} ${midY}, ${toX} ${toY}`}
                stroke="#bfbfbf"
                strokeWidth={1.5}
                fill="none"
                markerEnd="url(#arrowhead)"
              />
            )
          })}

          {/* 箭头标记 */}
          <defs>
            <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
              <polygon points="0 0, 8 3, 0 6" fill="#bfbfbf" />
            </marker>
          </defs>

          {/* 绘制节点 */}
          {nodes.map((node: any) => {
            const pos = positions.get(node.id)
            if (!pos) return null
            const color = DAG_NODE_COLOR[node.status] || DAG_NODE_COLOR.pending
            return (
              <g key={node.id}>
                <rect
                  x={pos.x}
                  y={pos.y}
                  width={nodeWidth}
                  height={nodeHeight}
                  rx={6}
                  fill="white"
                  stroke={color}
                  strokeWidth={2}
                />
                <text
                  x={pos.x + nodeWidth / 2}
                  y={pos.y + 22}
                  textAnchor="middle"
                  fontSize={12}
                  fontWeight="bold"
                  fill="#333"
                >
                  {node.name?.length > 12 ? node.name.slice(0, 12) + '...' : node.name || node.id}
                </text>
                <text
                  x={pos.x + nodeWidth / 2}
                  y={pos.y + 40}
                  textAnchor="middle"
                  fontSize={11}
                  fill="#999"
                >
                  {STAGE_LABEL[node.stage_id] || node.stage_id}
                </text>
                <circle
                  cx={pos.x + nodeWidth - 10}
                  cy={pos.y + 10}
                  r={4}
                  fill={color}
                />
              </g>
            )
          })}
        </svg>
      </div>

      {/* 图例 */}
      <Space style={{ marginTop: 12 }}>
        {Object.entries(DAG_NODE_COLOR).map(([status, color]) => (
          <Space key={status} size={4}>
            <div style={{ width: 12, height: 12, borderRadius: 2, background: color }} />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {status === 'pending' ? '待执行' :
               status === 'running' ? '运行中' :
               status === 'completed' ? '已完成' :
               status === 'failed' ? '失败' : '已跳过'}
            </Text>
          </Space>
        ))}
      </Space>
    </div>
  )
}
