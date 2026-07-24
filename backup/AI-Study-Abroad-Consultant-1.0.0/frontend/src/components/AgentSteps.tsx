import { useState, type ElementType, type ReactNode } from 'react'
import {
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Database,
  FolderSearch,
  GraduationCap,
  Search,
  Sparkles,
  Zap,
} from 'lucide-react'
import type { AgentEvent } from '../types'

const TOOL_LABELS: Record<string, { label: string; icon: ElementType }> = {
  search_general: { label: '搜尋資料庫', icon: Database },
  search_school: { label: '搜尋學校資料', icon: Search },
  search_page_type: { label: '篩選頁面類型', icon: FolderSearch },
  fetch_professor: { label: '查找教授資訊', icon: GraduationCap },
  school_recommend: { label: '產生選校建議', icon: BookOpen },
}

const TOOL_RESULT_LABELS: Record<string, string> = {
  search_general: '已取得搜尋結果',
  search_school: '已取得學校資料',
  search_page_type: '已完成頁面篩選',
  fetch_professor: '已整理教授資訊',
  school_recommend: '已產生選校建議',
}

const LLM_PURPOSE_LABELS: Record<string, string> = {
  planner: '規劃查詢方向',
  finalizer: '整理最終回答',
}

interface Props {
  events: AgentEvent[]
}

export function AgentSteps({ events }: Props) {
  const [open, setOpen] = useState(false)
  const safeEvents = events || []
  const llmCalls = safeEvents.filter((event) => event.type === 'llm_call')
  const searchRounds = safeEvents.filter(
    (event) => event.type === 'thinking' && typeof event.step === 'number',
  ).length
  const hasSteps = safeEvents.some((event) =>
    ['tool_call', 'thinking', 'llm_call', 'tool_result'].includes(event.type),
  )

  if (!hasSteps) return null

  let llmCallIndex = 0

  return (
    <div className="mb-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex max-w-full items-center gap-2 rounded-full border border-black/8 bg-white px-3 py-1.5 text-xs font-medium text-gray-500 shadow-sm transition hover:bg-gray-50 hover:text-gray-800 dark:border-white/10 dark:bg-white/[0.05] dark:text-gray-400 dark:hover:bg-white/[0.08] dark:hover:text-gray-100"
      >
        <span
          className={`grid h-5 w-5 place-items-center rounded-full bg-gray-100 transition dark:bg-white/10 ${
            open ? 'rotate-90' : ''
          }`}
        >
          <ChevronRight size={12} strokeWidth={3} />
        </span>
        <span>查看 AI 思考過程</span>
        <span className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-600 dark:bg-blue-500/12 dark:text-blue-300">
          <Sparkles size={10} />
          Gemini {llmCalls.length}
        </span>
        {searchRounds > 0 && (
          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-500 dark:bg-white/10 dark:text-gray-300">
            {searchRounds} 輪搜尋
          </span>
        )}
      </button>

      {open && (
        <div className="relative mt-3 space-y-0 pl-4 text-sm before:absolute before:inset-y-2 before:left-[7px] before:w-px before:bg-gray-200 dark:before:bg-white/10">
          {safeEvents.map((event, index) => {
            if (event.type === 'thinking' && event.step === 'extension_function') {
              return (
                <StepRow
                  key={index}
                  color="amber"
                  icon={Zap}
                  title="啟用延伸查詢"
                  detail="正在補充教授或選校相關資訊"
                />
              )
            }

            if (event.type === 'thinking' && typeof event.step === 'number') {
              return (
                <StepDot key={index} color="gray">
                  第 {event.step} 輪搜尋與分析
                </StepDot>
              )
            }

            if (event.type === 'tool_call') {
              const toolInfo = TOOL_LABELS[event.tool] || { label: event.tool, icon: Search }
              const Icon = toolInfo.icon
              const isExtension = event.tool === 'fetch_professor' || event.tool === 'school_recommend'

              return (
                <div key={index} className="flex gap-4 py-2">
                  <StepIcon icon={Icon} color={isExtension ? 'amber' : 'blue'} />
                  <div className="min-w-0 flex-1 rounded-2xl border border-black/6 bg-white p-3 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-gray-800 dark:text-gray-200">
                        {toolInfo.label}
                      </span>
                      <span className="shrink-0 rounded-md bg-gray-100 px-1.5 py-0.5 text-[10px] font-mono text-gray-400 dark:bg-white/10">
                        {isExtension ? 'EXT' : 'DB'}
                      </span>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {event.args.query && <Chip>{event.args.query}</Chip>}
                      {event.args.school_id && <Chip>{event.args.school_id.toUpperCase()}</Chip>}
                      {event.args.page_type && <Chip>{event.args.page_type}</Chip>}
                    </div>
                  </div>
                </div>
              )
            }

            if (event.type === 'tool_result') {
              const isExtension = event.tool === 'fetch_professor' || event.tool === 'school_recommend'
              return (
                <StepDot key={index} color={isExtension ? 'amber' : 'emerald'}>
                  <span className="inline-flex items-center gap-1">
                    <CheckCircle2 size={13} />
                    {TOOL_RESULT_LABELS[event.tool] ?? '已取得結果'}
                    {event.preview && (
                      <span className="ml-1 text-gray-400 dark:text-gray-500">{event.preview}</span>
                    )}
                  </span>
                </StepDot>
              )
            }

            if (event.type === 'llm_call') {
              llmCallIndex += 1
              const roundLabel =
                event.purpose === 'planner' && event.round != null ? `，第 ${event.round} 輪` : ''

              return (
                <StepRow
                  key={index}
                  color="blue"
                  icon={BrainCircuit}
                  title={`Gemini API #${llmCallIndex}`}
                  detail={`${LLM_PURPOSE_LABELS[event.purpose] ?? event.purpose}${roundLabel}`}
                />
              )
            }

            return null
          })}
        </div>
      )}
    </div>
  )
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="max-w-full truncate rounded-lg bg-gray-100 px-2 py-1 text-xs text-gray-600 dark:bg-white/10 dark:text-gray-300">
      {children}
    </span>
  )
}

