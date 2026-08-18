import { useEffect, useState, useCallback } from 'react'
import { Card, Table, Button, Modal, Form, Input, Tag, Space, Typography, message, Popconfirm, Statistic, Row, Col, Empty } from 'antd'
import { PlusOutlined, FolderOutlined, DeleteOutlined, EditOutlined, AppstoreOutlined } from '@ant-design/icons'
import { projectService, Project } from '../services/directorApi'
import { useProject } from '../contexts/ProjectContext'
import dayjs from 'dayjs'

const { Title, Text } = Typography
const { TextArea } = Input

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [statsMap, setStatsMap] = useState<Record<string, any>>({})
  const [form] = Form.useForm()
  const { setCurrentProject } = useProject()

  const loadProjects = useCallback(async () => {
    setLoading(true)
    try {
      const res = await projectService.list()
      setProjects(res.projects || [])
      // 加载每个项目的统计
      const stats: Record<string, any> = {}
      await Promise.all((res.projects || []).map(async (p: Project) => {
        try {
          const s = await projectService.getStats(p.project_id)
          stats[p.project_id] = s.stats
        } catch {
          stats[p.project_id] = { asset_count: 0 }
        }
      }))
      setStatsMap(stats)
    } catch (e: any) {
      message.error(e.message || '加载项目失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadProjects() }, [loadProjects])

  const handleCreate = () => {
    setEditingProject(null)
    form.resetFields()
    setModalOpen(true)
  }

  const handleEdit = (project: Project) => {
    setEditingProject(project)
    form.setFieldsValue({
      name: project.name,
      description: project.description,
      status: project.status,
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      if (editingProject) {
        await projectService.update(editingProject.project_id, values)
        message.success('项目已更新')
      } else {
        const res = await projectService.create(values)
        message.success(`项目已创建: ${res.project.name}`)
      }
      setModalOpen(false)
      loadProjects()
    } catch (e: any) {
      if (e.errorFields) return // 表单校验错误，不提示
      message.error(e.message || '操作失败')
    }
  }

  const handleDelete = async (projectId: string) => {
    try {
      await projectService.delete(projectId)
      message.success('项目已删除')
      loadProjects()
    } catch (e: any) {
      message.error(e.message || '删除失败')
    }
  }

  const handleSwitchTo = (project: Project) => {
    setCurrentProject(project)
    message.success(`已切换到项目: ${project.name}`)
  }

  const columns = [
    {
      title: '项目名称',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: Project) => (
        <Space>
          <FolderOutlined style={{ color: '#1677ff' }} />
          <a onClick={() => handleSwitchTo(record)}>{name}</a>
        </Space>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (desc: string) => desc || <Text type="secondary">-</Text>,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const map: Record<string, { color: string; text: string }> = {
          active: { color: 'blue', text: '进行中' },
          archived: { color: 'default', text: '已归档' },
          completed: { color: 'green', text: '已完成' },
        }
        const cfg = map[status] || { color: 'default', text: status }
        return <Tag color={cfg.color}>{cfg.text}</Tag>
      },
    },
    {
      title: '资产数',
      key: 'asset_count',
      width: 90,
      render: (_: any, record: Project) => statsMap[record.project_id]?.asset_count || 0,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 160,
      render: (t: number) => t ? dayjs.unix(t).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: any, record: Project) => (
        <Space size={4}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>编辑</Button>
          <Popconfirm
            title="确认删除该项目？"
            description="项目下的资产不会被删除，但会失去项目归属。"
            onConfirm={() => handleDelete(record.project_id)}
            okText="删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Title level={3} style={{ margin: 0 }}>
          <FolderOutlined style={{ marginRight: 8 }} />
          项目管理
        </Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
          新建项目
        </Button>
      </div>

      {/* 统计概览 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card size="small">
            <Statistic title="项目总数" value={projects.length} prefix={<FolderOutlined />} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="进行中" value={projects.filter(p => p.status === 'active').length} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic title="已完成" value={projects.filter(p => p.status === 'completed').length} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card size="small">
            <Statistic
              title="总资产数"
              value={Object.values(statsMap).reduce((sum: number, s: any) => sum + (s?.asset_count || 0), 0)}
              prefix={<AppstoreOutlined />}
            />
          </Card>
        </Col>
      </Row>

      <Card>
        {projects.length === 0 && !loading ? (
          <Empty
            description="暂无项目，点击右上角创建第一个项目"
            style={{ padding: 60 }}
          >
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新建项目</Button>
          </Empty>
        ) : (
          <Table
            columns={columns}
            dataSource={projects}
            rowKey="project_id"
            loading={loading}
            pagination={false}
            size="middle"
          />
        )}
      </Card>

      <Modal
        title={editingProject ? '编辑项目' : '新建项目'}
        open={modalOpen}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
        okText={editingProject ? '保存' : '创建'}
        cancelText="取消"
      >
        <Form form={form} layout="vertical" initialValues={{ status: 'active' }}>
          <Form.Item
            name="name"
            label="项目名称"
            rules={[{ required: true, message: '请输入项目名称' }]}
          >
            <Input placeholder="如：江南烟雨短片" maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label="项目描述">
            <TextArea rows={3} placeholder="项目简介、目标、备注等" maxLength={500} />
          </Form.Item>
          {editingProject && (
            <Form.Item name="status" label="状态">
              <Input placeholder="active / archived / completed" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  )
}
