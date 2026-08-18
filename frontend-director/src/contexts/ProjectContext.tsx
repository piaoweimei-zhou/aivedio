import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from 'react'
import { projectService, Project } from '../services/directorApi'

interface ProjectContextValue {
  /** 当前选中的项目（null = 全部/未分类） */
  currentProject: Project | null
  /** 所有项目列表 */
  projects: Project[]
  /** 加载状态 */
  loading: boolean
  /** 切换当前项目 */
  setCurrentProject: (project: Project | null) => void
  /** 刷新项目列表 */
  refreshProjects: () => Promise<void>
  /** 当前项目 ID（便捷访问，用于 API 过滤参数） */
  currentProjectId: string | null
}

const ProjectContext = createContext<ProjectContextValue | undefined>(undefined)

const STORAGE_KEY = 'director.currentProjectId'

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([])
  const [currentProject, setCurrentProjectState] = useState<Project | null>(null)
  const [loading, setLoading] = useState(false)

  const refreshProjects = useCallback(async () => {
    setLoading(true)
    try {
      const res = await projectService.list()
      setProjects(res.projects || [])
    } catch {
      setProjects([])
    } finally {
      setLoading(false)
    }
  }, [])

  // 初始化：加载项目列表 + 恢复上次选中的项目
  useEffect(() => {
    refreshProjects().then(() => {
      const savedId = localStorage.getItem(STORAGE_KEY)
      if (savedId) {
        projectService.get(savedId).then((res: any) => {
          if (res.project) setCurrentProjectState(res.project)
        }).catch(() => {
          // 保存的项目已删除，清除
          localStorage.removeItem(STORAGE_KEY)
        })
      }
    })
  }, [refreshProjects])

  const setCurrentProject = useCallback((project: Project | null) => {
    setCurrentProjectState(project)
    if (project) {
      localStorage.setItem(STORAGE_KEY, project.project_id)
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [])

  const value: ProjectContextValue = {
    currentProject,
    projects,
    loading,
    setCurrentProject,
    refreshProjects,
    currentProjectId: currentProject?.project_id || null,
  }

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
}

export function useProject() {
  const ctx = useContext(ProjectContext)
  if (!ctx) {
    throw new Error('useProject 必须在 ProjectProvider 内使用')
  }
  return ctx
}
