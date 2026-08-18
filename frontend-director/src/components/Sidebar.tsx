import { Menu } from 'antd'
import {
  PictureOutlined,
  VideoCameraOutlined,
  ScissorOutlined,
  ExportOutlined,
  SettingOutlined,
  AppstoreOutlined,
  FolderOutlined,
  ThunderboltOutlined,
  ApartmentOutlined,
  StarOutlined,
  MessageOutlined,
  RocketOutlined,
  FileTextOutlined,
  DesktopOutlined,
  BlockOutlined,
  FileImageOutlined,
} from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'

const menuItems = [
  { key: '/projects', icon: <FolderOutlined />, label: '项目' },
  { key: '/assets', icon: <AppstoreOutlined />, label: '资产库' },
  { key: '/storyboard', icon: <PictureOutlined />, label: '分镜画布' },
  { key: '/video', icon: <VideoCameraOutlined />, label: '视频生成' },
  { key: '/script', icon: <FileTextOutlined />, label: 'AI剧本' },
  { key: '/screen-record', icon: <DesktopOutlined />, label: '屏幕录制' },
  { key: '/compose', icon: <BlockOutlined />, label: '分屏合成' },
  { key: '/graphic', icon: <FileImageOutlined />, label: '图文生成' },
  { key: '/one-click-video', icon: <RocketOutlined />, label: '一键成片' },
  { key: '/batches', icon: <ThunderboltOutlined />, label: '批量任务' },
  { key: '/workflow-templates', icon: <ApartmentOutlined />, label: '工作流模板' },
  { key: '/presets', icon: <StarOutlined />, label: '任务预设' },
  { key: '/prompts', icon: <MessageOutlined />, label: '提示词中心' },
  { key: '/edit', icon: <ScissorOutlined />, label: '视频剪辑' },
  { key: '/export', icon: <ExportOutlined />, label: '成片导出' },
  { key: '/settings', icon: <SettingOutlined />, label: '供应商设置' },
]

export default function DirectorSidebar() {
  const navigate = useNavigate()
  const location = useLocation()

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px', textAlign: 'center', color: '#fff', fontSize: 16, fontWeight: 600 }}>
        导演工作台
      </div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[location.pathname]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
      />
    </div>
  )
}
