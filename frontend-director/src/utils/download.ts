import { message } from 'antd'

/**
 * 下载图片到本地
 * @param url 图片URL
 * @param filename 保存的文件名（不含扩展名）
 */
export function downloadImage(url: string, filename?: string): void {
  if (!url) return

  const ext = url.split('.').pop()?.split('?')[0] || 'png'
  const name = filename ? `${filename}.${ext}` : `image_${Date.now()}.${ext}`

  // 先尝试 fetch 下载（支持同源和 CORS 的图片）
  fetch(url, { mode: 'cors' })
    .then(res => {
      if (!res.ok) throw new Error('Network response was not ok')
      return res.blob()
    })
    .then(blob => {
      const blobUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = name
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    })
    .catch(() => {
      // fetch 失败时（跨域无 CORS），改用新标签页打开让用户手动保存
      window.open(url, '_blank')
      message.info('已在新标签页打开图片，请右键另存为')
    })
}
