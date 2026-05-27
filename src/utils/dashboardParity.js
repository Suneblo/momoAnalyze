import {
  MEMORY_MODE_CRITICAL_POINT,
  MEMORY_MODE_REVIEW_SPAN,
  memoryThresholdMatches,
  parseMemoryThresholdSpecsFromText,
  thresholdIntersectsCriticalPoint,
  thresholdIntersectsReviewSpan,
} from '@/utils/memoryThresholds'

export const WORD_LIST_COLUMNS = [
  { key: 'word', label: '单词', default: true, sort: 'word' },
  { key: 'vocId', label: 'voc_id', default: false, sort: 'voc_id' },
  { key: 'currentState', label: '当前状态', default: true, sort: 'current_state' },
  { key: 'isOverdue', label: '是否逾期', default: true, sort: 'overdue_first' },
  { key: 'studyCount', label: '学习次数', default: true, sort: 'study_count' },
  { key: 'addDate', label: '加入日期', default: true, sort: 'add_date' },
  { key: 'firstStudyDate', label: '首次学习日期', default: true, sort: 'first_study_date' },
  { key: 'lastStudyDate', label: '最近学习日期', default: true, sort: 'last_study_date' },
  { key: 'nextStudyDate', label: '下次复习日期', default: true, sort: 'next_study_date' },
  { key: 'lastResponse', label: '最近一次反应', default: true },
  { key: 'tags', label: '标签', default: true },
  { key: 'reviewSpanDays', label: '记忆持久度', default: false, sort: 'review_span' },
  { key: 'criticalDays', label: '遗忘临界点', default: false, sort: 'critical_day' },
  { key: 'todayOrder', label: '今日顺序', default: false, sort: 'today_order' },
  { key: 'todayFirstResponse', label: '今日首次反应', default: false },
  { key: 'todayIsNew', label: '今日是否新词', default: false },
  { key: 'todayIsFinished', label: '今日是否完成', default: false },
]

export const SUMMARY_TABLE_PARAMS = [
  { key: 'date', label: '日期', table: true, group: 'base' },
  { key: 'overviewUpdateTime', label: '更新时间', subLabel: '总览更新时分秒', table: true, group: 'base', format: v => v || '-' },
  { key: 'totalWords', label: '总词数', table: true, group: 'overview' },
  { key: 'todayFirstForgetCount', label: '今日首次忘记数', subLabel: '已完成单词', table: true, group: 'progress' },
  { key: 'dailyNewLearnedCount', label: '当日已新学', subLabel: '已完成新词', table: true, group: 'progress' },
  { key: 'dailyPendingNewCount', label: '当日待新学', subLabel: '已选未学新词', table: true, group: 'progress' },
  { key: 'dailyReviewedCount', label: '当日已复习', subLabel: '已完成复习', table: true, group: 'progress' },
  {
    key: 'dailyAllReviewAvgStudyCount',
    label: '当日复习平均学习次数',
    subLabel: '全部复习词累计平均',
    table: true,
    group: 'base',
    format: v => Number.isFinite(Number(v)) ? Number(v).toFixed(2) : '-',
  },
  { key: 'finished', label: '已完成', subLabel: '实际完成数量', table: true, group: 'progress' },
  { key: 'total', label: '总数', subLabel: '每日目标数量', table: true, group: 'progress' },
  {
    key: 'studyTimeText',
    valueKey: 'studyTimeMs',
    label: '学习时长',
    subLabel: '当天用时',
    table: true,
    group: 'study_time',
    format: (v, row) => row?.hasProgressData ? (row.studyTimeText || formatDuration(row.studyTimeMs)) : '-',
  },
  { key: '熟知', label: '熟知', table: true, group: 'overview' },
  { key: '顽固', label: '顽固', table: true, group: 'overview' },
  { key: '逾期', label: '逾期', table: true, group: 'overview' },
  { key: '忘记', label: '忘记', table: true, group: 'overview' },
  { key: '模糊', label: '模糊', table: true, group: 'overview' },
  { key: '认识', label: '认识', table: true, group: 'overview' },
]

const OVERVIEW_TRAILING_TABLE_PARAM_KEYS = ['熟知', '顽固', '逾期', '忘记', '模糊', '认识']
const DAILY_TRAILING_TABLE_PARAM_KEYS = [
  'todayFirstForgetCount',
  'dailyNewLearnedCount',
  'dailyPendingNewCount',
  'dailyReviewedCount',
  'dailyAllReviewAvgStudyCount',
  'finished',
  'total',
  'studyTimeText',
  'overviewUpdateTime',
]
const TRAILING_TABLE_PARAM_KEYS = OVERVIEW_TRAILING_TABLE_PARAM_KEYS.concat(DAILY_TRAILING_TABLE_PARAM_KEYS)

export function getSummaryTableParams() {
  return SUMMARY_TABLE_PARAMS.filter(x => x.table)
}

export function getMainSummaryTableParams() {
  return getSummaryTableParams().filter(x => !TRAILING_TABLE_PARAM_KEYS.includes(x.key))
}

