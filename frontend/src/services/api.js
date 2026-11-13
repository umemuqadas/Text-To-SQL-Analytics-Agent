const API_BASE = import.meta.env.VITE_API_BASE || '/api/v1'

async function request(path, options = {}) {
  // A multipart body must keep the browser's own Content-Type (with boundary).
  const headers = options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }
  const response = await fetch(`${API_BASE}${path}`, { headers, ...options })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(typeof detail.detail === 'string' ? detail.detail : JSON.stringify(detail))
  }
  return response.json()
}

export const askQuestion = (question, conversationId) =>
  request('/ask', { method: 'POST', body: JSON.stringify({ question, conversation_id: conversationId }) })

export const fetchSchema = () => request('/schema')

export const fetchSuggestions = () => request('/suggestions')

export const fetchHealth = () => request('/health')

export const fetchSources = () => request('/datasources')

export const uploadSources = (files) => {
  const form = new FormData()
  files.forEach((file) => form.append('files', file))
  return request('/datasources/upload', { method: 'POST', body: form })
}

export const addConnection = (name, url) =>
  request('/datasources/connection', { method: 'POST', body: JSON.stringify({ name, url }) })

export const setActiveSource = (id) =>
  request('/datasources/active', { method: 'PUT', body: JSON.stringify({ id }) })

export const deleteSource = (id) => request(`/datasources/${id}`, { method: 'DELETE' })

export const fetchHistory = (conversationId) =>
  request(`/history?limit=100${conversationId ? `&conversation_id=${conversationId}` : ''}`)

export const fetchRun = (runId) => request(`/history/${runId}`)

export const clearHistory = (conversationId) =>
  request(`/history${conversationId ? `?conversation_id=${conversationId}` : ''}`, { method: 'DELETE' })

export const validateSql = (sql) =>
  fetch(`${API_BASE}/sql/validate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sql }),
  }).then(async (response) => {
    const text = await response.text()
    if (!response.ok) {
      const detail = text ? JSON.parse(text).detail : 'Rejected'
      throw new Error(detail)
    }
    return text
  })

export const exportUrl = (runId, format = 'csv') => `${API_BASE}/export/${runId}?fmt=${format}`

export const downloadSql = (sql, name = 'query.sql') => {
  const blob = new Blob([sql], { type: 'text/plain' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = name
  link.click()
  URL.revokeObjectURL(url)
}
