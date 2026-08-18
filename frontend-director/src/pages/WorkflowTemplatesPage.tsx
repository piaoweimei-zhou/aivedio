import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Card, Row, Col, Tag, Modal, Form, Select,
  Input, message, Empty, Tooltip, Steps, Divider, Alert, Switch,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, ThunderboltOutlined, PlayCircleOutlined,
  DeleteOutlined, EditOutlined, EyeOutlined, ApartmentOutlined,
} from '@ant-design/icons'
import {
  workflowTemplateService, WorkflowTemplate, WorkflowStepTemplate,
} from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'

const { Title, Text, Paragraph } = Typography

const CATEGORY_COLOR: Record<string, string> = {
  preset: 'blue',
  custom: 'green',
}

const CATEGORY_TEXT: Record<string, string> = {
  preset: '预置',
  custom: '自定义',
}

const TEMPLATE_ICON: Record<string, string> = {
  preset_concept_to_video: '🎬',
  preset_storyboard_to_video: '🎥',
  preset_batch_storyboard_to_video: '📋',
  preset_character_pipeline: '👤',
  preset_refine_pipeline: '✨',
  preset_video_edit_pipeline: '🎞️',
}

export default function WorkflowTemplatesPage() {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([])
  const [loading, setLoading] = useState(false)
  const [detailVisible, setDetailVisible] = useState(false)
  const [detailTemplate, setDetailTemplate] = useState<WorkflowTemplate | null>(null)
  const [createBatchVisible, setCreateBatchVisible] = useState(false)
  const [createBatchTemplate, setCreateBatchTemplate] = useState<WorkflowTemplate | null>(null)
  const [createBatchForm] = Form.useForm()
  const { currentProject } = useProject()

  const loadTemplates = async () => {
    setLoading(true)
    try {
      const res = await workflowTemplateService.list()
      setTemplates(res.templates || [])
    } catch (e: any) {
      message.error('加载模板失败: ' + (e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTemplates()
  }, [])

  const handleDetail = (tpl: WorkflowTemplate) => {
    setDetailTemplate(tpl)
    setDetailVisible(true)
  }

  const handleCreateBatch = (tpl: WorkflowTemplate) => {
    setCreateBatchTemplate(tpl)
    createBatchForm.resetFields()
    createBatchForm.setFieldsValue({
      name: `${tpl.name}_${new Date().toLocaleDateString()}`,
      stop_on_failure: true,
      auto_start: false,
    })
    setCreateBatchVisible(true)
  }

  const handleDelete = async (tpl: WorkflowTemplate) => {
    try {
      await workflowTemplateService.delete(tpl.template_id)
      message.success('模板已删除')
      loadTemplates()
    } catch (e: any) {
      message.error('删除失败: ' + (e.message || e))
    }
  }

  const submitCreateBatch = async () => {
    if (!createBatchTemplate) return
    try {
      const values = await createBatchForm.validateFields()
      // 收集用户选择的输入资产
      const inputAssets: Record<string, string[]> = {}
      if (createBatchTemplate.required_inputs) {
        for (const req of createBatchTemplate.required_inputs) {
          const ids = values[`input_${req.key}`]
          if (ids && ids.length > 0) {
            inputAssets[req.key] = ids
          }
        }
      }

      const res = await workflowTemplateService.createBatch(
        createBatchTemplate.template_id,
        {
          name: values.name,
          project_id: values.project_id || currentProject?.project_id || '',
          input_assets: inputAssets,
          stop_on_failure: values.stop_on_failure,
          auto_start: values.auto_start,
        }
      )
      message.success(`批量任务已创建${values.auto_start ? '并启动' : ''}`)
      setCreateBatchVisible(false)
      // 跳转到批量任务页面
      window.location.href = '/batches'
    } catch (e: any) {
      if (e.errorFields) return  // 表单校验错误
      message.error('创建失败: ' + (e.message || e))
    }
  }

  const presetTemplates = templates.filter(t => t.category === 'preset')
  const customTemplates = templates.filter(t => t.category === 'custom')

  const renderTemplateCard = (tpl: WorkflowTemplate) => (
    <Col xs={24} sm={12} lg={8} xl={6} key={tpl.template_id}>
      <Card
        hoverable
        size="small"
        title={
          <Space>
            <span style={{ fontSize: 18 }}>
              {TEMPLATE_ICON[tpl.template_id] || '⚡'}
            </span>
            <Text strong>{tpl.name}</Text>
          </Space>
        }
        extra={
          <Tag color={CATEGORY_COLOR[tpl.category]}>
            {CATEGORY_TEXT[tpl.category]}
          </Tag>
        }
        actions={[
          <Tooltip title="查看详情" key="detail">
            <EyeOutlined onClick={() => handleDetail(tpl)} />
          </Tooltip>,
          <Tooltip title="从模板创建批量任务" key="create">
            <PlayCircleOutlined onClick={() => handleCreateBatch(tpl)} />
          </Tooltip>,
          ...(tpl.category === 'custom' ? [
            <Tooltip title="删除" key="delete">
              <DeleteOutlined onClick={() => handleDelete(tpl)} />
            </Tooltip>,
          ] : []),
        ]}
      >
        <Paragraph
          type="secondary"
          ellipsis={{ rows: 3 }}
          style={{ marginBottom: 8, minHeight: 60 }}
        >
          {tpl.description || '无描述'}
        </Paragraph>
        <Space size={[4, 4]} wrap>
          <Tag icon={<ApartmentOutlined />} color="processing">
            {tpl.steps.length} 步
          </Tag>
          {tpl.required_inputs?.length > 0 && (
            <Tag color="warning">
              需 {tpl.required_inputs.length} 类输入
            </Tag>
          )}
        </Space>
      </Card>
    </Col>
  )

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Space>
          <Title level={4} style={{ margin: 0 }}>
            <ThunderboltOutlined /> 工作流模板
          </Title>
          {currentProject && (
            <Tag color="blue">当前项目: {currentProject.name}</Tag>
          )}
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadTemplates} loading={loading}>
            刷新
          </Button>
        </Space>
      </Space>

      <Alert
        type="info"
        showIcon
        message="工作流模板是预置的生产流水线，一键创建批量任务"
        description="选择合适的模板，按提示选择输入资产，系统会自动编排步骤并传递上下文。"
        style={{ marginBottom: 16 }}
      />

      {presetTemplates.length > 0 && (
        <>
          <Title level={5} style={{ marginTop: 8 }}>
            预置模板
          </Title>
          <Row gutter={[12, 12]}>
            {presetTemplates.map(renderTemplateCard)}
          </Row>
        </>
      )}

      {customTemplates.length > 0 && (
        <>
          <Divider />
          <Title level={5}>
            自定义模板
          </Title>
          <Row gutter={[12, 12]}>
            {customTemplates.map(renderTemplateCard)}
          </Row>
        </>
      )}

      {templates.length === 0 && !loading && (
        <Empty description="暂无工作流模板" />
      )}

      {/* 模板详情弹窗 */}
      <Modal
        title={detailTemplate ? `模板详情: ${detailTemplate.name}` : ''}
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailVisible(false)}>关闭</Button>,
          detailTemplate && (
            <Button
              key="create"
              type="primary"
              icon={<PlayCircleOutlined />}
              onClick={() => {
                setDetailVisible(false)
                handleCreateBatch(detailTemplate)
              }}
            >
              从模板创建批量任务
            </Button>
          ),
        ]}
        width={700}
      >
        {detailTemplate && (
          <div>
            <Paragraph type="secondary">
              {detailTemplate.description || '无描述'}
            </Paragraph>

            {detailTemplate.required_inputs?.length > 0 && (
              <>
                <Title level={5}>需要的输入资产</Title>
                <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
                  {detailTemplate.required_inputs.map((req, i) => (
                    <Card key={i} size="small">
                      <Space>
                        <Tag color="blue">{req.label}</Tag>
                        <Text type="secondary">
                          类型: {req.asset_type || '任意'}
                          {req.content_type ? ` · 内容: ${req.content_type}` : ''}
                        </Text>
                      </Space>
                    </Card>
                  ))}
                </Space>
              </>
            )}

            <Title level={5}>步骤编排</Title>
            <Steps
              direction="vertical"
              size="small"
              current={detailTemplate.steps.length - 1}
              items={detailTemplate.steps.map((s, i) => ({
                title: `${i + 1}. ${s.name || s.stage_id}`,
                description: (
                  <Space direction="vertical" size={2}>
                    <Text type="secondary">阶段: {s.stage_id}</Text>
                    {s.description && <Text type="secondary">{s.description}</Text>}
                    <Space size={4}>
                      <Tag>
                        {s.input_mode === 'auto' ? '自动引用前序' :
                         s.input_mode === 'fixed' ? '固定输入' : '用户选择'}
                      </Tag>
                      {s.input_from_steps && s.input_from_steps.length > 0 && (
                        <Tag color="cyan">引用: {s.input_from_steps.join(', ')}</Tag>
                      )}
                    </Space>
                  </Space>
                ),
              }))}
            />
          </div>
        )}
      </Modal>

      {/* 从模板创建批量任务弹窗 */}
      <Modal
        title={createBatchTemplate ? `从模板创建: ${createBatchTemplate.name}` : ''}
        open={createBatchVisible}
        onCancel={() => setCreateBatchVisible(false)}
        onOk={submitCreateBatch}
        okText="创建"
        width={600}
      >
        {createBatchTemplate && (
          <Form form={createBatchForm} layout="vertical">
            <Form.Item
              name="name"
              label="批量任务名称"
              rules={[{ required: true, message: '请输入名称' }]}
            >
              <Input placeholder="输入批量任务名称" />
            </Form.Item>

            <Form.Item name="project_id" label="所属项目">
              <Select
                allowClear
                placeholder="选择项目（可选，默认当前项目）"
                style={{ width: '100%' }}
                options={[
                  { value: '', label: '不指定项目' },
                  ...(currentProject ? [{ value: currentProject.project_id, label: currentProject.name }] : []),
                ]}
              />
            </Form.Item>

            {createBatchTemplate.required_inputs?.length > 0 && (
              <>
                <Divider orientation="left">选择输入资产</Divider>
                {createBatchTemplate.required_inputs.map(req => (
                  <Form.Item
                    key={req.key}
                    name={`input_${req.key}`}
                    label={req.label}
                    tooltip={`资产类型: ${req.asset_type || '任意'}, 内容类型: ${req.content_type || '任意'}`}
                  >
                    <Select
                      mode="multiple"
                      placeholder={`选择${req.label}（可多选）`}
                      allowClear
                      showSearch
                      optionFilterProp="label"
                    />
                  </Form.Item>
                ))}
                <Alert
                  type="warning"
                  message="提示"
                  description="输入资产选择框支持搜索。如果模板步骤需要用户选择输入，请在此处选择对应资产。"
                  style={{ marginBottom: 16 }}
                />
              </>
            )}

            <Divider orientation="left">执行选项</Divider>
            <Form.Item name="stop_on_failure" valuePropName="checked" label="失败时停止">
              <Switch />
            </Form.Item>
            <Form.Item name="auto_start" valuePropName="checked" label="创建后自动启动">
              <Switch />
            </Form.Item>
          </Form>
        )}
      </Modal>
    </div>
  )
}
