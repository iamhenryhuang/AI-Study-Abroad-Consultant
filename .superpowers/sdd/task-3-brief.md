# Task 3 Brief — 前端 SSE 串流模組 chatApi.js

多輪聊天功能的第三個 task：新增前端與後端 `/api/chat` 溝通的 SSE 串流模組。只新增一個檔案：`frontend/src/chatApi.js`。

## Global Constraints
- 前端 API_BASE 慣例：`(import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '')`（空字串走 Vite proxy 到 8000）。
- 後端 SSE 格式：標準 `data: {json}\n\n`。
- 不能用原生 `EventSource`（僅 GET），須 `fetch` POST + `ReadableStream` 手動解析。
- 環境：Windows。用 Bash 工具跑 git/node 若找不到，改用 PowerShell 工具。
- 只新增 `frontend/src/chatApi.js`，不要動其他檔案。

## Files
- Create: `frontend/src/chatApi.js`

## Interface（後續 Task 4 依賴）
- `parseSSE(buffer: string) -> { events: object[], rest: string }`——純函式，以 `\n\n` 切 SSE frame、去 `data:` 前綴、`JSON.parse`；不完整尾段留在 `rest`。
- `streamChat(payload: object, onEvent: (event) => void, signal?: AbortSignal) -> Promise<void>`——POST `/api/chat`，逐事件呼叫 `onEvent`。

## Step 1: 寫實作 `frontend/src/chatApi.js`

```js
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
```

## Step 2: 驗證純解析函式（node 片段）
Run（專案根目錄）:
```bash
node --input-type=module -e "import('./frontend/src/chatApi.js').then(({parseSSE})=>{const {events,rest}=parseSSE('data: {\"type\":\"thinking\",\"step\":1}\n\ndata: {\"type\":\"answer\",\"text\":\"hi\"}\n\ndata: {\"type\":\"part');console.log('events',events.length,'types',events.map(e=>e.type).join(','),'rest?',rest.length>0)})"
```
Expected: `events 2 types thinking,answer rest? true`

（若 node 對 `--input-type` 或 dynamic import 有問題，可改寫等價的 node 驗證方式，只要能證明 parseSSE 正確切出 2 個事件、保留不完整尾段即可；把實際用的指令與輸出寫進報告。）

## Step 3: Commit
```bash
git add frontend/src/chatApi.js
git commit -m "feat: add SSE streaming client for chat"
```
（Bash git 不可用時改用 PowerShell。）
