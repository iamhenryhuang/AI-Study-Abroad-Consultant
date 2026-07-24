import { useEffect, useRef, useState } from 'react'
import {
  Menu,
  MessageSquare,
  Moon,
  PanelLeftClose,
  Plus,
  Settings,
  Sparkles,
  Sun,
  Trash2,
  User,
  X,
} from 'lucide-react'
import { useStreamChat } from './hooks/useStreamChat'
import { SettingsModal } from './components/SettingsModal'
import { UserProfileModal } from './components/UserProfileModal'
import { ChatInput } from './components/ChatInput'
import { MessageBubble } from './components/MessageBubble'

export default function App() {
  const {
    messages,
    isStreaming,
    sendMessage,
    sessions,
    currentSessionId,
    startNewSession,
    switchSession,
    deleteSession,
  } = useStreamChat()

  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [isProfileOpen, setIsProfileOpen] = useState(false)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const [isDark, setIsDark] = useState(() => {
    const stored = localStorage.getItem('theme')
    if (stored) return stored === 'dark'
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    document.documentElement.classList.toggle('dark', isDark)
    localStorage.setItem('theme', isDark ? 'dark' : 'light')
  }, [isDark])

  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)')
    const handler = (event: MediaQueryListEvent) => {
      if (event.matches) setIsSidebarOpen(false)
    }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  const handleNewSession = () => {
    startNewSession()
    setIsSidebarOpen(false)
  }

  const handleSwitchSession = (id: string) => {
    switchSession(id)
    setIsSidebarOpen(false)
  }

  return (
    <div className="h-dvh overflow-hidden bg-[#fbfbfb] text-[#1f1f1f] antialiased transition-colors duration-300 dark:bg-[#1b1c1d] dark:text-gray-100">
      {isSidebarOpen && (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-black/40 md:hidden"
          aria-label="關閉側欄"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div className="flex h-full">
        <aside
          className={`fixed inset-y-0 left-0 z-40 flex w-[280px] shrink-0 flex-col border-r border-black/5 bg-[#f5f5f5] px-3 py-3 shadow-2xl shadow-black/10 transition-transform duration-300 md:static md:translate-x-0 md:shadow-none dark:border-white/8 dark:bg-[#111213] ${
            isSidebarOpen ? 'translate-x-0' : '-translate-x-full'
          }`}
        >
          <div className="mb-3 flex items-center gap-2 px-1">
            <button
              type="button"
              onClick={handleNewSession}
              className="flex h-10 flex-1 items-center gap-2 rounded-xl border border-black/8 bg-white px-3 text-sm font-medium text-gray-800 shadow-sm transition hover:bg-gray-50 active:scale-[0.99] dark:border-white/10 dark:bg-[#1f2021] dark:text-gray-100 dark:hover:bg-[#292a2b]"
            >
              <Plus size={17} />
              新對話
            </button>
            <button
              type="button"
              onClick={() => setIsSidebarOpen(false)}
              className="grid h-10 w-10 place-items-center rounded-xl text-gray-500 transition hover:bg-black/5 md:hidden dark:text-gray-400 dark:hover:bg-white/10"
              aria-label="收合側欄"
            >
              <X size={19} />
            </button>
          </div>

          <div className="mb-3 rounded-2xl border border-black/5 bg-white/70 p-3 dark:border-white/8 dark:bg-white/[0.03]">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-blue-500 via-teal-400 to-emerald-400 text-white">
                <Sparkles size={15} />
              </span>
              留學申請 AI
            </div>
            <p className="text-xs leading-5 text-gray-500 dark:text-gray-400">
              幫你整理 CS 選校、教授、申請策略與文件方向。
            </p>
          </div>

          <div className="flex-1 overflow-y-auto pb-3 custom-scrollbar">
            <p className="px-3 pb-2 text-xs font-medium text-gray-400">最近對話</p>
            <div className="space-y-1">
              {sessions.length === 0 && (
                <p className="px-3 py-2 text-sm text-gray-400">尚未開始對話</p>
              )}

              {sessions.map((session) => (
                <div
                  key={session.id}
                  onClick={() => handleSwitchSession(session.id)}
                  className={`group flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2.5 text-sm transition ${
                    currentSessionId === session.id
                      ? 'bg-black/7 text-gray-950 dark:bg-white/12 dark:text-white'
                      : 'text-gray-600 hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/8'
                  }`}
                >
                  <MessageSquare size={16} className="shrink-0 text-gray-400" />
                  <span className="min-w-0 flex-1 truncate">{session.title || '未命名對話'}</span>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation()
                      deleteSession(session.id)
                    }}
                    className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-gray-400 opacity-0 transition hover:bg-red-500/10 hover:text-red-500 group-hover:opacity-100"
                    title="刪除對話"
                    aria-label="刪除對話"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-1 border-t border-black/6 pt-3 dark:border-white/8">
            <button
              type="button"
              onClick={() => {
                setIsProfileOpen(true)
                setIsSidebarOpen(false)
              }}
              className="flex h-10 w-full items-center gap-3 rounded-xl px-3 text-sm font-medium text-gray-700 transition hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/8"
            >
              <User size={17} className="text-gray-500" />
              個人資料
            </button>
            <button
              type="button"
              onClick={() => {
                setIsSettingsOpen(true)
                setIsSidebarOpen(false)
              }}
              className="flex h-10 w-full items-center gap-3 rounded-xl px-3 text-sm font-medium text-gray-700 transition hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/8"
            >
              <Settings size={17} className="text-gray-500" />
              設定
            </button>
          </div>
        </aside>

        <main className="relative flex min-w-0 flex-1 flex-col bg-white dark:bg-[#1b1c1d]">
          <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center justify-between border-b border-black/5 bg-white/85 px-3 backdrop-blur-xl dark:border-white/8 dark:bg-[#1b1c1d]/85 sm:px-5">
            <div className="flex min-w-0 items-center gap-2">
              <button
                type="button"
                onClick={() => setIsSidebarOpen((open) => !open)}
                className="grid h-10 w-10 place-items-center rounded-xl text-gray-600 transition hover:bg-black/5 md:hidden dark:text-gray-300 dark:hover:bg-white/8"
                aria-label="開啟側欄"
              >
                {isSidebarOpen ? <PanelLeftClose size={20} /> : <Menu size={20} />}
              </button>
              <button
                type="button"
                onClick={handleNewSession}
                className="hidden h-10 items-center gap-2 rounded-xl px-3 text-sm font-medium text-gray-700 transition hover:bg-black/5 md:flex dark:text-gray-200 dark:hover:bg-white/8"
              >
                <Sparkles size={17} className="text-teal-500" />
                AI 留學顧問
              </button>
              <div className="min-w-0 md:hidden">
                <p className="truncate text-sm font-semibold">AI 留學顧問</p>
              </div>
            </div>

            <button
              type="button"
              onClick={() => setIsDark((dark) => !dark)}
              title={isDark ? '切換為淺色模式' : '切換為深色模式'}
              className="grid h-10 w-10 place-items-center rounded-xl text-gray-600 transition hover:bg-black/5 dark:text-gray-300 dark:hover:bg-white/8"
              aria-label="切換主題"
            >
              {isDark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </header>

          <div className="flex-1 overflow-y-auto pb-36 custom-scrollbar">
            {messages.length === 0 ? (
              <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col justify-center px-5 py-14">
                <div className="mb-8">
                  <div className="mb-5 inline-flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 via-teal-400 to-emerald-400 text-white shadow-lg shadow-teal-500/20">
                    <Sparkles size={22} />
                  </div>
                  <h1 className="text-3xl font-semibold tracking-normal text-gray-900 dark:text-white sm:text-4xl">
                    今天想規劃哪一段申請？
                  </h1>
                  <p className="mt-3 max-w-2xl text-base leading-7 text-gray-500 dark:text-gray-400">
                    可以問我選校名單、教授研究方向、申請時程、SOP 架構，或請我依你的背景做策略建議。
                  </p>
                </div>

                <div className="grid gap-3 sm:grid-cols-2">
                  {[
                    '依照我的背景推薦 CS 碩士選校',
                    '幫我整理 Stanford CS 相關教授',
                    'SOP 第一段應該怎麼寫？',
                    '比較 CMU、UCSD、Georgia Tech 的申請重點',
                  ].map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => sendMessage(prompt)}
                      disabled={isStreaming}
                      className="min-h-20 rounded-2xl border border-black/8 bg-[#f7f7f7] p-4 text-left text-sm leading-6 text-gray-700 transition hover:border-black/15 hover:bg-white hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-gray-300 dark:hover:border-white/16 dark:hover:bg-white/[0.07]"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="w-full">
                {messages.map((message, index) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    isLast={index === messages.length - 1}
                  />
                ))}
              </div>
            )}
            <div ref={bottomRef} className="h-2 w-full" />
          </div>

          <ChatInput onSend={sendMessage} disabled={isStreaming} />
        </main>
      </div>

      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
      <UserProfileModal isOpen={isProfileOpen} onClose={() => setIsProfileOpen(false)} />
    </div>
  )
}
