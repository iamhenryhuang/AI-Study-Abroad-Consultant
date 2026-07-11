const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

async function request(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const validation = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).join('、')
      : body.detail
    throw new Error(validation || '伺服器暫時無法處理，請稍後再試')
  }
  return body
}

export function uploadExperience(payload) {
  return request('/api/experiences', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function searchExperiences({ school, program = '', limit = 20, offset = 0 }) {
  const params = new URLSearchParams({ apply_school: school, limit, offset })
  if (program.trim()) params.set('apply_program', program.trim())
  return request(`/api/experiences?${params}`)
}
