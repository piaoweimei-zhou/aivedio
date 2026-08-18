import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Card, Form, Select, Input, InputNumber,
  message, Tag, Row, Col, Spin, Empty, Collapse, Image,
} from 'antd'
import {
  ThunderboltOutlined, ReloadOutlined, PictureOutlined,
} from '@ant-design/icons'
import {
  graphicApi, stageApi, assetApi,
  GraphicTypeOption, GraphicParams,
} from '../services/directorApi'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

// 配色方案选项
const STYLE_OPTIONS = [
  { value: 'modern', label: '现代蓝（清爽）' },
  { value: 'minimal', label: '极简灰（克制）' },
  { value: 'warm', label: '暖橙（亲和）' },
  { value: 'tech', label: '科技黑（深色）' },
]

// 画幅预设
const SIZE_PRESETS = [
  { value: '1080x1350', label: '4:5 竖版（小红书/抖音图文）', width: 1080, height: 1350 },
  { value: '1080x1080', label: '1:1 方形（朋友圈）', width: 1080, height: 1080 },
  { value: '1080x1920', label: '9:16 全屏（抖音/视频封面）', width: 1080, height: 1920 },
  { value: '800x2000', label: '长图（教程/清单）', width: 800, height: 2000 },
]

export default function GraphicPage() {
  const [form] = Form.useForm()
  const [graphicTypes, setGraphicTypes] = useState<GraphicTypeOption[]>([])
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string>('')
  const [taskStatus, setTaskStatus] = useState<any>(null)
  const [graphics, setGraphics] = useState<any[]>([])
  const [sizePreset, setSizePreset] = useState('1080x1350')

  // 加载图文类型列表
  useEffect(() => {
    graphicApi.listGraphicTypes().then(res => {
      setGraphicTypes(res.graphic_types || [])
      if (res.graphic_types?.length > 0) {
        form.setFieldValue('graphic_type', res.graphic_types[0].type)
      }
    }).catch(err => {
      message.error(`加载图文类型失败: ${err.message}`)
    })
    loadGraphics()
  }, [])

  // 轮询任务状态
  useEffect(() => {
    if (!taskId) return
    const timer = setInterval(async () => {
      try {
        const res = await stageApi.getTask(taskId)
        setTaskStatus(res)
        if (res.status === 'completed' || res.status === 'failed') {
          clearInterval(timer)
          setLoading(false)
          if (res.status === 'completed' && res.success) {
            message.success(`图文生成成功！耗时 ${res.elapsed_ms}ms`)
            loadGraphics()
          } else if (res.status === 'failed') {
            message.error(`图文生成失败: ${res.error || '未知错误'}`)
          }
        }
      } catch (e: any) {
        console.error('轮询失败', e)
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [taskId])

  const loadGraphics = async () => {
    try {
      const res = await assetApi.list({ asset_type: 'image' })
      // 只显示 graphic 生成的图（带 graphic_type 元数据）
      const filtered = (res.assets || []).filter(
        (a: any) => a.metadata?.graphic_type
      )
      setGraphics(filtered)
    } catch (e: any) {
      // 静默
    }
  }

  const handleSubmit = async (values: any) => {
    setLoading(true)
    setTaskStatus(null)
    try {
      const preset = SIZE_PRESETS.find(p => p.value === values.size_preset)
      const params: GraphicParams = {
        topic: values.topic,
        graphic_type: values.graphic_type,
        title: values.title || '',
        style: values.style || 'modern',
        model: values.model || '',
        temperature: values.temperature ?? 0.7,
        max_tokens: values.max_tokens || 2048,
        width: preset?.width || 1080,
        height: preset?.height || 1350,
        extra_instructions: values.extra_instructions || '',
      }
      const res = await graphicApi.generate(params)
      setTaskId(res.task_id)
      message.info(`图文任务已提交，task_id=${res.task_id}`)
    } catch (err: any) {
      setLoading(false)
      message.error(`提交失败: ${err.message}`)
    }
  }

  // ⚠️ hook 必须放在 find 回调之外：Form.useWatch 是 hook，写在
  // .find 回调内会在数组元素数量变化时改变单次渲染的 hook 调用次数，
  // 触发 React「Rendered more hooks」白屏。
  const watchedGraphicType = Form.useWatch('graphic_type', form)
  const selectedType = graphicTypes.find(
    t => t.type === watchedGraphicType
  )

  const resolveImageUrl = (url: string) => {
    if (!url) return ''
    if (url.startsWith('http://') || url.startsWith('https://')) return url
    // 本地 /output/graphic/xxx.png → /api/director/output/graphic/xxx.png
    if (url.startsWith('/output/')) {
      return `/api/director${url}`
    }
    return url
  }

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>
        <PictureOutlined /> 图文生成
      </Title>
      <Paragraph type="secondary">
        通过 LLM 生成结构化内容 + Pillow 渲染为 6 种图文卡片：信息图 / 对比图 / 教程图 / 清单图 / 金句图 / 数据图
      </Paragraph>

      <Row gutter={16}>
        {/* 左侧：表单 */}
        <Col span={12}>
          <Card title="图文配置" bordered>
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              initialValues={{
                graphic_type: 'infographic',
                style: 'modern',
                size_preset: '1080x1350',
                temperature: 0.7,
                max_tokens: 2048,
              }}
            >
              <Form.Item
                name="topic"
                label="主题"
                rules={[{ required: true, message: '请输入主题' }]}
                tooltip="例如：AI工具效率对比 / 5步学会写文案"
              >
                <Input placeholder="例如：AI工具效率对比" />
              </Form.Item>

              <Form.Item
                name="graphic_type"
                label="图文类型"
                rules={[{ required: true }]}
              >
                <Select options={graphicTypes.map(t => ({
                  value: t.type,
                  label: `${t.label} - ${t.desc}`,
                }))} />
              </Form.Item>

              {selectedType && (
                <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
                  <Text strong>类型说明：</Text>
                  <Paragraph style={{ margin: '4px 0 0' }}>
                    {selectedType.desc}
                  </Paragraph>
                </Card>
              )}

              <Form.Item name="title" label="标题（可空，由 LLM 生成）">
                <Input placeholder="留空让 LLM 自行设计" />
              </Form.Item>

              <Row gutter={8}>
                <Col span={8}>
                  <Form.Item name="style" label="配色风格">
                    <Select options={STYLE_OPTIONS} />
                  </Form.Item>
                </Col>
                <Col span={16}>
                  <Form.Item name="size_preset" label="画幅预设">
                    <Select
                      options={SIZE_PRESETS.map(p => ({
                        value: p.value,
                        label: p.label,
                      }))}
                      onChange={(v) => setSizePreset(v)}
                    />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={8}>
                <Col span={8}>
                  <Form.Item name="temperature" label="温度">
                    <InputNumber min={0} max={2} step={0.05} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="max_tokens" label="MaxTokens">
                    <InputNumber min={500} max={8000} step={500} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={8}>
                  <Form.Item name="model" label="模型（空=默认）">
                    <Input placeholder="deepseek-chat" />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item name="extra_instructions" label="额外要求（可选）">
                <TextArea
                  rows={2}
                  placeholder="例如：突出数据对比 / 加入品牌名 / 适合小红书发布"
                />
              </Form.Item>

              <Form.Item>
                <Space>
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={<ThunderboltOutlined />}
                    loading={loading}
                  >
                    生成图文
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={() => form.resetFields()}>
                    重置
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 右侧：任务状态 + 历史 */}
        <Col span={12}>
          <Card title="任务状态" bordered style={{ marginBottom: 16 }}>
            {taskId ? (
              <div>
                <Tag color={taskStatus?.status === 'completed' ? 'success' :
                  taskStatus?.status === 'failed' ? 'error' : 'processing'}>
                  {taskStatus?.status || 'pending'}
                </Tag>
                <Text> task_id: {taskId}</Text>
                {taskStatus?.status === 'running' && <Spin size="small" style={{ marginLeft: 8 }} />}
                {taskStatus?.asset && (
                  <div style={{ marginTop: 8 }}>
                    <Text>新图文资产：</Text>
                    <Tag color="blue">{taskStatus.asset.asset_id}</Tag>
                    <Text>{taskStatus.asset.name}</Text>
                  </div>
                )}
                {taskStatus?.error && (
                  <Paragraph type="danger" style={{ marginTop: 8 }}>
                    {taskStatus.error}
                  </Paragraph>
                )}
              </div>
            ) : (
              <Empty description="尚未提交图文任务" />
            )}
          </Card>

          <Card
            title={`历史图文 (${graphics.length})`}
            bordered
            extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadGraphics}>刷新</Button>}
          >
            {graphics.length === 0 ? (
              <Empty description="暂无图文" />
            ) : (
              <Collapse
                items={graphics.map(g => ({
                  key: g.asset_id,
                  label: (
                    <Space>
                      <Text strong>{g.name}</Text>
                      <Tag color="blue">{g.metadata?.graphic_type_label || g.metadata?.graphic_type}</Tag>
                      <Text type="secondary">{g.metadata?.topic}</Text>
                    </Space>
                  ),
                  children: (
                    <div>
                      <Paragraph>
                        <Text strong>topic: </Text>
                        <Text>{g.metadata?.topic}</Text>
                        <Text strong style={{ marginLeft: 16 }}>style: </Text>
                        <Text>{g.metadata?.style || '-'}</Text>
                        <Text strong style={{ marginLeft: 16 }}>size: </Text>
                        <Text>{g.metadata?.width}x{g.metadata?.height}</Text>
                      </Paragraph>
                      {g.urls && g.urls.length > 0 && (
                        <div style={{ textAlign: 'center', background: '#fafafa', padding: 12, borderRadius: 8 }}>
                          <Image
                            src={resolveImageUrl(g.urls[0])}
                            alt={g.name}
                            style={{ maxHeight: 500, objectFit: 'contain' }}
                            placeholder={<Spin />}
                          />
                        </div>
                      )}
                      {g.metadata?.content && (
                        <Card size="small" title="LLM 生成内容" style={{ marginTop: 12 }}>
                          <Paragraph copyable style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                            {JSON.stringify(g.metadata.content, null, 2)}
                          </Paragraph>
                        </Card>
                      )}
                    </div>
                  ),
                }))}
              />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
