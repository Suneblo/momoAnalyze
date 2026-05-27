import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSettingsStore } from './settingsStore'

function sortRowsByDateAsc(rows) {
  return [...(rows || [])].sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')))
}

function asNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function pick(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== '') return value
  }
  return undefined
}


function normalizeStudyStatus(day, row) {
  const raw = day.studyStatus
  const today = raw.today || {}
  const overall = raw.overall || {}
  const critical = raw.critical || {}
  const summary = day.summary || {}

  const normalized = {
    version: raw.version || 2,
    date: raw.date || day.date,
    snapshot: pick(raw.snapshot, raw.capturedAt, ''),
    capturedAt: pick(raw.capturedAt, raw.snapshot, ''),
    today,
    overall,
    critical,
    checks: raw.checks || {},
    dbSnapshot: raw.dbSnapshot || {},
  }

  row.studyStatus = normalized

  row.totalWords = asNumber(pick(overall.totalWords, summary.totalWords, row.totalWords))
  row['熟知'] = asNumber(pick(overall.wellKnown, summary['熟知'], row['熟知']))
  row['顽固'] = asNumber(pick(overall.sticking, summary['顽固'], row['顽固']))
  row['认识'] = asNumber(pick(overall.knownState, summary['认识'], row['认识']))
  row['模糊'] = asNumber(pick(overall.vagueState, summary['模糊'], row['模糊']))
  row['忘记'] = asNumber(pick(overall.forgetState, summary['忘记'], row['忘记']))
  row['逾期'] = asNumber(pick(overall.overdue, summary['逾期'], row['逾期']))
  row.avgStudyCount = asNumber(pick(overall.avgStudyCount, summary.avgStudyCount, row.avgStudyCount))

  row.snapshot = pick(normalized.snapshot, row.snapshot, '')
  row.capturedAt = pick(normalized.capturedAt, normalized.snapshot, row.capturedAt, '')
  row.finished = asNumber(pick(today.finished, row.finished, row['已完成']))
  row.total = asNumber(pick(today.total, row.total, row['总数']))
  row.studyTimeMs = asNumber(pick(today.studyTimeMs, row.studyTimeMs))

  row.todayKnownCount = asNumber(pick(today.known, row.todayKnownCount))
  row.todayVagueCount = asNumber(pick(today.vague, row.todayVagueCount))
  row.todayForgetCount = asNumber(pick(today.forget, row.todayForgetCount, row.todayFirstForgetCount))
  row.todayFirstForgetCount = row.todayForgetCount
  row.todayAllKnownCount = asNumber(pick(today.allKnown, row.todayAllKnownCount))
  row.todayAllVagueCount = asNumber(pick(today.allVague, row.todayAllVagueCount))
  row.todayAllForgetCount = asNumber(pick(today.allForget, row.todayAllForgetCount, row.todayAllFirstForgetCount))
  row.todayAllFirstForgetCount = row.todayAllForgetCount

  row.dailyReviewedCount = asNumber(pick(today.reviewDone, row.dailyReviewedCount))
  row.dailyPendingReviewCount = asNumber(pick(today.reviewPending, row.dailyPendingReviewCount))
  row.dailyReviewTaskCount = asNumber(pick(today.reviewTotal, row.dailyReviewTaskCount), row.dailyReviewedCount + row.dailyPendingReviewCount)
  row.dailyAllReviewCount = row.dailyReviewTaskCount

  row.dailyNewLearnedCount = asNumber(pick(today.newDone, row.dailyNewLearnedCount))
  row.dailyPendingNewCount = asNumber(pick(today.newPending, row.dailyPendingNewCount))
  row.dailyNewTaskCount = asNumber(pick(today.newTotal, row.dailyNewTaskCount), row.dailyNewLearnedCount + row.dailyPendingNewCount)
  row.dailyAllNewCount = row.dailyNewTaskCount

  row.unfinishedCount = asNumber(pick(today.unfinished, row.unfinishedCount), Math.max(row.total - row.finished, 0))
  row.criticalDueToday = asNumber(critical.dueToday)
  row.criticalDueByOffset = critical.dueByOffset || {}
  row.memoryNextDueItems = Array.isArray(critical.memoryNextDueItems)
    ? critical.memoryNextDueItems
    : (Array.isArray(summary.memoryNextDueItems) ? summary.memoryNextDueItems : (row.memoryNextDueItems || []))
}

