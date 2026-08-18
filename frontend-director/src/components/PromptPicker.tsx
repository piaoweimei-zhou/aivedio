import { useEffect, useState } from 'react'
import { Modal, Select, Input, Button, Space, message, Divider, Tag } from 'antd'
import { ThunderboltOutlined, SearchOutlined } from '@ant-design/icons'
import { promptService, PromptEntry } from '../services/directorApi'

const { TextArea } = Input

interface PromptPickerProps {
  open: boolean
  onClose: () => void
  onSelect: (result: {
    prompt_id: string
    content: string
    resolved: string
    variables: Record<string, string>
  }) => void
  stageId?: string
  projectId?: string
  initialContent?: string
}

/**
 * 提示词选择器
 *
 * 从提示词库中选择一个提示词，填入变量值，解析后返回最终 prompt 字符串。
 * 供各生成页（概念图/分镜/视频）使用。
 *
 * 使用方式：
 * <PromptPicker
 *   open={pickerOpen}
 *   onClose={() => setPickerOpen(false)}
 *   onSelect={({ resolved }) => setPrompt(resolved)}
 *   stageId="video"
 * />
 */
export default function PromptPicker({
  open,
  onClose,
  onSelect,
  stageId,
  projectId,
  initialContent,
}: PromptPickerProps) {
  const [prompts, setPrompts] = useState<PromptEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedId, setSelectedId] = useState<string>('')
  const [selectedPrompt, setSelectedPrompt] = useState<PromptEntry | null>(null)
  const [variables, setVariables] = useState<Record<string, string>>({})
  const [resolved, setResolved] = useState<string>('')
  const [keyword, setKeyword] = useState<string>('')

  const loadPrompts = async () => {
    setLoading(true)
    try {
      const res = await promptService.list({
        project_id: projectId || '',
        stage_id: stageId || '',
        keyword,
      })
      setPrompts(res.prompts || [])
    } catch (e: any) {
      message.error('加载提示词失败: ' + (e.message || e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      loadPrompts()
      setSelectedId('')
      setSelectedPrompt(null)
      setVariables({})
      setResolved('')
    }
  }, [open, stageId, projectId, keyword])

  const handleSelect = (promptId: string) => {
    setSelectedId(promptId)
    const prompt = prompts.find(p => p.prompt_id === promptId)
    setSelectedPrompt(prompt || null)
    // 初始化变量默认值
    if (prompt) {
      const vars: Record<string, string> = {}
      prompt.variables.forEach(v => {
        vars[v.name] = v.default || ''
      })
      setVariables(vars)
    }
    setResolved('')
  }

  const handleResolve = async () => {
    if (!selectedPrompt) return
    try {
      const res = await promptService.resolve(selectedPrompt.prompt_id, variables)
      setResolved(res.resolved)
    } catch (e: any) {
      message.error('解析失败: ' + (e.message || e))
    }
  }

  const handleConfirm = () => {
    if (!selectedPrompt) {
      message.warning('请先选择一个提示词')
      return
    }
    if (selectedPrompt.variables.some(v => v.required && !variables[v.name])) {
      message.warning('请填写所有必填变量')
      return
    }
    // 若未解析，先解析一次
    const finalResolved = resolved || selectedPrompt.content
    onSelect({
      prompt_id: selectedPrompt.prompt_id,
      content: selectedPrompt.content,
      resolved: finalResolved,
      variables,
    })
    onClose()
  }

  return (
    <Modal
      title="从提示词库选择"
      open={open}
      onCancel={onClose}
      onOk={handleConfirm}
      width={680}
      okText="使用此提示词"
      cancelText="取消"
    >
      {/* 搜索栏 */}
      <Space style={{ marginBottom: 12, width: '100%' }}>
        <Input
          style={{ width: 300 }}
          placeholder="搜索提示词"
          prefix={<SearchOutlined />}
          allowClear
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
        />
        <Button icon={<ThunderboltOutlined />} onClick={loadPrompts} loading={loading}>
          刷新
        </Button>
      </Space>

      {/* 提示词选择 */}
      <Select
        style={{ width: '100%', marginBottom: 12 }}
        placeholder="选择一个提示词"
        showSearch
        value={selectedId || undefined}
        onChange={handleSelect}
        filterOption={false}
        loading={loading}
        options={prompts.map(p => ({
          value: p.prompt_id,
          label: `${p.name}${p.quality_score >= 4 ? ' ★' : ''}${p.tags.length > 0 ? ` [${p.tags.join(',')}]` : ''}`,
        }))}
      />

      {/* 选中提示词详情 */}
      {selectedPrompt && (
        <div>
          <Divider />
          <div style={{ marginBottom: 8 }}>
            <strong>提示词内容：</strong>
            {selectedPrompt.tags.map(t => (
              <Tag key={t} color="cyan" style={{ marginLeft: 4 }}>{t}</Tag>
            ))}
          </div>
          <div style={{
            background: '#f5f5f5',
            padding: 12,
            borderRadius: 4,
            marginBottom: 12,
            whiteSpace: 'pre-wrap',
          }}>
            {selectedPrompt.content}
          </div>

          {/* 变量填写 */}
          {selectedPrompt.variables.length > 0 && (
            <>
              <strong>变量值：</strong>
              <div style={{ marginTop: 8 }}>
                {selectedPrompt.variables.map(v => (
                  <Space key={v.name} style={{ display: 'flex', marginBottom: 8 }} align="center">
                    <Tag color="blue" style={{ minWidth: 100 }}>{`{${v.name}}`}</Tag>
                    <Input
                      style={{ width: 300 }}
                      placeholder={v.description || `请输入 ${v.name}`}
                      value={variables[v.name] || ''}
                      onChange={e => setVariables({
                        ...variables,
                        [v.name]: e.target.value,
                      })}
                    />
                    {v.required && <Tag color="red">必填</Tag>}
                  </Space>
                ))}
              </div>
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                onClick={handleResolve}
                style={{ marginTop: 8 }}
              >
                解析预览
              </Button>
            </>
          )}

          {/* 解析结果 */}
          {resolved && (
            <>
              <Divider />
              <strong>解析结果：</strong>
              <div style={{
                background: '#e6f7ff',
                padding: 12,
                borderRadius: 4,
                marginTop: 8,
                border: '1px solid #91d5ff',
                whiteSpace: 'pre-wrap',
              }}>
                {resolved}
              </div>
            </>
          )}
        </div>
      )}

      {/* 当前内容（若传入） */}
      {initialContent && !selectedPrompt && (
        <div style={{ marginTop: 12 }}>
          <Divider />
          <strong>当前提示词：</strong>
          <div style={{
            background: '#fafafa',
            padding: 12,
            borderRadius: 4,
            marginTop: 8,
            whiteSpace: 'pre-wrap',
          }}>
            {initialContent}
          </div>
        </div>
      )}
    </Modal>
  )
}
