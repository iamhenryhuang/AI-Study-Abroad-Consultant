import { X } from 'lucide-react'

interface Props {
  isOpen: boolean
  onClose: () => void
}

export function UserProfileModal({ isOpen, onClose }: Props) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative bg-white dark:bg-[#2a2a2a] rounded-2xl shadow-xl w-full max-w-lg overflow-hidden animate-fade-in-up">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">使用者資料 User Profile</h2>
          <button
            onClick={onClose}
            className="text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors p-1 rounded-md hover:bg-gray-100 dark:hover:bg-gray-700"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form Body - UI Only */}
        <div className="px-6 py-6 space-y-5 max-h-[70vh] overflow-y-auto custom-scrollbar">

          <div className="space-y-1">
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">目標科系</h3>
            <input
              type="text"
              placeholder="例如：Computer Science, Data Science"
              className="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg text-sm text-gray-800 dark:text-gray-100 bg-white dark:bg-[#333] placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:focus:ring-indigo-400 focus:border-indigo-500 dark:focus:border-indigo-400"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">GPA</h3>
              <input
                type="text"
                placeholder="例如：3.8 / 4.0"
                className="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg text-sm text-gray-800 dark:text-gray-100 bg-white dark:bg-[#333] placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:focus:ring-indigo-400 focus:border-indigo-500 dark:focus:border-indigo-400"
              />
            </div>
            <div className="space-y-1">
              <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">語文成績 (TOEFL/IELTS)</h3>
              <input
                type="text"
                placeholder="例如：TOEFL 105"
                className="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg text-sm text-gray-800 dark:text-gray-100 bg-white dark:bg-[#333] placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:focus:ring-indigo-400 focus:border-indigo-500 dark:focus:border-indigo-400"
              />
            </div>
          </div>

          <div className="space-y-1">
            <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">研究經驗 / 工作經驗簡述</h3>
            <textarea
              rows={3}
              placeholder="請簡短描述您的相關經驗..."
              className="w-full px-3 py-2 border border-gray-200 dark:border-gray-700 rounded-lg text-sm text-gray-800 dark:text-gray-100 bg-white dark:bg-[#333] placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:focus:ring-indigo-400 focus:border-indigo-500 dark:focus:border-indigo-400 resize-none"
            />
          </div>

        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-gray-50 dark:bg-[#222222] border-t border-gray-100 dark:border-gray-700 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-white dark:bg-[#333] border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm font-medium rounded-lg shadow-sm transition-colors"
          >
            取消
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-black dark:bg-white hover:bg-gray-800 dark:hover:bg-gray-200 text-white dark:text-black text-sm font-medium rounded-lg shadow-sm transition-colors"
          >
            儲存資料
          </button>
        </div>
      </div>
    </div>
  )
}
