import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Card, Form, Input, InputNumber,
  message, Tag, Row, Col, Empty, Radio, Alert, List, Checkbox,
} from 'antd'
import {
  PictureOutlined, ReloadOutlined, ThunderboltOutlined,
  ColumnWidthOutlined, ColumnHeightOutlined, AppstoreOutlined,
} from '@ant-design/icons'
import {
  composeApi, stageApi, assetApi,
  ComposeParams,
} from '../services/directorApi'

const { Title, Text, Paragraph } = Typography

const LAYOUT_OPTIONS = [
  { value: 'horizontal', label: '横向并排', icon: <ColumnWidthOutlined /> },
  { value: 'vertical', label: '纵向堆叠', icon: <ColumnHeightOutlined /> },
  { value: 'grid', label: '网格', icon: <AppstoreOutlined /> },
  { value: 'split_compare', label: '对比分屏', icon: <PictureOutlined /> },
]

export default function ComposePage() {
  const [form] = Form.useForm()
  const [assets, setAssets] = useState<any[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string>('')
  const [taskStatus, setTaskStatus] = useState<any>(null)
  const [assetFilter, setAssetFilter] = useState<'video' | 'image' | 'all'>('all')

  useEffect(() => {
    loadAssets()
  }, [])

  // 轮询任务
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
            message.success(`合成完成！耗时 ${res.elapsed_ms}ms`)
          } else if (res.status === 'failed') {
            message.error(`合成失败: ${res.error}`)
          }
        }
      } catch (e) {
        // ignore
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [taskId])

  const loadAssets = async () => {
    try {
      // 拉 video + image
      const [vRes, iRes] = await Promise.all([
        assetApi.list({ asset_type: 'video' }),
        assetApi.list({ asset_type: 'image' }),
      ])
      const videos = (vRes.assets || []).map((a: any) => ({ ...a, _kind: 'video' }))
      const images = (iRes.assets || []).map((a: any) => ({ ...a, _kind: 'image' }))
      setAssets([...videos, ...images])
    } catch (e: any) {
      message.error(`加载资产失败: ${e.message}`)
    }
  }

  const filteredAssets = assets.filter(a => {
    if (assetFilter === 'all') return true
    if (assetFilter === 'video') return a._kind === 'video' || a.asset_type === 'video'
    return a._kind === 'image' || a.asset_type === 'image'
  })

  const handleSubmit = async (values: any) => {
    if (selectedIds.length < 2) {
      message.error('至少选择 2 个素材')
      return
    }
    if (values.layout === 'split_compare' && selectedIds.length !== 2) {
      message.error('对比分屏模式需要恰好 2 个素材')
      return
    }

    setLoading(true)
    setTaskStatus(null)
    try {
      const labelsStr = values.labels || ''
      const labels = labelsStr.split('\n').map((s: string) => s.trim()).filter(Boolean)

      const params: ComposeParams = {
        layout: values.layout,
        columns: values.columns || 2,
        gap: values.gap ?? 10,
        labels,
        name: values.name || `合成_${Date.now()}`,
        size: values.size || '1920x1080',
        duration: values.duration || 10,
        bg_color: values.bg_color || '0x000000',
      }
      const res = await composeApi.compose(selectedIds, params)
      setTaskId(res.task_id)
      message.info(`合成任务已提交，task_id=${res.task_id}`)
    } catch (err: any) {
      setLoading(false)
      message.error(`提交失败: ${err.message}`)
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>
        <ThunderboltOutlined /> 分屏合成
      </Title>
      <Paragraph type="secondary">
        把多个视频/图片按横向 / 纵向 / 网格 / 对比模式拼接为单一画面，支持给每路加文字标签
      </Paragraph>

      <Alert
        type="info"
        showIcon
        message="使用说明"
        description={
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>至少选择 2 个素材；网格模式建议 4 个</li>
            <li>对比分屏（split_compare）只接受 2 个素材，左右各半</li>
            <li>不同分辨率会自动缩放到目标尺寸（默认 1920x1080）</li>
            <li>图片会按目标时长循环生成视频，再参与合成</li>
          </ul>
        }
        style={{ marginBottom: 16 }}
      />

      <Row gutter={16}>
        {/* 左侧：素材选择 */}
        <Col span={14}>
          <Card
            title={`素材库（已选 ${selectedIds.length} 个）`}
            variant="outlined"
            extra={
              <Space>
                <Radio.Group
                  value={assetFilter}
                  onChange={e => setAssetFilter(e.target.value)}
                  size="small"
                  optionType="button"
                  buttonStyle="solid"
                >
                  <Radio.Button value="all">全部</Radio.Button>
                  <Radio.Button value="video">视频</Radio.Button>
                  <Radio.Button value="image">图片</Radio.Button>
                </Radio.Group>
                <Button size="small" icon={<ReloadOutlined />} onClick={loadAssets}>刷新</Button>
              </Space>
            }
          >
            {filteredAssets.length === 0 ? (
              <Empty description="暂无素材，请先在资产库上传" />
            ) : (
              <List
                dataSource={filteredAssets}
                renderItem={(item) => (
                  <List.Item>
                    <Checkbox
                      checked={selectedIds.includes(item.asset_id)}
                      onChange={e => {
                        if (e.target.checked) {
                          setSelectedIds([...selectedIds, item.asset_id])
                        } else {
                          setSelectedIds(selectedIds.filter(id => id !== item.asset_id))
                        }
                      }}
                    >
                      <Space>
                        <Tag color={item._kind === 'video' ? 'blue' : 'orange'}>
                          {item._kind}
                        </Tag>
                        <Text strong>{item.name}</Text>
                        <Text type="secondary">{item.asset_id.slice(0, 8)}</Text>
                        {item.urls?.[0] && (
                          <a href={item.urls[0]} target="_blank" rel="noreferrer">
                            预览
                          </a>
                        )}
                      </Space>
                    </Checkbox>
                  </List.Item>
                )}
                style={{ maxHeight: 400, overflow: 'auto' }}
              />
            )}
          </Card>
        </Col>

        {/* 右侧：合成配置 */}
        <Col span={10}>
          <Card title="合成参数" variant="outlined">
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              initialValues={{
                layout: 'horizontal',
                columns: 2,
                gap: 10,
                size: '1920x1080',
                duration: 10,
                bg_color: '0x000000',
              }}
            >
              <Form.Item name="layout" label="布局模式" rules={[{ required: true }]}>
                <Radio.Group optionType="button" buttonStyle="solid">
                  {LAYOUT_OPTIONS.map(opt => (
                    <Radio.Button key={opt.value} value={opt.value}>
                      {opt.icon} {opt.label}
                    </Radio.Button>
                  ))}
                </Radio.Group>
              </Form.Item>

              <Form.Item noStyle shouldUpdate>
                {({ getFieldValue }) => getFieldValue('layout') === 'grid' && (
                  <Form.Item name="columns" label="网格列数">
                    <InputNumber min={1} max={6} style={{ width: '100%' }} />
                  </Form.Item>
                )}
              </Form.Item>

              <Row gutter={8}>
                <Col span={12}>
                  <Form.Item name="size" label="目标尺寸">
                    <Input placeholder="1920x1080" />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="gap" label="间距(像素)">
                    <InputNumber min={0} max={200} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Row gutter={8}>
                <Col span={12}>
                  <Form.Item name="duration" label="图片转视频时长(秒)">
                    <InputNumber min={1} max={120} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="bg_color" label="背景色">
                    <Input placeholder="0x000000" />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item
                name="labels"
                label="每路标签（每行一个，按选择顺序）"
                tooltip="标签会以 drawtext 叠加到每路视频顶部"
              >
                <Input.TextArea
                  rows={Math.max(2, selectedIds.length)}
                  placeholder={'例如：\n左：原始\n右：精修'}
                />
              </Form.Item>

              <Form.Item name="name" label="合成资产名称">
                <Input placeholder="合成_xxx" />
              </Form.Item>

              <Form.Item>
                <Button
                  type="primary"
                  htmlType="submit"
                  icon={<ThunderboltOutlined />}
                  loading={loading}
                  disabled={selectedIds.length < 2}
                  block
                >
                  开始合成 ({selectedIds.length} 个素材)
                </Button>
              </Form.Item>
            </Form>
          </Card>

          <Card title="任务状态" variant="outlined" style={{ marginTop: 16 }}>
            {taskId ? (
              <div>
                <Tag color={taskStatus?.status === 'completed' ? 'success' :
                  taskStatus?.status === 'failed' ? 'error' : 'processing'}>
                  {taskStatus?.status || 'pending'}
                </Tag>
                <Text> task_id: {taskId}</Text>
                {taskStatus?.asset && (
                  <Paragraph style={{ marginTop: 8 }}>
                    <Text strong>合成资产：</Text>
                    <Tag color="blue">{taskStatus.asset.asset_id}</Tag>
                    <Text>{taskStatus.asset.name}</Text>
                  </Paragraph>
                )}
                {taskStatus?.error && (
                  <Paragraph type="danger" style={{ marginTop: 8 }}>
                    {taskStatus.error}
                  </Paragraph>
                )}
              </div>
            ) : (
              <Empty description="尚未提交合成任务" />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
