import { useState, useCallback, useMemo } from 'react'
import { Typography, Card, Select, Button, Space, Input, InputNumber, Row, Col, message, Table, Tag, Empty } from 'antd'
import { ScissorOutlined, MergeCellsOutlined, ReloadOutlined, PlayCircleOutlined } from '@ant-design/icons'
import { useDirectorStore } from '../stores/directorStore'
import Timeline, { TimelineClip } from '../components/Timeline'

const { Title, Text } = Typography

const EDIT_MODES = [
  { label: '拼接 (Concat)', value: 'concat' },
  { label: '裁剪 (Trim)', value: 'trim' },
  { label: '时间线编排 (Timeline)', value: 'timeline' },
  { label: '卡点剪辑 (Beat)', value: 'beat' },
]

const BEAT_TEMPLATES = [
  {
    id: 'energetic',
    name: '燃点快切',
    desc: '120 BPM 快速卡点 · 滑动转场 · 呼啸音效',
    bpm: 120,
    beats_per_cut: 2,
    transition: 'slideleft',
    sfx: 'whoosh',
    target_duration: 30,
  },
  {
    id: 'rhythm',
    name: '节奏卡点',
    desc: '80 BPM 中速卡点 · 淡入淡出转场 · 重击音效',
    bpm: 80,
    beats_per_cut: 2,
    transition: 'fade',
    sfx: 'hit',
    target_duration: 30,
  },
  {
    id: 'emotional',
    name: '情绪慢卡',
    desc: '40 BPM 慢速卡点 · 黑场转场 · 柔和音效',
    bpm: 40,
    beats_per_cut: 1,
    transition: 'fadeblack',
    sfx: 'soft',
    target_duration: 30,
  },
]