export function getTrailingSummaryTableParams() {
  return OVERVIEW_TRAILING_TABLE_PARAM_KEYS
    .concat(DAILY_TRAILING_TABLE_PARAM_KEYS)
    .map(key => SUMMARY_TABLE_PARAMS.find(x => x.key === key && x.table))
    .filter(Boolean)
}

export function sortRowsByDateAsc(rows) {
  return [...(rows || [])].sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')))
}

export function displayRowsOnly(rows) {
  return sortRowsByDateAsc((rows || []).filter(row => row && !row.isGapMarker && row.date))
}

export function formatDuration(ms) {
  const totalMs = Number(ms) || 0
  if (!totalMs) return '-'
  const totalMinutes = Math.round(totalMs / 60000)
  if (totalMinutes < 60) return `${totalMinutes}分`
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes > 0 ? `${hours}时${minutes}分` : `${hours}时`
}

export function getParamValue(row, param) {
  if (!row) return NaN
  if (param.key === 'date') return row.date || '-'
  if (param.key === 'overviewUpdateTime') return row.overviewUpdateTime || '-'
  if (param.key === 'studyTimeText') return row.hasProgressData ? (row.studyTimeText || formatDuration(row.studyTimeMs)) : '-'
  if (param.key === 'dailyAllReviewAvgStudyCount') {
    const n = Number(row[param.key])
    return Number.isFinite(n) ? n : NaN
  }
  if (['totalWords', '熟知', '顽固', '认识', '模糊', '忘记', '逾期'].includes(param.key)) {
    return row.hasOverviewData ? Number(row[param.key] || 0) : NaN
  }
  if (['todayFirstForgetCount', 'dailyNewLearnedCount', 'dailyPendingNewCount', 'dailyReviewedCount', 'finished', 'total'].includes(param.key)) {
    return row.hasProgressData ? Number(row[param.key] || 0) : NaN
  }
  const v = row[param.key]
  return Number.isFinite(Number(v)) ? Number(v) : (v || '-')
}

export function formatParamValue(row, param) {
  if (param?.format) return param.format(row?.[param.key], row)
  const value = getParamValue(row, param)
  return Number.isFinite(value) ? String(value) : (value || '-')
}

export function getThresholdModes(spec) {
  const modes = []
  if (thresholdIntersectsCriticalPoint(spec)) modes.push(MEMORY_MODE_CRITICAL_POINT)
  if (thresholdIntersectsReviewSpan(spec)) modes.push(MEMORY_MODE_REVIEW_SPAN)
  return modes
}

export function isExactZeroThreshold(spec) {
  return spec && Number(spec.lower) === 0 && Number(spec.upper) === 0 && spec.lowerInclusive && spec.upperInclusive
}

export function getMemoryColumnLabel(spec, mode) {
  const label = spec?.headerLabel || ''
  if (mode === MEMORY_MODE_CRITICAL_POINT) {
    return `遗忘临界点${label}${isExactZeroThreshold(spec) ? '（当日待复习）' : ''}`
  }
  return `记忆持久度${label}`
}

export function parseMemoryThresholds(text) {
  const specs = parseMemoryThresholdSpecsFromText(text || '')
  return specs.length ? specs : parseMemoryThresholdSpecsFromText('1,2,3,4,5,6,7,15,30')
}

export function computeMemoryThresholdStats(items, thresholds, mode) {
  const out = { counts: {}, avgs: {} }
  for (const spec of thresholds || []) {
    const matched = (items || []).filter(item => memoryThresholdMatches(spec, Number(item.days), mode))
    out.counts[spec.id] = matched.length
    const values = matched.map(item => Number(item.studyCount)).filter(Number.isFinite)
    out.avgs[spec.id] = values.length ? values.reduce((a, b) => a + b, 0) / values.length : NaN
  }
  return out
}

export function getMemoryStatsForRow(row, thresholds, mode) {
  const statKey = mode === MEMORY_MODE_CRITICAL_POINT ? 'memoryCriticalStats' : 'memoryReviewSpanStats'
  if (row?.[statKey]?.counts) return row[statKey]
  const items = mode === MEMORY_MODE_CRITICAL_POINT ? (row?.memoryNextDueItems || []) : (row?.memoryReviewSpanItems || row?.memoryDiffItems || [])
  return computeMemoryThresholdStats(items, thresholds, mode)
}

export function formatMemoryThresholdCell(row, spec, mode, thresholds = null) {
  if (!row || !row.hasOverviewData || !spec) return '-'
  const stats = getMemoryStatsForRow(row, thresholds || [spec], mode)
  if (!stats?.counts || typeof stats.counts[spec.id] === 'undefined') return '-'
  const count = Number(stats.counts[spec.id] || 0)
  const avg = Number(stats.avgs?.[spec.id])
  return `${count}/${Number.isFinite(avg) ? avg.toFixed(2) : '-'}`
}

