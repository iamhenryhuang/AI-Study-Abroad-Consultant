import { useRef, useState } from 'react'
import { ArrowUp, Loader2, Plus } from 'lucide-react'

interface Props {
  onSend: (query: string) => void
  disabled: boolean
}

const CONTENT_WIDTH = 'max-w-3xl xl:max-w-4xl'

export function ChatInput({ onSend, disabled }: Props) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const resizeTextarea = () => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
    requestAnimationFrame(resizeTextarea)
  }

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSubmit(event as unknown as React.FormEvent)
    }
  }

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-white via-white/95 to-transparent px-3 pb-4 pt-12 dark:from-[#1b1c1d] dark:via-[#1b1c1d]/96">
      <div className={`${CONTENT_WIDTH} mx-auto pointer-events-auto`}>
        <form
          onSubmit={handleSubmit}
          className="rounded-[28px] border border-black/8 bg-white p-2 shadow-[0_12px_40px_rgba(0,0,0,0.10)] transition focus-within:border-black/15 dark:border-white/10 dark:bg-[#2a2b2d] dark:shadow-[0_12px_45px_rgba(0,0,0,0.35)]"
        >
          <div className="flex items-end gap-2">
            <button
              type="button"
              className="mb-1 grid h-9 w-9 shrink-0 place-items-center rounded-full text-gray-500 transition hover:bg-black/5 hover:text-gray-800 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
              title="附加內容"
              aria-label="附加內容"
            >
              <Plus size={20} />
            </button>

            <textarea
              ref={textareaRef}
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
                requestAnimationFrame(resizeTextarea)
              }}
              onKeyDown={handleKeyDown}
              disabled={disabled}
              rows={1}
              placeholder="詢問選校、教授、申請文件或留學策略..."
              className="max-h-[220px] min-h-11 flex-1 resize-none bg-transparent px-1 py-3 text-[15px] leading-6 text-gray-900 outline-none placeholder:text-gray-400 disabled:opacity-60 dark:text-gray-100 dark:placeholder:text-gray-500 custom-scrollbar"
            />

            <button
              type="submit"
              disabled={disabled || !value.trim()}
              className="mb-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-[#1f1f1f] text-white transition hover:bg-black disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 dark:bg-white dark:text-black dark:hover:bg-gray-100 dark:disabled:bg-white/12 dark:disabled:text-gray-500"
              title="送出"
              aria-label="送出訊息"
            >
              {disabled ? <Loader2 size={18} className="animate-spin" /> : <ArrowUp size={19} />}
            </button>
          </div>
        </form>
        <p className="mt-2 text-center text-[11px] leading-5 text-gray-400 dark:text-gray-500">
          AI 可能會出錯，重要資訊請再確認學校官方頁面。
        </p>
      </div>
    </div>
  )
}
