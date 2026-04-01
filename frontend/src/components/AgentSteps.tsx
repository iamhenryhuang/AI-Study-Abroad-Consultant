import { useState } from 'react'
import { ChevronRight, Database, Search, FolderSearch, CheckCircle2, Sparkles, BrainCircuit, GraduationCap, BookOpen, Zap } from 'lucide-react'
import type { AgentEvent } from '../types'

const TOOL_LABELS: Record<string, { label: string; icon: React.ElementType }> = {
  search_general:    { label: '全庫向量檢索',      icon: Database },
  search_school:     { label: '特定學校深度檢索',  icon: Search },
  search_page_type:  { label: '分類頁面過濾',      icon: FolderSearch },
  fetch_professor:   { label: '教授資料抓取',      icon: GraduationCap },
  school_recommend:  { label: '備案學校推薦分析',  icon: BookOpen },
}

const TOOL_RESULT_LABELS: Record<string, string> = {
  search_general:    '檢索成功，找到相關文獻',
  search_school:     '檢索成功，找到相關文獻',
  search_page_type:  '檢索成功，找到相關文獻',
  fetch_professor:   '教授資料抓取完成',
  school_recommend:  '備案學校推薦完成',
}

const LLM_PURPOSE_LABELS: Record<string, string> = {
  planner:   '資料充足性評估',
  finalizer: '生成最終回答',
}

interface Props {
  events: AgentEvent[]
}

