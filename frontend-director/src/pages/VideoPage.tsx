import { useEffect, useState, useCallback, useRef, useMemo } from 'react'
import { Typography, Form, Select, Input, InputNumber, Button, Space, Card, Progress, Table, message, Tag, Image, Row, Col, Tabs, Modal, Empty } from 'antd'
import { PlayCircleOutlined, ReloadOutlined, StopOutlined, VideoCameraOutlined, DownloadOutlined, UploadOutlined, MessageOutlined } from '@ant-design/icons'
import { videoService, msrVideoService } from '../services/directorApi'
import { useDirectorStore } from '../stores/directorStore'
import { downloadImage } from '../utils/download'
import ProjectSelector from '../components/ProjectSelector'
import { useProject } from '../contexts/ProjectContext'
import PromptPicker from '../components/PromptPicker'

const { Title, Text } = Typography
const { TextArea } = Input

interface VideoTask {
  task_id: string
  status: string
  provider_id: string
  prompt: string
  image_urls: string[]
  model: string
  duration: number
  aspect_ratio: string
  resolution: string
  video_url: string
  error: string
  progress: number
  elapsed_ms: number
  created_at: number
}

const PROVIDERS = [
  { label: 'MiniMax H3 (本地文本出片)', value: 'minimax_h3' },
  { label: '即梦 (Jimeng)', value: 'jimeng' },
  { label: 'RunningHub', value: 'runninghub' },
  { label: '火山引擎 (VolcEngine)', value: 'volcengine' },
]

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

