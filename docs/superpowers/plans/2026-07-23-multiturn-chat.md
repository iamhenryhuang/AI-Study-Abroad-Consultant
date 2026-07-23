# 多輪 AI 諮詢聊天頁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在既有前端加一個全螢幕聊天頁，串流顯示 agent 答案與可摺疊思考步驟，並支援多輪對話（後端以查詢改寫收斂上下文，agent 本體不動）。

**Architecture:** 後端 `/api/chat` 在 `run_agent` 前加一個獨立的 `contextualize_query` 前置步驟，用歷史把跟隨問題改寫成獨立問題。前端新增 `#/chat` 全螢幕頁，用 `fetch`+`ReadableStream` 讀 SSE，逐事件更新畫面；每次送出帶上前面的對話當 history。

**Tech Stack:** FastAPI + Pydantic、OpenAI（`call_llm`，mini）、React 19 + Vite、SSE（`text/event-stream`）、unittest。

## Global Constraints

- 後端 SSE 傳輸格式：標準 `data: {json}\n\n`，收到 `answer`/`error` 事件即結束串流。
- SSE 事件類型：`thinking` / `tool_call` / `tool_result` / `answer_chunk` / `answer` / `error`。
- `call_llm(prompt, model_name=DEFAULT_MODEL, temperature=0.0) -> str`，來自 `generator.client`（`DEFAULT_MODEL`＝gpt-4.1/mini 級別）。
- 前端 API_BASE 慣例：`(import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')`（空字串時走 Vite proxy 到 8000）。
- 前端不能用原生 `EventSource`（僅 GET），須 `fetch` POST + `ReadableStream` 手動解析。
- 不持久化：對話僅存 React state，重整即消失。
- `history` 上限 20 則（後端 `max_length=20`）。
- 後端測試框架：unittest，執行 `python -m unittest discover tests -p "test_x.py" -v`（沿用 `tests/test_vectorize.py` 慣例，測試檔開頭 `sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))`）。
- 前端目前無測試框架；前端任務以「啟動 app 實際操作」驗證。SSE 純解析函式可用 node 片段驗證。
- Agent 本體（`retriever/agent/`）、hybrid search、DB schema、既有 `/api/experiences` 與 upload/search 前端頁面，全部不動。

## File Structure

| 檔案 | 責任 |
|------|------|
| `backend/scripts/retriever/contextualize.py`（新） | 查詢改寫：跟隨問題 → 獨立問題 |
| `tests/test_contextualize.py`（新） | contextualize 單元測試 |
| `backend/api.py`（改） | `ChatMessage` 模型 + `history` 欄位 + 呼叫 `contextualize_query` |
| `frontend/src/chatApi.js`（新） | SSE 串流讀取 + 純解析函式 `parseSSE` |
| `frontend/src/ChatPage.jsx`（新） | 聊天頁 UI、訊息狀態、事件處理 |
| `frontend/src/App.jsx`（改） | `#/chat` 路由 + 導覽連結 |
| `frontend/src/styles.css`（改） | 聊天頁樣式 |

---

### Task 1: 後端 contextualize_query（查詢改寫）

**Files:**
- Create: `backend/scripts/retriever/contextualize.py`
- Test: `tests/test_contextualize.py`

**Interfaces:**
- Consumes: `generator.client.call_llm(prompt: str) -> str`
- Produces: `contextualize_query(query: str, history: list[dict]) -> str`——`history` 為 `[{"role": "user"|"assistant", "content": str}, ...]`；無歷史時原樣回傳 query 且不呼叫 LLM；LLM 失敗時降級回原 query。Task 2 呼叫。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contextualize.py
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever import contextualize


