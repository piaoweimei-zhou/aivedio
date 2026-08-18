import { useEffect, useState, useCallback, useMemo } from 'react'
import {
  Typography, Card, Row, Col, Select, Button, Space, Image, Empty,
  Input, Tag, Tabs, Modal, InputNumber, message, Tooltip, Dropdown, Drawer, Descriptions, Timeline, Divider, Radio, Segmented, Spin
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, StarOutlined, ExpandOutlined,
  ScissorOutlined, ThunderboltOutlined, EyeOutlined,
  PictureOutlined, VideoCameraOutlined, AppstoreOutlined,
  InfoCircleOutlined, BranchesOutlined, ClockCircleOutlined,
  GlobalOutlined, CopyOutlined, DatabaseOutlined, ClearOutlined, EditOutlined,
  DownloadOutlined
} from '@ant-design/icons'
import { useDirectorStore } from '../stores/directorStore'
import directorApi from '../services/directorApi'
import IframeEmbed from '../components/IframeEmbed'
import PoseEditor from '../components/PoseEditor'
import { downloadImage } from '../utils/download'
import ProjectSelector from '../components/ProjectSelector'
import { useProject } from '../contexts/ProjectContext'

const { Title, Text } = Typography

const ASSET_TYPE_TABS = [
  { key: 'all', label: '全部' },
  { key: 'concept', label: '概念图' },
  { key: 'edit', label: '精修' },
  { key: 'multi_view', label: '三视图' },
  { key: 'pano', label: '360全景' },
  { key: 'storyboard', label: '分镜' },
  { key: 'storyboard_multi', label: '多人分镜' },
  { key: 'storyboard_layered', label: '分层渲染' },
  { key: 'storyboard_batch', label: '批量分镜' },
  { key: 'csv', label: 'CSV脚本' },
  { key: 'video', label: '视频' },
  { key: 'pose', label: '姿态' },
  { key: 'lineart', label: '线稿' },
  { key: 'depth', label: '深度图' },
  { key: 'template_production', label: '模板制作' },
  { key: 'mask', label: '蒙版' },
]

const CONTENT_TYPE_OPTIONS = [
  { key: '', label: '全部内容' },
  { key: 'character', label: '角色' },
  { key: 'scene', label: '场景' },
  { key: 'prop', label: '道具' },
]

const TYPE_ICON: Record<string, React.ReactNode> = {
  concept: <PictureOutlined />,
  character: <StarOutlined />,
  scene: <AppstoreOutlined />,
  storyboard: <EyeOutlined />,
  storyboard_multi: <BranchesOutlined />,
  storyboard_layered: <CopyOutlined />,
  storyboard_batch: <DatabaseOutlined />,
  template_production: <AppstoreOutlined />,
  mask: <ClearOutlined />,
  depth_clean: <ExpandOutlined />,
  csv: <DatabaseOutlined />,
  video: <VideoCameraOutlined />,
  edit: <ThunderboltOutlined />,
  pose: <ScissorOutlined />,
  lineart: <ScissorOutlined />,
  depth: <ExpandOutlined />,
}

const TYPE_COLOR: Record<string, string> = {
  concept: 'blue',
  character: 'purple',
  scene: 'green',
  storyboard: 'orange',
  storyboard_multi: 'lime',
  storyboard_layered: 'gold',
  storyboard_batch: 'purple',
  template_production: 'orange',
  mask: 'geekblue',
  depth_clean: 'cyan',
  csv: 'cyan',
  video: 'red',
  edit: 'cyan',
  pose: 'magenta',
  lineart: 'geekblue',
  depth: 'volcano',
}

const CONTENT_COLOR: Record<string, string> = {
  character: 'purple',
  scene: 'green',
  prop: 'gold',
}

const SIZE_OPTIONS = [
  { key: '', label: '自动（按内容类型）' },
  { key: '1536x864', label: '1536×864（宽屏·场景/道具）' },
  { key: '1920x1080', label: '1920×1080（16:9 横屏·场景/道具）' },
  { key: '1024x1536', label: '1024×1536（竖版·角色）' },
  { key: '1080x1920', label: '1080×1920（手机竖屏）' },
]

