import { ref } from 'vue'

const BASE = ''
const CLOUD_WRITE_CONFIRM_HEADER = 'X-Momo-Cloud-Write-Confirm'
const CLOUD_WRITE_CONFIRM_VALUE = 'waited-5s'

function cacheBust(url) {
  const sep = url.includes('?') ? '&' : '?'
  return `${BASE}${url}${sep}_momo_t=${Date.now()}`
}

function cloudWriteHeaders(headers = {}) {
  return {
    ...headers,
    [CLOUD_WRITE_CONFIRM_HEADER]: CLOUD_WRITE_CONFIRM_VALUE,
  }
}

export function useApi() {
  const loading = ref(false)
  const error = ref(null)

  async function fetchJson(url, options = {}) {
    loading.value = true
    error.value = null
    try {
      const resp = await fetch(cacheBust(url), { cache: 'no-store', ...options })
      const data = await resp.json()
      return data
    } catch (e) {
      error.value = e.message || String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  async function fetchDashboardPage(offset = 0, limit = 3, order = 'asc', options = {}) {
    const params = new URLSearchParams({
      offset: String(offset),
      limit: String(limit),
      order: String(order || 'asc'),
    })
    if (options.memoryThresholds) params.set('memoryThresholds', String(options.memoryThresholds))
    return fetchJson(`/api/dashboard-data-page?${params.toString()}`)
  }

  async function fetchAlerts() {
    return fetchJson('/api/alerts')
  }

  async function fetchTodayWorkspace(date) {
    const q = date ? `?date=${encodeURIComponent(date)}` : ''
    return fetchJson(`/api/today-workspace${q}`)
  }

  async function fetchNotepads(limit = 10, offset = 0) {
    return fetchJson(`/api/notepads?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`)
  }

  async function fetchNotepad(id) {
    return fetchJson(`/api/notepads/${encodeURIComponent(id)}`)
  }

  async function syncFromApi() {
    loading.value = true
    try {
      const resp = await fetch(cacheBust('/api/sync'), { method: 'POST', cache: 'no-store' })
      if (resp.headers.get('Transfer-Encoding') === 'chunked') {
        const reader = resp.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = '', finalResult = null
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''
          for (const line of lines) {
            if (!line.trim()) continue
            try {
              const data = JSON.parse(line)
              if (data.result) {
                finalResult = data.result
              }
            } catch (e) { /* ignore parse errors */ }
          }
        }
        return finalResult
      }
      return resp.json()
    } catch (e) {
      error.value = e.message || String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  async function postJson(url, body, options = {}) {
    loading.value = true
    error.value = null
    try {
      const headers = options.cloudWrite
        ? cloudWriteHeaders(options.headers)
        : { ...(options.headers || {}) }
      const resp = await fetch(cacheBust(url), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify(body),
        cache: 'no-store'
      })
      return resp.json()
    } catch (e) {
      error.value = e.message || String(e)
      return null
    } finally {
      loading.value = false
    }
  }

  async function createNotepad(payload) {
    return postJson('/api/notepads/create', payload, { cloudWrite: true })
  }

  async function updateNotepad(payload) {
    return postJson('/api/notepads/update', payload, { cloudWrite: true })
  }

  async function addWordsToNotepad(payload) {
    return postJson('/api/notepads/add-words', payload, { cloudWrite: true })
  }

  async function analyzeArticle(payload) {
    return postJson('/api/article/analyze', payload)
  }

  async function advanceStudyWords(payload) {
    return postJson('/api/study/advance', payload, { cloudWrite: true })
  }

  async function fetchFsrsPrediction(payload = {}) {
    return postJson('/api/fsrs-prediction', payload)
  }

  return {
    loading,
    error,
    fetchJson,
    fetchDashboardPage,
    fetchAlerts,
    fetchTodayWorkspace,
    fetchNotepads,
    fetchNotepad,
    syncFromApi,
    postJson,
    createNotepad,
    updateNotepad,
    addWordsToNotepad,
    analyzeArticle,
    advanceStudyWords,
    fetchFsrsPrediction,
  }
}
