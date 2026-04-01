import { useState, useEffect, useRef } from 'react'
import { Settings, Plus, MessageSquare, Trash2, User, Sun, Moon, X } from 'lucide-react'
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
    deleteSession
  } = useStreamChat()

  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [isProfileOpen, setIsProfileOpen] = useState(false)
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // Theme state: read from localStorage, fallback to system preference
  const [isDark, setIsDark] = useState(() => {
    const stored = localStorage.getItem('theme')
    if (stored) return stored === 'dark'
    return window.matchMedia('(prefers-color-scheme: dark)').matches
  })

  useEffect(() => {
    if (isDark) {
      document.documentElement.classList.add('dark')
      localStorage.setItem('theme', 'dark')
    } else {
      document.documentElement.classList.remove('dark')
      localStorage.setItem('theme', 'light')
    }
  }, [isDark])

  // Close sidebar when viewport becomes desktop-sized
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)')
    const handler = (e: MediaQueryListEvent) => { if (e.matches) setIsSidebarOpen(false) }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
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
    <div className="flex h-screen overflow-hidden bg-white dark:bg-[#212121] font-sans text-gray-800 dark:text-gray-100 transition-colors duration-300">

      {/* ─── 行動版遮罩 Mobile backdrop ─── */}
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/40 z-30 md:hidden"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      {/* ─── 側邊欄 Sidebar ─── */}
      <aside className={`
        w-[260px] h-screen shrink-0
        bg-[#f9f9f9] dark:bg-[#171717]
        border-r border-[#e5e5e5] dark:border-gray-800
        text-gray-700 dark:text-gray-300
        flex flex-col pt-3 pb-4 px-3
        fixed md:static z-40
        transition-transform duration-300
        ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'} md:translate-x-0
      `}>

        {/* New Chat 按鈕 */}
        <button
          onClick={handleNewSession}
          className="flex items-center gap-2 w-full px-3 py-2.5 bg-white dark:bg-[#2a2a2a] border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm hover:bg-gray-50 dark:hover:bg-gray-700 transition-all mb-4 text-sm font-medium text-gray-700 dark:text-gray-200 cursor-pointer active:scale-[0.98]"
        >
          <Plus size={16} />
          New chat
        </button>

        {/* 對話紀錄區塊 */}
        <div className="flex-1 overflow-y-auto space-y-1 mb-2 custom-scrollbar">
          {sessions.map(session => (
            <div
              key={session.id}
              onClick={() => handleSwitchSession(session.id)}
              className={`group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer transition-colors text-sm ${
                currentSessionId === session.id
                  ? 'bg-gray-200 dark:bg-gray-700 font-medium'
                  : 'hover:bg-gray-200/50 dark:hover:bg-gray-800/60'
              }`}
            >
              <div className="flex items-center gap-2.5 overflow-hidden w-full">
                <MessageSquare size={14} className="shrink-0 text-gray-500 dark:text-gray-500" />
                <span className="truncate text-gray-700 dark:text-gray-300">{session.title}</span>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); deleteSession(session.id) }}
                className="opacity-0 group-hover:opacity-100 hover:text-red-500 dark:hover:text-red-400 transition-opacity p-1 -mr-1 shrink-0 text-gray-400 dark:text-gray-600 cursor-pointer"
                title="刪除對話"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        {/* 底部設定與個人資料 */}
        <div className="mt-auto pt-4 border-t border-gray-200/60 dark:border-gray-800 pb-1 space-y-1">
          <button
            onClick={() => { setIsProfileOpen(true); setIsSidebarOpen(false) }}
            className="flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-gray-200/50 dark:hover:bg-gray-800/60 transition-colors text-gray-700 dark:text-gray-300 font-medium"
          >
            <User size={18} className="text-gray-500 dark:text-gray-500" />
            <span className="text-sm">個人資料</span>
          </button>

          <button
            onClick={() => { setIsSettingsOpen(true); setIsSidebarOpen(false) }}
            className="flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-gray-200/50 dark:hover:bg-gray-800/60 transition-colors text-gray-700 dark:text-gray-300 font-medium"
          >
            <Settings size={18} className="text-gray-500 dark:text-gray-500" />
            <span className="text-sm">設定</span>
          </button>
        </div>
      </aside>

      {/* ─── 主對話區域 Chat Main ─── */}
      <main className="flex-1 flex flex-col relative min-w-0 bg-white dark:bg-[#212121] transition-colors duration-300">

        {/* 頂部導航欄 Header */}
        <header className="sticky top-0 z-10 bg-white/95 dark:bg-[#212121]/95 backdrop-blur px-4 py-3 shrink-0 flex items-center justify-between border-b border-gray-100 dark:border-gray-800/60">

          {/* 行動版漢堡 / 關閉按鈕 */}
          <button
            onClick={() => setIsSidebarOpen(o => !o)}
            className="md:hidden p-2 -ml-2 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-100 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
            aria-label="Toggle sidebar"
          >
            {isSidebarOpen
              ? <X size={22} />
              : <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="4" x2="20" y1="12" y2="12" /><line x1="4" x2="20" y1="6" y2="6" /><line x1="4" x2="20" y1="18" y2="18" /></svg>
            }
          </button>

          <div className="flex-1 flex items-center min-w-0 ml-1 md:ml-0">
            <h1
              onClick={handleNewSession}
              className="text-base sm:text-lg font-medium text-gray-700 dark:text-gray-200 cursor-pointer hover:text-gray-900 dark:hover:text-white transition-colors truncate"
            >
              留學顧問 AI
              <span className="hidden sm:inline text-xs text-gray-400 dark:text-gray-600 ml-2 font-normal">北美 CS 研究所</span>
            </h1>
          </div>

          {/* 深色 / 淺色切換 */}
          <button
            onClick={() => setIsDark(d => !d)}
            title={isDark ? '切換為淺色模式' : '切換為深色模式'}
            className="p-2 rounded-lg text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-100 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors shrink-0"
          >
            {isDark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </header>

        {/* 對話視窗 Main Scroll Area */}
        <div className="flex-1 overflow-y-auto w-full pb-36 custom-scrollbar">

          {/* 首頁引導畫面 */}
          {messages.length === 0 && (
            <div className="h-full min-h-[60vh] flex flex-col justify-center items-center px-6 text-center">
              <div className="w-16 h-16 rounded-full bg-gray-100 dark:bg-gray-800 flex items-center justify-center mb-5">
                <span className="text-3xl">🎓</span>
              </div>
              <h2 className="text-xl sm:text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-3">
                我可以幫忙解答什麼？
              </h2>
              <p className="text-sm text-gray-400 dark:text-gray-500 max-w-sm">
                詢問北美 CS 研究所的申請、選校、教授、SOP 等任何問題
              </p>
            </div>
          )}

          {/* 訊息泡泡列表 */}
          {messages.length > 0 && (
            <div className="w-full">
              {messages.map((msg, index) => (
                <MessageBubble key={msg.id} message={msg} isLast={index === messages.length - 1} />
              ))}
            </div>
          )}

          <div ref={bottomRef} className="h-4 w-full" />
        </div>

        {/* 對話輸入框 */}
        <ChatInput onSend={sendMessage} disabled={isStreaming} />
      </main>

      <SettingsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
      <UserProfileModal isOpen={isProfileOpen} onClose={() => setIsProfileOpen(false)} />
    </div>
  )
}
