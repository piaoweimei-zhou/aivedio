import { useEffect, useState } from 'react'
import {
  Typography, Button, Space, Table, Modal, Form, Select, Input, InputNumber,
  message, Tag, Popconfirm, Empty, Tooltip, Card, Row, Col, Statistic, Divider,
  Timeline,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EyeOutlined,
  ThunderboltOutlined, CopyOutlined, TagsOutlined, SearchOutlined,
  StarOutlined, StarFilled, HistoryOutlined, RollbackOutlined,
} from '@ant-design/icons'
import { promptService, PromptEntry } from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'

const { Title, Text, Paragraph } = Typography
const { TextArea } = Input

// 阶段选项
const stageOptions = [
  { value: '', label: '通用（所有阶段）' },
  { value: 'concept', label: '概念图生成' },
  { value: 'storyboard', label: '分镜生成' },
  { value: 'batch_storyboard', label: 'CSV批量分镜' },
  { value: 'video', label: '视频生成' },
  { value: 'refine', label: '精修/超分' },
  { value: 'edit', label: '视频剪辑' },
  { value: 'export', label: '成片导出' },
]

// 分类选项
const categoryOptions = [
  { value: 'custom', label: '自定义', color: 'default' },
  { value: 'action', label: '动作', color: 'blue' },
  { value: 'dialogue', label: '对话', color: 'green' },
  { value: 'scene', label: '场景', color: 'orange' },
  { value: 'transition', label: '转场', color: 'purple' },
  { value: 'style', label: '风格', color: 'magenta' },
]

const categoryColorMap: Record<string, string> = {
  custom: 'default',
  action: 'blue',
  dialogue: 'green',
  scene: 'orange',
  transition: 'purple',
  style: 'magenta',
}