export function buildSummaryColumns(thresholdText) {
  const thresholds = parseMemoryThresholds(thresholdText)
  // =0、<0、<1 这类阈值只显示临界点/逾期含义；>=1 的阈值同时显示记忆持久度和临界点。
  const keepParamKeys = ['date', 'overviewUpdateTime', 'dailyAllReviewAvgStudyCount']
  const baseColumns = getSummaryTableParams()
    .filter(param => keepParamKeys.includes(param.key))
    .map(param => ({
      key: param.key,
      label: param.label,
      subLabel: param.subLabel || '',
      formatter: row => formatParamValue(row, param),
    }))

  const memoryThresholdColumns = thresholds.flatMap(spec => {
    const columns = []

    if (thresholdIntersectsCriticalPoint(spec)) {
      columns.push({
        key: `${MEMORY_MODE_CRITICAL_POINT}:${spec.id}`,
        label: getMemoryColumnLabel(spec, MEMORY_MODE_CRITICAL_POINT),
        subLabel: '数量/平均学习次数',
        formatter: row => formatMemoryThresholdCell(row, spec, MEMORY_MODE_CRITICAL_POINT, thresholds),
      })
    }

    if (thresholdIntersectsReviewSpan(spec)) {
      columns.push({
        key: `${MEMORY_MODE_REVIEW_SPAN}:${spec.id}`,
        label: getMemoryColumnLabel(spec, MEMORY_MODE_REVIEW_SPAN),
        subLabel: '数量/平均学习次数',
        formatter: row => formatMemoryThresholdCell(row, spec, MEMORY_MODE_REVIEW_SPAN, thresholds),
      })
    }

    return columns
  })

  return baseColumns.concat(memoryThresholdColumns)
}

export function markdownEscapeCell(value) {
  if (value === null || typeof value === 'undefined') return '-'
  const s = String(value)
  if (!s || s === 'NaN') return '-'
  return s.replace(/\|/g, '\\|').replace(/\r?\n/g, '<br>')
}

export function markdownTable(headers, rows) {
  if (!rows.length) return '_无数据_'
  const head = `| ${headers.map(markdownEscapeCell).join(' | ')} |`
  const sep = `| ${headers.map(() => '---').join(' | ')} |`
  const body = rows.map(row => `| ${row.map(markdownEscapeCell).join(' | ')} |`)
  return [head, sep, ...body].join('\n')
}

export function buildSummaryMarkdown(rows, thresholdText) {
  const cleanRows = displayRowsOnly(rows).filter(row => row.hasData !== false)
  const columns = buildSummaryColumns(thresholdText)
  const headers = columns.map(col => col.subLabel ? `${col.label}（${col.subLabel}）` : col.label)
  const tableRows = cleanRows.map(row => columns.map(col => col.formatter(row)))
  return markdownTable(headers, tableRows)
}

export function formatWordValue(item, key) {
  const v = item?.[key]
  if (key === 'word') return item?.word || ''
  if (key === 'todayIsNew' || key === 'todayIsFinished') return v === true ? '是' : (v === false ? '否' : '')
  if (key === 'isOverdue') return v === true ? '是' : (v === false ? '否' : '')
  if (key === 'studyCount') return v === null || typeof v === 'undefined' ? '' : String(v)
  if (key === 'reviewSpanDays') return v === null || typeof v === 'undefined' ? '' : `${v}天`
  if (key === 'criticalDays') {
    if (v === null || typeof v === 'undefined') return ''
    const n = Number(v)
    if (!Number.isFinite(n)) return String(v)
    return n < 0 ? `逾期${Math.abs(n)}天` : (n === 0 ? '今天' : `${n}天后`)
  }
  if (Array.isArray(v)) return v.join(',')
  return v === null || typeof v === 'undefined' ? '' : String(v)
}

export function wordListSortLabel(sortKey) {
  const found = WORD_LIST_COLUMNS.find(c => c.sort === sortKey)
  if (found) return found.label
  if (sortKey === 'overdue_first') return '逾期优先'
  return sortKey || ''
}

export function buildWordListText(items, meta = {}, visibleColumnKeys = null, sort = 'word', dir = 'asc', mode = 'page') {
  const selectedKeys = Array.isArray(visibleColumnKeys) && visibleColumnKeys.length
    ? visibleColumnKeys
    : WORD_LIST_COLUMNS.filter(c => c.default).map(c => c.key)
  const columns = WORD_LIST_COLUMNS.filter(c => selectedKeys.includes(c.key))
  const lines = []
  lines.push('# 单词列表')
  lines.push(`日期：${meta.date || '-'}`)
  lines.push(`快照：${meta.snapshotTime || '-'}`)
  lines.push(`筛选结果：${meta.total ?? items.length} 条；当前输出：${items.length} 条${mode === 'page' ? `；第 ${meta.page || 1} 页` : ''}`)
  lines.push(`排序：${wordListSortLabel(sort)} ${dir === 'asc' ? '升序' : '降序'}`)
  lines.push('')
  lines.push(columns.map(c => c.label).join('\t'))
  for (const item of items || []) {
    lines.push(columns.map(c => formatWordValue(item, c.key)).join('\t'))
  }
  return lines.join('\n')
}

export function formatPredictionCount(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  if (Math.abs(n) >= 100 || Number.isInteger(n)) return String(Math.round(n))
  return n.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}
