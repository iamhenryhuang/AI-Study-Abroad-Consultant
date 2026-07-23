const API_BASE = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '')

// 純函式：把累積的字串 buffer 切成一個個 SSE 事件物件。
// 後端格式為 `data: {json}\n\n`，尾端不完整的一段留在 rest 等下次補齊。
export function parseSSE(buffer) {
  const events = []
  let idx
  while ((idx = buffer.indexOf('\n\n')) !== -1) {
    const frame = buffer.slice(0, idx)
    buffer = buffer.slice(idx + 2)
    const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
    if (dataLine) {
      const json = dataLine.slice(5).trim()   // 去掉 "data:" 前綴
      try {
        events.push(JSON.parse(json))
      } catch {
        // 忽略解析失敗的畸形 frame
      }
    }
  }
  return { events, rest: buffer }
}

export async function streamChat(payload, onEvent, signal) {
  const response = await fetch(`${API_BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new Error('伺服器連線失敗，請稍後再試')
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const { events, rest } = parseSSE(buffer)
    buffer = rest
    for (const event of events) onEvent(event)
  }
}
