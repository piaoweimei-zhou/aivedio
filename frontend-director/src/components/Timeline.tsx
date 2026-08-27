import { useEffect, useRef, useState, useCallback } from 'react'
import { Typography, Button, Slider, message } from 'antd'
import {
  PlayCircleOutlined,
  PauseCircleOutlined,
  StepForwardOutlined,
  StepBackwardOutlined,
  ScissorOutlined,
} from '@ant-design/icons'

const { Text } = Typography

export interface TimelineClip {
  id: string
  name: string
  url: string
  duration: number   // 秒
  startFrame: number
  endFrame: number
}

interface TimelineProps {
  clips: TimelineClip[]
  fps?: number
  onClipClick?: (clipId: string) => void
  onClipMove?: (clipId: string, newStartFrame: number) => void
  onClipSplit?: (clipId: string, frame: number) => void
  onClipDelete?: (clipId: string) => void
}

export default function Timeline({
  clips,
  fps = 24,
  onClipClick,
  onClipSplit,
}: TimelineProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [currentFrame, setCurrentFrame] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [zoom, setZoom] = useState(1)
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null)
  const playTimerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // 总帧数
  const totalFrames = clips.reduce((max, c) => Math.max(max, c.endFrame), 0) || 100

  // 帧宽（像素/帧）
  const frameWidth = 2 * zoom

  // 播放/暂停
  const togglePlay = useCallback(() => {
    if (playing) {
      if (playTimerRef.current) clearInterval(playTimerRef.current)
      setPlaying(false)
    } else {
      setPlaying(true)
      playTimerRef.current = setInterval(() => {
        setCurrentFrame(f => {
          if (f >= totalFrames - 1) {
            setPlaying(false)
            if (playTimerRef.current) clearInterval(playTimerRef.current)
            return f
          }
          return f + 1
        })
      }, 1000 / fps)
    }
  }, [playing, fps, totalFrames])

  // 清理
  useEffect(() => {
    return () => {
      if (playTimerRef.current) clearInterval(playTimerRef.current)
    }
  }, [])

  // 点击时间轴
  const handleTimelineClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current) return
    const rect = containerRef.current.getBoundingClientRect()
    const x = e.clientX - rect.left
    const frame = Math.floor(x / frameWidth)
    setCurrentFrame(Math.max(0, Math.min(frame, totalFrames - 1)))
  }

  // 点击片段
  const handleClipClick = (clipId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelectedClipId(clipId)
    onClipClick?.(clipId)
  }

  // 分割片段
  const handleSplit = () => {
    if (!selectedClipId) {
      message.warning('请先选择一个片段')
      return
    }
    onClipSplit?.(selectedClipId, currentFrame)
  }

  const clipColors = ['#1677ff', '#52c41a', '#faad14', '#722ed1', '#eb2f96', '#13c2c2']

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#1a1a2e', color: '#fff', borderRadius: 8, overflow: 'hidden' }}>
      {/* 工具栏 */}
      <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 12, borderBottom: '1px solid #333' }}>
        <Button
          type="text"
          icon={playing ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
          onClick={togglePlay}
          style={{ color: '#fff' }}
        />
        <Button
          type="text"
          icon={<StepBackwardOutlined />}
          onClick={() => setCurrentFrame(f => Math.max(0, f - fps))}
          style={{ color: '#fff' }}
        />
        <Button
          type="text"
          icon={<StepForwardOutlined />}
          onClick={() => setCurrentFrame(f => Math.min(totalFrames - 1, f + fps))}
          style={{ color: '#fff' }}
        />
        <Button
          type="text"
          icon={<ScissorOutlined />}
          onClick={handleSplit}
          style={{ color: selectedClipId ? '#faad14' : '#666' }}
        />
        <Text style={{ color: '#ccc', fontSize: 12, minWidth: 120 }}>
          帧: {currentFrame} / {totalFrames} | {Math.floor(currentFrame / fps)}s
        </Text>
        <Text style={{ color: '#999', fontSize: 12 }}>缩放:</Text>
        <Slider
          min={0.5}
          max={4}
          step={0.1}
          value={zoom}
          onChange={setZoom}
          style={{ width: 100, margin: 0 }}
        />
      </div>

      {/* 时间轴 */}
      <div
        ref={containerRef}
        style={{ flex: 1, position: 'relative', overflowX: 'auto', cursor: 'crosshair' }}
        onClick={handleTimelineClick}
      >
        {/* 刻度 */}
        <div style={{ height: 20, position: 'relative', borderBottom: '1px solid #333' }}>
          {Array.from({ length: Math.ceil(totalFrames / fps) + 1 }, (_, i) => (
            <div
              key={i}
              style={{
                position: 'absolute',
                left: i * fps * frameWidth,
                top: 0,
                height: 20,
                borderLeft: '1px solid #444',
                paddingLeft: 2,
                fontSize: 10,
                color: '#888',
                lineHeight: '20px',
              }}
            >
              {i}s
            </div>
          ))}
        </div>

        {/* 片段轨道 */}
        <div style={{ position: 'relative', height: 48, marginTop: 4 }}>
          {clips.map((clip, idx) => {
            const left = clip.startFrame * frameWidth
            const width = (clip.endFrame - clip.startFrame) * frameWidth
            const color = clipColors[idx % clipColors.length]
            const isSelected = clip.id === selectedClipId
            return (
              <div
                key={clip.id}
                onClick={(e) => handleClipClick(clip.id, e)}
                style={{
                  position: 'absolute',
                  left,
                  top: 4,
                  width: Math.max(width, 20),
                  height: 40,
                  background: color,
                  opacity: isSelected ? 1 : 0.7,
                  borderRadius: 4,
                  border: isSelected ? '2px solid #fff' : '1px solid rgba(255,255,255,0.2)',
                  display: 'flex',
                  alignItems: 'center',
                  padding: '0 8px',
                  fontSize: 11,
                  color: '#fff',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                  cursor: 'pointer',
                  transition: 'opacity 0.15s',
                }}
              >
                {clip.name}
              </div>
            )
          })}
        </div>

        {/* 播放头 */}
        <div
          style={{
            position: 'absolute',
            left: currentFrame * frameWidth,
            top: 0,
            bottom: 0,
            width: 2,
            background: '#ff4d4f',
            pointerEvents: 'none',
            zIndex: 10,
          }}
        >
          <div style={{
            width: 0,
            height: 0,
            borderLeft: '6px solid transparent',
            borderRight: '6px solid transparent',
            borderTop: '8px solid #ff4d4f',
            position: 'relative',
            left: -5,
          }} />
        </div>
      </div>
    </div>
  )
}
