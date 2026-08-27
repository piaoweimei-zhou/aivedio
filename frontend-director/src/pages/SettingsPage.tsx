import { useEffect, useState, useCallback } from 'react'
import { Typography, Card, Table, Tag, Space, Button, Input, message, Descriptions, Tooltip, Alert, Modal, Steps, Result } from 'antd'
import { ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined, SaveOutlined, ApiOutlined, QuestionCircleOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { providerApi } from '../services/directorApi'

const { Title, Text } = Typography

interface EnvKey {
  key: string
  label: string
  description: string
  value: string
}

const ENV_KEYS: EnvKey[] = [
  { key: 'OPENAI_API_KEY', label: 'OpenAI API Key', description: 'OpenAI / DeepSeek 图片+文本（剧本）', value: '' },
  { key: 'OPENAI_BASE_URL', label: 'OpenAI Base URL', description: 'DeepSeek 填 https://api.deepseek.com', value: '' },
  { key: 'OPENAI_TEXT_MODEL', label: 'OpenAI 文本模型', description: 'AI 剧本用，DeepSeek 默认 deepseek-chat', value: '' },
  { key: 'GEMINI_API_KEY', label: 'Gemini API Key', description: 'Gemini 图片生成', value: '' },
  { key: 'ARK_API_KEY', label: '火山引擎 API Key', description: '火山引擎 图片+视频+文本（ARK_API_KEY）', value: '' },
  { key: 'VOLCENGINE_IMAGE_MODEL', label: '火山引擎图像模型', description: 'Seedream Endpoint ID（如 ep-xxx）', value: '' },
  { key: 'VOLCENGINE_VIDEO_MODEL', label: '火山引擎视频模型', description: 'Seedance Endpoint ID（如 ep-xxx）', value: '' },
  { key: 'VOLCENGINE_TEXT_MODEL', label: '火山引擎文本模型', description: '豆包 Endpoint ID（如 ep-xxx）', value: '' },
  { key: 'RUNNINGHUB_API_KEY', label: 'RunningHub API Key', description: 'RunningHub 图片+视频', value: '' },
  { key: 'MODELSCOPE_API_KEY', label: 'ModelScope API Key', description: 'ModelScope 三视图', value: '' },
  { key: 'JIMENG_CLI_PATH', label: '即梦 CLI 路径', description: '即梦视频生成', value: '' },
  { key: 'FFMPEG_PATH', label: 'ffmpeg 路径', description: '剪辑/导出/录屏/分屏合成', value: '' },
]

export default function SettingsPage() {
  const [providers, setProviders] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [envValues, setEnvValues] = useState<Record<string, string>>({})
  const [testLoading, setTestLoading] = useState<string | null>(null)
  const [, setTestResults] = useState<Record<string, boolean>>({})

  // 从后端加载已保存的 API Key（密钥由服务端 .env 管理，不落浏览器）
  useEffect(() => {
    providerApi.getConfig()
      .then(res => {
        if (res?.config) setEnvValues(res.config)
      })
      .catch(() => { /* 后端不可用时保持空配置 */ })
  }, [])

  const loadProviders = async () => {
    setLoading(true)
    try {
      const res = await providerApi.list()
      setProviders(res.providers || [])
    } catch {
      setProviders([
        { id: 'comfyui', name: 'ComfyUI (本地)', capabilities: ['image', 'refine', 'upscale'], available: true },
        { id: 'openai_compat', name: 'OpenAI 兼容 (图片+文本)', capabilities: ['image', 'text'], available: false },
        { id: 'runninghub', name: 'RunningHub (云端)', capabilities: ['image', 'video'], available: false },
        { id: 'jimeng', name: '即梦 (Jimeng CLI)', capabilities: ['image', 'video'], available: false },
        { id: 'volcengine', name: '火山引擎 (方舟)', capabilities: ['image', 'video'], available: false },
        { id: 'gemini', name: 'Gemini', capabilities: ['image'], available: false },
        { id: 'modelscope', name: 'ModelScope', capabilities: ['image'], available: false },
      ])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadProviders() }, [])

  // 保存 API Key 到后端 .env（服务端统一管理）
  const handleSave = useCallback(async (envKey: string) => {
    try {
      await providerApi.saveConfig({ [envKey]: envValues[envKey] || '' })
      message.success(`${envKey} 已保存到服务端配置`)
      // 刷新供应商状态
      loadProviders()
    } catch {
      message.error(`${envKey} 保存失败`)
    }
  }, [envValues])

  // 测试连通性
  const handleTest = useCallback(async (providerId: string) => {
    setTestLoading(providerId)
    try {
      await providerApi.get(providerId)
      setTestResults(prev => ({ ...prev, [providerId]: true }))
      message.success(`${providerId} 连通性测试通过`)
    } catch {
      setTestResults(prev => ({ ...prev, [providerId]: false }))
      message.error(`${providerId} 连通性测试失败`)
    } finally {
      setTestLoading(null)
    }
  }, [])

  // ============= Provider 配置向导 =============
  const [wizardOpen, setWizardOpen] = useState(false)
  const [wizardStep, setWizardStep] = useState(0)
  const [wizardMeta, setWizardMeta] = useState<any[]>([])
  const [wizardValues, setWizardValues] = useState<Record<string, string>>({})
  const [wizardTesting, setWizardTesting] = useState(false)
  const [wizardTestResult, setWizardTestResult] = useState<Record<string, boolean | null>>({})
  const [wizardSaving, setWizardSaving] = useState(false)
  const [wizardDone, setWizardDone] = useState(false)

  const openWizard = async () => {
    setWizardOpen(true)
    setWizardStep(0)
    setWizardDone(false)
    setWizardValues({})
    setWizardTestResult({})
    try {
      const res = await providerApi.getConfigMeta()
      setWizardMeta(res.providers || [])
    } catch {
      message.error('加载配置元数据失败')
    }
  }

  const handleWizardTest = async (providerId: string) => {
    setWizardTesting(true)
    try {
      const res = await providerApi.testConfig(providerId, wizardValues)
      setWizardTestResult(prev => ({ ...prev, [providerId]: res.available }))
      if (res.available) {
        message.success(`${providerId} 测试通过`)
      } else {
        message.warning(`${providerId} 测试失败：${res.message}`)
      }
    } catch (e: any) {
      setWizardTestResult(prev => ({ ...prev, [providerId]: false }))
      message.error(`${providerId} 测试失败`)
    } finally {
      setWizardTesting(false)
    }
  }

  const handleWizardSave = async () => {
    setWizardSaving(true)
    try {
      const res = await providerApi.saveConfig(wizardValues)
      message.success(res.message)
      setWizardDone(true)
      loadProviders()
    } catch {
      message.error('保存配置失败')
    } finally {
      setWizardSaving(false)
    }
  }

  const columns = [
    {
      title: '供应商',
      dataIndex: 'name',
      key: 'name',
      width: 180,
    },
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 120,
      render: (v: string) => <Text copyable style={{ fontSize: 12 }}>{v}</Text>,
    },
    {
      title: '能力',
      dataIndex: 'capabilities',
      key: 'caps',
      render: (caps: string[]) => (
        <Space size={4}>
          {caps?.map(c => <Tag key={c} color={c === 'image' ? 'blue' : c === 'video' ? 'red' : c === 'text' ? 'purple' : c === 'refine' ? 'cyan' : 'green'}>{c}</Tag>)}
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'available',
      key: 'available',
      width: 70,
      render: (v: boolean) => v
        ? <Tag icon={<CheckCircleOutlined />} color="success">可用</Tag>
        : <Tag icon={<CloseCircleOutlined />} color="default">未配置</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, r: any) => (
        <Button
          size="small"
          icon={<ApiOutlined />}
          onClick={() => handleTest(r.id)}
          loading={testLoading === r.id}
        >
          测试
        </Button>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <Title level={3}>供应商设置</Title>

      <Card
        title="供应商列表"
        extra={
          <Space>
            <Button type="primary" icon={<ThunderboltOutlined />} onClick={openWizard}>
              配置向导
            </Button>
            <Button icon={<ReloadOutlined />} onClick={loadProviders} loading={loading}>刷新</Button>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Table
          dataSource={providers}
          columns={columns}
          rowKey="id"
          size="small"
          pagination={false}
        />
      </Card>

      <Card title="环境变量配置" style={{ marginBottom: 24 }}>
        <Alert
          message="API Key 配置说明"
          description="配置后保存到后端 .env 文件，由服务端统一管理，不存储在浏览器本地。保存后立即生效，可刷新供应商列表查看状态。"
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Descriptions column={1} size="small" bordered>
          {ENV_KEYS.map(env => (
            <Descriptions.Item key={env.key} label={
              <Space size={4}>
                <Text strong style={{ fontSize: 12 }}>{env.key}</Text>
                <Tooltip title={env.description}>
                  <QuestionCircleOutlined style={{ color: '#888', fontSize: 12 }} />
                </Tooltip>
              </Space>
            }>
              <Space style={{ width: '100%' }}>
                <Input.Password
                  value={envValues[env.key] || ''}
                  onChange={e => setEnvValues(prev => ({ ...prev, [env.key]: e.target.value }))}
                  placeholder="点击输入 API Key"
                  style={{ width: 300 }}
                />
                <Button
                  size="small"
                  type="primary"
                  icon={<SaveOutlined />}
                  onClick={() => handleSave(env.key)}
                >
                  保存
                </Button>
              </Space>
            </Descriptions.Item>
          ))}
        </Descriptions>
      </Card>

      <Card title="阶段-供应商映射">
        <Text type="secondary">
          每个生产阶段支持的供应商在阶段定义中声明，可通过 /api/director/stages 查询。
          选择资产后，系统自动匹配可用的阶段和供应商。
        </Text>
      </Card>

      {/* Provider 配置向导 */}
      <Modal
        title="Provider 配置向导"
        open={wizardOpen}
        onCancel={() => setWizardOpen(false)}
        width={720}
        footer={
          wizardDone ? (
            <Button type="primary" onClick={() => setWizardOpen(false)}>完成</Button>
          ) : (
            <Space>
              <Button onClick={() => setWizardOpen(false)}>取消</Button>
              {wizardStep > 0 && (
                <Button onClick={() => setWizardStep(s => s - 1)}>上一步</Button>
              )}
              {wizardStep < wizardMeta.length - 1 && (
                <Button type="primary" onClick={() => setWizardStep(s => s + 1)}>下一步</Button>
              )}
              {wizardStep === wizardMeta.length - 1 && (
                <Button type="primary" icon={<SaveOutlined />} loading={wizardSaving} onClick={handleWizardSave}>
                  保存全部配置
                </Button>
              )}
            </Space>
          )
        }
      >
        {wizardDone ? (
          <Result
            status="success"
            title="配置已保存"
            subTitle="配置已写入后端 .env 文件并立即生效。请刷新供应商列表查看状态。"
          />
        ) : wizardMeta.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40 }}>加载中...</div>
        ) : (
          <>
            <Steps
              current={wizardStep}
              size="small"
              style={{ marginBottom: 24 }}
              items={wizardMeta.map((p, i) => ({
                title: p.name,
                status: i < wizardStep ? 'finish' : i === wizardStep ? 'process' : 'wait',
              }))}
            />

            {wizardMeta[wizardStep] && (
              <div>
                <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
                  <Title level={5}>{wizardMeta[wizardStep].name}</Title>
                  <Text type="secondary">{wizardMeta[wizardStep].description}</Text>
                  <Space size={4}>
                    {wizardMeta[wizardStep].capabilities?.map((c: string) => (
                      <Tag key={c} color={c === 'image' ? 'blue' : c === 'video' ? 'red' : 'green'}>{c}</Tag>
                    ))}
                  </Space>
                  {wizardMeta[wizardStep].docs_url && (
                    <a href={wizardMeta[wizardStep].docs_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
                      查看文档 →
                    </a>
                  )}
                </Space>

                {wizardMeta[wizardStep].required_envs?.map((env: any) => (
                  <div key={env.key} style={{ marginBottom: 12 }}>
                    <Space style={{ width: '100%' }}>
                      <div style={{ flex: 1 }}>
                        <Text strong style={{ fontSize: 12 }}>{env.label}</Text>
                        <Text type="secondary" style={{ fontSize: 11, marginLeft: 8 }}>{env.key}</Text>
                        {env.required && <Text type="danger" style={{ fontSize: 11 }}> *</Text>}
                      </div>
                    </Space>
                    <Input.Password
                      value={wizardValues[env.key] || ''}
                      onChange={e => setWizardValues(prev => ({ ...prev, [env.key]: e.target.value }))}
                      placeholder={env.default || `请输入 ${env.label}`}
                      style={{ marginTop: 4 }}
                    />
                  </div>
                ))}

                <Space style={{ marginTop: 16 }}>
                  <Button
                    icon={<ApiOutlined />}
                    loading={wizardTesting}
                    onClick={() => handleWizardTest(wizardMeta[wizardStep].provider_id)}
                  >
                    测试此 Provider
                  </Button>
                  {wizardTestResult[wizardMeta[wizardStep].provider_id] === true && (
                    <Tag icon={<CheckCircleOutlined />} color="success">测试通过</Tag>
                  )}
                  {wizardTestResult[wizardMeta[wizardStep].provider_id] === false && (
                    <Tag icon={<CloseCircleOutlined />} color="error">测试失败</Tag>
                  )}
                </Space>
              </div>
            )}
          </>
        )}
      </Modal>
    </div>
  )
}
