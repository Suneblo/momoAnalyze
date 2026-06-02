import { ref } from 'vue'

const BASE = ''
const CLOUD_WRITE_CONFIRM_HEADER = 'X-Momo-Cloud-Write-Confirm'
const CLOUD_WRITE_CONFIRM_VALUE = 'waited-5s'
const inFlightGetRequests = new Map()

function cacheBust(url) {
  const sep = url.includes('?') ? '&' : '?'
  return `${BASE}${url}${sep}_momo_t=${Date.now()}`
}

function pageIsHidden() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden'
}

function waitUntilPageVisible() {
  if (!pageIsHidden()) return Promise.resolve()
  return new Promise(resolve => {
    const onVisible = () => {
      if (pageIsHidden()) return
      document.removeEventListener('visibilitychange', onVisible)
      resolve()
    }
    document.addEventListener('visibilitychange', onVisible)
  })
}

function cloudWriteHeaders(headers = {}) {
  return {
    ...headers,
    [CLOUD_WRITE_CONFIRM_HEADER]: CLOUD_WRITE_CONFIRM_VALUE,
  }
}

function apiResponseError(url, status, statusText, contentType, detail = '') {
  const http = status ? `HTTP ${status}${statusText ? ` ${statusText}` : ''}` : '网络请求失败'
  const type = contentType ? `，响应类型：${contentType}` : ''
  const suffix = detail ? `；${detail}` : ''
  return `${http}${type}：后端接口没有返回可用 JSON。请确认 Python 后端已启动，并且后端端口没有被前端/静态服务占用。接口：${url}${suffix}`
}

async function parseJsonResponse(resp, url) {
  const contentType = resp.headers.get('content-type') || ''
  const text = await resp.text()
  const trimmed = text.trim()
  const lowerType = contentType.toLowerCase()
  const looksJson = lowerType.includes('json') || trimmed.startsWith('{') || trimmed.startsWith('[')

  if (!looksJson) {
    return {
      success: false,
      status: resp.status,
      contentType,
      error: apiResponseError(url, resp.status, resp.statusText, contentType),
    }
  }

  let data
  try {
    data = trimmed ? JSON.parse(trimmed) : {}
  } catch (e) {
    return {
      success: false,
      status: resp.status,
      contentType,
      error: apiResponseError(url, resp.status, resp.statusText, contentType, e.message || String(e)),
    }
  }

  if (!resp.ok) {
    const payload = data && typeof data === 'object' && !Array.isArray(data) ? data : { result: data }
    return {
      ...payload,
      success: false,
      status: resp.status,
      contentType,
      error: payload.error || payload.message || `接口请求失败：HTTP ${resp.status}${resp.statusText ? ` ${resp.statusText}` : ''}`,
    }
  }

  return data
}

export function useApi() {
  const loading = ref(false)
  const error = ref(null)

  async function fetchJson(url, options = {}) {
    const {
      dedupe = true,
      waitForVisible = true,
      ...fetchOptions
    } = options
    const requestKey = `${BASE}${url}`

    if (waitForVisible) await waitUntilPageVisible()
    if (dedupe && inFlightGetRequests.has(requestKey)) {
      return inFlightGetRequests.get(requestKey)
    }

    const requestPromise = (async () => {
      loading.value = true
      error.value = null
      try {
        const resp = await fetch(cacheBust(url), { cache: 'no-store', ...fetchOptions })
        const data = await parseJsonResponse(resp, url)
        if (data?.success === false && data.error) error.value = data.error
        return data
      } catch (e) {
        error.value = e.message || String(e)
        return { success: false, error: error.value }
      } finally {
        loading.value = false
      }
    })()

    if (dedupe) {
      inFlightGetRequests.set(requestKey, requestPromise)
    }

    try {
      const data = await requestPromise
      return data
    } finally {
      if (inFlightGetRequests.get(requestKey) === requestPromise) {
        inFlightGetRequests.delete(requestKey)
      }
    }
  }

  async function fetchJsonImmediate(url, options = {}) {
    return fetchJson(url, { waitForVisible: false, ...options })
  }

  async function fetchJsonNoDedupe(url, options = {}) {
    return fetchJson(url, { dedupe: false, ...options })
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

  async function fetchTodayCustomWords(date) {
    const q = date ? `?date=${encodeURIComponent(date)}` : ''
    return fetchJson(`/api/custom-words/today${q}`)
  }

  async function saveCustomWord(payload = {}) {
    return postJson('/api/custom-words', payload)
  }

  async function fetchTimepointDiff(options = {}) {
    const params = new URLSearchParams()
    if (options.date) params.set('date', String(options.date))
    if (options.snapshotA) params.set('snapshotA', String(options.snapshotA))
    if (options.snapshotB) params.set('snapshotB', String(options.snapshotB))
    if (options.memoryThresholds) params.set('memoryThresholds', String(options.memoryThresholds))
    if (options.criticalFutureDays !== undefined) params.set('criticalFutureDays', String(options.criticalFutureDays))
    if (Array.isArray(options.copyKeys) && options.copyKeys.length) params.set('copyKeys', options.copyKeys.join(','))
    if (options.copyDetails) params.set('copyDetails', JSON.stringify(options.copyDetails))
    const q = params.toString()
    return fetchJson(`/api/timepoint-diff${q ? `?${q}` : ''}`)
  }

  async function fetchNotepads(limit = 10, offset = 0) {
    return fetchJson(`/api/notepads?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`)
  }

  async function fetchNotepad(id) {
    return fetchJson(`/api/notepads/${encodeURIComponent(id)}`)
  }

  async function syncFromApi() {
    inFlightGetRequests.clear()
    loading.value = true
    try {
      const resp = await fetch(cacheBust('/api/sync'), { method: 'POST', cache: 'no-store' })
      if (resp.headers.get('Transfer-Encoding') === 'chunked') {
        const reader = resp.body.getReader()
        const decoder = new TextDecoder('utf-8')
        let buffer = '', finalResult = null, finalError = ''
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
              } else if (data.error) {
                finalError = String(data.error)
              }
            } catch (e) { /* ignore parse errors */ }
          }
        }
        if (finalResult) return finalResult
        if (finalError) return { success: false, error: finalError }
        return { success: false, error: '同步接口未返回结果' }
      }
      const data = await parseJsonResponse(resp, '/api/sync')
      if (data?.success === false && data.error) error.value = data.error
      return data
    } catch (e) {
      error.value = e.message || String(e)
      return { success: false, error: error.value }
    } finally {
      inFlightGetRequests.clear()
      loading.value = false
    }
  }

  async function postJson(url, body, options = {}) {
    inFlightGetRequests.clear()
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
      const data = await parseJsonResponse(resp, url)
      if (data?.success === false && data.error) error.value = data.error
      return data
    } catch (e) {
      error.value = e.message || String(e)
      return { success: false, error: error.value }
    } finally {
      inFlightGetRequests.clear()
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

  async function fetchFsrsPredictionDay(payload = {}) {
    return postJson('/api/fsrs-prediction-day', payload)
  }

  async function fetchFsrsPredictionTrace(payload = {}) {
    return postJson('/api/fsrs-prediction-trace', payload)
  }

  return {
    loading,
    error,
    fetchJson,
    fetchJsonImmediate,
    fetchJsonNoDedupe,
    fetchDashboardPage,
    fetchAlerts,
    fetchTodayWorkspace,
    fetchTodayCustomWords,
    saveCustomWord,
    fetchTimepointDiff,
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
    fetchFsrsPredictionDay,
    fetchFsrsPredictionTrace,
  }
}
