import { X } from 'lucide-react'

interface Props {
  isOpen: boolean
  onClose: () => void
}

export function SettingsModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/45 backdrop-blur-sm"
        aria-label="關閉設定"
        onClick={onClose}
      />

      <div className="relative w-full max-w-md overflow-hidden rounded-3xl border border-black/8 bg-white shadow-2xl dark:border-white/10 dark:bg-[#242527]">
        <div className="flex items-center justify-between border-b border-black/6 px-6 py-4 dark:border-white/8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">設定</h2>
          <button
            type="button"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded-xl text-gray-400 transition hover:bg-black/5 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white"
            aria-label="關閉"
          >
            <X size={20} />
          </button>
        </div>

        <div className="px-6 py-10 text-center">
          <p className="text-sm leading-6 text-gray-500 dark:text-gray-400">
            目前尚未開放更多設定。之後可以放模型選擇、回覆語氣或檢索偏好。
          </p>
        </div>

        <div className="flex justify-end border-t border-black/6 bg-gray-50 px-6 py-4 dark:border-white/8 dark:bg-white/[0.03]">
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-[#1f1f1f] px-4 py-2 text-sm font-medium text-white transition hover:bg-black dark:bg-white dark:text-black dark:hover:bg-gray-100"
          >
            完成
          </button>
        </div>
      </div>
    </div>
  )
}
