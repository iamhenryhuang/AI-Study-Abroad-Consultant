import { useRef, useState } from 'react'
import { CheckCircle2, FileText, Trash2, UploadCloud } from 'lucide-react'

export function ResumeUpload() {
  const [file, setFile] = useState<File | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = event.target.files?.[0]
    if (selected && selected.type === 'application/pdf') {
      setFile(selected)
    } else if (selected) {
      alert('請上傳 PDF 檔案')
    }
    event.target.value = ''
  }

  return (
    <div className="mb-6">
      <p className="mb-3 px-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
        Context
      </p>

      <div
        className={`relative mx-1 overflow-hidden rounded-2xl border transition ${
          file
            ? 'border-blue-400/30 bg-blue-500/10'
            : 'cursor-pointer border-dashed border-gray-300 bg-transparent hover:border-blue-400 hover:bg-blue-500/5 dark:border-white/15'
        }`}
        onClick={() => {
          if (!file) fileInputRef.current?.click()
        }}
      >
        <input
          type="file"
          ref={fileInputRef}
          onChange={handleFileChange}
          accept=".pdf,application/pdf"
          className="hidden"
        />

        {file ? (
          <div className="flex items-center gap-3 p-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-500/15 text-blue-500">
              <FileText size={18} />
            </div>
            <div className="min-w-0 flex-1 pr-1">
              <p className="truncate text-sm font-medium text-gray-800 dark:text-gray-100">
                {file.name}
              </p>
              <p className="mt-0.5 flex items-center gap-1 text-[10px] text-blue-500">
                <CheckCircle2 size={10} className="text-emerald-500" />
                已加入申請背景
              </p>
            </div>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation()
                setFile(null)
              }}
              title="移除檔案"
              className="mr-1 rounded-lg p-1.5 text-gray-400 transition hover:bg-red-500/10 hover:text-red-500"
            >
              <Trash2 size={15} />
            </button>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center gap-1.5 p-4 text-center text-gray-400">
            <div className="mb-1 rounded-full bg-gray-100 p-2 dark:bg-white/10">
              <UploadCloud size={20} />
            </div>
            <p className="text-sm font-medium text-gray-600 dark:text-gray-300">上傳履歷 PDF</p>
            <p className="text-[10px] leading-4 text-gray-400">讓 AI 更了解你的申請背景</p>
          </div>
        )}
      </div>
    </div>
  )
}