export default function VideoPage() {
  const { assets, loadAssets, selectedAssetIds } = useDirectorStore()
  const { currentProjectId } = useProject()
  const [tasks, setTasks] = useState<VideoTask[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [form] = Form.useForm()
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [promptPickerOpen, setPromptPickerOpen] = useState(false)

  // MSR 状态
  const [msrSubmitting, setMsrSubmitting] = useState(false)
  const [msrRefs, setMsrRefs] = useState<{ ref1: string; ref2: string; ref3: string; ref4: string; bg: string }>({
    ref1: '', ref2: '', ref3: '', ref4: '', bg: '',
  })
  const [msrGlobalPrompt, setMsrGlobalPrompt] = useState('')
  const [msrLocalPrompts, setMsrLocalPrompts] = useState('')
  const [msrWidth, setMsrWidth] = useState(1280)
  const [msrHeight, setMsrHeight] = useState(720)
  const [msrFrameCount, setMsrFrameCount] = useState(41)
  const [msrSeed, setMsrSeed] = useState(39372529035560)
  // ⭐ 视频质量参数（与后端 canvas_api MsrVideoRequest 对齐）
  const [msrFps, setMsrFps] = useState(24)
  const [msrCfg, setMsrCfg] = useState(1.0)
  const [msrSteps, setMsrSteps] = useState(20)
  const [msrDuration, setMsrDuration] = useState<number | undefined>(undefined)
  const [msrTaskId, setMsrTaskId] = useState('')
  const [msrStatus, setMsrStatus] = useState('')
  const [msrResult, setMsrResult] = useState<any>(null)
  const msrPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // MSR 资产选择弹窗
  const [assetPickerRole, setAssetPickerRole] = useState<'ref1' | 'ref2' | 'ref3' | 'ref4' | 'bg' | null>(null)

  useEffect(() => {
    loadTasks()
    // D4 修复：确保资产列表已加载，供用户选择无限画布生成的结果作为视频输入
    loadAssets(currentProjectId ? { project_id: currentProjectId } : undefined)
    // 轮询任务状态
    pollRef.current = setInterval(loadTasks, 5000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [currentProjectId])

  const loadTasks = async () => {
    try {
      const res = await videoService.listTasks()
      if (res.success) {
        setTasks(res.tasks || [])
      }
    } catch { /* ignore */ }
  }

  // 获取选中资产的图片 URL
  const selectedImageUrls = useMemo(() => assets
    .filter(a => selectedAssetIds.includes(a.asset_id))
    .flatMap(a => a.urls || []), [assets, selectedAssetIds])

  const handleSubmit = useCallback(async (values: any) => {
    setSubmitting(true)
    try {
      const res = await videoService.submit({
        provider_id: values.provider_id,
        prompt: values.prompt || '',
        image_urls: selectedImageUrls.length > 0 ? selectedImageUrls : undefined,
        model: values.model || '',
        duration: values.duration || 5,
        aspect_ratio: values.aspect_ratio || '16:9',
        resolution: values.resolution || '480p',
        // ⭐ 补齐视频质量参数（后端 video_stage 已支持，与 MSR 表单对齐）
        steps: values.steps,
        cfg: values.cfg,
        fps: values.fps,
        seed: values.seed,
      })
      if (res.success) {
        message.success('视频生成任务已提交')
        loadTasks()
      } else {
        message.error('提交失败')
      }
    } catch (e: any) {
      message.error(e.message || '提交失败')
    } finally {
      setSubmitting(false)
    }
  }, [selectedImageUrls])

  const handleCancel = useCallback(async (taskId: string) => {
    try {
      await videoService.cancelTask(taskId)
      message.info('任务已取消')
      loadTasks()
    } catch { /* ignore */ }
  }, [])

  // MSR 从资产库选择图片：打开资产选择弹窗
  const selectMsrFromAssets = useCallback((role: 'ref1' | 'ref2' | 'ref3' | 'ref4' | 'bg') => {
    setAssetPickerRole(role)
  }, [])

  // MSR 提交
  const handleMsrSubmit = useCallback(async () => {
    if (!msrRefs.ref1 || !msrRefs.ref2) {
      message.error('角色1和角色2参考图必填')
      return
    }
    if (!msrGlobalPrompt) {
      message.error('全局提示词必填')
      return
    }
    setMsrSubmitting(true)
    setMsrStatus('pending')
    setMsrResult(null)
    try {
      const res = await msrVideoService.submit({
        ref1_image_url: msrRefs.ref1,
        ref2_image_url: msrRefs.ref2,
        ref3_image_url: msrRefs.ref3 || undefined,
        ref4_image_url: msrRefs.ref4 || undefined,
        bg_image_url: msrRefs.bg || undefined,
        global_prompt: msrGlobalPrompt,
        local_prompts: msrLocalPrompts || undefined,
        width: msrWidth,
        height: msrHeight,
        frame_count: msrFrameCount,
        seed: msrSeed,
        // ⭐ 补齐视频质量参数（后端已支持，节点 37/50/27 注入）
        fps: msrFps,
        cfg: msrCfg,
        steps: msrSteps,
        duration: msrDuration,
      })
      if (res.success && res.task_id) {
        setMsrTaskId(res.task_id)
        message.success('MSR 视频生成任务已提交')
        // 开始轮询
        msrPollRef.current = setInterval(async () => {
          try {
            const task = await msrVideoService.getTask(res.task_id)
            setMsrStatus(task.status)
            if (task.status === 'succeeded') {
              setMsrResult(task.result)
              if (msrPollRef.current) clearInterval(msrPollRef.current)
              setMsrSubmitting(false)
              message.success('MSR 视频生成完成')
              loadAssets() // 刷新资产库，D5 回写的视频资产可见
            } else if (task.status === 'failed') {
              setMsrResult({ error: task.error })
              if (msrPollRef.current) clearInterval(msrPollRef.current)
              setMsrSubmitting(false)
              message.error('MSR 视频生成失败: ' + (task.error || ''))
            }
          } catch { /* ignore */ }
        }, 5000)
      } else {
        message.error(res.error || '提交失败')
        setMsrSubmitting(false)
      }
    } catch (e: any) {
      message.error(e.message || '提交失败')
      setMsrSubmitting(false)
    }
  }, [msrRefs, msrGlobalPrompt, msrLocalPrompts, msrWidth, msrHeight, msrFrameCount, msrSeed, msrFps, msrCfg, msrSteps, msrDuration, loadAssets])

  useEffect(() => {
    return () => {
      if (msrPollRef.current) clearInterval(msrPollRef.current)
    }
  }, [])

  const statusTag = (status: string) => {
    const map: Record<string, { color: string; text: string }> = {
      pending: { color: 'default', text: '等待中' },
      running: { color: 'processing', text: '生成中' },
      completed: { color: 'success', text: '已完成' },
      failed: { color: 'error', text: '失败' },
    }
    const s = map[status] || { color: 'default', text: status }
    return <Tag color={s.color}>{s.text}</Tag>
  }

  const columns = [
    {
      title: '任务ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 140,
      render: (id: string) => <Text copyable style={{ fontSize: 12 }}>{id}</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (s: string) => statusTag(s),
    },
    {
      title: '供应商',
      dataIndex: 'provider_id',
      key: 'provider_id',
      width: 100,
    },
    {
      title: '提示词',
      dataIndex: 'prompt',
      key: 'prompt',
      ellipsis: true,
      width: 200,
    },
    {
      title: '进度',
      dataIndex: 'progress',
      key: 'progress',
      width: 120,
      render: (p: number, row: VideoTask) =>
        row.status === 'running'
          ? <Progress percent={Math.round(p)} size="small" />
          : row.status === 'completed'
            ? <Progress percent={100} size="small" />
            : '-',
    },
    {
      title: '耗时',
      dataIndex: 'elapsed_ms',
      key: 'elapsed_ms',
      width: 80,
      render: (ms: number) => ms > 0 ? `${(ms / 1000).toFixed(1)}s` : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, row: VideoTask) => (
        <Space>
          {row.status === 'running' && (
            <Button size="small" danger icon={<StopOutlined />} onClick={() => handleCancel(row.task_id)}>
              取消
            </Button>
          )}
          {row.video_url && (
            <a href={row.video_url} target="_blank" rel="noreferrer">下载</a>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Title level={3} style={{ margin: 0 }}>视频生成</Title>
        <ProjectSelector />
      </Space>

      <Tabs
        defaultActiveKey="standard"
        items={[
          {
            key: 'standard',
            label: '标准视频生成',
            children: (
              <Row gutter={24}>
        {/* 提交表单 */}
        <Col xs={24} lg={10}>
          <Card title="提交任务" style={{ marginBottom: 24 }}>
            {selectedImageUrls.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <Text type="secondary">已选择 {selectedImageUrls.length} 张图片作为输入</Text>
                <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  {selectedImageUrls.slice(0, 4).map((url, i) => (
                    <div key={i} style={{ position: 'relative', display: 'inline-block' }}>
                      <Image src={url} width={60} height={60} style={{ objectFit: 'cover', borderRadius: 4 }} />
                      <Button
                        type="text"
                        icon={<DownloadOutlined />}
                        size="small"
                        style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', width: 18, height: 18, fontSize: 10, padding: 0, lineHeight: '18px', zIndex: 1 }}
                        onClick={() => downloadImage(url, `image_${i + 1}`)}
                      />
                    </div>
                  ))}
                  {selectedImageUrls.length > 4 && (
                    <Text type="secondary">+{selectedImageUrls.length - 4} more</Text>
                  )}
                </div>
              </div>
            )}

            <Form
              form={form}
              layout="vertical"
              initialValues={{ provider_id: 'jimeng', duration: 5, aspect_ratio: '16:9', resolution: '480p' }}
              onFinish={handleSubmit}
            >
              <Form.Item name="provider_id" label="供应商">
                <Select options={PROVIDERS} />
              </Form.Item>
              <Form.Item label="提示词">
                <Space.Compact style={{ width: '100%' }}>
                  <Form.Item name="prompt" noStyle>
                    <Input.TextArea rows={3} placeholder="描述视频内容..." style={{ flex: 1 }} />
                  </Form.Item>
                </Space.Compact>
                <Button
                  size="small"
                  icon={<MessageOutlined />}
                  onClick={() => setPromptPickerOpen(true)}
                  style={{ marginTop: 4 }}
                >
                  从提示词库选择
                </Button>
              </Form.Item>
              <Form.Item name="model" label="模型（可选）">
                <Input placeholder="留空使用默认模型" />
              </Form.Item>
              <Row gutter={16}>
                <Col span={8}>
                  <Form.Item name="duration" label="时长(秒)">
                    <InputNumber min={1} max={30} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="aspect_ratio" label="比例">
                    <Select options={ASPECT_RATIOS} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="resolution" label="分辨率">
                    <Select options={RESOLUTIONS} />
                  </Form.Item>
                </Col>
              </Row>
              {/* ⭐ 视频质量参数：steps/cfg/fps/seed（与 MSR 表单对齐，后端 video_stage 已支持） */}
              <Row gutter={16}>
                <Col span={6}>
                  <Form.Item name="steps" label="采样步数">
                    <InputNumber min={1} max={100} placeholder="留空用默认" style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="cfg" label="CFG 引导强度">
                    <InputNumber min={0.1} max={10} step={0.1} placeholder="留空用默认" style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="fps" label="帧率">
                    <InputNumber min={1} max={60} placeholder="留空用默认" style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="seed" label="种子">
                    <InputNumber min={0} placeholder="留空随机" style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item>
                <Button type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={submitting} block>
                  提交生成
                </Button>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 任务列表 */}
        <Col xs={24} lg={14}>
          <Card
            title="任务列表"
            extra={<Button icon={<ReloadOutlined />} onClick={loadTasks}>刷新</Button>}
          >
            <Table
              dataSource={tasks}
              columns={columns}
              rowKey="task_id"
              size="small"
              pagination={{ pageSize: 10 }}
              scroll={{ x: 800 }}
            />
          </Card>

          {/* 最新完成的视频预览 */}
          {tasks.filter(t => t.status === 'completed' && t.video_url).length > 0 && (
            <Card title="最近生成" style={{ marginTop: 16 }}>
              {tasks
                .filter(t => t.status === 'completed' && t.video_url)
                .slice(0, 3)
                .map(t => (
                  <div key={t.task_id} style={{ marginBottom: 12 }}>
                    <video
                      src={t.video_url}
                      controls
                      style={{ width: '100%', maxHeight: 300, borderRadius: 8 }}
                    />
                    <Text type="secondary" style={{ fontSize: 12 }}>{t.task_id} | {t.provider_id} | {(t.elapsed_ms / 1000).toFixed(1)}s</Text>
                  </div>
                ))}
            </Card>
          )}
        </Col>
      </Row>
            ),
          },
          {
            key: 'msr',
            label: 'MSR 多角色视频生成',
            children: (
              <Row gutter={24}>
        <Col xs={24} lg={12}>
          <Card title="MSR 参考图与提示词" style={{ marginBottom: 24 }}>
            {/* 参考图选择 - 最多5个：4个角色 + 1个背景 */}
            <Row gutter={[16, 16]}>
              {([
                { role: 'ref1' as const, label: '角色1（必填）', required: true },
                { role: 'ref2' as const, label: '角色2（必填）', required: true },
                { role: 'ref3' as const, label: '角色3（可选）', required: false },
                { role: 'ref4' as const, label: '角色4（可选）', required: false },
                { role: 'bg' as const, label: '背景图（可选）', required: false },
              ]).map(({ role, label, required }) => {
                const url = msrRefs[role]
                return (
                  <Col xs={12} md={8} key={role}>
                    <div style={{ marginBottom: 16 }}>
                      <Text strong style={{ display: 'block', marginBottom: 8 }}>
                        {label}
                      </Text>
                      {url ? (
                        <div style={{ position: 'relative' }}>
                          <Image src={url} width="100%" height={120} style={{ objectFit: 'cover', borderRadius: 8 }} />
                          <Button
                            type="text"
                            size="small"
                            danger
                            style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(0,0,0,0.5)', color: '#fff' }}
                            onClick={() => setMsrRefs(prev => ({ ...prev, [role]: '' }))}
                          >移除</Button>
                        </div>
                      ) : (
                        <div style={{ width: '100%', height: 120, border: `2px dashed ${required ? '#ff4d4f' : '#d9d9d9'}`, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 8 }}>
                          <UploadOutlined style={{ fontSize: 24, color: '#999' }} />
                          <Button size="small" onClick={() => selectMsrFromAssets(role)}>从资产库选</Button>
                        </div>
                      )}
                    </div>
                  </Col>
                )
              })}
            </Row>

            {/* 提示词 */}
            <div style={{ marginTop: 16 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>全局提示词（场景描述）</Text>
              <TextArea
                rows={4}
                value={msrGlobalPrompt}
                onChange={e => setMsrGlobalPrompt(e.target.value)}
                placeholder="描述场景：如 烟雨朦胧的江南小镇，石板小路被雨水打湿..."
              />
            </div>
            <div style={{ marginTop: 12 }}>
              <Text strong style={{ display: 'block', marginBottom: 8 }}>分镜提示词（角色动作，每行一段）</Text>
              <TextArea
                rows={5}
                value={msrLocalPrompts}
                onChange={e => setMsrLocalPrompts(e.target.value)}
                placeholder="石板小路上，女人迎面走来，双手轻轻提着裙摆。&#10;男人迎面站着，微微一笑说：没想到会在这里遇见你。"
              />
            </div>

            {/* 参数 */}
            <Row gutter={16} style={{ marginTop: 16 }}>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>宽度</Text>
                <InputNumber value={msrWidth} onChange={v => setMsrWidth(v || 1280)} min={256} max={2048} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>高度</Text>
                <InputNumber value={msrHeight} onChange={v => setMsrHeight(v || 720)} min={256} max={2048} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>帧数</Text>
                <InputNumber value={msrFrameCount} onChange={v => setMsrFrameCount(v || 41)} min={1} max={500} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>种子</Text>
                <InputNumber value={msrSeed} onChange={v => setMsrSeed(v || 0)} min={0} style={{ width: '100%' }} />
              </Col>
            </Row>

            {/* ⭐ 视频质量参数：fps/cfg/steps/duration（与后端节点 37/50/27 对齐） */}
            <Row gutter={16} style={{ marginTop: 12 }}>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>帧率 (fps)</Text>
                <InputNumber value={msrFps} onChange={v => setMsrFps(v || 24)} min={1} max={60} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>CFG 引导强度</Text>
                <InputNumber value={msrCfg} onChange={v => setMsrCfg(v ?? 1.0)} min={0.1} max={10} step={0.1} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>采样步数 (steps)</Text>
                <InputNumber value={msrSteps} onChange={v => setMsrSteps(v || 20)} min={1} max={100} style={{ width: '100%' }} />
              </Col>
              <Col span={6}>
                <Text strong style={{ display: 'block', marginBottom: 4 }}>时长 (秒，可选)</Text>
                <InputNumber value={msrDuration} onChange={v => setMsrDuration(v ?? undefined)} min={0.1} max={60} step={0.1} placeholder="留空用帧数" style={{ width: '100%' }} />
              </Col>
            </Row>

            <Button
              type="primary"
              icon={<VideoCameraOutlined />}
              loading={msrSubmitting}
              onClick={handleMsrSubmit}
              block
              style={{ marginTop: 16 }}
              disabled={!msrRefs.ref1 || !msrRefs.ref2 || !msrGlobalPrompt}
            >
              生成 MSR 视频
            </Button>
          </Card>
        </Col>

        {/* MSR 结果 */}
        <Col xs={24} lg={12}>
          <Card title="MSR 生成状态与结果">
            {msrStatus && (
              <div style={{ marginBottom: 16 }}>
                <Space>
                  <Text>任务状态：</Text>
                  {statusTag(msrStatus)}
                  {msrTaskId && <Text type="secondary" copyable style={{ fontSize: 12 }}>{msrTaskId}</Text>}
                </Space>
              </div>
            )}
            {msrSubmitting && (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Progress type="circle" percent={msrStatus === 'running' ? 50 : 20} />
                <div style={{ marginTop: 16 }}>
                  <Text type="secondary">视频生成中，LTX-2.3 MSR 工作流耗时较长，请耐心等待...</Text>
                </div>
              </div>
            )}
            {msrResult?.error && (
              <div style={{ color: '#ff4d4f' }}>
                <Text type="danger">生成失败：{msrResult.error}</Text>
              </div>
            )}
            {msrResult?.videos?.length > 0 && (
              <div>
                {msrResult.videos.map((v: any, i: number) => (
                  <div key={i} style={{ marginBottom: 16 }}>
                    <video
                      src={v.url}
                      controls
                      style={{ width: '100%', maxHeight: 400, borderRadius: 8 }}
                    />
                    <Space style={{ marginTop: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>视频 {i + 1}</Text>
                      {msrResult.asset_id && (
                        <Tag color="green">已存入资产库: {msrResult.asset_id}</Tag>
                      )}
                      <a href={v.url} target="_blank" rel="noreferrer">下载</a>
                    </Space>
                  </div>
                ))}
              </div>
            )}
            {!msrStatus && !msrResult && (
              <div style={{ textAlign: 'center', padding: 60, color: '#999' }}>
                <VideoCameraOutlined style={{ fontSize: 48, marginBottom: 16 }} />
                <div>选择参考图并填写提示词后，点击"生成 MSR 视频"</div>
              </div>
            )}
          </Card>
        </Col>
      </Row>
            ),
          },
        ]}
      />
      {/* MSR 资产选择弹窗 */}
      <Modal
        title="从资产库选择图片"
        open={assetPickerRole !== null}
        onCancel={() => setAssetPickerRole(null)}
        footer={null}
        width={640}
      >
        {assets.length === 0 ? (
          <Empty description="资产库为空，请先在资产页生成或上传图片" />
        ) : (
          <div style={{ maxHeight: 480, overflow: 'auto' }}>
            <Row gutter={[8, 8]}>
              {assets.filter(a => a.urls?.length > 0).map(a => (
                <Col key={a.asset_id} xs={12} sm={8} md={6}>
                  <Card
                    hoverable
                    size="small"
                    styles={{ body: { padding: 4 } }}
                    onClick={() => {
                      const url = a.urls[0]
                      if (url && assetPickerRole) {
                        setMsrRefs(prev => ({ ...prev, [assetPickerRole]: url }))
                        message.success(`已选择: ${a.name || '未命名'}`)
                        setAssetPickerRole(null)
                      }
                    }}
                    cover={
                      <Image
                        src={a.urls[0]}
                        preview={false}
                        style={{ height: 100, objectFit: 'cover' }}
                        fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMTAwIiBoZWlnaHQ9IjEwMCIgZmlsbD0iI2YwZjBmMCIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiBkb21pbmFudC1iYXNlbGluZT0ibWlkZGxlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjY2NjIiBmb250LXNpemU9IjEyIj7ml6DnvKk8L3RleHQ+PC9zdmc+"
                      />
                    }
                  >
                    <div style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {a.name || '未命名'}
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          </div>
        )}
      </Modal>

      {/* 提示词库选择器 */}
      <PromptPicker
        open={promptPickerOpen}
        onClose={() => setPromptPickerOpen(false)}
        stageId="video"
        projectId={currentProjectId || undefined}
        onSelect={({ resolved }) => {
          form.setFieldValue('prompt', resolved)
          message.success('提示词已应用')
        }}
      />
    </div>
  )
}
