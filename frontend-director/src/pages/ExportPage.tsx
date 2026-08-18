import { useState, useCallback } from 'react'
import { Typography, Card, Select, Button, Space, Input, Row, Col, message, Table, Empty, Tag, InputNumber } from 'antd'
import { ExportOutlined, DownloadOutlined } from '@ant-design/icons'
import { useDirectorStore } from '../stores/directorStore'

const { Title, Text } = Typography

const FORMATS = [
  { label: 'MP4', value: 'mp4' },
  { label: 'WebM', value: 'webm' },
  { label: 'MOV', value: 'mov' },
  { label: 'AVI', value: 'avi' },
]

const CODECS = [
  { label: 'H.264 (libx264)', value: 'libx264' },
  { label: 'H.265 (libx265)', value: 'libx265' },
  { label: 'VP9 (libvpx-vp9)', value: 'libvpx-vp9' },
]

const RESOLUTIONS = [
  { label: '原始', value: '' },
  { label: '1920x1080', value: '1920x1080' },
  { label: '1280x720', value: '1280x720' },
  { label: '854x480', value: '854x480' },
]

const BITRATES = [
  { label: '自动', value: '' },
  { label: '2 Mbps', value: '2M' },
  { label: '5 Mbps', value: '5M' },
  { label: '10 Mbps', value: '10M' },
  { label: '20 Mbps', value: '20M' },
]

export default function ExportPage() {
  const { selectedAssetIds, executeStage, loadAssets } = useDirectorStore()
  const [format, setFormat] = useState('mp4')
  const [codec, setCodec] = useState('libx264')
  const [resolution, setResolution] = useState('')
  const [bitrate, setBitrate] = useState('')
  const [fps, setFps] = useState(0)
  const [outputName, setOutputName] = useState('成片')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<any[]>([])

  const handleExport = useCallback(async () => {
    if (selectedAssetIds.length === 0) {
      message.warning('请先在资产库中选择视频资产')
      return
    }
    setLoading(true)
    try {
      const result = await executeStage({
        stage_id: 'export',
        input_asset_ids: selectedAssetIds,
        provider_id: 'local',
        params: { format, codec, resolution, bitrate, fps, name: outputName },
      })
      if (result?.success) {
        message.success('导出完成')
        setResults(prev => [result, ...prev])
        loadAssets()
      } else {
        message.error(result?.error || '导出失败')
      }
    } catch (e: any) {
      message.error(e.message || '导出失败')
    } finally {
      setLoading(false)
    }
  }, [selectedAssetIds, format, codec, resolution, bitrate, fps, outputName, executeStage, loadAssets])

  const columns = [
    { title: '资产ID', dataIndex: ['asset', 'asset_id'], key: 'id', width: 140, render: (v: string) => <Text copyable style={{ fontSize: 12 }}>{v}</Text> },
    { title: '名称', dataIndex: ['asset', 'name'], key: 'name', width: 150 },
    { title: '格式', key: 'format', width: 80, render: (_: any, r: any) => <Tag>{r.asset?.metadata?.format || format}</Tag> },
    { title: '编码', key: 'codec', width: 100, render: (_: any, r: any) => <Tag>{r.asset?.metadata?.codec || codec}</Tag> },
    {
      title: '下载', key: 'download', render: (_: any, r: any) => {
        const url = r.asset?.metadata?.video_url || (r.asset?.urls || [])[0]
        return url
          ? <Button type="link" icon={<DownloadOutlined />} href={url} target="_blank">下载</Button>
          : '-'
      }
    },
  ]

  return (
    <div style={{ padding: 24, height: '100%', overflow: 'auto' }}>
      <Title level={3}>成片导出</Title>

      <Row gutter={24}>
        <Col xs={24} lg={10}>
          <Card title="导出设置" style={{ marginBottom: 24 }}>
            {selectedAssetIds.length > 0 && (
              <div style={{ marginBottom: 16, padding: '8px 12px', background: '#f6ffed', borderRadius: 6 }}>
                <Text type="secondary">已选择 {selectedAssetIds.length} 个视频资产</Text>
              </div>
            )}

            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <div>
                <Text>输出格式:</Text>
                <Select value={format} onChange={setFormat} options={FORMATS} style={{ width: '100%', marginTop: 4 }} />
              </div>
              <div>
                <Text>视频编码:</Text>
                <Select value={codec} onChange={setCodec} options={CODECS} style={{ width: '100%', marginTop: 4 }} />
              </div>
              <div>
                <Text>分辨率:</Text>
                <Select value={resolution} onChange={setResolution} options={RESOLUTIONS} style={{ width: '100%', marginTop: 4 }} />
              </div>
              <div>
                <Text>码率:</Text>
                <Select value={bitrate} onChange={setBitrate} options={BITRATES} style={{ width: '100%', marginTop: 4 }} />
              </div>
              <Row gutter={16}>
                <Col span={12}>
                  <Text>帧率(fps):</Text>
                  <InputNumber min={0} max={120} value={fps} onChange={v => setFps(v || 0)} style={{ width: '100%', marginTop: 4 }} placeholder="0=原始" />
                </Col>
                <Col span={12}>
                  <Text>输出名称:</Text>
                  <Input value={outputName} onChange={e => setOutputName(e.target.value)} style={{ marginTop: 4 }} />
                </Col>
              </Row>

              <Button
                type="primary"
                icon={<ExportOutlined />}
                onClick={handleExport}
                loading={loading}
                disabled={selectedAssetIds.length === 0}
                block
              >
                导出成片
              </Button>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title="导出记录">
            {results.length === 0 ? (
              <Empty description="暂无导出记录" />
            ) : (
              <Table dataSource={results} columns={columns} rowKey={(_, i) => String(i)} size="small" pagination={false} />
            )}
          </Card>
        </Col>
      </Row>
    </div>
  )
}
