import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Table, Modal, Form, Select, Input, message,
  Tag, Popconfirm, Empty, Tooltip, Switch, Descriptions,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EyeOutlined,
  StarOutlined, StarFilled, ThunderboltOutlined,
} from '@ant-design/icons'
import { presetService, Preset } from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'

const { Title, Text } = Typography

export default function PresetsPage() {
  const [presets, setPresets] = useState<Preset[]>([])
  const [loading, setLoading] = useState(false)
  const [createVisible, setCreateVisible] = useState(false)
  const [detailVisible, setDetailVisible] = useState(false)
  const [detailPreset, setDetailPreset] = useState<Preset | null>(null)
  const [createForm] = Form.useForm()
  const { currentProject } = useProject()

  // 可选 Stage 列表（简化版，实际可从 API 获取）
  const stageOptions = [
    { value: 'concept', label: '概念图生成' },
    { value: 'storyboard', label: '分镜生成' },
    { value: 'batch_storyboard', label: 'CSV批量分镜' },
    { value: 'video', label: '视频生成' },
    { value: 'refine', label: '精修/超分' },
    { value: 'edit', label: '视频剪辑' },
    { value: 'export', label: '成片导出' },
  ]

  const loadPresets = async () => {
    setLoading(true)
    try {
      const res = await presetService.list({
        project_id: currentProject?.project_id || '',
      })
      setPresets(res.presets || [])
    } catch (e: any) {
      message.error('加载预设失败: ' + (e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPresets()
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [currentProject?.project_id])

  const handleCreate = () => {
    createForm.resetFields()
    createForm.setFieldsValue({
      stage_id: 'video',
      is_default: false,
      params: '{}',
      reference_asset_ids: '',
    })
    setCreateVisible(true)
  }

  const submitCreate = async () => {
    try {
      const values = await createForm.validateFields()
      let params = {}
      try {
        params = values.params ? JSON.parse(values.params) : {}
      } catch {
        message.error('参数必须是有效的 JSON')
        return
      }
      const refIds = values.reference_asset_ids
        ? values.reference_asset_ids.split(',').map((s: string) => s.trim()).filter(Boolean)
        : []

      await presetService.create({
        name: values.name,
        stage_id: values.stage_id,
        project_id: currentProject?.project_id || '',
        provider_id: values.provider_id || '',
        params,
        reference_asset_ids: refIds,
        description: values.description || '',
        is_default: values.is_default,
      })
      message.success('预设已创建')
      setCreateVisible(false)
      loadPresets()
    } catch (e: any) {
      if (e.errorFields) return
      message.error('创建失败: ' + (e.message || e))
    }
  }

  const handleDelete = async (preset: Preset) => {
    try {
      await presetService.delete(preset.preset_id)
      message.success('预设已删除')
      loadPresets()
    } catch (e: any) {
      message.error('删除失败: ' + (e.message || e))
    }
  }

  const handleSetDefault = async (preset: Preset) => {
    if (!currentProject) {
      message.warning('请先选择项目')
      return
    }
    try {
      await presetService.setDefault(preset.preset_id, currentProject.project_id)
      message.success('已设为项目默认预设')
      loadPresets()
    } catch (e: any) {
      message.error('设置失败: ' + (e.message || e))
    }
  }

  const handleApply = async (preset: Preset) => {
    try {
      const res = await presetService.apply(preset.preset_id)
      // 复制参数到剪贴板
      const snapshot = JSON.stringify(res.snapshot, null, 2)
      await navigator.clipboard.writeText(snapshot)
      message.success('预设参数已复制到剪贴板，可粘贴到对应生成页面使用')
    } catch (e: any) {
      message.error('应用失败: ' + (e.message || e))
    }
  }

  const handleDetail = (preset: Preset) => {
    setDetailPreset(preset)
    setDetailVisible(true)
  }

  const columns = [
    {
      title: '预设名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: Preset) => (
        <Space>
          {record.is_default && <StarFilled style={{ color: '#faad14' }} />}
          <a onClick={() => handleDetail(record)}>{name}</a>
        </Space>
      ),
    },
    {
      title: '阶段',
      dataIndex: 'stage_id',
      key: 'stage_id',
      render: (stageId: string) => {
        const opt = stageOptions.find(o => o.value === stageId)
        return <Tag color="blue">{opt?.label || stageId}</Tag>
      },
    },
    {
      title: '供应商',
      dataIndex: 'provider_id',
      key: 'provider_id',
      render: (v: string) => v || <Text type="secondary">默认</Text>,
    },
    {
      title: '参考资产',
      dataIndex: 'reference_asset_ids',
      key: 'reference_asset_ids',
      render: (ids: string[]) => (
        <Tag>{ids?.length || 0} 个</Tag>
      ),
    },
    {
      title: '范围',
      key: 'scope',
      render: (_: any, record: Preset) => (
        record.project_id
          ? <Tag color="green">项目</Tag>
          : <Tag color="default">全局</Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: any, record: Preset) => (
        <Space size={4}>
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => handleDetail(record)} />
          </Tooltip>
          <Tooltip title="应用预设（复制参数）">
            <Button size="small" type="primary" icon={<ThunderboltOutlined />} onClick={() => handleApply(record)} />
          </Tooltip>
          {currentProject && !record.is_default && (
            <Tooltip title="设为项目默认">
              <Button size="small" icon={<StarOutlined />} onClick={() => handleSetDefault(record)} />
            </Tooltip>
          )}
          <Popconfirm title="确定删除？" onConfirm={() => handleDelete(record)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Title level={4} style={{ margin: 0 }}>
            <StarOutlined /> 任务预设
          </Title>
          {currentProject && (
            <Tag color="blue">当前项目: {currentProject.name}</Tag>
          )}
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadPresets} loading={loading}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建预设</Button>
        </Space>
      </Space>

      <Table
        columns={columns}
        dataSource={presets}
        rowKey="preset_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: false }}
        locale={{ emptyText: <Empty description="暂无预设，点击「新建预设」创建" /> }}
      />

      {/* 创建预设弹窗 */}
      <Modal
        title="新建预设"
        open={createVisible}
        onCancel={() => setCreateVisible(false)}
        onOk={submitCreate}
        okText="创建"
        width={600}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item name="name" label="预设名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：MSR视频标准参数" />
          </Form.Item>
          <Form.Item name="stage_id" label="阶段" rules={[{ required: true }]}>
            <Select options={stageOptions} />
          </Form.Item>
          <Form.Item name="provider_id" label="供应商（可选）">
            <Input placeholder="留空使用默认供应商" />
          </Form.Item>
          <Form.Item name="description" label="描述（可选）">
            <Input.TextArea rows={2} placeholder="预设说明" />
          </Form.Item>
          <Form.Item
            name="params"
            label="参数（JSON 格式）"
            tooltip="阶段参数，如 prompt / seed / size 等"
          >
            <Input.TextArea
              rows={4}
              placeholder='{"prompt": "...", "seed": 42, "size": "1920x1080"}'
              style={{ fontFamily: 'monospace' }}
            />
          </Form.Item>
          <Form.Item
            name="reference_asset_ids"
            label="参考资产 ID（逗号分隔）"
            tooltip="关联的参考资产 ID 列表"
          >
            <Input placeholder="asset_id1, asset_id2, asset_id3" />
          </Form.Item>
          {currentProject && (
            <Form.Item name="is_default" valuePropName="checked" label="设为项目默认预设">
              <Switch />
            </Form.Item>
          )}
        </Form>
      </Modal>

      {/* 预设详情弹窗 */}
      <Modal
        title={detailPreset ? `预设详情: ${detailPreset.name}` : ''}
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailVisible(false)}>关闭</Button>,
          detailPreset && (
            <Button
              key="apply"
              type="primary"
              icon={<ThunderboltOutlined />}
              onClick={() => handleApply(detailPreset)}
            >
              应用预设
            </Button>
          ),
        ]}
        width={600}
      >
        {detailPreset && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="名称">
              {detailPreset.is_default && <StarFilled style={{ color: '#faad14', marginRight: 4 }} />}
              {detailPreset.name}
            </Descriptions.Item>
            <Descriptions.Item label="阶段">
              {stageOptions.find(o => o.value === detailPreset.stage_id)?.label || detailPreset.stage_id}
            </Descriptions.Item>
            <Descriptions.Item label="供应商">
              {detailPreset.provider_id || '默认'}
            </Descriptions.Item>
            <Descriptions.Item label="描述">
              {detailPreset.description || '无'}
            </Descriptions.Item>
            <Descriptions.Item label="参考资产">
              {detailPreset.reference_asset_ids?.length > 0
                ? detailPreset.reference_asset_ids.join(', ')
                : '无'}
            </Descriptions.Item>
            <Descriptions.Item label="参数">
              <pre style={{ margin: 0, maxHeight: 200, overflow: 'auto', fontSize: 12 }}>
                {JSON.stringify(detailPreset.params, null, 2)}
              </pre>
            </Descriptions.Item>
            <Descriptions.Item label="范围">
              {detailPreset.project_id ? '项目级' : '全局'}
              {detailPreset.is_default && <Tag color="gold" style={{ marginLeft: 8 }}>项目默认</Tag>}
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>
    </div>
  )
}
