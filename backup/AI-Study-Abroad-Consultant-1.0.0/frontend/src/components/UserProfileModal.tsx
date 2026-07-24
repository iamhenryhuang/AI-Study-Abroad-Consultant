import { type ReactNode } from 'react'
import { X } from 'lucide-react'

interface Props {
  isOpen: boolean
  onClose: () => void
}

export function UserProfileModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/45 backdrop-blur-sm"
        aria-label="關閉個人資料"
        onClick={onClose}
      />

      <div className="relative w-full max-w-lg overflow-hidden rounded-3xl border border-black/8 bg-white shadow-2xl dark:border-white/10 dark:bg-[#242527]">
        <div className="flex items-center justify-between border-b border-black/6 px-6 py-4 dark:border-white/8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">個人資料</h2>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-xl text-gray-400 transition hover:bg-black/5 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white"
            aria-label="關閉"
          >
            <X size={20} />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-5 overflow-y-auto px-6 py-6 custom-scrollbar">
          <Field label="申請領域">
            <input
              type="text"
              placeholder="例如 Computer Science, Data Science"
              className="field-input"
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="GPA">
              <input type="text" placeholder="例如 3.8 / 4.0" className="field-input" />
            </Field>
            <Field label="英文成績">
              <input type="text" placeholder="例如 TOEFL 105" className="field-input" />
            </Field>
          </div>

          <Field label="研究興趣 / 申請背景">
            <textarea
              rows={4}
              placeholder="簡短描述你的研究、實習、專題或想申請的方向..."
              className="field-input resize-none"
            />
          </Field>
        </div>

        <div className="flex justify-end gap-3 border-t border-black/6 bg-gray-50 px-6 py-4 dark:border-white/8 dark:bg-white/[0.03]">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-black/8 bg-white px-4 py-2 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-white/10 dark:bg-white/[0.04] dark:text-gray-200 dark:hover:bg-white/[0.08]"
          >
            取消
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-[#1f1f1f] px-4 py-2 text-sm font-medium text-white transition hover:bg-black dark:bg-white dark:text-black dark:hover:bg-gray-100"
          >
            儲存
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium text-gray-700 dark:text-gray-300">{label}</span>
      {children}
    </label>
  )
}
