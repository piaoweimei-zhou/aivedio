import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Card, Form, Select, Input, InputNumber,
  message, Tag, Row, Col, Spin, Empty, Tabs, Modal, Collapse, Tooltip,
} from 'antd'
import {
  ThunderboltOutlined, ReloadOutlined, EyeOutlined, CopyOutlined,
  VideoCameraOutlined, FileTextOutlined, MessageOutlined,
} from '@ant-design/icons'
import {
  scriptApi, stageApi, assetApi,
  VideoTypeOption, ScriptData, ScriptParams,
} from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

// 钩子方式默认值
const HOOK_STYLE_OPTIONS = [
  { value: 'comment_1', label: '评论区扣1（强互动）' },
  { value: 'main_page', label: '主页进粉丝群（沉淀私域）' },
  { value: 'dm', label: '私信引导（一对一）' },
]

export default function ScriptPage() {
  const { currentProject } = useProject()
  const [form] = Form.useForm()
  const [videoTypes, setVideoTypes] = useState<VideoTypeOption[]>([])
  const [loading, setLoading] = useState(false)
  const [taskId, setTaskId] = useState<string>('')
  const [taskStatus, setTaskStatus] = useState<any>(null)
  const [scripts, setScripts] = useState<any[]>([])
  const [previewScript, setPreviewScript] = useState<ScriptData | null>(null)
  const [previewVisible, setPreviewVisible] = useState(false)

  // 加载视频类型列表
  useEffect(() => {
    scriptApi.listVideoTypes().then(res => {
      setVideoTypes(res.video_types || [])
      if (res.video_types?.length > 0) {
        form.setFieldValue('video_type', res.video_types[0].value)
      }
    }).catch(err => {
      message.error(`加载视频类型失败: ${err.message}`)
    })
    loadScripts()
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
            message.success(`剧本生成成功！耗时 ${res.elapsed_ms}ms`)
            loadScripts()
          } else if (res.status === 'failed') {
            message.error(`剧本生成失败: ${res.error || '未知错误'}`)
          }
        }
      } catch (e: any) {
        console.error('轮询失败', e)
      }
    }, 1500)
    return () => clearInterval(timer)
  }, [taskId])

  const loadScripts = async () => {
    try {
      const res = await assetApi.list({ asset_type: 'script' })
      setScripts(res.assets || [])
    } catch (e: any) {
      // 静默
    }
  }

  const handleSubmit = async (values: any) => {
    setLoading(true)
    setTaskStatus(null)
    try {
      const params: ScriptParams = {
        topic: values.topic,
        video_type: values.video_type,
        acts: values.acts || 3,
        duration_seconds: values.duration_seconds || 30,
        characters: (values.characters || '').split('\n').map((s: string) => s.trim()).filter(Boolean),
        tone_extra: values.tone_extra || '',
        target_audience: values.target_audience || '',
        hook_style: values.hook_style || 'comment_1',
        model: values.model || '',
        temperature: values.temperature ?? 0.85,
        max_tokens: values.max_tokens || 6000,
      }
      const res = await scriptApi.generate(params)
      setTaskId(res.task_id)
      message.info(`剧本任务已提交，task_id=${res.task_id}`)
    } catch (err: any) {
      setLoading(false)
      message.error(`提交失败: ${err.message}`)
    }
  }

  const handlePreview = async (assetId: string) => {
    try {
      const res = await scriptApi.getScript(assetId)
      setPreviewScript(res.script)
      setPreviewVisible(true)
    } catch (err: any) {
      message.error(`加载剧本失败: ${err.message}`)
    }
  }

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('已复制到剪贴板')
  }

  // ⚠️ 必须把 hook 调用放在 find 回调之外：Form.useWatch 是 hook，
  // 若在 .find 回调内调用，videoTypes 数组元素数量变化会导致 hook 调用次数
  // 在不同渲染间不一致，触发 React error「Rendered more hooks」白屏。
  const watchedVideoType = Form.useWatch('video_type', form)
  const selectedVideoType = videoTypes.find(
    v => v.value === watchedVideoType
  )

  return (
    <div style={{ padding: 24 }}>
      <Title level={3}>
        <FileTextOutlined /> AI 剧本生成
      </Title>
      <Paragraph type="secondary">
        通过 LLM 一键生成 6 种视频类型的结构化剧本：问题解决 / 效率对比 / 测评教程 / 趣味剧情 / 全AI短剧 / 图文叙事
      </Paragraph>

      <Row gutter={16}>
        {/* 左侧：表单 */}
        <Col span={12}>
          <Card title="剧本配置" variant="outlined">
            <Form
              form={form}
              layout="vertical"
              onFinish={handleSubmit}
              initialValues={{
                video_type: 'full_ai_short',
                acts: 3,
                duration_seconds: 30,
                hook_style: 'comment_1',
                temperature: 0.85,
                max_tokens: 6000,
              }}
            >
              <Form.Item
                name="topic"
                label="剧本主题"
                rules={[{ required: true, message: '请输入剧本主题' }]}
                tooltip="例如：批量重命名工具-古今穿越剧"
              >
                <Input placeholder="例如：批量重命名工具-古今穿越剧" />
              </Form.Item>

              <Form.Item
                name="video_type"
                label="视频类型"
                rules={[{ required: true }]}
              >
                <Select options={videoTypes.map(v => ({
                  value: v.value,
                  label: `${v.label} (${v.tone})`,
                }))} />
              </Form.Item>

              {selectedVideoType && (
                <Card size="small" style={{ marginBottom: 16, background: '#fafafa' }}>
                  <Text strong>结构说明：</Text>
                  <Paragraph style={{ whiteSpace: 'pre-wrap', margin: '4px 0 0' }}>
                    {selectedVideoType.structure}
                  </Paragraph>
                </Card>
              )}

              <Row gutter={8}>
                <Col span={6}>
                  <Form.Item name="acts" label="幕数">
                    <InputNumber min={1} max={10} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="duration_seconds" label="时长(秒)">
                    <InputNumber min={5} max={300} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="temperature" label="温度">
                    <InputNumber min={0} max={2} step={0.05} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
                <Col span={6}>
                  <Form.Item name="max_tokens" label="MaxTokens">
                    <InputNumber min={500} max={16000} step={500} style={{ width: '100%' }} />
                  </Form.Item>
                </Col>
              </Row>

              <Form.Item
                name="characters"
                label="角色列表（每行一个，可空）"
                tooltip="空则由 LLM 自行设计2-3个角色"
              >
                <TextArea
                  rows={3}
                  placeholder={'例如：\n皇帝 - 严肃古板\n太监 - 瑟瑟发抖\n现代人 - 自带工具'}
                />
              </Form.Item>

              <Form.Item name="target_audience" label="目标用户">
                <Input placeholder="如：设计师、摄影师、办公文员" />
              </Form.Item>

              <Form.Item name="tone_extra" label="额外基调要求">
                <Input placeholder="如：紧凑直击痛点，避免冗长铺垫" />
              </Form.Item>

              <Form.Item name="hook_style" label="结尾钩子方式">
                <Select options={HOOK_STYLE_OPTIONS} />
              </Form.Item>

              <Form.Item name="model" label="LLM 模型（空=DeepSeek 默认）">
                <Input placeholder="如 deepseek-chat / deepseek-reasoner" />
              </Form.Item>

              <Form.Item>
                <Space>
                  <Button
                    type="primary"
                    htmlType="submit"
                    icon={<ThunderboltOutlined />}
                    loading={loading}
                  >
                    生成剧本
                  </Button>
                  <Button icon={<ReloadOutlined />} onClick={() => form.resetFields()}>
                    重置
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        {/* 右侧：任务状态 + 历史剧本 */}
        <Col span={12}>
          <Card title="任务状态" variant="outlined" style={{ marginBottom: 16 }}>
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
                    <Text>新剧本资产：</Text>
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
              <Empty description="尚未提交剧本任务" />
            )}
          </Card>

          <Card
            title={`历史剧本 (${scripts.length})`}
            variant="outlined"
            extra={<Button size="small" icon={<ReloadOutlined />} onClick={loadScripts}>刷新</Button>}
          >
            {scripts.length === 0 ? (
              <Empty description="暂无剧本" />
            ) : (
              <Collapse
                items={scripts.map(s => ({
                  key: s.asset_id,
                  label: (
                    <Space>
                      <Text strong>{s.name}</Text>
                      <Tag>{s.metadata?.video_type || 'unknown'}</Tag>
                      <Text type="secondary">{s.metadata?.topic}</Text>
                    </Space>
                  ),
                  children: (
                    <div>
                      <Paragraph>
                        <Text strong>topic: </Text>
                        <Text>{s.metadata?.topic}</Text>
                      </Paragraph>
                      <Paragraph>
                        <Text strong>acts: </Text>
                        <Text>{s.metadata?.acts || '-'}</Text>
                        <Text strong style={{ marginLeft: 16 }}>duration: </Text>
                        <Text>{s.metadata?.duration_seconds}s</Text>
                        <Text strong style={{ marginLeft: 16 }}>model: </Text>
                        <Text>{s.metadata?.model || '-'}</Text>
                      </Paragraph>
                      {s.metadata?.characters && s.metadata.characters.length > 0 && (
                        <Paragraph>
                          <Text strong>角色：</Text>
                          {s.metadata.characters.map((c: string, i: number) => (
                            <Tag key={i} color="blue">{c}</Tag>
                          ))}
                        </Paragraph>
                      )}
                      <Space>
                        <Button
                          size="small"
                          icon={<EyeOutlined />}
                          onClick={() => handlePreview(s.asset_id)}
                        >
                          预览剧本
                        </Button>
                      </Space>
                    </div>
                  ),
                }))}
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* 剧本预览 Modal */}
      <Modal
        title={previewScript?.title || '剧本预览'}
        open={previewVisible}
        onCancel={() => setPreviewVisible(false)}
        footer={[
          <Button key="copy" icon={<CopyOutlined />} onClick={() => handleCopy(JSON.stringify(previewScript, null, 2))}>
            复制 JSON
          </Button>,
          <Button key="close" type="primary" onClick={() => setPreviewVisible(false)}>
            关闭
          </Button>,
        ]}
        width={800}
      >
        {previewScript?.parse_error && (
          <Paragraph type="warning">
            <Text strong>解析警告：</Text>{previewScript.parse_error}
          </Paragraph>
        )}

        {previewScript?.hook && (
          <Card size="small" title={<><MessageOutlined /> 结尾钩子</>} style={{ marginBottom: 12 }}>
            <Paragraph>{previewScript.hook}</Paragraph>
          </Card>
        )}

        {previewScript?.characters && previewScript.characters.length > 0 && (
          <Card size="small" title="角色" style={{ marginBottom: 12 }}>
            {previewScript.characters.map((c, i) => (
              <div key={i}>
                <Tag color="purple">{c.role}</Tag>
                <Text strong>{c.name}：</Text>
                <Text type="secondary">{c.desc}</Text>
              </div>
            ))}
          </Card>
        )}

        {previewScript?.covers && previewScript.covers.length > 0 && (
          <Card size="small" title="封面建议" style={{ marginBottom: 12 }}>
            {previewScript.covers.map((c, i) => (
              <div key={i}>
                <Tag color="orange">{c.layout}</Tag>
                <Text strong>{c.title}</Text>
                {c.subtitle && <Text type="secondary"> | {c.subtitle}</Text>}
              </div>
            ))}
          </Card>
        )}

        {previewScript?.acts && previewScript.acts.length > 0 && (
          <Card size="small" title={`分幕 (${previewScript.acts.length} 幕)`}>
            <Collapse
              items={previewScript.acts.map((act, i) => ({
                key: i,
                label: (
                  <Space>
                    <Tag color="blue">第{act.act}幕</Tag>
                    <Text strong>{act.scene}</Text>
                    {act.duration_seconds && <Text type="secondary">{act.duration_seconds}s</Text>}
                  </Space>
                ),
                children: (
                  <div>
                    {act.narration && (
                      <Paragraph>
                        <Text strong>旁白：</Text>{act.narration}
                      </Paragraph>
                    )}
                    {act.dialogues && act.dialogues.length > 0 && (
                      <div style={{ marginBottom: 8 }}>
                        <Text strong>台词：</Text>
                        {act.dialogues.map((d, j) => (
                          <Paragraph key={j} style={{ marginLeft: 16, marginBottom: 4 }}>
                            <Tag>{d.character}</Tag>
                            <Text>{d.line}</Text>
                          </Paragraph>
                        ))}
                      </div>
                    )}
                    {act.tts_texts && act.tts_texts.length > 0 && (
                      <div>
                        <Text strong>TTS 配音文本：</Text>
                        {act.tts_texts.map((t, j) => (
                          <Paragraph key={j} style={{ marginLeft: 16, marginBottom: 4 }}>
                            <Text>{t}</Text>
                          </Paragraph>
                        ))}
                      </div>
                    )}
                  </div>
                ),
              }))}
            />
          </Card>
        )}

        {previewScript?.raw_text && !previewScript.acts && (
          <Card size="small" title="原始文本">
            <Paragraph copyable style={{ whiteSpace: 'pre-wrap' }}>
              {previewScript.raw_text}
            </Paragraph>
          </Card>
        )}
      </Modal>
    </div>
  )
}
