import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Card, Form, Select, Input, InputNumber,
  message, Tag, Row, Col, Spin, Empty, Radio, Alert,
} from 'antd'
import {
  VideoCameraOutlined, ReloadOutlined, UploadOutlined,
  DesktopOutlined, InfoCircleOutlined,
} from '@ant-design/icons'
import {
  screenRecordApi, stageApi,
  ScreenRecordParams, RecordWindow,
} from '../services/directorApi'

const { Title, Text, Paragraph } = Typography

export default function ScreenRecordPage() {
  const [form] = Form.useForm()
  const [mode, setMode] = useState<'record' | 'upload'>('record')
  const [windows, setWindows] = useState<RecordWindow[]>([])
  const [windowsLoading, setWindowsLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string>('')
  const [taskStatus, setTaskStatus] = useState<any>(null)
  const [uploadFile, setUploadFile] = useState<File | null>(null)

  useEffect(() => {
    loadWindows()
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
            message.success(`录屏完成！耗时 ${res.elapsed_ms}ms`)
          } else if (res.status === 'failed') {
            message.error(`录屏失败: ${res.error}`)
          }
        }
      } catch (e) {
        // ignore
      }
    }, 2000)
    return () => clearInterval(timer)
  }, [taskId])

  const loadWindows = async () => {
    setWindowsLoading(true)
    try {
      const res = await screenRecordApi.listWindows()
      setWindows(res.windows || [])
    } catch (err: any) {
      message.error(`获取窗口列表失败: ${err.message}`)
    } finally {
      setWindowsLoading(false)
    }
  }

  const handleSubmit = async (values: any) => {
    setLoading(true)
    setTaskStatus(null)
    try {
      if (mode === 'upload') {
        if (!uploadFile) {
          message.error('请选择要上传的录屏文件')
          setLoading(false)
          return
        }
        // 上传模式：先上传文件，再创建资产
        message.info('正在上传录屏文件...')
        const asset = await screenRecordApi.uploadAndRegister(uploadFile, values.name || uploadFile.name)
        message.success(`上传成功，资产ID: ${asset.asset_id}`)
        setLoading(false)
        return
      }

      const params: ScreenRecordParams = {
        mode: 'record',
        duration: values.duration || 30,
        fps: values.fps || 15,
        name: values.name || `录屏_${Date.now()}`,
        window_title: values.window_title || '',
        region: values.region || '',
      }
      const res = await screenRecordApi.record(params)
      setTaskId(res.task_id)
      message.info(`录屏任务已提交，task_id=${res.task_id}`)
    } catch (err: any) {
      setLoading(false)
      message.error(`提交失败: ${err.message}`)
    }
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setUploadFile(e.target.files[0])
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>
        <DesktopOutlined /> 屏幕录制
      </Title>
      <Paragraph type="secondary">
        支持 ffmpeg 自动录制（Windows gdigrab / Linux x11grab / macOS avfoundation）和上传外部录屏文件两种模式
      </Paragraph>

      <Alert
        type="info"
        showIcon
        icon={<InfoCircleOutlined />}
        message="前置条件"
        description={
          <ul style={{ margin: 0, paddingLeft: 20 }}>
            <li>record 模式：服务器需安装 ffmpeg 并设置 FFMPEG_PATH 环境变量</li>
            <li>upload 模式：可上传 mp4/mkv/mov 等常见视频格式</li>
            <li>录屏文件保存到 backend/data/generated/</li>
          </ul>
        }
        style={{ marginBottom: 16 }}
      />

      <Row gutter={16}>
        {/* 左侧：表单 */}
        <Col span={14}>
          <Card title="录屏配置" variant="outlined">
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              initialValues={{
                mode: 'record',
                duration: 30,
                fps: 15,
                name: '',
              }}
            >
              <Form.Item label="录屏模式">
                <Radio.Group
                  value={mode}
                  onChange={e => setMode(e.target.value)}
                  optionType="button"
                  buttonStyle="solid"
                >
                  <Radio.Button value="record">
                    <DesktopOutlined /> ffmpeg 自动录制
                  </Radio.Button>
                  <Radio.Button value="upload">
                    <UploadOutlined /> 上传录屏文件
                  </Radio.Button>
                </Radio.Group>
              </Form.Item>

              {mode === 'record' ? (
                <>
                  <Form.Item
                    name="window_title"
                    label="窗口标题（含关键字匹配）"
                    tooltip="留空=录制全屏。Windows 上必须匹配到精确的窗口标题关键字"
                  >
                    <Select
                      showSearch
                      allowClear
                      placeholder="点击右侧按钮加载窗口列表"
                      options={windows.map(w => ({
                        value: w.title,
                        label: `${w.title} [${w.process}]`,
                      }))}
                      notFoundContent={windowsLoading ? <Spin size="small" /> : '无窗口'}
                    />
                  </Form.Item>

                  <Button
                    icon={<ReloadOutlined />}
                    onClick={loadWindows}
                    loading={windowsLoading}
                    size="small"
                    style={{ marginBottom: 16 }}
                  >
                    刷新窗口列表
                  </Button>

                  <Form.Item
                    name="region"
                    label="录制区域 (x,y,w,h)"
                    tooltip="留空=全屏。例如：100,100,1280,720"
                  >
                    <Input placeholder="例如：100,100,1280,720" />
                  </Form.Item>

                  <Row gutter={8}>
                    <Col span={8}>
                      <Form.Item name="duration" label="时长(秒)">
                        <InputNumber min={1} max={3600} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="fps" label="帧率">
                        <InputNumber min={5} max={60} style={{ width: '100%' }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="name" label="资产名称">
                        <Input placeholder="录屏_xxx" />
                      </Form.Item>
                    </Col>
                  </Row>
                </>
              ) : (
                <>
                  <Form.Item
                    label="选择文件"
                    required
                  >
                    <input
                      type="file"
                      accept="video/*"
                      onChange={handleFileChange}
                      style={{ width: '100%' }}
                    />
                    {uploadFile && (
                      <Paragraph style={{ marginTop: 8 }}>
                        <Tag color="blue">{uploadFile.name}</Tag>
                        <Text type="secondary">{(uploadFile.size / 1024 / 1024).toFixed(2)} MB</Text>
                      </Paragraph>
                    )}
                  </Form.Item>
                  <Form.Item name="name" label="资产名称">
                    <Input placeholder="留空=使用文件名" />
                  </Form.Item>
                </>
              )}

              <Form.Item>
                <Space>
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={mode === 'record' ? <VideoCameraOutlined /> : <UploadOutlined />}
                    loading={loading}
                  >
                    {mode === 'record' ? '开始录制' : '上传文件'}
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={() => form.resetFields()}>
                    重置
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 右侧：状态 */}
        <Col span={10}>
          <Card title="任务状态" variant="outlined">
            {taskId ? (
              <div>
                <Tag color={taskStatus?.status === 'completed' ? 'success' :
                  taskStatus?.status === 'failed' ? 'error' : 'processing'}>
                  {taskStatus?.status || 'pending'}
                </Tag>
                <Text> task_id: {taskId}</Text>
                {taskStatus?.status === 'running' && (
                  <div style={{ marginTop: 12 }}>
                    <Spin size="small" />
                    <Text type="secondary" style={{ marginLeft: 8 }}>
                      正在录制...（请勿关闭此页面）
                    </Text>
                  </div>
                )}
                {taskStatus?.asset && (
                  <Paragraph style={{ marginTop: 12 }}>
                    <Text strong>录屏资产：</Text><br />
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
              <Empty description="尚未提交录屏任务" />
            )}
          </Card>

          <Card title="已加载窗口列表" variant="outlined" style={{ marginTop: 16 }}>
            {windows.length === 0 ? (
              <Empty description="点击左侧 刷新窗口列表" />
            ) : (
              <div style={{ maxHeight: 300, overflow: 'auto' }}>
                {windows.map((w, i) => (
                  <div key={i} style={{ padding: '4px 0', borderBottom: '1px solid #f0f0f0' }}>
                    <Text strong>{w.title}</Text>
                    <Tag color="default" style={{ marginLeft: 8 }}>{w.process}</Tag>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
