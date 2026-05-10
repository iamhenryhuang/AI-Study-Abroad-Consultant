import { Sparkles, User } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Message } from '../types'
import { AgentSteps } from './AgentSteps'

interface Props {
  message: Message
  isLast?: boolean
}

const CONTENT_WIDTH = 'max-w-3xl xl:max-w-4xl'

export function MessageBubble({ message, isLast }: Props) {
  if (message.role === 'user') {
    return (
      <div className="flex w-full justify-center px-4 py-3 sm:px-6">
        <div className={`${CONTENT_WIDTH} flex w-full items-start justify-end gap-3`}>
          <div className="max-w-[82%] rounded-[24px] bg-[#f0f0f0] px-5 py-3 text-[15px] leading-7 text-gray-900 shadow-sm sm:max-w-[75%] dark:bg-[#303134] dark:text-gray-100">
            {message.text}
          </div>
          <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full border border-black/8 bg-white text-gray-500 dark:border-white/10 dark:bg-[#27282a] dark:text-gray-300">
            <User size={15} />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className={`group flex w-full justify-center px-4 py-5 sm:px-6 sm:py-6 ${
        !isLast ? 'border-b border-black/[0.04] dark:border-white/[0.06]' : ''
      }`}
    >
      <div className={`${CONTENT_WIDTH} flex w-full items-start gap-4`}>
        <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-gradient-to-br from-blue-500 via-teal-400 to-emerald-400 text-white shadow-sm">
          <Sparkles size={15} />
        </div>

        <div className="min-w-0 flex-1 space-y-4">
          <AgentSteps events={message.events} />

          {message.loading && !message.text ? (
            <div className="flex h-8 items-center gap-1.5">
              <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300 dark:bg-gray-600" />
              <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300 dark:bg-gray-600 [animation-delay:150ms]" />
              <span className="h-2 w-2 animate-bounce rounded-full bg-gray-300 dark:bg-gray-600 [animation-delay:300ms]" />
            </div>
          ) : (
            <div
              className={`
                break-words text-[15px] leading-7 tracking-normal
                prose max-w-none dark:prose-invert
                prose-p:my-3 prose-p:text-[15px] prose-p:leading-7
                prose-li:my-1 prose-li:text-[15px]
                prose-headings:mb-2 prose-headings:mt-5 prose-headings:font-semibold
                prose-strong:font-semibold
                prose-code:rounded-md prose-code:bg-gray-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[14px] prose-code:before:content-none prose-code:after:content-none
                dark:prose-code:bg-white/10
                prose-pre:overflow-x-auto prose-pre:rounded-2xl prose-pre:border prose-pre:border-black/8 prose-pre:bg-[#f7f7f7] prose-pre:text-[13px] prose-pre:leading-relaxed prose-pre:text-gray-900
                dark:prose-pre:border-white/10 dark:prose-pre:bg-[#111213] dark:prose-pre:text-gray-100
                prose-a:break-all prose-a:font-medium prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline dark:prose-a:text-blue-400
                ${message.error ? 'text-red-500 dark:text-red-400' : 'text-gray-900 dark:text-gray-100'}
              `}
            >
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