export default function AssetsPage() {
  const {
    assets, assetsLoading, loadAssets, createAsset, deleteAsset,
    selectedAssetIds, toggleAssetSelection, clearSelection,
    stages, loadStages, executeStage,
  } = useDirectorStore()
  const { currentProjectId } = useProject()

  const [activeTab, setActiveTab] = useState('all')
  const [contentTab, setContentTab] = useState('')
  const [searchText, setSearchText] = useState('')
  const [refineModalOpen, setRefineModalOpen] = useState(false)
  const [refineMode, setRefineMode] = useState<'refine' | 'upscale'>('refine')
  const [refinePrompt, setRefinePrompt] = useState('')
  const [refineFactor, setRefineFactor] = useState(2)
  const [refineLoading, setRefineLoading] = useState(false)

  // 资产库图片加载失败追踪（用于显示"重新生成"按钮）
  const [failedImages, setFailedImages] = useState<Set<string>>(new Set())
  const markImageFailed = useCallback((url: string) => {
    setFailedImages(prev => { const next = new Set(prev); next.add(url); return next })
  }, [])

  // 全景图弹窗
  const [panoModalOpen, setPanoModalOpen] = useState(false)
  const [panoAssetId, setPanoAssetId] = useState('')
  const [panoPrompt, setPanoPrompt] = useState('')
  const [panoLoading, setPanoLoading] = useState(false)
  const [panoElapsed, setPanoElapsed] = useState(0)

  // 多人分镜弹窗（三元约束：蒙版+深度图+OpenPose）
  const [multiPersonModalOpen, setMultiPersonModalOpen] = useState(false)
  const [multiPersonCharA, setMultiPersonCharA] = useState('')
  const [multiPersonCharB, setMultiPersonCharB] = useState('')
  const [multiPersonMask, setMultiPersonMask] = useState('')
  const [multiPersonDepth, setMultiPersonDepth] = useState('')
  const [multiPersonPose, setMultiPersonPose] = useState('')
  const [multiPersonTemplate, setMultiPersonTemplate] = useState('T01_双人正面对话')
  const [multiPersonPrompt, setMultiPersonPrompt] = useState('')
  const [multiPersonLoading, setMultiPersonLoading] = useState(false)
  const [multiPersonElapsed, setMultiPersonElapsed] = useState(0)

  // 分层渲染弹窗
  const [layeredModalOpen, setLayeredModalOpen] = useState(false)
  const [layeredCharA, setLayeredCharA] = useState('')
  const [layeredCharB, setLayeredCharB] = useState('')
  const [layeredCharC, setLayeredCharC] = useState('')
  const [layeredCharD, setLayeredCharD] = useState('')
  const [layeredMask, setLayeredMask] = useState('')
  const [layeredDepth, setLayeredDepth] = useState('')
  const [layeredTemplate, setLayeredTemplate] = useState('T09_四人围坐')
  const [layeredPromptA, setLayeredPromptA] = useState('')
  const [layeredPromptB, setLayeredPromptB] = useState('')
  const [layeredLoading, setLayeredLoading] = useState(false)
  const [layeredElapsed, setLayeredElapsed] = useState(0)

  // CSV批量生成弹窗
  const [batchModalOpen, setBatchModalOpen] = useState(false)
  const [batchCsvData, setBatchCsvData] = useState('')
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchElapsed, setBatchElapsed] = useState(0)

  // 模板批量提取弹窗
  const [templateExtractModalOpen, setTemplateExtractModalOpen] = useState(false)
  const [templateExtractAssetId, setTemplateExtractAssetId] = useState('')
  const [templateExtractId, setTemplateExtractId] = useState('T01_双人正面对话')
  const [templateExtractName, setTemplateExtractName] = useState('T01 双人正面对话')
  const [templateExtractDesc, setTemplateExtractDesc] = useState('')
  const [templateExtractScene, setTemplateExtractScene] = useState('')
  const [templateExtractPersonCount, setTemplateExtractPersonCount] = useState(2)
  const [templateExtractLoading, setTemplateExtractLoading] = useState(false)
  const [templateExtractElapsed, setTemplateExtractElapsed] = useState(0)

  // 模板清场+蒙版弹窗
  const [templateCleanModalOpen, setTemplateCleanModalOpen] = useState(false)
  const [templateCleanAssetId, setTemplateCleanAssetId] = useState('')
  const [templateCleanDepthAssetId, setTemplateCleanDepthAssetId] = useState('')
  const [templateCleanId, setTemplateCleanId] = useState('T01_双人正面对话')
  const [templateCleanName, setTemplateCleanName] = useState('')
  const [templateCleanLoading, setTemplateCleanLoading] = useState(false)
  const [templateCleanElapsed, setTemplateCleanElapsed] = useState(0)

  // 模板Pose优化弹窗
  const [templatePoseModalOpen, setTemplatePoseModalOpen] = useState(false)
  const [templatePoseAssetId, setTemplatePoseAssetId] = useState('')
  const [templatePoseId, setTemplatePoseId] = useState('T01_双人正面对话')
  const [templatePoseName, setTemplatePoseName] = useState('')
  const [templatePoseLoading, setTemplatePoseLoading] = useState(false)
  const [templatePoseElapsed, setTemplatePoseElapsed] = useState(0)
  const [templatePoseResultUrl, setTemplatePoseResultUrl] = useState('')
  const [templatePoseEditorOpen, setTemplatePoseEditorOpen] = useState(false)

  // 新建资产
  const [createModalOpen, setCreateModalOpen] = useState(false)
  const [createMode, setCreateMode] = useState<'upload' | 'generate'>('upload')
  const [newAssetType, setNewAssetType] = useState('concept')
  const [newContentType, setNewContentType] = useState('')
  const [newAssetName, setNewAssetName] = useState('')
  const [newSize, setNewSize] = useState('')
  const [editSize, setEditSize] = useState('')
  const [editPrompt, setEditPrompt] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string>('')
  const [createLoading, setCreateLoading] = useState(false)
  const [generatePrompt, setGeneratePrompt] = useState('')
  const [generateLoading, setGenerateLoading] = useState(false)

  // 详情侧面板
  const [detailAsset, setDetailAsset] = useState<any>(null)
  const [detailOpen, setDetailOpen] = useState(false)
  const [angleIframeOpen, setAngleIframeOpen] = useState(false)
  const [lineage, setLineage] = useState<any[]>([])

  // 提取操作（三视图/姿态/线稿/深度图）
  const [extractionModalOpen, setExtractionModalOpen] = useState(false)
  const [extractionLoading, setExtractionLoading] = useState(false)
  const [extractionElapsed, setExtractionElapsed] = useState(0)
  const [extractionLabel, setExtractionLabel] = useState('')
  const [extractionResult, setExtractionResult] = useState<any>(null)
  const [children, setChildren] = useState<any[]>([])

  useEffect(() => {
    loadAssets(currentProjectId ? { project_id: currentProjectId } : undefined)
    loadStages()
  }, [currentProjectId])

  // 过滤资产
  const filteredAssets = useMemo(() => assets.filter(a => {
    if (activeTab !== 'all') {
      // "模板制作" tab 按 content_type 过滤，其余按 asset_type 过滤
      if (activeTab === 'template_production') {
        if (a.content_type !== 'template_production') return false
      } else {
        if (a.asset_type !== activeTab) return false
      }
    }
    if (contentTab && a.content_type !== contentTab) return false
    if (searchText && !a.name.toLowerCase().includes(searchText.toLowerCase())) return false
    return true
  }), [assets, activeTab, contentTab, searchText])

  // 查看详情
  const handleShowDetail = useCallback(async (asset: any) => {
    setDetailAsset(asset)
    setDetailOpen(true)
    setEditSize(asset.metadata?.size || '')
    setEditPrompt(asset.metadata?.prompt || '')
    try {
      const [linRes, childRes] = await Promise.all([
        directorApi.assetLineage(asset.asset_id),
        directorApi.assetChildren(asset.asset_id),
      ])
      setLineage(linRes.lineage || [])
      setChildren(childRes.children || [])
    } catch {
      setLineage([])
      setChildren([])
    }
  }, [])

  // 精修/超分
  const handleRefine = useCallback(async () => {
    if (selectedAssetIds.length === 0) {
      message.warning('请先选择资产')
      return
    }
    setRefineLoading(true)
    try {
      // 从选中资产中读取 content_type
      const srcAsset = assets.find(a => a.asset_id === selectedAssetIds[0])
      const result = await executeStage({
        stage_id: 'refine',
        input_asset_ids: selectedAssetIds,
        provider_id: 'comfyui',
        params: {
          mode: refineMode,
          prompt: refinePrompt || undefined,
          upscale_factor: refineFactor,
          content_type: srcAsset?.content_type || '',
        },
      })
      if (result?.success) {
        message.success(refineMode === 'refine' ? '精修完成' : '超分完成')
        loadAssets()
        setRefineModalOpen(false)
      } else {
        message.error(result?.error || '操作失败')
      }
    } catch (e: any) {
      message.error(e.message || '操作失败')
    } finally {
      setRefineLoading(false)
    }
  }, [selectedAssetIds, refineMode, refinePrompt, refineFactor, executeStage, loadAssets, assets])

  // 姿态/线稿/深度图提取
  const handleExtraction = useCallback(async (stageId: string, label: string, assetId?: string) => {
    const ids = assetId ? [assetId] : selectedAssetIds
    if (ids.length === 0) {
      message.warning('请先选择资产')
      return
    }
    setExtractionLabel(label)
    setExtractionResult(null)
    setExtractionElapsed(0)
    setExtractionLoading(true)
    setExtractionModalOpen(true)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setExtractionElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: stageId,
        input_asset_ids: ids,
        provider_id: 'comfyui',
      })
      clearInterval(timer)
      setExtractionElapsed(Math.floor((Date.now() - startTime) / 1000))
      if (result?.success) {
        setExtractionLoading(false)
        setExtractionModalOpen(false)
        message.success(`${label}完成`)
        // 切换到对应标签页
        const tabMap: Record<string, string> = {
          angle: 'multi_view',
          pose_extraction: 'pose',
          lineart_extraction: 'lineart',
          depth_map: 'depth',
          extract_all: 'all',
        }
        setActiveTab(tabMap[stageId] || 'all')
        clearSelection()
        if (result.asset?.asset_id) {
          toggleAssetSelection(result.asset.asset_id)
        }
        await loadAssets()
      } else {
        setExtractionLoading(false)
        message.error(result?.error || `${label}失败`)
      }
    } catch (e: any) {
      clearInterval(timer)
      setExtractionLoading(false)
      message.error(e.message || `${label}失败`)
    }
  }, [selectedAssetIds, executeStage, loadAssets, assets])

  // 资产图片重新生成：根据 asset_type 推断 stage_id 重新执行
  const handleRegenerateAsset = useCallback(async (asset: any) => {
    const meta = asset.metadata || {}
    const prompt = meta.prompt || ''
    // 根据资产类型映射到重新生成用的 stage_id 和参数
    const stageMap: Record<string, string> = {
      concept: 'concept',
      edit: 'refine',
      storyboard: 'refine',
      character: 'refine',
      pose: 'pose_extraction',
      lineart: 'lineart_extraction',
      depth: 'depth_map',
      pano: 'pano',
      multi_view: 'angle',
    }
    const stageId = stageMap[asset.asset_type] || 'refine'
    // 文生图阶段（concept）不需要输入资产，只需要 prompt
    const noInputStages = ['concept']
    const inputIds = noInputStages.includes(stageId) ? [] : (asset.parent_id ? [asset.parent_id] : [])
    const hide = message.loading(`正在重新生成 ${asset.name}...`, 0)
    try {
      const params: Record<string, any> = {
        content_type: asset.content_type || '',
        name: asset.name || '',
      }
      // 根据阶段类型设置不同参数
      if (stageId === 'concept') {
        params.prompt = prompt
        if (meta.size) params.size = meta.size
      } else if (stageId === 'refine') {
        params.prompt = prompt
        params.mode = 'refine'
        params.upscale_factor = 1
      } else if (stageId === 'pano') {
        params.prompt = prompt || '360 degree panoramic view'
      }
      const result = await executeStage({
        stage_id: stageId,
        input_asset_ids: inputIds,
        provider_id: 'comfyui',
        params,
      })
      hide()
      if (result?.success) {
        message.success(`${asset.name} 重新生成完成`)
        await loadAssets()
      } else {
        message.error(result?.error || '重新生成失败')
      }
    } catch (e: any) {
      hide()
      message.error(e.message || '重新生成失败')
    }
  }, [executeStage, loadAssets])

  // 全景图生成（弹窗确认）
  const handlePanoConfirm = useCallback(async () => {
    if (!panoAssetId) return
    setPanoLoading(true)
    setPanoElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setPanoElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'pano',
        input_asset_ids: [panoAssetId],
        provider_id: 'comfyui',
        params: { prompt: panoPrompt || '360 degree panoramic view' },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`全景图生成完成，耗时 ${elapsed}s`)
        setPanoModalOpen(false)
        setPanoElapsed(0)
        loadAssets()
      } else {
        message.error(result?.error || `全景图生成失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `全景图生成失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setPanoLoading(false)
      setPanoElapsed(0)
    }
  }, [panoAssetId, panoPrompt, executeStage, loadAssets])

  // 多人分镜生成（弹窗确认 — 三元约束）
  const handleMultiPersonConfirm = useCallback(async () => {
    if (!multiPersonCharA || !multiPersonCharB) {
      message.warning('请选择人物A和人物B')
      return
    }
    setMultiPersonLoading(true)
    setMultiPersonElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setMultiPersonElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'multi_person',
        input_asset_ids: [multiPersonCharA, multiPersonCharB, multiPersonMask, multiPersonDepth, multiPersonPose].filter(Boolean),
        provider_id: 'comfyui',
        params: {
          prompt: multiPersonPrompt || 'Two characters in a scene, natural interaction, cinematic lighting',
          template_name: multiPersonTemplate,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`多人分镜生成完成，耗时 ${elapsed}s`)
        setMultiPersonModalOpen(false)
        setMultiPersonElapsed(0)
        loadAssets()
      } else {
        message.error(result?.error || `多人分镜生成失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `多人分镜生成失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setMultiPersonLoading(false)
      setMultiPersonElapsed(0)
    }
  }, [multiPersonCharA, multiPersonCharB, multiPersonMask, multiPersonDepth, multiPersonPose, multiPersonPrompt, multiPersonTemplate, executeStage, loadAssets])

  // 分层渲染生成（弹窗确认）
  const handleLayeredConfirm = useCallback(async () => {
    if (!layeredCharA || !layeredCharB) {
      message.warning('请至少选择A组人物1和人物2')
      return
    }
    setLayeredLoading(true)
    setLayeredElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setLayeredElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const inputIds = [layeredCharA, layeredCharB, layeredCharC, layeredCharD, layeredMask, layeredDepth].filter(Boolean)
      const result = await executeStage({
        stage_id: 'layered_render',
        input_asset_ids: inputIds,
        provider_id: 'comfyui',
        params: {
          prompt_a: layeredPromptA || 'Group A characters in scene, cinematic lighting',
          prompt_b: layeredPromptB || 'Group B characters in scene, cinematic lighting',
          template_name: layeredTemplate,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`分层渲染完成，耗时 ${elapsed}s`)
        setLayeredModalOpen(false)
        setLayeredElapsed(0)
        loadAssets()
      } else {
        message.error(result?.error || `分层渲染失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `分层渲染失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setLayeredLoading(false)
      setLayeredElapsed(0)
    }
  }, [layeredCharA, layeredCharB, layeredCharC, layeredCharD, layeredMask, layeredDepth, layeredPromptA, layeredPromptB, layeredTemplate, executeStage, loadAssets])

  // CSV批量生成（弹窗确认）
  const handleBatchConfirm = useCallback(async () => {
    if (!batchCsvData.trim()) {
      message.warning('请输入CSV分镜脚本')
      return
    }
    setBatchLoading(true)
    setBatchElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setBatchElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'batch_storyboard',
        input_asset_ids: [],
        provider_id: 'comfyui',
        params: {
          csv_data: batchCsvData,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`批量分镜生成完成，耗时 ${elapsed}s`)
        setBatchModalOpen(false)
        setBatchElapsed(0)
        loadAssets()
      } else {
        message.error(result?.error || `批量分镜生成失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `批量分镜生成失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setBatchLoading(false)
      setBatchElapsed(0)
    }
  }, [batchCsvData, executeStage, loadAssets])

  // 模板批量提取（弹窗确认）
  const handleTemplateExtractConfirm = useCallback(async () => {
    if (!templateExtractAssetId) {
      message.warning('请先右键选择一张参考构图图')
      return
    }
    if (!templateExtractId) {
      message.warning('请选择模板编号')
      return
    }
    setTemplateExtractLoading(true)
    setTemplateExtractElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setTemplateExtractElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'template_batch_extract',
        input_asset_ids: [templateExtractAssetId],
        provider_id: 'comfyui',
        params: {
          template_id: templateExtractId,
          template_name: templateExtractName,
          description: templateExtractDesc,
          scene: templateExtractScene,
          person_count: templateExtractPersonCount,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`模板三图提取完成，耗时 ${elapsed}s`)
        setTemplateExtractModalOpen(false)
        setTemplateExtractElapsed(0)
        setActiveTab('all')
        loadAssets()
      } else {
        message.error(result?.error || `模板提取失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `模板提取失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setTemplateExtractLoading(false)
      setTemplateExtractElapsed(0)
    }
  }, [templateExtractAssetId, templateExtractId, templateExtractName, templateExtractDesc, templateExtractScene, templateExtractPersonCount, executeStage, loadAssets])

  // 模板清场+蒙版（弹窗确认）
  const handleTemplateCleanConfirm = useCallback(async () => {
    if (!templateCleanAssetId) {
      message.warning('请先右键选择一张参考构图图')
      return
    }
    setTemplateCleanLoading(true)
    setTemplateCleanElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setTemplateCleanElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const inputIds = [templateCleanAssetId]
      if (templateCleanDepthAssetId) {
        inputIds.push(templateCleanDepthAssetId)
      }
      const result = await executeStage({
        stage_id: 'template_clean',
        input_asset_ids: inputIds,
        provider_id: 'comfyui',
        params: {
          template_id: templateCleanId,
          template_name: templateCleanName,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`模板清场+蒙版生成完成，耗时 ${elapsed}s`)
        setTemplateCleanModalOpen(false)
        setTemplateCleanElapsed(0)
        setActiveTab('all')
        loadAssets()
      } else {
        message.error(result?.error || `模板清场失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `模板清场失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setTemplateCleanLoading(false)
      setTemplateCleanElapsed(0)
    }
  }, [templateCleanAssetId, templateCleanDepthAssetId, templateCleanId, templateCleanName, executeStage, loadAssets])

  // 模板Pose优化（弹窗确认）
  const handleTemplatePoseConfirm = useCallback(async () => {
    if (!templatePoseAssetId) {
      message.warning('请先右键选择一张Pose骨架图')
      return
    }
    setTemplatePoseLoading(true)
    setTemplatePoseElapsed(0)
    const startTime = Date.now()
    const timer = setInterval(() => {
      setTemplatePoseElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'template_pose',
        input_asset_ids: [templatePoseAssetId],
        provider_id: 'comfyui',
        params: {
          template_id: templatePoseId,
          template_name: templatePoseName,
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      if (result?.success) {
        message.success(`Pose简化完成，耗时 ${elapsed}s`)
        // 保存简化后的Pose图URL，用于PoseEditor交互修正
        const resultUrl = result?.asset?.urls?.[0] || result?.images?.[0] || ''
        if (resultUrl) {
          setTemplatePoseResultUrl(resultUrl)
          setTimeout(() => setTemplatePoseEditorOpen(true), 0)
        }
        setTemplatePoseModalOpen(false)
        setTemplatePoseElapsed(0)
        setActiveTab('pose')
        loadAssets()
      } else {
        message.error(result?.error || `Pose简化失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      message.error(e.message || `Pose简化失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setTemplatePoseLoading(false)
      setTemplatePoseElapsed(0)
    }
  }, [templatePoseAssetId, templatePoseId, templatePoseName, executeStage, loadAssets])

  // PoseEditor 保存修正后的Pose图
  const handlePoseEditorSave = useCallback(async (dataUrl: string) => {
    try {
      // 幂等性：先删除该模板的旧修正资产，避免重复保存创建多个修正资产
      try {
        const existingAssets = await directorApi.listAssets()
        const oldCorrected = existingAssets?.filter(
          (a: any) => a.metadata?.template_id === templatePoseId
            && a.metadata?.extraction_type === 'template_pose_corrected'
        )
        if (oldCorrected?.length) {
          for (const a of oldCorrected) {
            await directorApi.deleteAsset(a.asset_id)
          }
        }
      } catch {
        // 旧资产删除失败不阻塞流程
      }

      // 将 dataURL 转换为 File 对象
      const res = await fetch(dataUrl)
      const blob = await res.blob()
      const file = new File([blob], `${templatePoseId}_pose_corrected.png`, { type: 'image/png' })
      // 上传到后端
      const uploadResult = await directorApi.uploadAssetFile(file)
      if (uploadResult?.url) {
        // 创建资产记录
        await directorApi.createAsset({
          asset_type: 'pose',
          content_type: 'template_production',
          name: `${templatePoseName || templatePoseId} 修正Pose`,
          urls: [uploadResult.url],
          metadata: {
            template_id: templatePoseId,
            extraction_type: 'template_pose_corrected',
            corrected: true,
          },
          parent_id: templatePoseAssetId,
        })
        // 更新 manifest 中的 pose_simplified 指向修正后的图
        try {
          await directorApi.updateTemplateManifest(templatePoseId, {
            files: { pose_simplified: `${templatePoseId}_pose_corrected.png` },
            pose_corrected: true,
          })
        } catch (manifestErr: any) {
          console.warn('更新manifest失败，修正图已保存但manifest未更新', manifestErr)
        }
      }
      message.success('Pose修正图已上传保存')
      setTemplatePoseEditorOpen(false)
      setTemplatePoseResultUrl('')
      loadAssets()
    } catch (e: any) {
      message.error(e.message || 'Pose修正图上传失败')
    }
  }, [templatePoseId, templatePoseName, loadAssets])

  // 新建资产 - 打开弹窗时根据当前Tab预选类型
  const openCreateModal = useCallback(() => {
    const tabType = activeTab !== 'all' ? activeTab : 'concept'
    setNewAssetType(tabType)
    setNewContentType('')
    setNewAssetName('')
    setNewSize('')
    setUploadFile(null)
    setPreviewUrl('')
    setCreateMode('upload')
    setGeneratePrompt('')
    setCreateModalOpen(true)
  }, [activeTab])

  // 选择文件
  const handleFileSelect = useCallback((file: File) => {
    setUploadFile(file)
    // 生成预览 URL
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    // 智能预填名称：去掉扩展名
    const nameWithoutExt = file.name.replace(/\.[^.]+$/, '')
    setNewAssetName(nameWithoutExt)
    // 根据文件类型自动预选资产类型
    const ext = file.name.split('.').pop()?.toLowerCase() || ''
    if (['mp4', 'webm', 'mov'].includes(ext) && newAssetType !== 'video') {
      setNewAssetType('video')
    }
  }, [newAssetType])

  // 创建资产（上传文件 → 调用 createAsset）
  const handleCreateAsset = useCallback(async () => {
    if (!uploadFile) {
      message.warning('请先选择文件')
      return
    }
    if (!newAssetName.trim()) {
      message.warning('请输入资产名称')
      return
    }
    setCreateLoading(true)
    try {
      // 1. 上传文件
      const uploadRes = await directorApi.uploadAssetFile(uploadFile)
      if (!uploadRes?.success || !uploadRes?.url) {
        message.error(uploadRes?.detail || '文件上传失败')
        return
      }
      // 2. 创建资产
      const result = await createAsset({
        asset_type: newAssetType,
        content_type: newContentType,
        name: newAssetName.trim(),
        urls: [uploadRes.url],
        project_id: currentProjectId || undefined,
      })
      if (result) {
        message.success('资产创建成功')
        setCreateModalOpen(false)
        if (previewUrl) URL.revokeObjectURL(previewUrl)
        setUploadFile(null)
        setPreviewUrl('')
        setNewAssetName('')
        loadAssets()
      } else {
        message.error('创建失败')
      }
    } catch (e: any) {
      message.error(e.message || '创建失败')
    } finally {
      setCreateLoading(false)
    }
  }, [uploadFile, newAssetType, newAssetName, previewUrl, createAsset, loadAssets])

  // AI 生成资产
  const [generateElapsed, setGenerateElapsed] = useState(0)
  const handleGenerateSubmit = useCallback(async () => {
    if (!generatePrompt.trim()) {
      message.warning('请输入画面描述')
      return
    }
    if (!newAssetName.trim()) {
      message.warning('请输入资产名称')
      return
    }
    setGenerateLoading(true)
    setGenerateElapsed(0)
    const startTime = Date.now()
    // 每秒更新耗时显示
    const timer = setInterval(() => {
      setGenerateElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    try {
      const result = await executeStage({
        stage_id: 'concept',
        input_asset_ids: [],
        provider_id: 'comfyui',
        params: {
          prompt: generatePrompt.trim(),
          content_type: newContentType,
          name: newAssetName.trim(),
          ...(newSize ? { size: newSize } : {}),
        },
      })
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      console.log('[AssetsPage] AI 生成结果:', result)
      if (result?.success) {
        message.success(`AI 生成完成，耗时 ${elapsed}s，资产已创建`)
        setCreateModalOpen(false)
        setGeneratePrompt('')
        setNewAssetName('')
        loadAssets()
      } else {
        message.error(result?.error || `AI 生成失败（耗时 ${elapsed}s）`)
      }
    } catch (e: any) {
      clearInterval(timer)
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      console.error('[AssetsPage] AI 生成异常:', e)
      message.error(e.message || `AI 生成失败（耗时 ${elapsed}s）`)
    } finally {
      clearInterval(timer)
      setGenerateLoading(false)
      setGenerateElapsed(0)
    }
  }, [generatePrompt, newAssetType, newAssetName, executeStage, loadAssets])

  // 右键菜单操作
  const getActionMenu = (asset: any) => ({
    items: [
      {
        key: 'refine',
        icon: <ThunderboltOutlined />,
        label: '精修',
        onClick: () => { clearSelection(); toggleAssetSelection(asset.asset_id); setRefineMode('refine'); setRefineModalOpen(true) },
      },
      {
        key: 'upscale',
        icon: <ExpandOutlined />,
        label: '超分放大',
        onClick: () => { clearSelection(); toggleAssetSelection(asset.asset_id); setRefineMode('upscale'); setRefineModalOpen(true) },
      },
      {
        key: 'angle',
        icon: <EyeOutlined />,
        label: '生成三视图',
        onClick: () => { handleExtraction('angle', '三视图', asset.asset_id) },
      },
      { type: 'divider' as const },
      {
        key: 'extract_all',
        icon: <AppstoreOutlined />,
        label: '提取三图（线稿+深度+姿态）',
        onClick: () => { handleExtraction('extract_all', '三合一提取', asset.asset_id) },
      },
      { type: 'divider' as const },
      {
        key: 'pose',
        icon: <ScissorOutlined />,
        label: '提取姿态',
        onClick: () => { handleExtraction('pose_extraction', '姿态提取', asset.asset_id) },
      },
      {
        key: 'lineart',
        icon: <ScissorOutlined />,
        label: '提取线稿',
        onClick: () => { handleExtraction('lineart_extraction', '线稿提取', asset.asset_id) },
      },
      {
        key: 'depth',
        icon: <ExpandOutlined />,
        label: '提取深度图',
        onClick: () => { handleExtraction('depth_map', '深度图提取', asset.asset_id) },
      },
      {
        key: 'pano',
        icon: <GlobalOutlined />,
        label: '生成全景图',
        onClick: () => { setPanoAssetId(asset.asset_id); setPanoPrompt(''); setPanoModalOpen(true) },
      },
      {
        key: 'multi_person',
        icon: <BranchesOutlined />,
        label: '多人分镜（三元约束）',
        onClick: () => {
          setMultiPersonCharA(asset.asset_id)
          setMultiPersonCharB('')
          setMultiPersonMask('')
          setMultiPersonDepth('')
          setMultiPersonPose('')
          setMultiPersonTemplate('T01_双人正面对话')
          setMultiPersonPrompt('')
          setMultiPersonModalOpen(true)
        },
      },
      {
        key: 'layered_render',
        icon: <CopyOutlined />,
        label: '分层渲染（4-5人）',
        onClick: () => {
          setLayeredCharA(asset.asset_id)
          setLayeredCharB('')
          setLayeredCharC('')
          setLayeredCharD('')
          setLayeredMask('')
          setLayeredDepth('')
          setLayeredTemplate('T09_四人围坐')
          setLayeredPromptA('')
          setLayeredPromptB('')
          setLayeredModalOpen(true)
        },
      },
      {
        key: 'batch_storyboard',
        icon: <DatabaseOutlined />,
        label: 'CSV批量分镜',
        onClick: () => {
          setBatchCsvData('')
          setBatchModalOpen(true)
        },
      },
      {
        key: 'template_extract',
        icon: <AppstoreOutlined />,
        label: '制作模板三件套',
        onClick: () => {
          setTemplateExtractAssetId(asset.asset_id)
          setTemplateExtractId('T01_双人正面对话')
          setTemplateExtractName('T01 双人正面对话')
          setTemplateExtractDesc('')
          setTemplateExtractScene('')
          setTemplateExtractPersonCount(2)
          setTemplateExtractModalOpen(true)
        },
      },
      {
        key: 'template_clean',
        icon: <ClearOutlined />,
        label: '模板清场+蒙版',
        onClick: () => {
          setTemplateCleanAssetId(asset.asset_id)
          setTemplateCleanDepthAssetId('')
          setTemplateCleanId('T01_双人正面对话')
          setTemplateCleanModalOpen(true)
        },
      },
      {
        key: 'template_pose',
        icon: <EditOutlined />,
        label: 'Pose简化优化',
        onClick: () => {
          setTemplatePoseAssetId(asset.asset_id)
          setTemplatePoseId('T01_双人正面对话')
          setTemplatePoseName(asset.name || '')
          setTemplatePoseModalOpen(true)
        },
      },
      // Pose类型资产可直接打开编辑器手动修正
      ...(asset.asset_type === 'pose' && asset.urls?.length ? [{
        key: 'pose_edit',
        icon: <EditOutlined />,
        label: 'Pose手动编辑',
        onClick: () => {
          setTemplatePoseResultUrl(asset.urls[0])
          setTemplatePoseAssetId(asset.parent_id || asset.asset_id)
          setTemplatePoseId(asset.metadata?.template_id || '')
          setTemplatePoseName(asset.name || '')
          // 延迟打开 Modal，确保 URL 状态先生效（destroyOnHidden 需要重新创建内容）
          setTimeout(() => setTemplatePoseEditorOpen(true), 0)
        },
      }] : []),
      { type: 'divider' as const },
      {
        key: 'delete',
        label: '删除',
        danger: true,
        onClick: () => { deleteAsset(asset.asset_id) },
      },
    ],
  })

  return (
    <div style={{ padding: 24, height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 头部 */}
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Space>
          <Title level={3} style={{ margin: 0 }}>资产库</Title>
          <ProjectSelector />
        </Space>
        <Space>
          {selectedAssetIds.length > 0 && (
            <>
              <Tag color="blue">已选 {selectedAssetIds.length}</Tag>
              <Button size="small" icon={<ThunderboltOutlined />} onClick={() => { setRefineMode('refine'); setRefineModalOpen(true) }}>
                精修
              </Button>
              <Button size="small" icon={<ExpandOutlined />} onClick={() => { setRefineMode('upscale'); setRefineModalOpen(true) }}>
                超分
              </Button>
              <Button size="small" onClick={clearSelection}>取消选择</Button>
            </>
          )}
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
            新建资产
          </Button>
          <Input.Search
            placeholder="搜索资产..."
            allowClear
            style={{ width: 200 }}
            onSearch={setSearchText}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => !e.target.value && setSearchText('')}
          />
          <Button icon={<ReloadOutlined />} onClick={() => loadAssets(currentProjectId ? { project_id: currentProjectId } : undefined)}>刷新</Button>
        </Space>
      </div>

      {/* 类型标签页 */}
      <Tabs
        activeKey={activeTab}
        onChange={(key) => { setActiveTab(key); setContentTab('') }}
        items={ASSET_TYPE_TABS.map(t => ({ key: t.key, label: t.label }))}
        style={{ marginBottom: 8 }}
      />

      {/* 内容类型子筛选（仅在非"全部"Tab时显示） */}
      {activeTab !== 'all' && (
        <div style={{ marginBottom: 16 }}>
          <Segmented
            value={contentTab || ''}
            onChange={(val) => setContentTab(val as string)}
            options={CONTENT_TYPE_OPTIONS.map(o => ({ value: o.key, label: o.label }))}
          />
        </div>
      )}

      {/* 资产网格 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {filteredAssets.length === 0 ? (
          <Empty description="暂无资产" style={{ marginTop: 80 }}>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
              新建资产
            </Button>
          </Empty>
        ) : (() => {
          // "模板制作" tab 或 content_type 为 template_production 的资产：平铺显示，不使用父子嵌套
          const isTemplateProduction = activeTab === 'template_production' || filteredAssets.some(a => a.content_type === 'template_production')
          if (isTemplateProduction) {
            return (
              <Row gutter={[16, 16]}>
                {filteredAssets.map(asset => (
                  <Col key={asset.asset_id} xs={12} sm={8} md={6} lg={4} xl={3}>
                    <Dropdown menu={getActionMenu(asset)} trigger={['contextMenu']}>
                      <Card
                        hoverable
                        style={{
                          cursor: 'pointer',
                          outline: selectedAssetIds.includes(asset.asset_id) ? '2px solid #1677ff' : 'none',
                        }}
                        cover={
                          asset.urls && asset.urls.length > 0 ? (
                            <div style={{ position: 'relative' }}>
                              <Image
                                src={asset.urls[0]}
                                alt={asset.name}
                                style={{ height: 140, objectFit: 'cover' }}
                                preview={{ src: asset.urls[0] }}
                                fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iI2YwZjBmMCIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiBkb21pbmFudC1iYXNlbGluZT0ibWlkZGxlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjY2NjIiBmb250LXNpemU9IjE0Ij7ml6DnvKnnlaXlm748L3RleHQ+PC9zdmc+"
                                onError={() => markImageFailed(asset.urls[0])}
                              />
                              {/* 图片加载失败时显示重新生成按钮 */}
                              {failedImages.has(asset.urls[0]) && (
                                <Button
                                  type="primary"
                                  size="small"
                                  icon={<ReloadOutlined />}
                                  style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 2 }}
                                  onClick={(e) => { e.stopPropagation(); handleRegenerateAsset(asset) }}
                                >
                                  重新生成
                                </Button>
                              )}
                              <Button
                                type="text"
                                icon={<DownloadOutlined />}
                                size="small"
                                style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', zIndex: 1 }}
                                onClick={(e) => { e.stopPropagation(); downloadImage(asset.urls[0], asset.name) }}
                              />
                            </div>
                          ) : (
                            <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
                              {TYPE_ICON[asset.asset_type] || <AppstoreOutlined style={{ fontSize: 32, color: '#d9d9d9' }} />}
                            </div>
                          )
                        }
                        onClick={() => toggleAssetSelection(asset.asset_id)}
                        onDoubleClick={() => handleShowDetail(asset)}
                        size="small"
                      >
                        <Card.Meta
                          title={
                            <Space size={4}>
                              <Tag color={TYPE_COLOR[asset.asset_type] || 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                                {asset.asset_type}
                              </Tag>
                              <Text ellipsis style={{ fontSize: 12, maxWidth: 100 }}>{asset.name}</Text>
                            </Space>
                          }
                          description={
                            <div>
                              {asset.metadata?.size && (
                                <Tag style={{ fontSize: 10, lineHeight: '14px', padding: '0 3px', marginBottom: 2 }}>
                                  {asset.metadata.size}
                                </Tag>
                              )}
                              {asset.metadata?.extraction_type && (
                                <Tag color="orange" style={{ fontSize: 10, lineHeight: '14px', padding: '0 3px', marginBottom: 2 }}>
                                  {asset.metadata.extraction_type === 'template_batch_extract' ? '提取' :
                                   asset.metadata.extraction_type === 'template_clean' ? '清场' :
                                   asset.metadata.extraction_type === 'template_pose' ? 'Pose简化' :
                                   asset.metadata.extraction_type}
                                </Tag>
                              )}
                            </div>
                          }
                        />
                      </Card>
                    </Dropdown>
                  </Col>
                ))
              }
            </Row>
            )
          }

          // 其他 tab：使用父子嵌套布局
          const childrenMap: Record<string, any[]> = {}
          const parentIds = new Set<string>()
          filteredAssets.forEach(a => {
            if (a.parent_id) {
              parentIds.add(a.parent_id)
              if (!childrenMap[a.parent_id]) childrenMap[a.parent_id] = []
              childrenMap[a.parent_id].push(a)
            }
          })
          // 筛选出根资产（无 parent_id，或 parent 不在当前过滤列表中）
          const rootAssets = filteredAssets.filter(a => !a.parent_id || !filteredAssets.some(fa => fa.asset_id === a.parent_id))
          return (
            <Row gutter={[16, 16]}>
              {rootAssets.map(asset => {
                const children = childrenMap[asset.asset_id] || []
                return (
                  <Col key={asset.asset_id} xs={12} sm={8} md={6} lg={4} xl={3}>
                    <Dropdown menu={getActionMenu(asset)} trigger={['contextMenu']}>
                      <Card
                        hoverable
                        style={{
                          cursor: 'pointer',
                          outline: selectedAssetIds.includes(asset.asset_id) ? '2px solid #1677ff' : 'none',
                        }}
                        cover={
                          asset.urls && asset.urls.length > 0 ? (
                            asset.asset_type === 'video' ? (
                              <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#000' }}>
                                <VideoCameraOutlined style={{ fontSize: 32, color: '#fff' }} />
                              </div>
                            ) : (
                              <div style={{ position: 'relative' }}>
                                <Image
                                  src={asset.asset_type === 'multi_view' ? asset.urls[asset.urls.length - 1] : asset.urls[0]}
                                  alt={asset.name}
                                  style={{ height: 140, objectFit: 'cover' }}
                                  preview={{ src: asset.asset_type === 'multi_view' ? asset.urls[asset.urls.length - 1] : asset.urls[0] }}
                                  fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iI2YwZjBmMCIvPjx0ZXh0IHg9IjUwJSIgeT0iNTAlIiBkb21pbmFudC1iYXNlbGluZT0ibWlkZGxlIiB0ZXh0LWFuY2hvcj0ibWlkZGxlIiBmaWxsPSIjY2NjIiBmb250LXNpemU9IjE0Ij7ml6DnvKnnlaXlm748L3RleHQ+PC9zdmc+"
                                  onError={() => markImageFailed(asset.asset_type === 'multi_view' ? asset.urls[asset.urls.length - 1] : asset.urls[0])}
                                />
                                {failedImages.has(asset.asset_type === 'multi_view' ? asset.urls[asset.urls.length - 1] : asset.urls[0]) && (
                                  <Button
                                    type="primary"
                                    size="small"
                                    icon={<ReloadOutlined />}
                                    style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', zIndex: 2 }}
                                    onClick={(e) => { e.stopPropagation(); handleRegenerateAsset(asset) }}
                                  >
                                    重新生成
                                  </Button>
                                )}
                                <Button
                                  type="text"
                                  icon={<DownloadOutlined />}
                                  size="small"
                                  style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', zIndex: 1 }}
                                  onClick={(e) => { e.stopPropagation(); downloadImage(asset.asset_type === 'multi_view' ? asset.urls[asset.urls.length - 1] : asset.urls[0], asset.name) }}
                                />
                              </div>
                            )
                          ) : (
                            <div style={{ height: 140, display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5' }}>
                              {TYPE_ICON[asset.asset_type] || <AppstoreOutlined style={{ fontSize: 32, color: '#d9d9d9' }} />}
                            </div>
                          )
                        }
                        onClick={() => toggleAssetSelection(asset.asset_id)}
                        onDoubleClick={() => handleShowDetail(asset)}
                        size="small"
                      >
                        <Card.Meta
                          title={
                            <Space size={4}>
                              <Tag color={TYPE_COLOR[asset.asset_type] || 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                                {asset.asset_type}
                              </Tag>
                              {asset.content_type && (
                                <Tag color={CONTENT_COLOR[asset.content_type] || 'default'} style={{ fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>
                                  {asset.content_type}
                                </Tag>
                              )}
                              <Text ellipsis style={{ fontSize: 12, maxWidth: 80 }}>{asset.name}</Text>
                            </Space>
                          }
                          description={
                            <div>
                              {/* 分辨率标签 */}
                              {asset.metadata?.size && (
                                <Tag style={{ fontSize: 10, lineHeight: '14px', padding: '0 3px', marginBottom: 2 }}>
                                  {asset.metadata.size}
                                </Tag>
                              )}
                              {asset.metadata?.upscale_factor && (
                                <Tag color="cyan" style={{ fontSize: 10, lineHeight: '14px', padding: '0 3px', marginBottom: 2 }}>
                                  {asset.metadata.upscale_factor}x
                                </Tag>
                              )}
                              {/* 提示词预览 */}
                              {asset.metadata?.prompt && (
                                <div
                                  style={{ fontSize: 10, color: '#888', lineHeight: '14px', maxHeight: 28, overflow: 'hidden', textOverflow: 'ellipsis', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
                                  title={asset.metadata.prompt}
                                >
                                  {asset.metadata.prompt}
                                </div>
                              )}
                              {/* 子资产（精修/超分）缩略图 */}
                              {children.length > 0 && (
                                <div style={{ marginTop: 8, borderTop: '1px solid #f0f0f0', paddingTop: 6 }}>
                                  <Text type="secondary" style={{ fontSize: 10 }}>衍生 ({children.length})</Text>
                                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                                    {children.map(child => {
                                      const isUpscale = child.metadata?.mode === 'upscale' || child.metadata?.upscale_factor
                                      const childLabel = isUpscale ? `${child.metadata?.size || ''}` : child.name
                                      return (
                                        <div
                                          key={child.asset_id}
                                          style={{
                                            width: 56, height: 56, borderRadius: 4, overflow: 'hidden',
                                            border: selectedAssetIds.includes(child.asset_id) ? '2px solid #1677ff' : '1px solid #e8e8e8',
                                            cursor: 'pointer', position: 'relative', flexShrink: 0,
                                            background: '#fafafa',
                                          }}
                                          onClick={(e) => { e.stopPropagation(); toggleAssetSelection(child.asset_id) }}
                                          onDoubleClick={(e) => { e.stopPropagation(); handleShowDetail(child) }}
                                        >
                                          {child.urls?.[0] ? (
                                            <div style={{ position: 'relative', width: '100%', height: '100%' }}>
                                              <img
                                                src={child.urls[0]}
                                                alt={child.name}
                                                style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                              />
                                              {/* 下载按钮 */}
                                              <Button
                                                type="text"
                                                icon={<DownloadOutlined />}
                                                size="small"
                                                style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 0, border: 'none', width: 18, height: 18, fontSize: 10, padding: 0, lineHeight: '18px', zIndex: 2 }}
                                                onClick={(e) => { e.stopPropagation(); downloadImage(child.urls[0], child.name) }}
                                              />
                                              {/* 超分分辨率角标 */}
                                              {isUpscale && child.metadata?.size && (
                                                <div style={{
                                                  position: 'absolute', bottom: 0, left: 0, right: 0,
                                                  background: 'rgba(0,0,0,0.6)', color: '#fff',
                                                  fontSize: 8, textAlign: 'center', lineHeight: '14px',
                                                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                                                }}>
                                                  {child.metadata.size}
                                                </div>
                                              )}
                                              {/* 超分倍数角标 */}
                                              {child.metadata?.upscale_factor && (
                                                <div style={{
                                                  position: 'absolute', top: 0, right: 0,
                                                  background: '#1677ff', color: '#fff',
                                                  fontSize: 8, lineHeight: '14px', padding: '0 3px',
                                                  borderBottomLeftRadius: 4,
                                                }}>
                                                  {child.metadata.upscale_factor}x
                                                </div>
                                              )}
                                            </div>
                          ) : (
                            <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f5f5f5', fontSize: 10, color: '#ccc' }}>
                              无图
                            </div>
                          )}
                                        </div>
                                      )
                                    })}
                                  </div>
                                </div>
                              )}
                            </div>
                          }
                        />
                      </Card>
                    </Dropdown>
                  </Col>
                )
              })}
            </Row>
          )
        })()}
      </div>

      {/* 精修/超分弹窗 */}
      <Modal
        title={refineMode === 'refine' ? '图生图 · 单图精修' : '超分设置'}
        open={refineModalOpen}
        onOk={handleRefine}
        onCancel={() => setRefineModalOpen(false)}
        confirmLoading={refineLoading}
        okText={refineMode === 'refine' ? '开始精修' : '开始超分'}
        width={520}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 源图预览 */}
          {(() => {
            const srcAsset = assets.find(a => a.asset_id === selectedAssetIds[0])
            if (!srcAsset) return <Text type="secondary">已选择 {selectedAssetIds.length} 个资产</Text>
            const ct = srcAsset.content_type || ''
            return (
              <Card size="small" style={{ background: '#fafafa' }}>
                <div style={{ display: 'flex', gap: 12 }}>
                  {srcAsset.urls?.[0] && (
                    <div style={{ position: 'relative', flexShrink: 0 }}>
                      <Image
                        src={srcAsset.urls[0]}
                        alt={srcAsset.name}
                        style={{ width: 80, height: 80, objectFit: 'cover', borderRadius: 6 }}
                        preview={{ src: srcAsset.urls[0] }}
                        fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHJlY3Qgd2lkdGg9IjgwIiBoZWlnaHQ9IjgwIiBmaWxsPSIjZjVmNWY1Ii8+PC9zdmc+"
                      />
                      <Button
                        type="text"
                        icon={<DownloadOutlined />}
                        size="small"
                        style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', width: 20, height: 20, fontSize: 11, padding: 0, lineHeight: '20px', zIndex: 1 }}
                        onClick={(e) => { e.stopPropagation(); downloadImage(srcAsset.urls[0], srcAsset.name) }}
                      />
                    </div>
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ marginBottom: 4 }}>
                      <Text strong ellipsis style={{ fontSize: 13 }}>{srcAsset.name}</Text>
                    </div>
                    <Space size={4} wrap>
                      <Tag color={TYPE_COLOR[srcAsset.asset_type]} style={{ fontSize: 10 }}>{srcAsset.asset_type}</Tag>
                      {ct && <Tag color={CONTENT_COLOR[ct]} style={{ fontSize: 10 }}>{ct}</Tag>}
                      {srcAsset.metadata?.size && <Tag style={{ fontSize: 10 }}>{srcAsset.metadata.size}</Tag>}
                    </Space>
                  </div>
                </div>
              </Card>
            )
          })()}

          {refineMode === 'refine' ? (
            <>
              {/* 精修模式：修改指令输入 */}
              <div>
                <Text strong>修改指令</Text>
                <Input.TextArea
                  rows={4}
                  value={refinePrompt}
                  onChange={e => setRefinePrompt(e.target.value)}
                  placeholder={(() => {
                    const ct = assets.find(a => a.asset_id === selectedAssetIds[0])?.content_type
                    if (ct === 'character') return '将原图中人物的xxx修改为yyy，保持脸部特征和发型完全不变。写清楚改哪里、改成什么样'
                    if (ct === 'scene') return '将原图的白天场景修改为xx场景，整体色调改为xx。必须强调保持场景结构不变'
                    if (ct === 'prop') return '将原图中的材质从xx修改为yy，增加高光反射。必须写清楚材质改为什么'
                    return '描述你想要修改的内容...'
                  })()}
                  style={{ marginTop: 4 }}
                />
              </div>
              {/* 写作指南 */}
              {(() => {
                const ct = assets.find(a => a.asset_id === selectedAssetIds[0])?.content_type
                if (ct === 'character') return <Text type="secondary" style={{ fontSize: 11 }}>角色精修：必须强调"保持脸部不变"，写动词指令（改哪里→改成什么），不要只写名词</Text>
                if (ct === 'scene') return <Text type="secondary" style={{ fontSize: 11 }}>场景精修：必须强调"整体色调改为..."，允许光影突变，LoRA 一致性已自动降低</Text>
                if (ct === 'prop') return <Text type="secondary" style={{ fontSize: 11 }}>道具精修：必须强调"材质改为..."，允许完全推翻原材质，LoRA 已降至最低</Text>
                return <Text type="secondary" style={{ fontSize: 11 }}>这是图生图模式，请写"改哪里、改成什么样"的动词指令，而非名词描述</Text>
              })()}
            </>
          ) : (
            <>
              {/* 超分模式 */}
              <div>
                <Text>放大倍数:</Text>
                <InputNumber
                  min={2} max={4} value={refineFactor}
                  onChange={v => setRefineFactor(v || 2)}
                  style={{ width: '100%', marginTop: 4 }}
                />
              </div>
            </>
          )}
        </Space>
      </Modal>

      {/* 全景图弹窗 */}
      <Modal
        title="生成全景图"
        open={panoModalOpen}
        onOk={handlePanoConfirm}
        onCancel={() => { if (!panoLoading) { setPanoModalOpen(false); setPanoElapsed(0) } }}
        confirmLoading={panoLoading}
        okText={panoLoading ? `生成中 ${panoElapsed}s` : '开始生成'}
        cancelButtonProps={{ disabled: panoLoading }}
        width={520}
        maskClosable={!panoLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 源图预览 */}
          {(() => {
            const srcAsset = assets.find(a => a.asset_id === panoAssetId)
            if (!srcAsset) return <Text type="secondary">未选择资产</Text>
            return (
              <Card size="small" style={{ background: '#fafafa' }}>
                <div style={{ display: 'flex', gap: 12 }}>
                  {srcAsset.urls?.[0] && (
                    <div style={{ position: 'relative', flexShrink: 0 }}>
                      <Image
                        src={srcAsset.urls[0]}
                        alt={srcAsset.name}
                        style={{ width: 80, height: 80, objectFit: 'cover', borderRadius: 6 }}
                        preview={{ src: srcAsset.urls[0] }}
                        fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iODAiIGhlaWdodD0iODAiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+PHJlY3Qgd2lkdGg9IjgwIiBoZWlnaHQ9IjgwIiBmaWxsPSIjZjVmNWY1Ii8+PC9zdmc+"
                      />
                      <Button
                        type="text"
                        icon={<DownloadOutlined />}
                        size="small"
                        style={{ position: 'absolute', top: 0, right: 0, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', width: 20, height: 20, fontSize: 11, padding: 0, lineHeight: '20px', zIndex: 1 }}
                        onClick={(e) => { e.stopPropagation(); downloadImage(srcAsset.urls[0], srcAsset.name) }}
                      />
                    </div>
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ marginBottom: 4 }}>
                      <Text strong ellipsis style={{ fontSize: 13 }}>{srcAsset.name}</Text>
                    </div>
                    <Space size={4} wrap>
                      <Tag color={TYPE_COLOR[srcAsset.asset_type]} style={{ fontSize: 10 }}>{srcAsset.asset_type}</Tag>
                      {srcAsset.content_type && <Tag color={CONTENT_COLOR[srcAsset.content_type]} style={{ fontSize: 10 }}>{srcAsset.content_type}</Tag>}
                    </Space>
                  </div>
                </div>
              </Card>
            )
          })()}
          {/* 生成中进度提示 */}
          {panoLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在生成全景图... 已耗时 {panoElapsed}s</Text>
              </div>
            </div>
          )}
          {/* 提示词输入（生成中禁用） */}
          <div>
            <Text strong>全景描述</Text>
            <Input.TextArea
              rows={4}
              value={panoPrompt}
              onChange={e => setPanoPrompt(e.target.value)}
              placeholder="描述360度全景视角的场景，例如：360 degree panoramic shot, full wide-angle living room interior, ultra realistic..."
              style={{ marginTop: 4 }}
              disabled={panoLoading}
            />
          </div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            基于参考图生成360度全景图，提示词描述全景视角下的场景细节。留空则使用默认提示词。
          </Text>
        </Space>
      </Modal>

      {/* 多人分镜弹窗（三元约束：蒙版+深度图+OpenPose） */}
      <Modal
        title="多人分镜（三元约束）"
        open={multiPersonModalOpen}
        onOk={handleMultiPersonConfirm}
        onCancel={() => { if (!multiPersonLoading) { setMultiPersonModalOpen(false); setMultiPersonElapsed(0) } }}
        confirmLoading={multiPersonLoading}
        okText={multiPersonLoading ? `生成中 ${multiPersonElapsed}s` : '开始生成'}
        cancelButtonProps={{ disabled: multiPersonLoading }}
        width={620}
        maskClosable={!multiPersonLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 模板选择 */}
          <div>
            <Text strong>分镜模板</Text>
            <Select
              value={multiPersonTemplate}
              onChange={setMultiPersonTemplate}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              options={[
                { value: 'T01_双人正面对话', label: 'T01 双人正面对话' },
                { value: 'T02_双人侧身对话', label: 'T02 双人侧身对话' },
                { value: 'T03_一前一后', label: 'T03 一前一后' },
                { value: 'T04_并肩行走', label: 'T04 并肩行走' },
                { value: 'T05_对坐交谈', label: 'T05 对坐交谈' },
                { value: 'T06_一人站立一人坐着', label: 'T06 一人站立一人坐着' },
                { value: 'T07_拥抱', label: 'T07 拥抱' },
                { value: 'T08_握手', label: 'T08 握手' },
                { value: 'T09_追逐', label: 'T09 追逐' },
                { value: 'T10_群像构图', label: 'T10 群像构图' },
              ]}
            />
          </div>

          {/* 人物A */}
          <div>
            <Text strong>人物A（必选）</Text>
            <Select
              value={multiPersonCharA || undefined}
              onChange={setMultiPersonCharA}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              placeholder="选择人物A资产"
              showSearch
              optionFilterProp="label"
              options={assets
                .filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type))
                .map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* 人物B */}
          <div>
            <Text strong>人物B（必选）</Text>
            <Select
              value={multiPersonCharB || undefined}
              onChange={setMultiPersonCharB}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              placeholder="选择人物B资产"
              showSearch
              optionFilterProp="label"
              options={assets
                .filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type))
                .map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* 蒙版（可选） */}
          <div>
            <Text strong>蒙版图（可选，线稿/分镜可作蒙版参考）</Text>
            <Select
              value={multiPersonMask || undefined}
              onChange={setMultiPersonMask}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              placeholder="选择蒙版资产（可留空）"
              allowClear
              showSearch
              optionFilterProp="label"
              options={assets
                .filter(a => ['lineart', 'storyboard', 'storyboard_multi'].includes(a.asset_type))
                .map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* 深度图（可选） */}
          <div>
            <Text strong>深度图（可选，ControlNet约束）</Text>
            <Select
              value={multiPersonDepth || undefined}
              onChange={setMultiPersonDepth}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              placeholder="选择深度图资产（可留空）"
              allowClear
              showSearch
              optionFilterProp="label"
              options={assets
                .filter(a => a.asset_type === 'depth')
                .map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* OpenPose（可选） */}
          <div>
            <Text strong>OpenPose骨架（可选，ControlNet约束）</Text>
            <Select
              value={multiPersonPose || undefined}
              onChange={setMultiPersonPose}
              style={{ width: '100%', marginTop: 4 }}
              disabled={multiPersonLoading}
              placeholder="选择姿态资产（可留空）"
              allowClear
              showSearch
              optionFilterProp="label"
              options={assets
                .filter(a => a.asset_type === 'pose')
                .map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* 生成中进度提示 */}
          {multiPersonLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在生成多人分镜... 已耗时 {multiPersonElapsed}s</Text>
              </div>
            </div>
          )}

          {/* 提示词输入 */}
          <div>
            <Text strong>分镜描述</Text>
            <Input.TextArea
              rows={4}
              value={multiPersonPrompt}
              onChange={e => setMultiPersonPrompt(e.target.value)}
              placeholder="描述多人分镜场景，例如：Two characters facing each other in a sunlit courtyard, natural conversation, cinematic lighting, medium shot..."
              style={{ marginTop: 4 }}
              disabled={multiPersonLoading}
            />
          </div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            基于人物A、人物B和三元约束（蒙版+深度图+OpenPose）生成多人分镜。缺少深度图或姿态图时自动禁用对应ControlNet。留空提示词则使用默认描述。
          </Text>
        </Space>
      </Modal>

      {/* 分层渲染弹窗（4-5人场景分A/B组生成+合成） */}
      <Modal
        title="分层渲染（4-5人）"
        open={layeredModalOpen}
        onOk={handleLayeredConfirm}
        onCancel={() => { if (!layeredLoading) { setLayeredModalOpen(false); setLayeredElapsed(0) } }}
        confirmLoading={layeredLoading}
        okText={layeredLoading ? `生成中 ${layeredElapsed}s` : '开始生成'}
        cancelButtonProps={{ disabled: layeredLoading }}
        width={660}
        maskClosable={!layeredLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 模板选择 */}
          <div>
            <Text strong>分镜模板</Text>
            <Select
              value={layeredTemplate}
              onChange={setLayeredTemplate}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              options={[
                { value: 'T09_四人围坐', label: 'T09 四人围坐（分层）' },
                { value: 'T10_两前两后', label: 'T10 两前两后（分层）' },
              ]}
            />
          </div>

          {/* A组 */}
          <div style={{ background: '#f6ffed', padding: 12, borderRadius: 8, border: '1px solid #b7eb8f' }}>
            <Text strong style={{ color: '#389e0d' }}>A组人物</Text>
            <Select
              value={layeredCharA || undefined}
              onChange={setLayeredCharA}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="A组人物1（必选）"
              showSearch optionFilterProp="label"
              options={assets.filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type)).map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
            <Select
              value={layeredCharB || undefined}
              onChange={setLayeredCharB}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="A组人物2（必选）"
              showSearch optionFilterProp="label"
              options={assets.filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type)).map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
            <Input.TextArea
              rows={2}
              value={layeredPromptA}
              onChange={e => setLayeredPromptA(e.target.value)}
              placeholder="A组提示词，例如：Two people sitting on the left side of a round table..."
              style={{ marginTop: 4 }}
              disabled={layeredLoading}
            />
          </div>

          {/* B组 */}
          <div style={{ background: '#fff7e6', padding: 12, borderRadius: 8, border: '1px solid #ffd591' }}>
            <Text strong style={{ color: '#d46b08' }}>B组人物</Text>
            <Select
              value={layeredCharC || undefined}
              onChange={setLayeredCharC}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="B组人物1（可选）"
              allowClear
              showSearch optionFilterProp="label"
              options={assets.filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type)).map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
            <Select
              value={layeredCharD || undefined}
              onChange={setLayeredCharD}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="B组人物2（可选）"
              allowClear
              showSearch optionFilterProp="label"
              options={assets.filter(a => ['concept', 'multi_view', 'storyboard', 'storyboard_multi'].includes(a.asset_type)).map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
            <Input.TextArea
              rows={2}
              value={layeredPromptB}
              onChange={e => setLayeredPromptB(e.target.value)}
              placeholder="B组提示词，例如：Two people sitting on the right side of a round table..."
              style={{ marginTop: 4 }}
              disabled={layeredLoading}
            />
          </div>

          {/* 蒙版和深度图 */}
          <div>
            <Text strong>完整蒙版（可选）</Text>
            <Select
              value={layeredMask || undefined}
              onChange={setLayeredMask}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="选择蒙版资产（可留空）"
              allowClear showSearch optionFilterProp="label"
              options={assets.filter(a => ['lineart', 'storyboard', 'storyboard_multi'].includes(a.asset_type)).map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>
          <div>
            <Text strong>共享深度图（可选）</Text>
            <Select
              value={layeredDepth || undefined}
              onChange={setLayeredDepth}
              style={{ width: '100%', marginTop: 4 }}
              disabled={layeredLoading}
              placeholder="选择深度图资产（可留空）"
              allowClear showSearch optionFilterProp="label"
              options={assets.filter(a => a.asset_type === 'depth').map(a => ({ value: a.asset_id, label: `${a.name} (${a.asset_type})` }))}
            />
          </div>

          {/* 生成中进度 */}
          {layeredLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在分层渲染... 已耗时 {layeredElapsed}s</Text>
              </div>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            分层渲染将A/B组人物分别生成后自动合成。适用于4-5人场景，解决AI注意力稀释问题。无B组人物时仅生成A组。
          </Text>
        </Space>
      </Modal>

      {/* CSV批量分镜弹窗 */}
      <Modal
        title="CSV批量分镜生成"
        open={batchModalOpen}
        onOk={handleBatchConfirm}
        onCancel={() => { if (!batchLoading) { setBatchModalOpen(false); setBatchElapsed(0) } }}
        confirmLoading={batchLoading}
        okText={batchLoading ? `生成中 ${batchElapsed}s` : '开始批量生成'}
        cancelButtonProps={{ disabled: batchLoading }}
        width={720}
        maskClosable={!batchLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>CSV分镜脚本</Text>
            <Input.TextArea
              rows={12}
              value={batchCsvData}
              onChange={e => setBatchCsvData(e.target.value)}
              placeholder={`镜头号,模板编号,人物A,人物B,人物C,人物D,区域A提示词,区域B提示词,全局提示词\n1,T01_双人正面对话,男主A001,女主B002,,,man looking at woman,woman talking to man,modern living room\n2,T02_男主过肩镜头,男主A001,女主B002,,,man back to camera,woman crossing arms,modern living room\n3,T09_四人围坐,男主A001,女主B002,配角C003,配角D004,two people on left,two people on right,round table meeting`}
              style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 12 }}
              disabled={batchLoading}
            />
          </div>

          {/* 生成中进度 */}
          {batchLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在批量生成分镜... 已耗时 {batchElapsed}s</Text>
              </div>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            CSV格式：镜头号,模板编号,人物A,人物B,人物C(可选),人物D(可选),区域A提示词,区域B提示词,全局提示词。
            T01-T08使用多人分镜工作流，T09-T10自动使用分层渲染工作流。支持中英文列名。
          </Text>
        </Space>
      </Modal>

      {/* 模板批量提取弹窗（制作模板三件套） */}
      <Modal
        title="制作模板三件套（Pose + 深度 + 线稿）"
        open={templateExtractModalOpen}
        onOk={handleTemplateExtractConfirm}
        onCancel={() => { if (!templateExtractLoading) { setTemplateExtractModalOpen(false); setTemplateExtractElapsed(0) } }}
        confirmLoading={templateExtractLoading}
        okText={templateExtractLoading ? `提取中 ${templateExtractElapsed}s` : '开始提取'}
        cancelButtonProps={{ disabled: templateExtractLoading }}
        width={560}
        maskClosable={!templateExtractLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>模板编号</Text>
            <Select
              value={templateExtractId}
              onChange={(val) => {
                setTemplateExtractId(val)
                // 自动填充名称
                const nameMap: Record<string, string> = {
                  'T01_双人正面对话': 'T01 双人正面对话',
                  'T02_双人侧身对话': 'T02 双人侧身对话',
                  'T03_一前一后': 'T03 一前一后',
                  'T04_并肩行走': 'T04 并肩行走',
                  'T05_对坐交谈': 'T05 对坐交谈',
                  'T06_一人站立一人坐着': 'T06 一人站立一人坐着',
                  'T07_拥抱': 'T07 拥抱',
                  'T08_握手': 'T08 握手',
                  'T09_追逐': 'T09 追逐',
                  'T10_群像构图': 'T10 群像构图',
                }
                setTemplateExtractName(nameMap[val] || val)
              }}
              style={{ width: '100%', marginTop: 4 }}
              disabled={templateExtractLoading}
              options={[
                { value: 'T01_双人正面对话', label: 'T01 双人正面对话' },
                { value: 'T02_双人侧身对话', label: 'T02 双人侧身对话' },
                { value: 'T03_一前一后', label: 'T03 一前一后' },
                { value: 'T04_并肩行走', label: 'T04 并肩行走' },
                { value: 'T05_对坐交谈', label: 'T05 对坐交谈' },
                { value: 'T06_一人站立一人坐着', label: 'T06 一人站立一人坐着' },
                { value: 'T07_拥抱', label: 'T07 拥抱' },
                { value: 'T08_握手', label: 'T08 握手' },
                { value: 'T09_追逐', label: 'T09 追逐' },
                { value: 'T10_群像构图', label: 'T10 群像构图' },
              ]}
            />
          </div>

          <div>
            <Text strong>模板名称</Text>
            <Input
              value={templateExtractName}
              onChange={e => setTemplateExtractName(e.target.value)}
              style={{ marginTop: 4 }}
              disabled={templateExtractLoading}
              placeholder="如：T01 双人正面对话"
            />
          </div>

          <Row gutter={12}>
            <Col span={12}>
              <Text strong>人物数量</Text>
              <InputNumber
                value={templateExtractPersonCount}
                onChange={val => setTemplateExtractPersonCount(val || 2)}
                min={1} max={6}
                style={{ width: '100%', marginTop: 4 }}
                disabled={templateExtractLoading}
              />
            </Col>
            <Col span={12}>
              <Text strong>适用场景</Text>
              <Input
                value={templateExtractScene}
                onChange={e => setTemplateExtractScene(e.target.value)}
                style={{ marginTop: 4 }}
                disabled={templateExtractLoading}
                placeholder="如：对话场景、采访"
              />
            </Col>
          </Row>

          <div>
            <Text strong>描述</Text>
            <Input.TextArea
              rows={2}
              value={templateExtractDesc}
              onChange={e => setTemplateExtractDesc(e.target.value)}
              style={{ marginTop: 4 }}
              disabled={templateExtractLoading}
              placeholder="模板构图描述，如：两人面对面站立交谈"
            />
          </div>

          {/* 提取中进度 */}
          {templateExtractLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在提取模板三图（Pose + 深度 + 线稿）... 已耗时 {templateExtractElapsed}s</Text>
              </div>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            从参考构图图自动提取 Pose 骨架图、深度图、线稿图，输出文件按规范命名（如 T01_双人正面对话_pose.png）并自动更新模板清单。
          </Text>
        </Space>
      </Modal>

      {/* 模板清场+蒙版弹窗 */}
      <Modal
        title="模板清场+蒙版生成（SAM2 自动识别人物）"
        open={templateCleanModalOpen}
        onOk={handleTemplateCleanConfirm}
        onCancel={() => { if (!templateCleanLoading) { setTemplateCleanModalOpen(false); setTemplateCleanElapsed(0) } }}
        confirmLoading={templateCleanLoading}
        okText={templateCleanLoading ? `处理中 ${templateCleanElapsed}s` : '开始清场'}
        cancelButtonProps={{ disabled: templateCleanLoading }}
        width={480}
        maskClosable={!templateCleanLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>模板编号</Text>
            <Select
              value={templateCleanId}
              onChange={(val, option) => {
                setTemplateCleanId(val)
                setTemplateCleanName(((option as any)?.label as string) || val)
              }}
              style={{ width: '100%', marginTop: 4 }}
              disabled={templateCleanLoading}
              options={[
                { value: 'T01_双人正面对话', label: 'T01 双人正面对话' },
                { value: 'T02_双人侧身对话', label: 'T02 双人侧身对话' },
                { value: 'T03_一前一后', label: 'T03 一前一后' },
                { value: 'T04_并肩行走', label: 'T04 并肩行走' },
                { value: 'T05_对坐交谈', label: 'T05 对坐交谈' },
                { value: 'T06_一人站立一人坐着', label: 'T06 一人站立一人坐着' },
                { value: 'T07_拥抱', label: 'T07 拥抱' },
                { value: 'T08_握手', label: 'T08 握手' },
                { value: 'T09_追逐', label: 'T09 追逐' },
                { value: 'T10_群像构图', label: 'T10 群像构图' },
              ]}
            />
          </div>

          <div>
            <Text strong>原始深度图（可选）</Text>
            <Input
              value={templateCleanDepthAssetId}
              onChange={e => setTemplateCleanDepthAssetId(e.target.value)}
              style={{ marginTop: 4 }}
              disabled={templateCleanLoading}
              placeholder="深度图资产ID（留空则自动提取）"
            />
          </div>

          {templateCleanLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">SAM2 识别人物 → 清场深度图 + 生成蒙版... 已耗时 {templateCleanElapsed}s</Text>
              </div>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            SAM2 自动识别人物区域，Inpaint 清场深度图（去除人物保留场景深度），同时生成蒙版（3像素模糊+1像素收缩）。
            需要 ComfyUI 已安装 ComfyUI-SAM2 和 ComfyUI-GroundingDINO 节点。
          </Text>
        </Space>
      </Modal>

      {/* 模板Pose优化弹窗 */}
      <Modal
        title="Pose简化优化（7节点骨架）"
        open={templatePoseModalOpen}
        onOk={handleTemplatePoseConfirm}
        onCancel={() => { if (!templatePoseLoading) { setTemplatePoseModalOpen(false); setTemplatePoseElapsed(0) } }}
        confirmLoading={templatePoseLoading}
        okText={templatePoseLoading ? `处理中 ${templatePoseElapsed}s` : '开始简化'}
        cancelButtonProps={{ disabled: templatePoseLoading }}
        width={480}
        maskClosable={!templatePoseLoading}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Text strong>模板编号</Text>
            <Select
              value={templatePoseId}
              onChange={(val, option) => {
                setTemplatePoseId(val)
                setTemplatePoseName(((option as any)?.label as string) || val)
              }}
              style={{ width: '100%', marginTop: 4 }}
              disabled={templatePoseLoading}
              options={[
                { value: 'T01_双人正面对话', label: 'T01 双人正面对话' },
                { value: 'T02_双人侧身对话', label: 'T02 双人侧身对话' },
                { value: 'T03_一前一后', label: 'T03 一前一后' },
                { value: 'T04_并肩行走', label: 'T04 并肩行走' },
                { value: 'T05_对坐交谈', label: 'T05 对坐交谈' },
                { value: 'T06_一人站立一人坐着', label: 'T06 一人站立一人坐着' },
                { value: 'T07_拥抱', label: 'T07 拥抱' },
                { value: 'T08_握手', label: 'T08 握手' },
                { value: 'T09_追逐', label: 'T09 追逐' },
                { value: 'T10_群像构图', label: 'T10 群像构图' },
              ]}
            />
          </div>

          {templatePoseLoading && (
            <div style={{ textAlign: 'center', padding: '8px 0' }}>
              <Spin />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary">正在简化骨架（去除手指/面部细节，保留7个关键节点）... 已耗时 {templatePoseElapsed}s</Text>
              </div>
            </div>
          )}

          <Text type="secondary" style={{ fontSize: 11 }}>
            从完整OpenPose骨架图自动简化为7节点骨架（头、肩、肘、胯、膝、脚），
            去除手指、面部等冗余关节点。需要 ComfyUI 已安装 DirectorPoseTools 自定义节点。
          </Text>
        </Space>
      </Modal>

      {/* Pose交互式修正弹窗 */}
      <Modal
        title="Pose交互式修正"
        open={templatePoseEditorOpen}
        onCancel={() => { setTemplatePoseEditorOpen(false); setTemplatePoseResultUrl('') }}
        footer={null}
        width={720}
        destroyOnHidden
      >
        {templatePoseResultUrl && (
          <PoseEditor
            imageUrl={templatePoseResultUrl}
            onSave={handlePoseEditorSave}
            width={640}
            height={640}
          />
        )}
        <div style={{ marginTop: 8, fontSize: 11, color: '#999' }}>
          黑色画笔覆盖错误关节 → 白色画笔画正确位置 → 保存后系统自动上传修正图
        </div>
      </Modal>

      {/* 详情侧面板 */}
      <Drawer
        title={
          <Space>
            <InfoCircleOutlined />
            <span>{detailAsset?.name || '资产详情'}</span>
            {detailAsset && <Tag color={TYPE_COLOR[detailAsset.asset_type]}>{detailAsset.asset_type}</Tag>}
            {detailAsset?.content_type && <Tag color={CONTENT_COLOR[detailAsset.content_type]}>{detailAsset.content_type}</Tag>}
          </Space>
        }
        placement="right"
        width={420}
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        extra={
          <Button size="small" onClick={() => { setDetailOpen(false); toggleAssetSelection(detailAsset?.asset_id) }}>
            选择
          </Button>
        }
      >
        {detailAsset && (
          <>
            {/* 预览 */}
            <div style={{ marginBottom: 16 }}>
              {detailAsset.asset_type === 'video' ? (
                <video src={detailAsset.urls?.[0]} controls style={{ width: '100%', borderRadius: 8, maxHeight: 240 }} />
              ) : (
                <div>
                  <Image
                    src={detailAsset.asset_type === 'multi_view' ? detailAsset.urls[detailAsset.urls.length - 1] : detailAsset.urls?.[0]}
                    alt={detailAsset.name}
                    style={{ width: '100%', borderRadius: 8, maxHeight: 240, objectFit: 'contain' }}
                    fallback="data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjAwIiBoZWlnaHQ9IjIwMCIgZmlsbD0iI2YwZjBmMCIvPjwvc3ZnPg=="
                  />
                  <div style={{ textAlign: 'center', marginTop: 8 }}>
                    <Button
                      type="link"
                      icon={<DownloadOutlined />}
                      size="small"
                      onClick={() => downloadImage(
                        detailAsset.asset_type === 'multi_view' ? detailAsset.urls[detailAsset.urls.length - 1] : detailAsset.urls?.[0],
                        detailAsset.name
                      )}
                    >
                      下载图片
                    </Button>
                  </div>
                </div>
              )}
            </div>

            {/* 元数据 */}
            <Card title="基本信息" size="small" style={{ marginBottom: 16 }}
              extra={
                <Button size="small" type="link" onClick={() => {
                  const newMeta = { ...detailAsset.metadata, size: editSize, prompt: editPrompt }
                  directorApi.updateAsset(detailAsset.asset_id, { metadata: newMeta }).then(() => {
                    detailAsset.metadata = newMeta
                    setDetailAsset({ ...detailAsset })
                    message.success('已保存')
                  })
                }}>保存修改</Button>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="资产ID"><Text copyable style={{ fontSize: 12 }}>{detailAsset.asset_id}</Text></Descriptions.Item>
                <Descriptions.Item label="类型"><Tag color={TYPE_COLOR[detailAsset.asset_type]}>{detailAsset.asset_type}</Tag></Descriptions.Item>
                <Descriptions.Item label="内容">{detailAsset.content_type ? <Tag color={CONTENT_COLOR[detailAsset.content_type]}>{detailAsset.content_type}</Tag> : '无'}</Descriptions.Item>
                <Descriptions.Item label="名称">{detailAsset.name}</Descriptions.Item>
                <Descriptions.Item label="版本">v{detailAsset.version || 1}</Descriptions.Item>
                <Descriptions.Item label="分辨率">
                  <Select
                    size="small"
                    value={editSize}
                    onChange={setEditSize}
                    style={{ width: '100%' }}
                    options={SIZE_OPTIONS.map(o => ({ value: o.key, label: o.label }))}
                  />
                </Descriptions.Item>
                <Descriptions.Item label="提示词">
                  <Input.TextArea
                    size="small"
                    value={editPrompt}
                    onChange={e => setEditPrompt(e.target.value)}
                    autoSize={{ minRows: 2, maxRows: 6 }}
                    style={{ fontSize: 12 }}
                  />
                </Descriptions.Item>
                {detailAsset.metadata?.model && (
                  <Descriptions.Item label="模型">{detailAsset.metadata.model}</Descriptions.Item>
                )}
                <Descriptions.Item label="创建时间">{new Date(detailAsset.created_at * 1000).toLocaleString()}</Descriptions.Item>
                <Descriptions.Item label="最后更新">{new Date(detailAsset.updated_at * 1000).toLocaleString()}</Descriptions.Item>
              </Descriptions>
            </Card>

            {/* 血缘关系 */}
            <Card title="血缘关系" size="small" style={{ marginBottom: 16 }}>
              <Timeline
                items={[
                  ...(lineage.length > 0
                    ? lineage.map((l: any) => ({
                        color: TYPE_COLOR[l.asset_type] || 'gray',
                        children: <Text style={{ fontSize: 12 }}>父资产: {l.name || l.asset_id?.slice(0, 12)} ({l.asset_type})</Text>,
                      }))
                    : [{ color: 'gray', children: <Text style={{ fontSize: 12, color: '#888' }}>无父资产（根节点）</Text> }]),
                  {
                    color: TYPE_COLOR[detailAsset.asset_type] || 'blue',
                    children: <Text strong style={{ fontSize: 12 }}>当前: {detailAsset.name}</Text>,
                  },
                  ...(children.length > 0
                    ? children.map((c: any) => ({
                        color: TYPE_COLOR[c.asset_type] || 'gray',
                        children: <Text style={{ fontSize: 12 }}>子资产: {c.name || c.asset_id?.slice(0, 12)} ({c.asset_type})</Text>,
                      }))
                    : []),
                ]}
              />
            </Card>

            {/* 多图展示 */}
            {detailAsset.urls?.length > 1 && (
              <Card title="所有图片" size="small">
                <Row gutter={[8, 8]}>
                  {detailAsset.urls.map((url: string, i: number) => (
                    <Col key={i} span={8}>
                      <div style={{ position: 'relative' }}>
                        <Image src={url} alt={`${detailAsset.name} #${i + 1}`} style={{ width: '100%', borderRadius: 4 }} />
                        <Button
                          type="text"
                          icon={<DownloadOutlined />}
                          size="small"
                          style={{ position: 'absolute', top: 2, right: 2, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', width: 20, height: 20, fontSize: 11, padding: 0, lineHeight: '20px', zIndex: 1 }}
                          onClick={(e) => { e.stopPropagation(); downloadImage(url, `${detailAsset.name}_${i + 1}`) }}
                        />
                      </div>
                    </Col>
                  ))}
                </Row>
              </Card>
            )}

            {/* 三视图 iframe */}
            {(detailAsset.asset_type === 'concept' || detailAsset.content_type === 'character') && (
              <Card
                title="三视图生成器"
                size="small"
                extra={
                  <Button
                    size="small"
                    icon={<GlobalOutlined />}
                    onClick={() => setAngleIframeOpen(true)}
                  >
                    打开三视图
                  </Button>
                }
              >
                <Text type="secondary" style={{ fontSize: 12 }}>
                  使用 Infinite-Canvas 三视图工具生成角色正面/侧面/背面
                </Text>
              </Card>
            )}
          </>
        )}
      </Drawer>

      {/* 新建资产弹窗 */}
      <Modal
        title="新建资产"
        open={createModalOpen}
        onOk={createMode === 'upload' ? handleCreateAsset : handleGenerateSubmit}
        onCancel={() => {
          setCreateModalOpen(false)
          if (previewUrl) URL.revokeObjectURL(previewUrl)
          setUploadFile(null)
          setPreviewUrl('')
          setGeneratePrompt('')
        }}
        confirmLoading={createMode === 'upload' ? createLoading : generateLoading}
        okText={createMode === 'upload' ? '创建' : (generateLoading ? `生成中... ${generateElapsed}s` : '生成并创建')}
        okButtonProps={{ disabled: createMode === 'upload' ? !uploadFile : !generatePrompt.trim() }}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 模式切换 */}
          <Segmented
            value={createMode}
            onChange={v => setCreateMode(v as 'upload' | 'generate')}
            options={[
              { label: '上传文件', value: 'upload' },
              { label: 'AI 生成', value: 'generate' },
            ]}
            block
          />

          {createMode === 'upload' ? (
            /* 文件上传区 */
            <div>
              <Text>选择文件:</Text>
              <div
                style={{
                  marginTop: 8,
                  border: '2px dashed #d9d9d9',
                  borderRadius: 8,
                  padding: previewUrl ? 12 : 24,
                  textAlign: 'center',
                  cursor: 'pointer',
                  transition: 'border-color 0.3s',
                  position: 'relative',
                }}
                onClick={() => {
                  const input = document.createElement('input')
                  input.type = 'file'
                  input.accept = '.png,.jpg,.jpeg,.webp,.mp4,.webm,.mov'
                  input.onchange = (e) => {
                    const f = (e.target as HTMLInputElement).files?.[0]
                    if (f) handleFileSelect(f)
                  }
                  input.click()
                }}
                onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.borderColor = '#1677ff' }}
                onDragLeave={(e) => { e.currentTarget.style.borderColor = '#d9d9d9' }}
                onDrop={(e) => {
                  e.preventDefault()
                  e.currentTarget.style.borderColor = '#d9d9d9'
                  const f = e.dataTransfer.files[0]
                  if (f) handleFileSelect(f)
                }}
              >
                {previewUrl ? (
                  <div>
                    {uploadFile?.type.startsWith('video/') ? (
                      <video src={previewUrl} style={{ maxWidth: '100%', maxHeight: 200, borderRadius: 4 }} controls />
                    ) : (
                      <img src={previewUrl} alt="预览" style={{ maxWidth: '100%', maxHeight: 200, borderRadius: 4, objectFit: 'contain' }} />
                    )}
                    <div style={{ marginTop: 8 }}>
                      <Text type="secondary" style={{ fontSize: 12 }}>{uploadFile?.name}</Text>
                    </div>
                  </div>
                ) : (
                  <div>
                    <PlusOutlined style={{ fontSize: 32, color: '#bfbfbf' }} />
                    <div style={{ marginTop: 8, color: '#8c8c8c' }}>
                      点击或拖拽文件到此处上传
                    </div>
                    <div style={{ marginTop: 4, color: '#bfbfbf', fontSize: 12 }}>
                      支持 PNG / JPG / WEBP / MP4 / WEBM / MOV
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* AI 生成 prompt 输入区 */
            <div>
              <Text>画面描述:</Text>
              <Input.TextArea
                rows={4}
                value={generatePrompt}
                onChange={e => setGeneratePrompt(e.target.value)}
                placeholder="描述你想生成的画面，例如：一个赛博朋克风格的城市街景，霓虹灯光，雨天..."
                maxLength={2000}
                showCount
                style={{ marginTop: 4 }}
              />
            </div>
          )}

          {/* 资产类型 */}
          <div>
            <Text>资产类型:</Text>
            <Radio.Group
              value={newAssetType}
              onChange={e => setNewAssetType(e.target.value)}
              style={{ marginTop: 8 }}
            >
              {ASSET_TYPE_TABS.filter(t => t.key !== 'all').map(t => (
                <Radio key={t.key} value={t.key}>{t.label}</Radio>
              ))}
            </Radio.Group>
          </div>

          {/* 内容类型 */}
          <div>
            <Text>内容类型:</Text>
            <Radio.Group
              value={newContentType}
              onChange={e => setNewContentType(e.target.value)}
              style={{ marginTop: 8 }}
            >
              <Radio value="">无</Radio>
              {CONTENT_TYPE_OPTIONS.filter(o => o.key !== '').map(o => (
                <Radio key={o.key} value={o.key}>{o.label}</Radio>
              ))}
            </Radio.Group>
          </div>

          {/* 尺寸选择（仅 AI 生成模式） */}
          {createMode === 'generate' && (
            <div>
              <Text>分辨率:</Text>
              <Select
                value={newSize}
                onChange={setNewSize}
                style={{ width: '100%', marginTop: 4 }}
                options={SIZE_OPTIONS.map(o => ({ value: o.key, label: o.label }))}
              />
            </div>
          )}

          {/* 资产名称 */}
          <div>
            <Text>资产名称:</Text>
            <Input
              value={newAssetName}
              onChange={e => setNewAssetName(e.target.value)}
              placeholder="请输入资产名称"
              style={{ marginTop: 4 }}
            />
          </div>
        </Space>
      </Modal>

      {/* 三视图 iframe 弹窗 */}
      <Modal
        title="三视图生成器 (Infinite-Canvas)"
        open={angleIframeOpen}
        onCancel={() => setAngleIframeOpen(false)}
        width="90vw"
        style={{ top: 20 }}
        footer={null}
        destroyOnHidden
      >
        <div style={{ height: '75vh' }}>
          <IframeEmbed
            src="/static/director/angle.html"
            title="Angle View Generator"
            style={{ height: '100%' }}
            onMessage={(data) => {
              if (data.type === 'asset-created') {
                loadAssets()
              }
            }}
          />
        </div>
      </Modal>

      {/* 提取结果弹窗（三视图/姿态/线稿/深度图） */}
      <Modal
        title={extractionLabel}
        open={extractionModalOpen}
        onCancel={() => { setExtractionModalOpen(false); setExtractionResult(null) }}
        width={720}
        footer={null}
        destroyOnHidden
      >
        {extractionLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>
              <ClockCircleOutlined spin />
            </div>
            <Text type="secondary" style={{ fontSize: 16 }}>
              {extractionLabel} 生成中...
            </Text>
            <div style={{ marginTop: 12 }}>
              <Text style={{ fontSize: 24, fontWeight: 'bold', color: '#1677ff' }}>
                {extractionElapsed}
              </Text>
              <Text type="secondary"> 秒</Text>
            </div>
          </div>
        ) : extractionResult ? (
          <Space direction="vertical" style={{ width: '100%' }} size="middle">
            {extractionResult.urls?.slice(-1).map((url: string, i: number) => (
              <div key={i} style={{ position: 'relative' }}>
                <Image
                  src={url}
                  alt={`${extractionResult.name}`}
                  style={{ width: '100%', borderRadius: 8 }}
                />
                <Button
                  type="text"
                  icon={<DownloadOutlined />}
                  size="small"
                  style={{ position: 'absolute', top: 4, right: 4, background: 'rgba(0,0,0,0.45)', color: '#fff', borderRadius: 4, border: 'none', zIndex: 1 }}
                  onClick={() => downloadImage(url, extractionResult.name)}
                />
              </div>
            ))}
            <div style={{ textAlign: 'center' }}>
              <Text type="secondary">
                耗时 {extractionElapsed} 秒 | {extractionResult.urls?.length || 0} 张
              </Text>
            </div>
            <div style={{ textAlign: 'center' }}>
              <Button type="primary" onClick={() => { setExtractionModalOpen(false); setExtractionResult(null) }}>
                完成
              </Button>
            </div>
          </Space>
        ) : null}
      </Modal>
    </div>
  )
}