export default function EditPage() {
  const { assets, loadAssets, selectedAssetIds, executeStage } = useDirectorStore()
  const [mode, setMode] = useState('concat')
  const [beatTemplate, setBeatTemplate] = useState(BEAT_TEMPLATES[0])
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(0)
  const [outputName, setOutputName] = useState('剪辑视频')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<any[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const videoAssets = useMemo(() => assets.filter(a => a.asset_type === 'video'), [assets])

  // 时间线片段
  const timelineClips: TimelineClip[] = useMemo(() => {
    let frameOffset = 0
    return selectedAssetIds.map((id) => {
      const asset = assets.find(a => a.asset_id === id)
      const duration = asset?.metadata?.duration || 5
      const startFrame = frameOffset
      const endFrame = frameOffset + Math.round(duration * 24)
      frameOffset = endFrame
      return {
        id,
        name: asset?.name || id.slice(0, 8),
        url: asset?.urls?.[0] || '',
        duration,
        startFrame,
        endFrame,
      }
    })
  }, [selectedAssetIds, assets])

  // 预览选中视频
  const handlePreview = useCallback((url: string) => {
    setPreviewUrl(url)
  }, [])

  const handleEdit = useCallback(async () => {
    if (selectedAssetIds.length === 0) {
      message.warning('请先在资产库中选择视频资产')
      return
    }
    setLoading(true)
    try {
      const params: any = { mode, name: outputName }
      if (mode === 'trim') {
        params.start = start
        params.end = end
      } else if (mode === 'beat') {
        Object.assign(params, {
          bpm: beatTemplate.bpm,
          beats_per_cut: beatTemplate.beats_per_cut,
          transition: beatTemplate.transition,
          sfx: beatTemplate.sfx,
          target_duration: beatTemplate.target_duration,
        })
      }
      const result = await executeStage({
        stage_id: 'edit',
        input_asset_ids: selectedAssetIds,
        provider_id: 'local',
        params,
      })
      if (result?.success) {
        message.success('剪辑完成')
        setResults(prev => [result, ...prev])
        loadAssets()
      } else {
        message.error(result?.error || '剪辑失败')
      }
    } catch (e: any) {
      message.error(e.message || '剪辑失败')
    } finally {
      setLoading(false)
    }
  }, [selectedAssetIds, mode, start, end, outputName, beatTemplate, executeStage, loadAssets])

  const columns = [
    { title: '资产ID', dataIndex: ['asset', 'asset_id'], key: 'id', width: 120, render: (v: string) => <Text copyable style={{ fontSize: 11 }}>{v?.slice(0, 12)}</Text> },
    { title: '名称', dataIndex: ['asset', 'name'], key: 'name', width: 120 },
    { title: '模式', key: 'mode', width: 70, render: (_: any, r: any) => <Tag>{r.asset?.metadata?.mode || mode}</Tag> },
    {
      title: '操作', key: 'action', width: 120,
      render: (_: any, r: any) => {
        const url = r.asset?.metadata?.video_url || (r.asset?.urls || [])[0]
        return url ? (
          <Space>
            <Button size="small" icon={<PlayCircleOutlined />} onClick={() => handlePreview(url)}>预览</Button>
            <a href={url} target="_blank" rel="noreferrer">下载</a>
          </Space>
        ) : '-'
      }
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      <Title level={3}>视频剪辑</Title>

      <Row gutter={24} style={{ flex: 1 }}>
        {/* 左侧：设置 + 时间线 */}
        <Col xs={24} lg={10}>
          <Card title="剪辑设置" style={{ marginBottom: 16 }}>
            {selectedAssetIds.length > 0 && (
              <div style={{ marginBottom: 16, padding: '8px 12px', background: '#e6f7ff', borderRadius: 6 }}>
                <Text type="secondary">已选择 {selectedAssetIds.length} 个视频资产</Text>
              </div>
            )}

            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <div>
                <Text>剪辑模式:</Text>
                <Select value={mode} onChange={setMode} options={EDIT_MODES} style={{ width: '100%', marginTop: 4 }} />
              </div>

              {mode === 'trim' && (
                <Row gutter={16}>
                  <Col span={12}>
                    <Text>开始(秒):</Text>
                    <InputNumber min={0} value={start} onChange={v => setStart(v || 0)} style={{ width: '100%', marginTop: 4 }} />
                  </Col>
                  <Col span={12}>
                    <Text>结束(秒):</Text>
                    <InputNumber min={0} value={end} onChange={v => setEnd(v || 0)} style={{ width: '100%', marginTop: 4 }} />
                  </Col>
                </Row>
              )}

              {mode === 'beat' && (
                <div>
                  <Text>卡点模板:</Text>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 4 }}>
                    {BEAT_TEMPLATES.map(t => (
                      <div
                        key={t.id}
                        onClick={() => setBeatTemplate(t)}
                        style={{
                          padding: '8px 12px',
                          borderRadius: 6,
                          cursor: 'pointer',
                          border: `1px solid ${beatTemplate.id === t.id ? '#1677ff' : '#d9d9d9'}`,
                          background: beatTemplate.id === t.id ? '#e6f4ff' : '#fff',
                        }}
                      >
                        <Text strong>{t.name}</Text>
                        <div><Text type="secondary" style={{ fontSize: 12 }}>{t.desc}</Text></div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div>
                <Text>输出名称:</Text>
                <Input value={outputName} onChange={e => setOutputName(e.target.value)} style={{ marginTop: 4 }} />
              </div>

              <Button
                type="primary"
                icon={mode === 'concat' ? <MergeCellsOutlined /> : mode === 'beat' ? <PlayCircleOutlined /> : <ScissorOutlined />}
                onClick={handleEdit}
                loading={loading}
                disabled={selectedAssetIds.length === 0}
                block
              >
                {mode === 'concat' ? '拼接视频' : mode === 'trim' ? '裁剪视频' : mode === 'beat' ? `卡点剪辑 · ${beatTemplate.name}` : '执行时间线编排'}
              </Button>
            </Space>
          </Card>

          {/* 可视化时间线 */}
          {mode === 'timeline' && timelineClips.length > 0 && (
            <Card title="时间线" size="small">
              <div style={{ height: 160 }}>
                <Timeline
                  clips={timelineClips}
                  fps={24}
                  onClipClick={(clipId) => {
                    const clip = timelineClips.find(c => c.id === clipId)
                    if (clip?.url) handlePreview(clip.url)
                  }}
                  onClipSplit={(clipId, frame) => {
                    message.info(`分割片段 ${clipId.slice(0, 8)} 在帧 ${frame}`)
                  }}
                  onClipDelete={(clipId) => {
                    useDirectorStore.getState().toggleAssetSelection(clipId)
                  }}
                />
              </div>
            </Card>
          )}

          {/* 素材选择 */}
          {videoAssets.length > 0 && (
            <Card title="素材列表" size="small" style={{ marginTop: 16 }}>
              <Space wrap>
                {videoAssets.slice(0, 8).map(v => (
                  <Button
                    key={v.asset_id}
                    size="small"
                    type={selectedAssetIds.includes(v.asset_id) ? 'primary' : 'default'}
                    onClick={() => useDirectorStore.getState().toggleAssetSelection(v.asset_id)}
                    style={{ maxWidth: 160 }}
                  >
                    {v.name || v.asset_id.slice(0, 8)}
                  </Button>
                ))}
                {videoAssets.length > 8 && <Text type="secondary">+{videoAssets.length - 8}个</Text>}
              </Space>
            </Card>
          )}
        </Col>

        {/* 右侧：预览 + 结果 */}
        <Col xs={24} lg={14}>
          {/* 视频预览 */}
          {previewUrl && (
            <Card title="预览" size="small" style={{ marginBottom: 16 }}>
              <video
                src={previewUrl}
                controls
                style={{ width: '100%', maxHeight: 300, borderRadius: 8 }}
                onError={() => setPreviewUrl(null)}
              />
            </Card>
          )}

          <Card
            title="剪辑结果"
            extra={
              <Space>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => loadAssets()}>刷新</Button>
              </Space>
            }
          >
            {results.length === 0 ? (
              <Empty description="暂无剪辑结果" />
            ) : (
              <Table dataSource={results} columns={columns} rowKey={(_, i) => String(i)} size="small" pagination={false} />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
