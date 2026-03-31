import { useState, useEffect, useRef } from 'react'
import { Settings, Plus, MessageSquare, Trash2, User } from 'lucide-react'
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
  const bottomRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="flex h-screen overflow-hidden bg-white font-sans text-gray-800 transition-colors duration-300">

      {/* ─── 側邊欄 Sidebar ─── */}
      <aside className="w-[260px] h-screen bg-[#f9f9f9] border-r border-[#e5e5e5] text-gray-700 flex flex-col pt-3 pb-4 px-3 shrink-0 absolute md:static z-40 transition-transform -translate-x-full md:translate-x-0">
        
        {/* New Chat 按鈕 */}
        <button
          onClick={startNewSession}
          className="flex items-center gap-2 w-full px-3 py-2.5 bg-white border border-gray-200 rounded-lg shadow-sm hover:bg-gray-50 transition-all mb-4 text-sm font-medium text-gray-700 cursor-pointer active:scale-[0.98]"
        >
          <Plus size={16} />
          New chat
        </button>

        {/* 對話紀錄區塊 */}
        <div className="flex-1 overflow-y-auto space-y-1 mb-2 custom-scrollbar">
          {sessions.map(session => (
            <div
              key={session.id}
              onClick={() => switchSession(session.id)}
              className={`group flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer transition-colors text-sm ${
                currentSessionId === session.id ? 'bg-gray-200 font-medium' : 'hover:bg-gray-200/50'
              }`}
            >
              <div className="flex items-center gap-2.5 overflow-hidden w-full">
                <MessageSquare size={14} className="shrink-0 text-gray-500" />
                <span className="truncate">{session.title}</span>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  deleteSession(session.id)
                }}
                className="opacity-0 group-hover:opacity-100 hover:text-red-500 transition-opacity p-1 -mr-1 shrink-0 text-gray-400 cursor-pointer"
                title="刪除對話"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>

        {/* 底部設定與個人資料 */}
        <div className="mt-auto pt-4 border-t border-gray-200/60 pb-1 space-y-1">
          <button
            onClick={() => setIsProfileOpen(true)}
            className="flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-gray-200/50 transition-colors text-gray-700 font-medium"
          >
            <User size={18} className="text-gray-500" />
            <span className="text-sm">個人資料</span>
          </button>
          
          <button
            onClick={() => setIsSettingsOpen(true)}
            className="flex items-center gap-3 w-full text-left px-3 py-2.5 rounded-lg hover:bg-gray-200/50 transition-colors text-gray-700 font-medium"
          >
            <Settings size={18} className="text-gray-500" />
            <span className="text-sm">設定</span>
          </button>
        </div>
      </aside>

      {/* ─── 主對話區域 Chat Main ─── */}
      <main className="flex-1 flex flex-col relative min-w-0 transition-colors duration-300 bg-white">
        
        {/* 頂部導航欄 Header */}
        <header className="sticky top-0 z-10 bg-white/95 backdrop-blur px-4 py-3 shrink-0 transition-all flex items-center justify-between">
          {/* 行動裝置側邊選單按鈕 */}
          <button className="md:hidden p-2 -ml-2 text-gray-500 hover:text-gray-800 rounded-lg hover:bg-gray-100 transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="4" x2="20" y1="12" y2="12" /><line x1="4" x2="20" y1="6" y2="6" /><line x1="4" x2="20" y1="18" y2="18" /></svg>
          </button>

          <div className="flex-1 flex items-center">
            <h1
              onClick={startNewSession}
              className="text-lg font-medium text-gray-700 cursor-pointer hover:text-gray-900 transition-colors inline-block"
            >
              留學顧問 AI <span className="text-xs text-gray-400 ml-2 font-normal">北美 CS 研究所</span>
            </h1>
          </div>
        </header>

        {/* 對話視窗 Main Scroll Area */}
        <div className="flex-1 overflow-y-auto w-full pb-32 scroll-smooth flex flex-col items-center">
          
          {/* 首頁引導畫面 */}
          {messages.length === 0 && (
            <div className="flex-1 flex flex-col justify-center items-center w-full px-6 max-w-3xl">
              <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-6">
                 <span className="text-3xl">🎓</span>
              </div>
              <h2 className="text-2xl font-semibold text-gray-800 mb-8 text-center">我可以幫忙解答什麼？</h2>
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

          <div ref={bottomRef} className="h-6 w-full" />
        </div>

        {/* 對話輸入框 */}
        <ChatInput onSend={sendMessage} disabled={isStreaming} />
      </main>

      {/* 設定彈窗 */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
      />

      {/* 使用者資料彈窗 */}
      <UserProfileModal 
        isOpen={isProfileOpen} 
        onClose={() => setIsProfileOpen(false)} 
      />
    </div>
  )
}
