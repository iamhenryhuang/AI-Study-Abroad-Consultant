import { Sparkles, User } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../types'
import { AgentSteps } from './AgentSteps'

interface Props {
  message: Message
  isLast?: boolean
}

// 共用的內容最大寬度 - 跟隨螢幕放大，避免全螢幕留白過多
const CONTENT_WIDTH = 'max-w-3xl xl:max-w-4xl 2xl:max-w-5xl'

export function MessageBubble({ message, isLast }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-center w-full py-3 sm:py-4 px-4 sm:px-6">
        <div className={`${CONTENT_WIDTH} w-full flex justify-end gap-3 items-start`}>
          <div className="bg-[#f4f4f4] dark:bg-[#2f2f2f] text-gray-800 dark:text-gray-100 rounded-3xl px-5 py-3 text-[15px] leading-relaxed max-w-[80%] sm:max-w-[75%] break-words">
            {message.text}
          </div>
          <div className="w-8 h-8 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#2a2a2a] flex items-center justify-center shrink-0">
            <User size={15} className="text-gray-500 dark:text-gray-400" strokeWidth={2} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={`flex justify-center w-full group py-5 sm:py-6 px-4 sm:px-6 ${!isLast ? 'border-b border-gray-100/60 dark:border-gray-800/50' : ''}`}>
      <div className={`${CONTENT_WIDTH} w-full flex gap-4 items-start`}>

        {/* AI Avatar */}
        <div className="w-8 h-8 rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#2a2a2a] flex items-center justify-center shrink-0 mt-0.5">
          <Sparkles size={14} className="text-gray-700 dark:text-gray-400" strokeWidth={2} />
        </div>

        <div className="flex-1 space-y-4 pt-0.5 min-w-0">
          <AgentSteps events={message.events} />

          {message.loading && !message.text ? (
            <div className="flex gap-1.5 items-center h-6">
              <span className="w-2 h-2 bg-gray-300 dark:bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-2 h-2 bg-gray-300 dark:bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-2 h-2 bg-gray-300 dark:bg-gray-600 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
          ) : (
            <div className={`
              font-sans text-[15px] leading-relaxed tracking-normal break-words
              prose dark:prose-invert max-w-none
              prose-p:my-3 prose-p:text-[15px] prose-p:leading-7
              prose-li:my-1 prose-li:text-[15px]
              prose-ul:text-[15px] prose-ol:text-[15px]
              prose-headings:font-semibold
              prose-strong:text-[15px]
              prose-code:text-[14px] prose-code:before:content-none prose-code:after:content-none
              prose-code:bg-gray-100 dark:prose-code:bg-gray-800
              prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded
              prose-pre:text-[13px] prose-pre:leading-relaxed
              prose-pre:bg-gray-50 dark:prose-pre:bg-gray-900
              prose-pre:border prose-pre:border-gray-200 dark:prose-pre:border-gray-700
              prose-pre:text-gray-800 dark:prose-pre:text-gray-100
              prose-pre:rounded-xl prose-pre:overflow-x-auto
              prose-a:text-[13px] prose-a:text-blue-600 dark:prose-a:text-blue-400
              prose-a:font-medium prose-a:break-all
              hover:prose-a:underline prose-a:no-underline
              ${message.error ? 'text-red-500 dark:text-red-400' : 'text-gray-800 dark:text-gray-100'}
            `}>
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
