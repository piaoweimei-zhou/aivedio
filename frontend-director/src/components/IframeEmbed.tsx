import { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { Spin, message } from 'antd'

interface IframeEmbedProps {
  src: string
  title: string
  style?: React.CSSProperties
  onMessage?: (data: any) => void
}

/**
 * 通用 iframe 嵌入组件
 * 用于嵌入 canvas.html / angle.html 等 Infinite-Canvas 页面
 *
 * 安全：消息通信验证 origin，postMessage 使用精确目标源
 */
export default function IframeEmbed({ src, title, style, onMessage }: IframeEmbedProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [loading, setLoading] = useState(true)

  // 从 src 推导允许的 origin
  const allowedOrigin = useMemo(() => {
    try {
      return new URL(src, window.location.origin).origin
    } catch {
      return window.location.origin
    }
  }, [src])

  // 监听 iframe 发来的消息（验证 origin）
  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.origin !== allowedOrigin) return
      if (e.data && onMessage) {
        onMessage(e.data)
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [onMessage, allowedOrigin])

  // 向 iframe 发送消息（使用精确目标源）
  const postMessage = useCallback((data: any) => {
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.postMessage(data, allowedOrigin)
    }
  }, [allowedOrigin])

  const handleLoad = useCallback(() => {
    setLoading(false)
  }, [])

  return (
    <div style={{ position: 'relative', ...style }}>
      {loading && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: '#f5f5f5', zIndex: 1,
        }}>
          <Spin /><span style={{ marginLeft: 12, color: '#999' }}>加载 {title}...</span>
        </div>
      )}
      <iframe
        ref={iframeRef}
        src={src}
        title={title}
        onLoad={handleLoad}
        style={{
          width: '100%',
          height: '100%',
          border: 'none',
          borderRadius: 8,
        }}
        sandbox="allow-scripts allow-same-origin allow-popups allow-forms allow-modals"
      />
    </div>
  )
}
