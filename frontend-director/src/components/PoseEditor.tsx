/**
 * PoseEditor - 模板Pose交互式修正画布
 *
 * 功能：
 * - 显示Pose骨架图
 * - 黑色画笔覆盖错误关节
 * - 白色画笔画正确位置
 * - 导出修正后的Pose图
 */

import React, { useRef, useState, useCallback, useEffect } from 'react'
import { Button, Space, Tooltip, Slider, message } from 'antd'
import {
  EditOutlined,
  UndoOutlined,
  RedoOutlined,
  SaveOutlined,
  ClearOutlined,
  BgColorsOutlined,
} from '@ant-design/icons'

interface PoseEditorProps {
  imageUrl: string
  onSave?: (dataUrl: string) => void
  width?: number
  height?: number
  /** 是否自动适配图片原始比例（默认 true） */
  autoAspect?: boolean
}

interface DrawAction {
  points: { x: number; y: number }[]
  color: string
  size: number
}

const PoseEditor: React.FC<PoseEditorProps> = ({
  imageUrl,
  onSave,
  width = 640,
  height = 640,
  autoAspect = true,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const bgCanvasRef = useRef<HTMLCanvasElement>(null)
  const [brushColor, setBrushColor] = useState<'white' | 'black'>('black')
  const [brushSize, setBrushSize] = useState(8)
  const [isDrawing, setIsDrawing] = useState(false)
  const [actions, setActions] = useState<DrawAction[]>([])
  const [redoStack, setRedoStack] = useState<DrawAction[]>([])
  // 使用 ref 存储 canvasSize，避免触发重渲染导致 canvas 被清空
  const canvasSize = useRef({ w: width, h: height })
  const currentAction = useRef<DrawAction | null>(null)
  // 标记是否需要重绘（由 actions 变化触发）
  const redrawFlag = useRef(0)

  const drawAction = (ctx: CanvasRenderingContext2D, action: DrawAction) => {
    if (action.points.length < 2) return
    ctx.beginPath()
    ctx.strokeStyle = action.color
    ctx.lineWidth = action.size
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    ctx.moveTo(action.points[0].x, action.points[0].y)
    for (let i = 1; i < action.points.length; i++) {
      ctx.lineTo(action.points[i].x, action.points[i].y)
    }
    ctx.stroke()
  }

  const doRedraw = useCallback(() => {
    const canvas = canvasRef.current
    const bgCanvas = bgCanvasRef.current
    if (!canvas || !bgCanvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    canvas.width = canvasSize.current.w
    canvas.height = canvasSize.current.h

    // 绘制背景
    ctx.drawImage(bgCanvas, 0, 0)

    // 重绘所有操作
    for (const action of actions) {
      drawAction(ctx, action)
    }
  }, [actions])

  // 当 actions 变化时自动重绘
  useEffect(() => {
    if (redrawFlag.current > 0) {
      redrawFlag.current = 0
      doRedraw()
    }
  }, [actions, doRedraw])

  // 加载背景图
  useEffect(() => {
    const bgCanvas = bgCanvasRef.current
    const canvas = canvasRef.current
    if (!bgCanvas || !canvas) return

    const img = new Image()
    img.onload = () => {
      // 计算适配尺寸：以 width 为基准，按图片比例计算高度
      let cw = width, ch = height
      if (autoAspect && img.naturalWidth && img.naturalHeight) {
        const imgRatio = img.naturalWidth / img.naturalHeight
        cw = width
        ch = Math.round(width / imgRatio)
      }

      // 设置画布尺寸并绘制背景
      bgCanvas.width = cw
      bgCanvas.height = ch
      const bgCtx = bgCanvas.getContext('2d')
      if (bgCtx) bgCtx.drawImage(img, 0, 0, cw, ch)

      canvas.width = cw
      canvas.height = ch
      const ctx = canvas.getContext('2d')
      if (ctx) ctx.drawImage(bgCanvas, 0, 0)

      canvasSize.current = { w: cw, h: ch }
    }
    img.onerror = () => {
      // 加载失败，使用黑色背景
      bgCanvas.width = width
      bgCanvas.height = height
      const bgCtx = bgCanvas.getContext('2d')
      if (bgCtx) {
        bgCtx.fillStyle = '#000'
        bgCtx.fillRect(0, 0, width, height)
      }
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d')
      if (ctx) ctx.drawImage(bgCanvas, 0, 0)
      canvasSize.current = { w: width, h: height }
      message.warning('图片加载失败，已使用黑色背景')
    }
    img.src = imageUrl
  }, [imageUrl, width, height, autoAspect])

  const getCanvasPos = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return { x: 0, y: 0 }
    const rect = canvas.getBoundingClientRect()
    const scaleX = canvas.width / rect.width
    const scaleY = canvas.height / rect.height
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    }
  }

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    setIsDrawing(true)
    const pos = getCanvasPos(e)
    currentAction.current = {
      points: [pos],
      color: brushColor,
      size: brushSize,
    }
    setRedoStack([])
  }

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing || !currentAction.current) return
    const pos = getCanvasPos(e)
    currentAction.current.points.push(pos)

    // 实时绘制
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const pts = currentAction.current.points
    if (pts.length >= 2) {
      ctx.beginPath()
      ctx.strokeStyle = currentAction.current.color
      ctx.lineWidth = currentAction.current.size
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.moveTo(pts[pts.length - 2].x, pts[pts.length - 2].y)
      ctx.lineTo(pts[pts.length - 1].x, pts[pts.length - 1].y)
      ctx.stroke()
    }
  }

  const handleMouseUp = () => {
    if (currentAction.current && currentAction.current.points.length > 1) {
      setActions(prev => [...prev, currentAction.current!])
    }
    currentAction.current = null
    setIsDrawing(false)
  }

  const handleUndo = () => {
    if (actions.length === 0) return
    const last = actions[actions.length - 1]
    setRedoStack(prev => [...prev, last])
    setActions(prev => prev.slice(0, -1))
    redrawFlag.current = 1
  }

  const handleRedo = () => {
    if (redoStack.length === 0) return
    const last = redoStack[redoStack.length - 1]
    setRedoStack(prev => prev.slice(0, -1))
    setActions(prev => [...prev, last])
    redrawFlag.current = 1
  }

  const handleClear = () => {
    setRedoStack([])
    setActions([])
    redrawFlag.current = 1
  }

  const handleSave = () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const dataUrl = canvas.toDataURL('image/png')
    onSave?.(dataUrl)
    message.success('Pose修正图已保存')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* 工具栏 */}
      <Space>
        <Tooltip title="黑色画笔（覆盖错误关节）">
          <Button
            type={brushColor === 'black' ? 'primary' : 'default'}
            icon={<EditOutlined />}
            onClick={() => setBrushColor('black')}
            style={brushColor === 'black' ? { background: '#000', borderColor: '#333' } : {}}
          >
            黑笔覆盖
          </Button>
        </Tooltip>
        <Tooltip title="白色画笔（画正确位置）">
          <Button
            type={brushColor === 'white' ? 'primary' : 'default'}
            icon={<BgColorsOutlined />}
            onClick={() => setBrushColor('white')}
          >
            白笔修正
          </Button>
        </Tooltip>
        <span style={{ marginLeft: 8 }}>笔刷大小:</span>
        <Slider
          min={2}
          max={30}
          value={brushSize}
          onChange={setBrushSize}
          style={{ width: 100 }}
        />
        <Button icon={<UndoOutlined />} onClick={handleUndo} disabled={actions.length === 0}>
          撤销
        </Button>
        <Button icon={<RedoOutlined />} onClick={handleRedo} disabled={redoStack.length === 0}>
          重做
        </Button>
        <Button icon={<ClearOutlined />} onClick={handleClear} disabled={actions.length === 0}>
          清除
        </Button>
        <Button type="primary" icon={<SaveOutlined />} onClick={handleSave}>
          保存
        </Button>
      </Space>

      {/* 画布 */}
      <div style={{ position: 'relative', border: '1px solid #ddd', borderRadius: 4, minHeight: 200 }}>
        <canvas ref={bgCanvasRef} style={{ display: 'none' }} />
        <canvas
          ref={canvasRef}
          width={width}
          height={height}
          style={{ cursor: 'crosshair', maxWidth: '100%', display: 'block', background: '#111' }}
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
        />
      </div>

      <div style={{ fontSize: 11, color: '#999' }}>
        黑色画笔覆盖错误关节 → 白色画笔画正确位置 → 保存后系统自动识别修正
      </div>
    </div>
  )
}

export default PoseEditor