export default function PromptsPage() {
  const [prompts, setPrompts] = useState<PromptEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [createVisible, setCreateVisible] = useState(false)
  const [editVisible, setEditVisible] = useState(false)
  const [detailVisible, setDetailVisible] = useState(false)
  const [resolveVisible, setResolveVisible] = useState(false)
  const [historyVisible, setHistoryVisible] = useState(false)
  const [detailPrompt, setDetailPrompt] = useState<PromptEntry | null>(null)
  const [resolvePrompt, setResolvePrompt] = useState<PromptEntry | null>(null)
  const [resolveResult, setResolveResult] = useState<string>('')
  const [resolveVars, setResolveVars] = useState<Record<string, string>>({})
  const [historyVersions, setHistoryVersions] = useState<any[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()
  const { currentProject } = useProject()

  // 过滤条件
  const [filterStage, setFilterStage] = useState<string>('')
  const [filterCategory, setFilterCategory] = useState<string>('')
  const [filterKeyword, setFilterKeyword] = useState<string>('')

  // 统计
  const [stats, setStats] = useState<any>(null)

  const loadPrompts = async () => {
    setLoading(true)
    try {
      const res = await promptService.list({
        project_id: currentProject?.project_id || '',
        stage_id: filterStage,
        category: filterCategory,
        keyword: filterKeyword,
      })
      setPrompts(res.prompts || [])
    } catch (e: any) {
      message.error('加载提示词失败: ' + (e.message || e))
    } finally {
      setLoading(false)
    }
  }

  const loadStats = async () => {
    try {
      const res = await promptService.stats(currentProject?.project_id || '')
      setStats(res.stats)
    } catch {
      // 忽略
    }
  }

  useEffect(() => {
    loadPrompts()
    loadStats()
  // eslint-disable-next-line react-hooks/exhaustive-deps -- load 函数非稳定（未 useCallback），依赖含 filter 条件即重载；补依赖会触发重载循环
  }, [currentProject?.project_id, filterStage, filterCategory, filterKeyword])

  // ==================== 创建 ====================
  const handleCreate = () => {
    createForm.resetFields()
    createForm.setFieldsValue({
      category: 'custom',
      stage_id: '',
      quality_score: 0,
      tags: '',
      description: '',
      content: '',
    })
    setCreateVisible(true)
  }

  const submitCreate = async () => {
    try {
      const values = await createForm.validateFields()
      const tags = values.tags
        ? values.tags.split(',').map((s: string) => s.trim()).filter(Boolean)
        : []

      await promptService.create({
        name: values.name,
        content: values.content,
        category: values.category || 'custom',
        stage_id: values.stage_id || '',
        tags,
        project_id: currentProject?.project_id || '',
        description: values.description || '',
        quality_score: values.quality_score || 0,
      })
      message.success('提示词创建成功')
      setCreateVisible(false)
      loadPrompts()
      loadStats()
    } catch (e: any) {
      if (e.errorFields) return
      message.error('创建失败: ' + (e.message || e))
    }
  }

  // ==================== 编辑 ====================
  const handleEdit = (record: PromptEntry) => {
    editForm.resetFields()
    editForm.setFieldsValue({
      name: record.name,
      content: record.content,
      category: record.category,
      stage_id: record.stage_id,
      tags: record.tags.join(', '),
      description: record.description,
      quality_score: record.quality_score,
    })
    setDetailPrompt(record)
    setEditVisible(true)
  }

  const submitEdit = async () => {
    if (!detailPrompt) return
    try {
      const values = await editForm.validateFields()
      const tags = values.tags
        ? values.tags.split(',').map((s: string) => s.trim()).filter(Boolean)
        : []

      await promptService.update(detailPrompt.prompt_id, {
        name: values.name,
        content: values.content,
        category: values.category,
        stage_id: values.stage_id,
        tags,
        description: values.description,
        quality_score: values.quality_score,
      })
      message.success('提示词已更新')
      setEditVisible(false)
      loadPrompts()
    } catch (e: any) {
      if (e.errorFields) return
      message.error('更新失败: ' + (e.message || e))
    }
  }

  // ==================== 解析（变量替换预览） ====================
  const handleResolve = (record: PromptEntry) => {
    setResolvePrompt(record)
    const vars: Record<string, string> = {}
    record.variables.forEach(v => {
      vars[v.name] = v.default || ''
    })
    setResolveVars(vars)
    setResolveResult('')
    setResolveVisible(true)
  }

  const submitResolve = async () => {
    if (!resolvePrompt) return
    try {
      const res = await promptService.resolve(resolvePrompt.prompt_id, resolveVars)
      setResolveResult(res.resolved)
      message.success('解析成功')
      loadPrompts()
    } catch (e: any) {
      message.error('解析失败: ' + (e.message || e))
    }
  }

  // ==================== 复制内容 ====================
  const handleCopy = (record: PromptEntry) => {
    navigator.clipboard.writeText(record.content).then(() => {
      message.success('提示词内容已复制到剪贴板')
    })
  }

  // ==================== 删除 ====================
  const handleDelete = async (record: PromptEntry) => {
    try {
      await promptService.delete(record.prompt_id)
      message.success('提示词已删除')
      loadPrompts()
      loadStats()
    } catch (e: any) {
      message.error('删除失败: ' + (e.message || e))
    }
  }

  // ==================== 阶段 C：设为项目默认 ====================
  const handleSetDefault = async (record: PromptEntry) => {
    if (!currentProject) {
      message.warning('请先选择项目')
      return
    }
    try {
      await promptService.setDefault(
        record.prompt_id,
        currentProject.project_id,
        record.stage_id || '',
      )
      message.success(`已设为项目「${currentProject.name}」${record.stage_id ? `-${record.stage_id}` : '通用'}默认提示词`)
      loadPrompts()
    } catch (e: any) {
      message.error('设置失败: ' + (e.message || e))
    }
  }

  const handleUnsetDefault = async (record: PromptEntry) => {
    try {
      await promptService.unsetDefault(record.prompt_id)
      message.success('已取消默认')
      loadPrompts()
    } catch (e: any) {
      message.error('取消失败: ' + (e.message || e))
    }
  }

  // ==================== 阶段 C：版本历史 ====================
  const handleHistory = async (record: PromptEntry) => {
    setDetailPrompt(record)
    setHistoryVisible(true)
    setHistoryLoading(true)
    try {
      const res = await promptService.history(record.prompt_id)
      setHistoryVersions(res.versions || [])
    } catch (e: any) {
      message.error('加载历史失败: ' + (e.message || e))
    } finally {
      setHistoryLoading(false)
    }
  }

  const handleRollback = async (version: number) => {
    if (!detailPrompt) return
    try {
      await promptService.rollback(detailPrompt.prompt_id, version)
      message.success(`已回滚到 v${version}`)
      setHistoryVisible(false)
      loadPrompts()
    } catch (e: any) {
      message.error('回滚失败: ' + (e.message || e))
    }
  }

  // ==================== 表格列 ====================
  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string, record: PromptEntry) => (
        <Space direction="vertical" size={0}>
          <Space>
            {record.is_default && (
              <Tooltip title="项目默认提示词">
                <StarFilled style={{ color: '#faad14' }} />
              </Tooltip>
            )}
            <a onClick={() => {
              setDetailPrompt(record)
              setDetailVisible(true)
            }}>{name}</a>
            {record.quality_score >= 4 && (
              <Tag color="gold">★{record.quality_score.toFixed(1)}</Tag>
            )}
            {record.version > 1 && (
              <Tag color="purple" style={{ fontSize: 11 }}>v{record.version}</Tag>
            )}
          </Space>
          {record.description && (
            <Text type="secondary" style={{ fontSize: 12 }}>{record.description}</Text>
          )}
        </Space>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 90,
      render: (cat: string) => {
        const opt = categoryOptions.find(o => o.value === cat)
        return <Tag color={categoryColorMap[cat] || 'default'}>{opt?.label || cat}</Tag>
      },
    },
    {
      title: '阶段',
      dataIndex: 'stage_id',
      key: 'stage_id',
      width: 120,
      render: (stageId: string) => {
        if (!stageId) return <Tag>通用</Tag>
        const opt = stageOptions.find(o => o.value === stageId)
        return <Tag color="blue">{opt?.label || stageId}</Tag>
      },
    },
    {
      title: '内容预览',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (content: string) => (
        <Text type="secondary" ellipsis={{ tooltip: content }} style={{ maxWidth: 280 }}>
          {content}
        </Text>
      ),
    },
    {
      title: '变量',
      key: 'variables',
      width: 120,
      render: (_: any, record: PromptEntry) => (
        record.variables.length > 0 ? (
          <Space size={2} wrap>
            {record.variables.map(v => (
              <Tag key={v.name} style={{ fontSize: 11 }}>{`{${v.name}}`}</Tag>
            ))}
          </Space>
        ) : <Text type="secondary">无</Text>
      ),
    },
    {
      title: '标签',
      dataIndex: 'tags',
      key: 'tags',
      width: 120,
      render: (tags: string[]) => (
        tags.length > 0 ? (
          <Space size={2} wrap>
            {tags.map(t => <Tag key={t} color="cyan" style={{ fontSize: 11 }}>{t}</Tag>)}
          </Space>
        ) : <Text type="secondary">-</Text>
      ),
    },
    {
      title: '使用',
      dataIndex: 'usage_count',
      key: 'usage_count',
      width: 60,
      render: (count: number) => <Text type="secondary">{count}</Text>,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: any, record: PromptEntry) => (
        <Space size={4}>
          <Tooltip title="查看详情">
            <Button size="small" icon={<EyeOutlined />} onClick={() => {
              setDetailPrompt(record)
              setDetailVisible(true)
            }} />
          </Tooltip>
          <Tooltip title="解析预览（变量替换）">
            <Button size="small" type="primary" icon={<ThunderboltOutlined />} onClick={() => handleResolve(record)} />
          </Tooltip>
          <Tooltip title="复制内容">
            <Button size="small" icon={<CopyOutlined />} onClick={() => handleCopy(record)} />
          </Tooltip>
          <Tooltip title="编辑">
            <Button size="small" icon={<TagsOutlined />} onClick={() => handleEdit(record)} />
          </Tooltip>
          <Tooltip title="版本历史">
            <Button size="small" icon={<HistoryOutlined />} onClick={() => handleHistory(record)} />
          </Tooltip>
          {currentProject && !record.is_default && (
            <Tooltip title="设为项目默认">
              <Button size="small" icon={<StarOutlined />} onClick={() => handleSetDefault(record)} />
            </Tooltip>
          )}
          {record.is_default && (
            <Popconfirm title="取消默认提示词？" onConfirm={() => handleUnsetDefault(record)}>
              <Tooltip title="已是默认，点击取消">
                <Button size="small" type="primary" ghost icon={<StarFilled />} />
              </Tooltip>
            </Popconfirm>
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
      <Title level={3}>提示词中心</Title>
      <Paragraph type="secondary">
        集中管理所有阶段的提示词，支持变量模板（{'{character}'}、{'{scene}'}）、分类标签、项目级隔离。
        好的提示词一次保存，反复复用。
      </Paragraph>

      {/* 统计卡片 */}
      {stats && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Card size="small">
              <Statistic title="提示词总数" value={stats.total} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="平均质量分" value={stats.avg_quality?.toFixed(1) || 0} suffix="/5" />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="累计使用次数" value={stats.total_usage} />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic title="当前项目" value={currentProject?.name || '全局'} />
            </Card>
          </Col>
        </Row>
      )}

      {/* 过滤栏 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            style={{ width: 160 }}
            placeholder="按阶段过滤"
            allowClear
            value={filterStage || undefined}
            onChange={v => setFilterStage(v || '')}
            options={stageOptions.filter(o => o.value)}
          />
          <Select
            style={{ width: 140 }}
            placeholder="按分类过滤"
            allowClear
            value={filterCategory || undefined}
            onChange={v => setFilterCategory(v || '')}
            options={categoryOptions}
          />
          <Input
            style={{ width: 220 }}
            placeholder="搜索名称/内容/描述"
            prefix={<SearchOutlined />}
            allowClear
            value={filterKeyword}
            onChange={e => setFilterKeyword(e.target.value)}
          />
          <Button icon={<ReloadOutlined />} onClick={loadPrompts}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建提示词</Button>
        </Space>
      </Card>

      {/* 列表 */}
      <Table
        columns={columns}
        dataSource={prompts}
        rowKey="prompt_id"
        loading={loading}
        size="small"
        pagination={{ pageSize: 20, showSizeChanger: true }}
        locale={{
          emptyText: <Empty description="暂无提示词，点击「新建提示词」创建" />
        }}
      />

      {/* 创建弹窗 */}
      <Modal
        title="新建提示词"
        open={createVisible}
        onOk={submitCreate}
        onCancel={() => setCreateVisible(false)}
        width={720}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：角色行走-正面" />
          </Form.Item>
          <Form.Item name="content" label="提示词内容" rules={[{ required: true, message: '请输入提示词内容' }]}>
            <TextArea
              rows={4}
              placeholder={'支持变量占位符，如：{character}正在{action}，{scene}背景，电影级光影'}
            />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input placeholder="可选，提示词用途说明" />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="category" label="分类">
                <Select options={categoryOptions} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="stage_id" label="绑定阶段">
                <Select options={stageOptions} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="quality_score" label="质量评分 (0-5)">
                <InputNumber min={0} max={5} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="tags" label="标签（逗号分隔）">
            <Input placeholder="如：行走, 正面, 角色" />
          </Form.Item>
          <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
            提示：{'{变量名}'} 格式的占位符会在保存时自动识别为变量，后续可通过"解析预览"填入实际值。
          </Paragraph>
        </Form>
      </Modal>

      {/* 编辑弹窗 */}
      <Modal
        title="编辑提示词"
        open={editVisible}
        onOk={submitEdit}
        onCancel={() => setEditVisible(false)}
        width={720}
        okText="保存"
        cancelText="取消"
      >
        <Form form={editForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="content" label="提示词内容" rules={[{ required: true, message: '请输入提示词内容' }]}>
            <TextArea rows={4} />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input />
          </Form.Item>
          <Row gutter={16}>
            <Col span={8}>
              <Form.Item name="category" label="分类">
                <Select options={categoryOptions} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="stage_id" label="绑定阶段">
                <Select options={stageOptions} />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="quality_score" label="质量评分 (0-5)">
                <InputNumber min={0} max={5} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="tags" label="标签（逗号分隔）">
            <Input />
          </Form.Item>
        </Form>
      </Modal>

      {/* 详情弹窗 */}
      <Modal
        title="提示词详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailVisible(false)}>关闭</Button>,
        ]}
        width={680}
      >
        {detailPrompt && (
          <div>
            <Title level={4}>{detailPrompt.name}</Title>
            <Paragraph type="secondary">{detailPrompt.description || '无说明'}</Paragraph>
            <Divider />
            <Row gutter={16}>
              <Col span={6}><Text strong>分类：</Text><Tag color={categoryColorMap[detailPrompt.category]}>{detailPrompt.category}</Tag></Col>
              <Col span={6}><Text strong>阶段：</Text><Tag>{detailPrompt.stage_id || '通用'}</Tag></Col>
              <Col span={6}><Text strong>质量分：</Text>{detailPrompt.quality_score.toFixed(1)}</Col>
              <Col span={6}><Text strong>使用次数：</Text>{detailPrompt.usage_count}</Col>
            </Row>
            <Divider />
            <Text strong>提示词内容：</Text>
            <Paragraph style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, marginTop: 8, whiteSpace: 'pre-wrap' }}>
              {detailPrompt.content}
            </Paragraph>
            {detailPrompt.variables.length > 0 && (
              <>
                <Text strong>变量定义：</Text>
                <div style={{ marginTop: 8 }}>
                  {detailPrompt.variables.map(v => (
                    <Tag key={v.name} color="blue" style={{ marginBottom: 4 }}>
                      {`{${v.name}}`}{v.default ? ` = ${v.default}` : ''}{v.required ? ' (必填)' : ''}
                    </Tag>
                  ))}
                </div>
              </>
            )}
            {detailPrompt.tags.length > 0 && (
              <>
                <Divider />
                <Text strong>标签：</Text>
                <Space size={4} wrap style={{ marginLeft: 8 }}>
                  {detailPrompt.tags.map(t => <Tag key={t} color="cyan">{t}</Tag>)}
                </Space>
              </>
            )}
          </div>
        )}
      </Modal>

      {/* 解析预览弹窗 */}
      <Modal
        title="解析预览 — 变量替换"
        open={resolveVisible}
        onOk={submitResolve}
        onCancel={() => setResolveVisible(false)}
        width={680}
        okText="解析"
        cancelText="关闭"
      >
        {resolvePrompt && (
          <div>
            <Text strong>提示词：</Text>
            <Paragraph style={{ background: '#f5f5f5', padding: 12, borderRadius: 4, marginTop: 8 }}>
              {resolvePrompt.content}
            </Paragraph>
            <Divider />
            <Text strong>填入变量值：</Text>
            <div style={{ marginTop: 8 }}>
              {resolvePrompt.variables.length === 0 ? (
                <Text type="secondary">此提示词无变量，可直接解析</Text>
              ) : (
                resolvePrompt.variables.map(v => (
                  <Row key={v.name} gutter={8} style={{ marginBottom: 8 }}>
                    <Col span={6}><Text strong>{`{${v.name}}`}</Text></Col>
                    <Col span={18}>
                      <Input
                        placeholder={v.description || `请输入 ${v.name}`}
                        value={resolveVars[v.name] || ''}
                        onChange={e => setResolveVars({
                          ...resolveVars,
                          [v.name]: e.target.value,
                        })}
                      />
                    </Col>
                  </Row>
                ))
              )}
            </div>
            {resolveResult && (
              <>
                <Divider />
                <Text strong>解析结果：</Text>
                <Paragraph style={{ background: '#e6f7ff', padding: 12, borderRadius: 4, marginTop: 8, border: '1px solid #91d5ff' }}>
                  {resolveResult}
                </Paragraph>
                <Button
                  size="small"
                  icon={<CopyOutlined />}
                  onClick={() => {
                    navigator.clipboard.writeText(resolveResult)
                    message.success('结果已复制')
                  }}
                >
                  复制结果
                </Button>
              </>
            )}
          </div>
        )}
      </Modal>

      {/* 阶段 C：版本历史弹窗 */}
      <Modal
        title={`版本历史 - ${detailPrompt?.name || ''}`}
        open={historyVisible}
        onCancel={() => setHistoryVisible(false)}
        footer={[
          <Button key="close" onClick={() => setHistoryVisible(false)}>关闭</Button>,
        ]}
        width={680}
      >
        {detailPrompt && (
          <div>
            <Paragraph type="secondary">
              当前版本：<Tag color="blue">v{detailPrompt.version}</Tag>
              每次编辑会自动保存历史版本，可随时回滚。
            </Paragraph>
            <Divider />
            {historyLoading ? (
              <Text type="secondary">加载中...</Text>
            ) : historyVersions.length === 0 ? (
              <Empty description="暂无历史版本（仅编辑后才会产生历史）" />
            ) : (
              <Timeline
                items={historyVersions.map(v => ({
                  color: v.version === detailPrompt.version ? 'blue' : 'gray',
                  children: (
                    <div key={v.version}>
                      <Space>
                        <Tag color={v.version === detailPrompt.version ? 'blue' : 'default'}>
                          v{v.version}
                        </Tag>
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {v.updated_at ? new Date(v.updated_at * 1000).toLocaleString() : ''}
                        </Text>
                        {v.version !== detailPrompt.version && (
                          <Popconfirm
                            title={`确定回滚到 v${v.version}？`}
                            description="当前版本会保存为历史，可再次回滚。"
                            onConfirm={() => handleRollback(v.version)}
                          >
                            <Button size="small" icon={<RollbackOutlined />}>
                              回滚到此版本
                            </Button>
                          </Popconfirm>
                        )}
                      </Space>
                      <div style={{
                        background: '#f5f5f5',
                        padding: 8,
                        borderRadius: 4,
                        marginTop: 4,
                        marginBottom: 12,
                        whiteSpace: 'pre-wrap',
                        fontSize: 13,
                      }}>
                        {v.content}
                      </div>
                    </div>
                  ),
                }))}
              />
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