export const useDataStore = defineStore('data', () => {
  const rawRows = ref([])
  const displayRows = ref([])
  const todayWorkspace = ref({ date: '', snapshots: [], allProgressText: '', error: '' })
  const alerts = ref([])
  const renderMeta = ref({ start: '', end: '', scanned: 0, dataDays: 0 })
  const wordList = ref({ items: [], total: 0, page: 1, date: '' })
  const loaded = computed(() => rawRows.value.length > 0)

  function computeDisplayRows() {
    const settings = useSettingsStore()
    const rows = sortRowsByDateAsc(rawRows.value)
    if (!rows?.length) {
      displayRows.value = []
      renderMeta.value = { start: '', end: '', scanned: 0, dataDays: 0 }
      return
    }

    let filtered = rows.filter(r => r.date && r.hasData)
    const preset = settings.rangePreset || '30'
    const customDays = settings.customDays || 30
    const refDate = new Date()
    refDate.setHours(4, 0, 0, 0)

    if (preset === '30') {
      const cutoff = new Date(refDate.getTime() - 30 * 86400000).toISOString().slice(0, 10)
      filtered = filtered.filter(r => r.date >= cutoff)
    } else if (preset === '7') {
      const cutoff = new Date(refDate.getTime() - 7 * 86400000).toISOString().slice(0, 10)
      filtered = filtered.filter(r => r.date >= cutoff)
    } else if (preset === '90') {
      const cutoff = new Date(refDate.getTime() - 90 * 86400000).toISOString().slice(0, 10)
      filtered = filtered.filter(r => r.date >= cutoff)
    } else if (preset === 'custom') {
      const cutoff = new Date(refDate.getTime() - customDays * 86400000).toISOString().slice(0, 10)
      filtered = filtered.filter(r => r.date >= cutoff)
    }

    if (settings.hideEmptyDays) {
      filtered = filtered.filter(r => r.hasData || r.hasOverviewData || r.hasProgressData)
    }

    displayRows.value = sortRowsByDateAsc(filtered)

    if (displayRows.value.length) {
      renderMeta.value = {
        start: displayRows.value[0]?.date || '',
        end: displayRows.value[displayRows.value.length - 1]?.date || '',
        scanned: rawRows.value.length,
        dataDays: displayRows.value.length,
      }
    } else {
      renderMeta.value = { start: '', end: '', scanned: rawRows.value.length, dataDays: 0 }
    }
  }

  function setDashboardData(data) {
    if (data.days) {
      rawRows.value = sortRowsByDateAsc(data.days.map(day => {
        const row = { ...day }
        if (day.summary) {
          for (const [k, v] of Object.entries(day.summary)) row[k] = v
        }
        row.overviewUpdateTime = day.overviewUpdateTime || row.overviewUpdateTime || ''
        normalizeStudyStatus(day, row)
        row.hasData = !!day.date
        row.hasOverviewData = !!day.summary || !!row.studyStatus?.overall
        row.hasProgressData = !!row.studyStatus?.today || !!day.latestProgress
        return row
      }))
      computeDisplayRows()
    }
    if (data.todayWorkspace) todayWorkspace.value = data.todayWorkspace
    if (data.alerts) alerts.value = data.alerts
  }

  function refreshDisplayRows() {
    computeDisplayRows()
  }

  return {
    rawRows, displayRows, todayWorkspace, alerts, renderMeta,
    wordList, loaded,
    setDashboardData, refreshDisplayRows,
  }
})