function StepRow({
  color,
  icon,
  title,
  detail,
}: {
  color: 'amber' | 'blue'
  icon: ElementType
  title: string
  detail: string
}) {
  const Icon = icon
  return (
    <div className="flex gap-4 py-2">
      <StepIcon icon={Icon} color={color} />
      <div className="min-w-0 flex-1 rounded-2xl border border-black/6 bg-white p-3 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
        <p className="text-xs font-semibold text-gray-800 dark:text-gray-200">{title}</p>
        <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{detail}</p>
      </div>
    </div>
  )
}

function StepIcon({
  icon,
  color,
}: {
  icon: ElementType
  color: 'amber' | 'blue'
}) {
  const Icon = icon
  const classes =
    color === 'amber'
      ? 'bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300'
      : 'bg-blue-100 text-blue-600 dark:bg-blue-500/15 dark:text-blue-300'

  return (
    <div
      className={`relative z-10 grid h-[18px] w-[18px] shrink-0 place-items-center rounded-full shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#1b1c1d] ${classes}`}
    >
      <Icon size={10} strokeWidth={3} />
    </div>
  )
}

function StepDot({
  color,
  children,
}: {
  color: 'gray' | 'emerald' | 'amber'
  children: ReactNode
}) {
  const dotClasses = {
    gray: 'bg-gray-300 dark:bg-gray-600',
    emerald: 'bg-emerald-400',
    amber: 'bg-amber-400',
  }[color]

  const textClasses = {
    gray: 'text-gray-400 dark:text-gray-500',
    emerald: 'text-emerald-600 dark:text-emerald-400',
    amber: 'text-amber-600 dark:text-amber-400',
  }[color]

  return (
    <div className="flex gap-4 py-1.5">
      <div
        className={`relative z-10 mt-1.5 h-2 w-2 shrink-0 rounded-full shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#1b1c1d] ${dotClasses}`}
      />
      <div className={`text-xs ${textClasses}`}>{children}</div>
    </div>
  )
}
