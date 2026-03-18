import { Sparkles, User } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../types'
import { AgentSteps } from './AgentSteps'

interface Props {
  message: Message
  isLast?: boolean
}

export function MessageBubble({ message, isLast }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end w-full py-4 px-4 sm:px-0">
        <div className="max-w-3xl w-full flex justify-end gap-3 sm:gap-4 overflow-hidden items-end sm:items-start pr-2">
          <div className="bg-[#f4f4f4] text-gray-800 rounded-3xl px-5 py-3 text-[15px] leading-relaxed max-w-[85%] wrap-break-word">
            {message.text}
          </div>
          <div className="w-8 h-8 rounded-full border border-gray-200 bg-white flex flex-col items-center justify-center shrink-0 ml-1">
            <User size={16} className="text-gray-500" strokeWidth={2} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`flex justify-center w-full group py-6 px-4 sm:px-0 ${!isLast ? 'border-b border-gray-100/50' : ''}`}>
      <div className="max-w-3xl w-full flex gap-4 md:gap-6 items-start overflow-hidden">
        {/* Flat minimal AI Avatar */}
        <div className="w-8 h-8 rounded-full border border-gray-200 bg-white flex flex-col items-center justify-center shrink-0 mt-0.5">
          <Sparkles size={14} className="text-gray-700" strokeWidth={2} />
        </div>

        <div className="flex-1 space-y-4 pt-1 min-w-0">
          <AgentSteps events={message.events} />

          {message.loading && !message.text ? (
            <div className="flex gap-1.5 items-center h-6">
              <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-2 h-2 bg-gray-300 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          ) : (
            <div
              className={`font-sans text-[15px] leading-relaxed tracking-normal prose max-w-none prose-p:my-3 prose-p:text-[15px] prose-p:leading-7 prose-li:my-1 prose-li:text-[15px] prose-ul:text-[15px] prose-ol:text-[15px] prose-headings:text-[15px] prose-headings:font-semibold prose-strong:text-[15px] prose-code:text-[15px] prose-pre:text-[15px] prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200 prose-pre:text-gray-800 prose-a:text-[13px] prose-a:text-blue-600 prose-a:font-medium prose-a:break-all hover:prose-a:underline prose-a:no-underline wrap-break-word ${message.error ? 'text-red-500' : 'text-gray-800'
                }`}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.text}
              </ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
