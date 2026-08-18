import { Select, Tag, Typography, Space, Button } from 'antd'
import { FolderOutlined, GlobalOutlined, PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useProject } from '../contexts/ProjectContext'

const { Text } = Typography

/**
 * 全局项目选择器
 * 显示在页面顶部，切换当前项目后所有页面自动按项目过滤资产
 */
export default function ProjectSelector() {
  const { currentProject, projects, setCurrentProject, loading } = useProject()
  const navigate = useNavigate()

  return (
    <Space size={8}>
      <FolderOutlined style={{ color: '#1677ff' }} />
      <Text type="secondary" style={{ fontSize: 13 }}>项目:</Text>
      <Select
        loading={loading}
        value={currentProject?.project_id || ''}
        onChange={(value) => {
          if (value === '') {
            setCurrentProject(null)
          } else {
            const p = projects.find(p => p.project_id === value)
            setCurrentProject(p || null)
          }
        }}
        style={{ width: 200 }}
        placeholder="全部资产（未分类）"
        optionLabelProp="label"
        options={[
          { value: '', label: <Space><GlobalOutlined /><span>全部</span></Space> },
          ...projects.map(p => ({
            value: p.project_id,
            label: <Space><span>{p.name}</span><Tag color={p.status === 'active' ? 'blue' : p.status === 'archived' ? 'default' : 'green'} style={{ fontSize: 10 }}>{p.status === 'active' ? '进行中' : p.status === 'archived' ? '已归档' : '已完成'}</Tag></Space>,
          })),
        ]}
      />
      <Button
        type="text"
        size="small"
        icon={<PlusOutlined />}
        onClick={() => navigate('/projects')}
      />
    </Space>
  )
}