class TestContextualizeQuery(unittest.TestCase):
    def test_no_history_returns_query_unchanged_without_llm(self):
        with patch.object(contextualize, "call_llm") as mock_llm:
            out = contextualize.contextualize_query("CMU 截止日是什麼？", [])
        self.assertEqual(out, "CMU 截止日是什麼？")
        mock_llm.assert_not_called()

    def test_with_history_returns_rewritten_query(self):
        history = [
            {"role": "user", "content": "CMU 的 GPA 門檻？"},
            {"role": "assistant", "content": "最低 3.0"},
        ]
        with patch.object(contextualize, "call_llm",
                          return_value="CMU 的申請截止日是什麼？") as mock_llm:
            out = contextualize.contextualize_query("那它的截止日呢？", history)
        self.assertEqual(out, "CMU 的申請截止日是什麼？")
        mock_llm.assert_called_once()

    def test_blank_rewrite_falls_back_to_original(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        with patch.object(contextualize, "call_llm", return_value="   "):
            out = contextualize.contextualize_query("那截止日呢？", history)
        self.assertEqual(out, "那截止日呢？")

    def test_llm_failure_degrades_to_original_query(self):
        history = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        with patch.object(contextualize, "call_llm", side_effect=RuntimeError("boom")):
            out = contextualize.contextualize_query("那截止日呢？", history)
        self.assertEqual(out, "那截止日呢？")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover tests -p "test_contextualize.py" -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'retriever.contextualize'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/scripts/retriever/contextualize.py
"""把承接前文的跟隨問題改寫成獨立問題（multi-turn chat 的前置步驟）。

放在 agent 之前：agent 本體仍只吃單一 query，多輪上下文在此收斂成一句
獨立問題。LLM 失敗時降級回原始 query，不讓改寫擋住對話。
"""
from __future__ import annotations

from generator.client import call_llm


def _build_prompt(query: str, history: list[dict]) -> str:
    lines = []
    for m in history:
        role = "使用者" if m.get("role") == "user" else "助理"
        lines.append(f"{role}：{m.get('content', '')}")
    convo = "\n".join(lines)
    return f"""你是一個問題改寫助理。根據以下對話歷史，把使用者最新的「跟隨問題」改寫成一個不需要上下文也能獨立理解的完整問題。

規則：
- 把代名詞（它、那個、這所學校…）換成歷史中明確的對象。
- 若最新問題本身已是獨立問題，原樣輸出即可。
- 只輸出改寫後的問題，不要任何解釋或前綴。

【對話歷史】
{convo}

【最新跟隨問題】
{query}

【改寫後的獨立問題】"""


def contextualize_query(query: str, history: list[dict]) -> str:
    """有歷史時用 LLM 把跟隨問題改寫成獨立問題；無歷史直接回傳原 query。

    history: [{"role": "user"|"assistant", "content": str}, ...]
    改寫結果為空、或 LLM 呼叫失敗時，降級回原始 query。
    """
    if not history:
        return query
    try:
        rewritten = call_llm(_build_prompt(query, history))
        return rewritten.strip() or query
    except Exception as e:
        print(f"[contextualize] 改寫失敗，改用原始問題：{e}")
        return query
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest discover tests -p "test_contextualize.py" -v`
Expected: PASS（4 tests OK）

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/retriever/contextualize.py tests/test_contextualize.py
git commit -m "feat: add query contextualization for multi-turn chat"
```

---

### Task 2: 後端 /api/chat 接線（history + 改寫）

**Files:**
- Modify: `backend/api.py`（import 區、`ChatRequest`、`run_in_thread`）

**Interfaces:**
- Consumes: `retriever.contextualize.contextualize_query(query, history)`（Task 1）
- Produces: `/api/chat` 現接受 `{query, max_steps, history:[{role,content}...]}`；`ChatRequest` 有 `history: list[ChatMessage]` 欄位。Task 3/4 的前端呼叫。

- [ ] **Step 1: 加入 import（`backend/api.py`）**

在第 22 行

```python
from retriever.agent import run_agent
```

下方新增一行：

```python
from retriever.contextualize import contextualize_query
```

- [ ] **Step 2: 擴充請求模型（`backend/api.py`）**

把原本的

```python
class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5
```

改成

```python
class ChatMessage(BaseModel):
    role: str        # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    max_steps: int = 5
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)
```

- [ ] **Step 3: 在 run_agent 前插入改寫（`backend/api.py`）**

在 `chat` 端點的 `run_in_thread` 內，把

```python
    def run_in_thread() -> None:
        try:
            result = run_agent(
                query=request.query,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
```

改成

```python
    def run_in_thread() -> None:
        try:
            standalone = contextualize_query(
                request.query,
                [m.model_dump() for m in request.history],
            )
            result = run_agent(
                query=standalone,
                max_steps=request.max_steps,
                verbose=False,
                on_event=on_event,
                cancel_event=cancel_event,
            )
```

- [ ] **Step 4: 驗證 app 可載入、ChatRequest 接受 history**

Run（在專案根目錄）:
```bash
python -c "import sys; sys.path.insert(0, 'backend'); from api import ChatRequest; req = ChatRequest(query='q', max_steps=5, history=[{'role':'user','content':'x'},{'role':'assistant','content':'y'}]); print('history len', len(req.history))"
```
Expected: 印出 `history len 2`（無 ImportError、模型接受 history 欄位）

- [ ] **Step 5: 手動端到端驗證（需 DB + OpenAI key + 模型）**

啟動後端 `python -m uvicorn backend.api:app --port 8000`，另開終端機：
```bash
curl -N -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" -d '{"query":"那它的截止日呢？","history":[{"role":"user","content":"CMU 的 GPA 門檻？"},{"role":"assistant","content":"最低 3.0"}]}'
```
Expected: 看到 `data: {"type":"thinking"...}` 起始的 SSE 串流，最後以 `data: {"type":"answer"...}` 結束（後端 log 會顯示 agent 實際查的是被改寫後、含「CMU 截止日」的獨立問題）。

- [ ] **Step 6: Commit**

```bash
git add backend/api.py
git commit -m "feat: wire multi-turn history into /api/chat via contextualization"
```

---

### Task 3: 前端 SSE 串流模組 chatApi.js

**Files:**
- Create: `frontend/src/chatApi.js`

**Interfaces:**
- Produces:
  - `parseSSE(buffer: string) -> { events: object[], rest: string }`——純函式，以 `\n\n` 切 SSE frame、去 `data:` 前綴、`JSON.parse`；不完整的尾段留在 `rest`。
  - `streamChat(payload: object, onEvent: (event) => void, signal?: AbortSignal) -> Promise<void>`——POST `/api/chat`，逐事件呼叫 `onEvent`。Task 4 的 ChatPage 使用。

- [ ] **Step 1: 寫實作**

```js
// frontend/src/chatApi.js
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

- [ ] **Step 2: 驗證純解析函式（node 片段）**

Run（在專案根目錄）:
```bash
node --input-type=module -e "import('./frontend/src/chatApi.js').then(({parseSSE})=>{const {events,rest}=parseSSE('data: {\"type\":\"thinking\",\"step\":1}\n\ndata: {\"type\":\"answer\",\"text\":\"hi\"}\n\ndata: {\"type\":\"part');console.log('events',events.length,'types',events.map(e=>e.type).join(','),'rest?',rest.length>0)})"
```
Expected: `events 2 types thinking,answer rest? true`（切出 2 個完整事件，不完整尾段留在 rest）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/chatApi.js
git commit -m "feat: add SSE streaming client for chat"
```

---

### Task 4: 前端聊天頁 ChatPage + 路由 + 樣式

**Files:**
- Create: `frontend/src/ChatPage.jsx`
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `streamChat(payload, onEvent, signal)`（Task 3）；後端 `/api/chat` 帶 history（Task 2）
- Produces: `#/chat` 全螢幕聊天頁

- [ ] **Step 1: 建立 ChatPage 元件**

```jsx
// frontend/src/ChatPage.jsx
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

- [ ] **Step 2: 接上路由與導覽（`frontend/src/App.jsx`）**

在檔案頂端 import 區（第 6 行 `import { searchExperiences, uploadExperience } from './api.js'` 附近）新增：

```jsx
import ChatPage from './ChatPage.jsx'
```

把 `App` 函式內的路由判斷

```jsx
  const route = window.location.hash === '#/search' ? 'search' : 'upload'
```

改成

```jsx
  const hash = window.location.hash
  const route = hash === '#/search' ? 'search' : hash === '#/chat' ? 'chat' : 'upload'
```

把 `App` 的 return 內容整段替換為（新增「AI 諮詢」導覽連結與 chat 分支）：

```jsx
  return <><header className="site-header"><a className="brand" href="#/upload"><span><GraduationCap size={22} /></span><div>留學經驗站<small>STUDY ABROAD STORIES</small></div></a><nav><a className={route === 'upload' ? 'active' : ''} href="#/upload">分享經驗</a><a className={route === 'search' ? 'active' : ''} href="#/search">查詢經驗</a><a className={route === 'chat' ? 'active' : ''} href="#/chat">AI 諮詢</a></nav></header>{route === 'chat' ? <ChatPage /> : route === 'search' ? <SearchPage /> : <UploadPage onView={() => navigate('search')} />}<footer>每一份經驗都來自個人分享，僅供申請準備參考。</footer></>
```

- [ ] **Step 3: 加樣式（`frontend/src/styles.css` 檔尾追加）**

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

- [ ] **Step 4: 手動端到端驗證**

三個服務都要活著：DB（Docker，5434）、後端（`python -m uvicorn backend.api:app --port 8000`）、前端（`cd frontend && npm install && npm run dev`）。

瀏覽器開 `http://localhost:5173/#/chat`：
1. 導覽列有「AI 諮詢」，點進去是全螢幕聊天頁、顯示空狀態提示。
2. 問「CMU 的 CS 碩士 GPA 門檻？」→ 使用者泡泡靠右出現；AI 泡泡靠左，上方「思考中…」步驟區即時更新，答案逐字浮現；完成後步驟區可點開看步驟。
3. **多輪驗證**：接著問「那它的截止日呢？」→ 答案應針對 CMU 截止日（證明 history 改寫生效），而非反問「哪所學校」。
4. 送出中出現紅色停止鈕，點擊可中止串流。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ChatPage.jsx frontend/src/App.jsx frontend/src/styles.css
git commit -m "feat: add full-screen multi-turn chat page"
```
