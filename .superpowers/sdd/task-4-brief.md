# Task 4 Brief — 前端聊天頁 ChatPage + 路由 + 樣式

多輪聊天功能的最後一個 task：新增全螢幕聊天頁、接上路由與導覽、加樣式。

## Global Constraints
- Task 3 已提供 `frontend/src/chatApi.js`，export：`streamChat(payload, onEvent, signal)`、`parseSSE(buffer)`。
- 後端 SSE 事件：`thinking` / `tool_call` / `tool_result` / `answer_chunk` / `answer` / `error`。
- 不持久化：對話僅存 React state。
- 環境：Windows。Bash 找不到 git/npm 時改用 PowerShell。Node v24 + npm 已裝（Windows）。
- 沿用現有前端慣例（lucide-react、className 風格）。

## Files
- Create: `frontend/src/ChatPage.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`（檔尾追加）

## Step 1: 建立 `frontend/src/ChatPage.jsx`

```jsx
import { useEffect, useRef, useState } from 'react'
import { LoaderCircle, Send, Sparkles, ChevronDown, Square } from 'lucide-react'
import { streamChat } from './chatApi.js'

const STEP_LABEL = {
  thinking: '思考中',
  tool_call: '呼叫工具',
  tool_result: '取得結果',
}

function stepText(event) {
  if (event.type === 'thinking') return `${STEP_LABEL.thinking}…`
  if (event.type === 'tool_call') return `${STEP_LABEL.tool_call}：${event.tool}`
  if (event.type === 'tool_result') return `${STEP_LABEL.tool_result}：${event.preview || event.tool}`
  return ''
}

function StepsPanel({ steps, done }) {
  const [open, setOpen] = useState(false)
  if (steps.length === 0) return null
  return (
    <div className={`steps ${open ? 'open' : ''}`}>
      <button type="button" className="steps-toggle" onClick={() => setOpen((v) => !v)}>
        <Sparkles size={14} />
        {done ? `思考過程（${steps.length} 步）` : '思考中…'}
        <ChevronDown size={14} className="steps-caret" />
      </button>
      {open && <ul>{steps.map((s, i) => <li key={i}>{s}</li>)}</ul>}
    </div>
  )
}

function Message({ message }) {
  if (message.role === 'user') {
    return <div className="msg user"><div className="bubble">{message.content}</div></div>
  }
  return (
    <div className="msg assistant">
      <StepsPanel steps={message.steps} done={message.done} />
      <div className="bubble">
        {message.content || (!message.done && <LoaderCircle className="spin" size={16} />)}
      </div>
    </div>
  )
}

export default function ChatPage() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  // 更新最後一則 assistant 訊息
  const patchLast = (patch) => setMessages((prev) => {
    const next = [...prev]
    const last = next[next.length - 1]
    next[next.length - 1] = typeof patch === 'function' ? patch(last) : { ...last, ...patch }
    return next
  })

  async function send(event) {
    event?.preventDefault()
    const text = input.trim()
    if (!text || busy) return

    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', steps: [], done: false },
    ])
    setInput('')
    setBusy(true)
    const controller = new AbortController()
    abortRef.current = controller

    try {
      await streamChat({ query: text, history, max_steps: 5 }, (evt) => {
        if (evt.type === 'answer_chunk') {
          patchLast((last) => ({ ...last, content: last.content + (evt.text || '') }))
        } else if (evt.type === 'answer') {
          // 最終 answer 帶完整答案（可能含 critic 附註），取代先前累積的串流文字；
          // 若某些路徑沒串流過 chunk 而直接送 answer，last.content 為空也能正確顯示。
          patchLast((last) => ({ ...last, content: evt.text || last.content, done: true }))
        } else if (evt.type === 'error') {
          patchLast({ content: `⚠️ ${evt.message || '發生錯誤'}`, done: true })
        } else {
          const label = stepText(evt)
          if (label) patchLast((last) => ({ ...last, steps: [...last.steps, label] }))
        }
      }, controller.signal)
      patchLast((last) => ({ ...last, done: true }))
    } catch (err) {
      if (err.name !== 'AbortError') {
        patchLast({ content: `⚠️ ${err.message}`, done: true })
      } else {
        patchLast((last) => ({ ...last, done: true }))
      }
    } finally {
      setBusy(false)
      abortRef.current = null
    }
  }

  function stop() {
    abortRef.current?.abort()
  }

  return (
    <main className="chat-page">
      <div className="chat-scroll" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="chat-empty">
            <Sparkles size={28} />
            <h2>問我任何留學申請問題</h2>
            <p>例如：CMU 的 CS 碩士 GPA 門檻是多少？可以接著追問細節。</p>
          </div>
        )}
        {messages.map((m, i) => <Message key={i} message={m} />)}
      </div>
      <form className="chat-input" onSubmit={send}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="輸入你的問題……"
          disabled={busy}
        />
        {busy
          ? <button type="button" className="stop-btn" onClick={stop} aria-label="停止"><Square size={18} /></button>
          : <button className="send-btn" aria-label="送出"><Send size={18} /></button>}
      </form>
    </main>
  )
}
```

