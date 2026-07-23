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