export function AgentSteps({ events }: Props) {
  const [open, setOpen] = useState(false)
  const safeEvents = events || []

  const llmCalls = safeEvents.filter(e => e.type === 'llm_call')
  // 只計算數字 step（實際搜尋輪次），不包含 extension_function
  const searchRounds = safeEvents.filter(
    e => e.type === 'thinking' && typeof e.step === 'number'
  ).length

  const hasSteps = safeEvents.some(e =>
    e.type === 'tool_call' || e.type === 'thinking' || e.type === 'llm_call'
  )
  if (!hasSteps) return null

  let llmCallIndex = 0

  return (
    <div className="mb-4 mt-2">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-2 text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 transition-colors group cursor-pointer bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm px-3 py-1.5 rounded-full border border-gray-200/50 dark:border-gray-700/50 shadow-sm"
      >
        <div className={`p-0.5 rounded-full bg-gray-100 dark:bg-gray-700 group-hover:bg-gray-200 dark:group-hover:bg-gray-600 transition-colors ${open ? 'rotate-90' : ''}`}>
          <ChevronRight size={12} strokeWidth={3} />
        </div>
        <span>AI 思考過程</span>

        {/* LLM 呼叫次數徽章 */}
        <span className="inline-flex items-center gap-1 bg-violet-50 dark:bg-violet-900/40 text-violet-600 dark:text-violet-300 border border-violet-100 dark:border-violet-800 px-2 py-0.5 rounded-full text-[10px] font-semibold">
          <Sparkles size={9} className="shrink-0" />
          Gemini {llmCalls.length} 次
        </span>

        {searchRounds > 0 && (
          <span className="inline-flex items-center gap-1 bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border border-gray-200 dark:border-gray-700 px-2 py-0.5 rounded-full text-[10px] font-semibold">
            {searchRounds} 輪搜尋
          </span>
        )}
      </button>

      {open && (
        <div className="mt-3 ml-4 space-y-0 text-sm relative before:absolute before:inset-y-2 before:-left-[11px] before:w-px before:bg-gray-200 dark:before:bg-gray-700">
          {safeEvents.map((event, i) => {

            // ── 擴充功能並行啟動 ──────────────────────
            if (event.type === 'thinking' && event.step === 'extension_function') {
              return (
                <div key={i} className="flex gap-4 items-start py-2">
                  <div className="relative z-10 w-[18px] h-[18px] rounded-full bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400 flex items-center justify-center -ml-[4px] shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#212121]">
                    <Zap size={10} strokeWidth={3} />
                  </div>
                  <div className="text-amber-600 dark:text-amber-400 text-xs italic">並行執行擴充功能（教授查詢 / 選校推薦）...</div>
                </div>
              )
            }

            // ── 搜尋輪次 ──────────────────────────────
            if (event.type === 'thinking' && typeof event.step === 'number') {
              return (
                <div key={i} className="flex gap-4 items-start py-2">
                  <div className="relative z-10 w-2 h-2 rounded-full bg-gray-300 dark:bg-gray-600 mt-1.5 shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#212121]" />
                  <div className="text-gray-400 dark:text-gray-500 italic text-xs">第 {event.step} 輪搜尋推演中...</div>
                </div>
              )
            }

            // ── 向量搜尋 / 擴充功能 tool_call ────────
            if (event.type === 'tool_call') {
              const toolInfo = TOOL_LABELS[event.tool] || { label: event.tool, icon: Search }
              const Icon = toolInfo.icon
              const isExtension = event.tool === 'fetch_professor' || event.tool === 'school_recommend'
              return (
                <div key={i} className="flex gap-4 items-start py-2 animate-slide-down">
                  <div className={`relative z-10 w-[18px] h-[18px] rounded-full flex items-center justify-center -ml-[4px] shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#212121] ${isExtension ? 'bg-amber-100 dark:bg-amber-900/50 text-amber-600 dark:text-amber-400' : 'bg-indigo-100 dark:bg-indigo-900/50 text-indigo-600 dark:text-indigo-400'}`}>
                    <Icon size={10} strokeWidth={3} />
                  </div>
                  <div className="flex-1 bg-white dark:bg-[#2a2a2a] border border-gray-100 dark:border-gray-700 rounded-xl p-3 shadow-sm">
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <span className="font-semibold text-gray-700 dark:text-gray-300 text-xs">{toolInfo.label}</span>
                      <span className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">{isExtension ? '外部資料' : '向量 DB'}</span>
                    </div>
                    <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                      {event.args.query && (
                        <span className="text-gray-500 dark:text-gray-400 text-xs bg-gray-50 dark:bg-gray-800 px-2 py-1 rounded truncate max-w-[200px]" title={event.args.query}>
                          "{event.args.query}"
                        </span>
                      )}
                      {event.args.school_id && (
                        <span className="bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300 px-2 py-1 rounded text-[10px] tracking-wide font-medium">
                          {event.args.school_id.toUpperCase()}
                        </span>
                      )}
                      {event.args.page_type && (
                        <span className="bg-emerald-50 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-300 px-2 py-1 rounded text-[10px] tracking-wide font-medium">
                          {event.args.page_type}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              )
            }

            // ── 搜尋 / 擴充結果 ──────────────────────
            if (event.type === 'tool_result') {
              const resultLabel = TOOL_RESULT_LABELS[event.tool] ?? '完成'
              const preview = event.preview
              const isExtension = event.tool === 'fetch_professor' || event.tool === 'school_recommend'
              return (
                <div key={i} className="flex gap-4 items-start py-1">
                  <div className={`relative z-10 w-2 h-2 rounded-full mt-1.5 shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#212121] ${isExtension ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                  <div className={`text-xs flex items-center gap-1 ${isExtension ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                    <CheckCircle2 size={12} className="shrink-0" />
                    <span>{resultLabel}</span>
                    {preview && (
                      <span className="text-gray-400 dark:text-gray-500 ml-1">— {preview}</span>
                    )}
                  </div>
                </div>
              )
            }

            // ── Gemini API 呼叫 ───────────────────────
            if (event.type === 'llm_call') {
              llmCallIndex += 1
              const callNum = llmCallIndex
              const purposeLabel = LLM_PURPOSE_LABELS[event.purpose] ?? event.purpose
              const roundLabel = event.purpose === 'planner' && event.round != null
                ? ` (第 ${event.round} 輪)`
                : ''
              return (
                <div key={i} className="flex gap-4 items-start py-2 animate-slide-down">
                  <div className="relative z-10 w-[18px] h-[18px] rounded-full bg-violet-100 dark:bg-violet-900/50 text-violet-600 dark:text-violet-300 flex items-center justify-center -ml-[4px] shadow-[0_0_0_4px_white] dark:shadow-[0_0_0_4px_#212121]">
                    <BrainCircuit size={10} strokeWidth={2.5} />
                  </div>
                  <div className="flex-1 bg-violet-50/60 dark:bg-violet-900/20 border border-violet-100 dark:border-violet-800/50 rounded-xl p-3 shadow-sm">
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-1.5">
                        <Sparkles size={10} className="text-violet-500 dark:text-violet-400 shrink-0" />
                        <span className="font-semibold text-violet-700 dark:text-violet-300 text-xs">Gemini API</span>
                        <span className="text-violet-500 dark:text-violet-400 text-xs">— {purposeLabel}{roundLabel}</span>
                      </div>
                      <span className="text-[10px] text-violet-400 dark:text-violet-400 font-mono bg-violet-100 dark:bg-violet-900/50 px-1.5 py-0.5 rounded">
                        #{callNum}
                      </span>
                    </div>
                  </div>
                </div>
              )
            }

            return null
          })}

          {/* 底部統計摘要 */}
          <div className="flex items-center gap-3 pt-3 pb-1 border-t border-gray-100 dark:border-gray-800 mt-2">
            <div className="flex items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
              <Sparkles size={10} className="text-violet-400 shrink-0" />
              <span className="font-semibold text-violet-500 dark:text-violet-400">{llmCalls.length}</span>
              <span>次 Gemini 呼叫</span>
            </div>
            <span className="text-gray-200 dark:text-gray-700">·</span>
            <div className="flex items-center gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
              <span className="font-semibold text-gray-500 dark:text-gray-400">{searchRounds}</span>
              <span>輪搜尋推演</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