## Step 2: 改 `frontend/src/App.jsx`（3 處精確 find/replace）

**2a. 加 import**——找到這行：
```jsx
import { searchExperiences, uploadExperience } from './api.js'
```
在它下方新增一行：
```jsx
import ChatPage from './ChatPage.jsx'
```

**2b. 路由判斷**——找到這行：
```jsx
  const route = window.location.hash === '#/search' ? 'search' : 'upload'
```
替換成兩行：
```jsx
  const hash = window.location.hash
  const route = hash === '#/search' ? 'search' : hash === '#/chat' ? 'chat' : 'upload'
```

**2c. return 內容**——找到 App 函式裡這整行 return（`return <><header className="site-header">...</footer></>`），替換成：
```jsx
  return <><header className="site-header"><a className="brand" href="#/upload"><span><GraduationCap size={22} /></span><div>留學經驗站<small>STUDY ABROAD STORIES</small></div></a><nav><a className={route === 'upload' ? 'active' : ''} href="#/upload">分享經驗</a><a className={route === 'search' ? 'active' : ''} href="#/search">查詢經驗</a><a className={route === 'chat' ? 'active' : ''} href="#/chat">AI 諮詢</a></nav></header>{route === 'chat' ? <ChatPage /> : route === 'search' ? <SearchPage /> : <UploadPage onView={() => navigate('search')} />}<footer>每一份經驗都來自個人分享，僅供申請準備參考。</footer></>
```

## Step 3: `frontend/src/styles.css` 檔尾追加

```css
/* ── AI 諮詢聊天頁 ── */
.chat-page { display: flex; flex-direction: column; height: calc(100vh - 140px); max-width: 820px; margin: 0 auto; width: 100%; }
.chat-scroll { flex: 1; overflow-y: auto; padding: 24px 16px; display: flex; flex-direction: column; gap: 18px; }
.chat-empty { margin: auto; text-align: center; color: #6b7280; display: flex; flex-direction: column; align-items: center; gap: 8px; }
.chat-empty h2 { margin: 4px 0 0; font-size: 1.2rem; }
.msg { display: flex; flex-direction: column; }
.msg.user { align-items: flex-end; }
.msg.assistant { align-items: flex-start; }
.msg .bubble { max-width: 78%; padding: 12px 16px; border-radius: 16px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.msg.user .bubble { background: #2563eb; color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: #f3f4f6; color: #111827; border-bottom-left-radius: 4px; }
.steps { margin-bottom: 6px; max-width: 78%; }
.steps-toggle { display: inline-flex; align-items: center; gap: 6px; background: none; border: none; color: #6b7280; font-size: 0.82rem; cursor: pointer; padding: 2px 0; }
.steps-caret { transition: transform 0.15s; }
.steps.open .steps-caret { transform: rotate(180deg); }
.steps ul { margin: 6px 0 0; padding-left: 18px; color: #6b7280; font-size: 0.82rem; display: flex; flex-direction: column; gap: 3px; }
.chat-input { display: flex; gap: 10px; padding: 14px 16px; border-top: 1px solid #e5e7eb; }
.chat-input input { flex: 1; padding: 12px 16px; border: 1px solid #d1d5db; border-radius: 12px; font-size: 0.95rem; }
.chat-input button { width: 46px; border: none; border-radius: 12px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: #fff; }
.chat-input .send-btn { background: #2563eb; }
.chat-input .stop-btn { background: #ef4444; }
```

## Step 4: 驗證前端能編譯（自動）
在 `frontend/` 目錄：
```bash
cd frontend
npm install
npm run build
```
Expected: build 成功、無語法/import 錯誤（會產生 `dist/`）。這證明 ChatPage.jsx、App.jsx 改動、chatApi import 都正確。
（`npm install` 第一次較久；若 node_modules 已存在可略過但保險起見仍可跑。）

瀏覽器實機驗證（多輪對話、串流畫面、停止鈕）由人類最後執行，你不需要開瀏覽器。

## Step 5: Commit
```bash
git add frontend/src/ChatPage.jsx frontend/src/App.jsx frontend/src/styles.css
git commit -m "feat: add full-screen multi-turn chat page"
```
（Bash git 不可用時改用 PowerShell。）
