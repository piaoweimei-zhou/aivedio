import { Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from 'antd'
import DirectorSidebar from './components/Sidebar'
import { ProjectProvider } from './contexts/ProjectContext'
import ProjectsPage from './pages/ProjectsPage'
import AssetsPage from './pages/AssetsPage'
import StoryboardPage from './pages/StoryboardPage'
import VideoPage from './pages/VideoPage'
import EditPage from './pages/EditPage'
import ExportPage from './pages/ExportPage'
import SettingsPage from './pages/SettingsPage'
import BatchesPage from './pages/BatchesPage'
import OneClickVideoPage from './pages/OneClickVideoPage'
import WorkflowTemplatesPage from './pages/WorkflowTemplatesPage'
import PresetsPage from './pages/PresetsPage'
import PromptsPage from './pages/PromptsPage'
import ScriptPage from './pages/ScriptPage'
import ScreenRecordPage from './pages/ScreenRecordPage'
import ComposePage from './pages/ComposePage'
import GraphicPage from './pages/GraphicPage'

const { Content, Sider } = Layout

export default function App() {
  return (
    <ProjectProvider>
      <Layout style={{ height: '100vh' }}>
        <Sider width={200} theme="dark">
          <DirectorSidebar />
        </Sider>
        <Content style={{ overflow: 'auto' }}>
          <Routes>
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<ProjectsPage />} />
            <Route path="/assets" element={<AssetsPage />} />
            <Route path="/storyboard" element={<StoryboardPage />} />
            <Route path="/video" element={<VideoPage />} />
            <Route path="/batches" element={<BatchesPage />} />
            <Route path="/one-click-video" element={<OneClickVideoPage />} />
            <Route path="/workflow-templates" element={<WorkflowTemplatesPage />} />
            <Route path="/presets" element={<PresetsPage />} />
            <Route path="/prompts" element={<PromptsPage />} />
            <Route path="/script" element={<ScriptPage />} />
            <Route path="/screen-record" element={<ScreenRecordPage />} />
            <Route path="/compose" element={<ComposePage />} />
            <Route path="/graphic" element={<GraphicPage />} />
            <Route path="/edit" element={<EditPage />} />
            <Route path="/export" element={<ExportPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Content>
      </Layout>
    </ProjectProvider>
  )
}
