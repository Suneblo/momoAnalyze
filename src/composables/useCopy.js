// Copy functionality composable
import { useCardStore } from '@/stores/cardStore'
import { useDataStore } from '@/stores/dataStore'
import { usePredictionStore } from '@/stores/predictionStore'
import { useSettingsStore } from '@/stores/settingsStore'
import {
  buildSummaryMarkdown,
  displayRowsOnly,
  formatDuration,
  formatPredictionCount,
  markdownTable,
  sortRowsByDateAsc,
} from '@/utils/dashboardParity'
import {
  buildPredictionMarkdown,
  buildProbabilityTableRows,
} from '@/utils/dashboardPrediction'
import { countReviewSpanForThreshold, getReviewSpanThresholds } from '@/utils/memoryThresholds'


const STUDY_STATUS_CARD_ID = '复习情况'
const ROW_DEPENDENT_COPY_KEYS = new Set(['summary', 'overviewChart', 'studyTime', 'memory'])

function hasUsefulCopyContent(copyKey, markdown) {
  const text = String(markdown || '').trim()
  if (!text) return false
  if (copyKey === 'notes') return true
  const emptyPatterns = [
    '_未勾选',
    '_暂无',
    '_无数据_',
    '暂无预测数据',
    '暂无复习概率分桶数据',
    '未查询单词列表',
    '暂无时间点',
    '暂无当日时间点数据',
    '暂无快照数据',
    '快照不足两个',
  ]
  return !emptyPatterns.some(pattern => text.includes(pattern))
}

function formatCount(n) {
  const value = Number(n)
  if (!Number.isFinite(value)) return '-'
  return String(Math.round(value))
}

function formatDecimal(n, digits = 1) {
  const value = Number(n)
  if (!Number.isFinite(value)) return '-'
  return value.toFixed(digits)
}

function formatValue(...values) {
  for (const value of values) {
    if (value !== undefined && value !== null && value !== '') return value
  }
  return '-'
}

function respCn(value) {
  const key = String(value ?? '').trim().toLowerCase()
  const map = {
    known: '认识',
    know: '认识',
    k: '认识',
    认识: '认识',
    vague: '模糊',
    fuzzy: '模糊',
    v: '模糊',
    模糊: '模糊',
    forget: '忘记',
    forgotten: '忘记',
    forgot: '忘记',
    f: '忘记',
    忘记: '忘记',
    easy: '熟知',
    熟知: '熟知',
    hard: '顽固',
    顽固: '顽固',
  }
  return map[key] || value || '-'
}


function escapeCsv(value) {
  const text = String(value ?? '')
  if (/[",\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`
  return text
}

function rowsToCSV(headers, rows) {
  const safeHeaders = Array.isArray(headers) ? headers : []
  const safeRows = Array.isArray(rows) ? rows : []
  const lines = [safeHeaders.map(escapeCsv).join(',')]
  for (const row of safeRows) {
    lines.push(safeHeaders.map(header => escapeCsv(row?.[header])).join(','))
  }
  return lines.join('\n')
}

function getDisplayRows() {
  const dataStore = useDataStore()
  const rows = dataStore.displayRows?.length
    ? dataStore.displayRows.filter(row => !row.isGapMarker)
    : dataStore.rawRows || []
  return sortRowsByDateAsc(rows)
}

function getCopyDetailEnabled(cardStore, cardId, detailKey) {
  const detailState = cardStore.copyDetail?.[cardId]
  if (!detailState || !Object.prototype.hasOwnProperty.call(detailState, detailKey)) return true
  return detailState[detailKey] !== false
}

const FIELD_NOTES_STORAGE_KEY = 'momoFieldNotesText'
const DEFAULT_FIELD_NOTES = `- 每日范围：东八区 04:00 为分界线，04:00 前属前一天。
- 逾期：下次复习日期早于到达日，不等于忘记。
- 忘记：上次反应为“忘记”，且未逾期。
- 模糊：上次反应为“模糊”，且未逾期。
- 认识：上次反应为“认识”，且未逾期。
- 熟知、顽固：墨墨标签，独立于状态。
- 当日已新学：当天已完成的新词数。
- 当日已复习：当天已完成的复习词数。
- 今日首次忘记数：当天首次作答“忘记”的数量。
- 按日期统计表的熟悉度范围列按“数量/平均学习次数”展示。`

function getSavedFieldNotesText() {
  try {
    const text = localStorage.getItem(FIELD_NOTES_STORAGE_KEY)
    return text && text.trim() ? text : DEFAULT_FIELD_NOTES
  } catch (e) {
    return DEFAULT_FIELD_NOTES
  }
}

function buildFieldNotesMarkdown() {
  return `# 字段说明

${getSavedFieldNotesText()}
`
}


function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function getStatusSource(row) {
  return row?.studyStatus?.overall || {}
}

function getTodaySource(row) {
  return row?.studyStatus?.today || {}
}

function getCriticalSource(row) {
  return row?.studyStatus?.critical || {}
}

function countDueOffset(row, offset) {
  const critical = getCriticalSource(row)
  const dueByOffset = critical.dueByOffset || row?.criticalDueByOffset || {}
  const direct = dueByOffset[String(offset)]
  return direct !== undefined && direct !== null ? toNumber(direct) : 0
}

function countDueToday(row) {
  const critical = getCriticalSource(row)
  return critical.dueToday !== undefined && critical.dueToday !== null ? toNumber(critical.dueToday) : 0
}

function getSnapshotSummaryValue(snapshot, key, fallback = '-') {
  const summary = snapshot?.summary || {}
  const today = snapshot?.studyStatus?.today || {}
  const overall = snapshot?.studyStatus?.overall || {}
  const critical = snapshot?.studyStatus?.critical || {}
  const map = {
    finished: [today.finished, summary.finished],
    total: [today.total, summary.total],
    unfinished: [today.unfinished, summary.unfinished, summary.todoItemsCount, Number(summary.total || 0) - Number(summary.finished || 0)],
    criticalDueToday: [critical.dueToday, summary.criticalDueToday],
    overdue: [overall.overdue, summary.overdue],
    known: [today.known, summary.known, summary.familiar],
    vague: [today.vague, summary.vague, summary.doneVague],
    forget: [today.forget, summary.forget, summary.firstForgetDone],
    allKnown: [today.allKnown, summary.allKnown, summary.allFamiliar],
    allVague: [today.allVague, summary.allVague],
    allForget: [today.allForget, summary.allForget, summary.firstForgetAll],
    reviewDone: [today.reviewDone, summary.reviewDone, summary.doneReviewWords],
    reviewPending: [today.reviewPending, summary.reviewPending, summary.pendingReview],
    reviewTotal: [today.reviewTotal, summary.reviewTotal, summary.reviewWords],
    newDone: [today.newDone, summary.newDone, summary.doneNewWords],
    newPending: [today.newPending, summary.newPending, summary.pendingNew],
    newTotal: [today.newTotal, summary.newTotal, summary.newWords],
    totalWords: [overall.totalWords, summary.totalWords],
    knownState: [overall.knownState, summary.knownState],
    vagueState: [overall.vagueState, summary.vagueState],
    forgetState: [overall.forgetState, summary.forgetState],
    studyTimeMs: [today.studyTimeMs, summary.studyTimeMs],
  }
  const values = map[key] || [summary[key]]
  for (const value of values) {
    if (value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value))) return Number(value)
  }
  return fallback
}

function snapshotTimeMs(snapshot) {
  const direct = Number(snapshot?.timeMs)
  if (Number.isFinite(direct) && direct > 0) return direct
  const parsed = Date.parse(snapshot?.name || snapshot?.displayName || '')
  return Number.isFinite(parsed) ? parsed : 0
}

function formatMsToSeconds(ms) {
  const n = Number(ms)
  return Number.isFinite(n) ? (n / 1000).toFixed(1) : '-'
}

function buildStudyTimeSummary(rows) {
  const cleanRows = displayRowsOnly(rows)
  const progressRows = cleanRows.filter(row => row?.hasProgressData !== false)
  const totalMs = progressRows.reduce((sum, row) => sum + (Number(row.studyTimeMs) || 0), 0)
  const nonZeroRows = progressRows.filter(row => Number(row.studyTimeMs) > 0)
  const maxRow = nonZeroRows.reduce((best, row) => {
    if (!best || Number(row.studyTimeMs) > Number(best.studyTimeMs)) return row
    return best
  }, null)

  return [
    '## 学习时长汇总',
    markdownTable(
      ['统计项', '值'],
      [
        ['显示天数', cleanRows.length],
        ['有进度数据天数', progressRows.length],
        ['有学习时长天数', nonZeroRows.length],
        ['总学习时长', formatDuration(totalMs)],
        ['总学习时长(分钟)', (totalMs / 60000).toFixed(1)],
        ['平均每个显示日(分钟)', cleanRows.length ? (totalMs / 60000 / cleanRows.length).toFixed(1) : '-'],
        ['平均每个有进度日(分钟)', progressRows.length ? (totalMs / 60000 / progressRows.length).toFixed(1) : '-'],
        ['最高学习时长日期', maxRow ? `${maxRow.date || '-'} / ${((Number(maxRow.studyTimeMs) || 0) / 60000).toFixed(1)} 分钟` : '-'],
      ]
    ),
  ].join('\n')
}

function buildProbabilityMarkdown(model) {
  const probabilityRows = buildProbabilityTableRows(model)
  if (!probabilityRows.length) return '暂无复习概率分桶数据。'
  const studyHeaders = model?.useStudyCountDimension === false
    ? ['全部']
    : (probabilityRows.studyRows || model?.studyBuckets || []).map(row => row.label)
  return [
    '## 复习概率分桶',
    markdownTable(
      ['记忆持久度 \\ 学习次数', ...studyHeaders],
      probabilityRows.map(row => [row.memory.label, ...row.cells.map(cell => cell.text)])
    ),
    '单元格格式：n/忘/模/认/Easy；n 为该区域真实样本数。- 表示合并后仍不足目标词数，空白格表示被上方合并区域覆盖。',
  ].join('\n')
}

function buildPredictionForecastSummary(result) {
  if (!result?.rows?.length) return '暂无预测数据。'
  const rows = result.rows || []
  const totalNew = rows.reduce((sum, row) => sum + (Number(row.predictedNew) || 0), 0)
  const totalReview = rows.reduce((sum, row) => sum + (Number(row.predictedReview) || 0), 0)
  return [
    '## 预测概要',
    markdownTable(
      ['项目', '值'],
      [
        ['模型参考日', result.referenceDate || '-'],
        ['预测天数', rows.length],
        ['预测日期范围', `${rows[0]?.date || '-'} ~ ${rows[rows.length - 1]?.date || '-'}`],
        ['预测新学总量', formatPredictionCount(totalNew)],
        ['预测复习总量', formatPredictionCount(totalReview)],
        ['预测总学习量', formatPredictionCount(totalNew + totalReview)],
        ['模拟词量', formatCount(result.simulationWordCount || 0)],
        ['概率样本', formatCount(result.distSummary?.sampleCount || result.reviewSampleCount || 0)],
        ['全局概率', result.distSummary?.global || '-'],
      ]
    ),
  ].join('\n')
}

function buildTodayWordStatsSection(dataStore) {
  const workspace = dataStore.todayWorkspace || {}
  const snapshots = Array.isArray(workspace.snapshots)
    ? [...workspace.snapshots].sort((a, b) => snapshotTimeMs(a) - snapshotTimeMs(b))
    : []

  const headers = [
    '快照时间', '原始快照', '已完成', '总数', '未完成', '记忆临界点(<=0天)', '逾期',
    '今日认识(已完成)', '今日模糊(已完成)', '今日忘记(已完成)',
    '今日认识(全部)', '今日模糊(全部)', '今日忘记(全部)',
    '今日已复习', '今日待复习', '今日复习总任务',
    '今日已新学', '今日待新学', '今日新学总任务',
    '当前总词数', '认识状态', '模糊状态', '忘记状态', '学习时长(秒)',
  ]

  return [
    `日期：${workspace.date || '-'}`,
    '单位：横轴从当天 04:00 开始，每小时一个刻度；折线点按数据库原始快照时间落点。',
    snapshots.length
      ? markdownTable(headers, snapshots.map(snapshot => [
        snapshot.displayName || snapshot.timeLabel || snapshot.name || '-',
        snapshot.name || '-',
        formatCount(getSnapshotSummaryValue(snapshot, 'finished')),
        formatCount(getSnapshotSummaryValue(snapshot, 'total')),
        formatCount(getSnapshotSummaryValue(snapshot, 'unfinished')),
        formatCount(getSnapshotSummaryValue(snapshot, 'criticalDueToday')),
        formatCount(getSnapshotSummaryValue(snapshot, 'overdue')),
        formatCount(getSnapshotSummaryValue(snapshot, 'known')),
        formatCount(getSnapshotSummaryValue(snapshot, 'vague')),
        formatCount(getSnapshotSummaryValue(snapshot, 'forget')),
        formatCount(getSnapshotSummaryValue(snapshot, 'allKnown')),
        formatCount(getSnapshotSummaryValue(snapshot, 'allVague')),
        formatCount(getSnapshotSummaryValue(snapshot, 'allForget')),
        formatCount(getSnapshotSummaryValue(snapshot, 'reviewDone')),
        formatCount(getSnapshotSummaryValue(snapshot, 'reviewPending')),
        formatCount(getSnapshotSummaryValue(snapshot, 'reviewTotal')),
        formatCount(getSnapshotSummaryValue(snapshot, 'newDone')),
        formatCount(getSnapshotSummaryValue(snapshot, 'newPending')),
        formatCount(getSnapshotSummaryValue(snapshot, 'newTotal')),
        formatCount(getSnapshotSummaryValue(snapshot, 'totalWords')),
        formatCount(getSnapshotSummaryValue(snapshot, 'knownState')),
        formatCount(getSnapshotSummaryValue(snapshot, 'vagueState')),
        formatCount(getSnapshotSummaryValue(snapshot, 'forgetState')),
        formatMsToSeconds(getSnapshotSummaryValue(snapshot, 'studyTimeMs', 0)),
      ]))
      : '_暂无快照数据_',
  ].join('\n')
}


function getTodayWorkspaceSnapshots(dataStore) {
  const workspace = dataStore.todayWorkspace || {}
  const snapshots = Array.isArray(workspace.snapshots)
    ? [...workspace.snapshots].sort((a, b) => snapshotTimeMs(a) - snapshotTimeMs(b))
    : []
  return { workspace, snapshots }
}

const TODAY_WORKSPACE_COPY_STATE_KEY = 'momoTodayWorkspaceCopyState'

function getTodayWorkspaceCopyState() {
  const fallback = {
    selectedSnapshotName: '',
    compareA: '',
    compareB: '',
    singleOptions: { progress: true, allItems: false, doneItems: false, todoItems: false },
    compareOptions: {
      tableStats: true,
      progress: true,
      added: true,
      removed: true,
      statusChanged: true,
      responseChanged: true,
      memoryChanged: true,
      studyCountChanged: true,
      studyTimeChanged: false,
    },
  }
  try {
    const saved = JSON.parse(localStorage.getItem(TODAY_WORKSPACE_COPY_STATE_KEY) || '{}') || {}
    return {
      ...fallback,
      ...saved,
      singleOptions: { ...fallback.singleOptions, ...(saved.singleOptions || {}) },
      compareOptions: { ...fallback.compareOptions, ...(saved.compareOptions || {}) },
    }
  } catch {
    return fallback
  }
}

function findWorkspaceSnapshot(snapshots, name, fallback = null) {
  if (name) {
    const matched = snapshots.find(snapshot => snapshot?.name === name || snapshot?.displayName === name || snapshot?.timeLabel === name)
    if (matched) return matched
  }
  return fallback
}

function workspaceSnapshotLabel(snapshot) {
  return snapshot?.displayName || snapshot?.timeLabel || snapshot?.name || '-'
}

function workspaceItemKey(item) {
  return item?.voc_id || ''
}

function simplifyWorkspaceItem(item) {
  return {
    单词: item?.voc_spelling || '',
    学习顺序: item?.order ?? '',
    首次反应: respCn(item?.first_response),
    是否新词: item?.is_new ? '是' : '否',
    是否完成: item?.is_finished ? '是' : '否',
    学习次数: item?.study_count ?? '',
  }
}

function workspaceProgressCopyRow(snapshot) {
  return {
    已完成: getSnapshotSummaryValue(snapshot, 'finished', ''),
    总数: getSnapshotSummaryValue(snapshot, 'total', ''),
    '学习时长(秒)': formatMsToSeconds(getSnapshotSummaryValue(snapshot, 'studyTimeMs', 0)),
    今日首次忘记数: getSnapshotSummaryValue(snapshot, 'firstForgetDone', getSnapshotSummaryValue(snapshot, 'forget', 0)),
    今日全部首次忘记数: getSnapshotSummaryValue(snapshot, 'firstForgetAll', getSnapshotSummaryValue(snapshot, 'allForget', 0)),
    '今日模糊数(已完成)': getSnapshotSummaryValue(snapshot, 'doneVague', getSnapshotSummaryValue(snapshot, 'vague', 0)),
    '今日模糊数(全部)': getSnapshotSummaryValue(snapshot, 'allVague', 0),
    今日认识数: getSnapshotSummaryValue(snapshot, 'familiar', getSnapshotSummaryValue(snapshot, 'known', 0)),
    今日新学数: getSnapshotSummaryValue(snapshot, 'newWords', getSnapshotSummaryValue(snapshot, 'newTotal', 0)),
    今日复习数: getSnapshotSummaryValue(snapshot, 'reviewWords', getSnapshotSummaryValue(snapshot, 'reviewTotal', 0)),
    今日待新学数: getSnapshotSummaryValue(snapshot, 'pendingNew', getSnapshotSummaryValue(snapshot, 'newPending', 0)),
  }
}

function workspaceItemsCsv(snapshot, kind) {
  const items = kind === 'done'
    ? snapshot?.doneItems
    : kind === 'todo'
      ? snapshot?.todoItems
      : snapshot?.allItems
  const headers = ['单词', '学习顺序', '首次反应', '是否新词', '是否完成', '学习次数']
  return rowsToCSV(headers, (Array.isArray(items) ? items : []).map(simplifyWorkspaceItem))
}

function workspaceProgressRow(snapshot) {
  return [
    workspaceSnapshotLabel(snapshot),
    snapshot?.name || '-',
    formatCount(getSnapshotSummaryValue(snapshot, 'finished')),
    formatCount(getSnapshotSummaryValue(snapshot, 'total')),
    formatCount(getSnapshotSummaryValue(snapshot, 'unfinished')),
    formatCount(getSnapshotSummaryValue(snapshot, 'criticalDueToday')),
    formatCount(getSnapshotSummaryValue(snapshot, 'overdue')),
    formatCount(getSnapshotSummaryValue(snapshot, 'known')),
    formatCount(getSnapshotSummaryValue(snapshot, 'vague')),
    formatCount(getSnapshotSummaryValue(snapshot, 'forget')),
    formatCount(getSnapshotSummaryValue(snapshot, 'allKnown')),
    formatCount(getSnapshotSummaryValue(snapshot, 'allVague')),
    formatCount(getSnapshotSummaryValue(snapshot, 'allForget')),
    formatCount(getSnapshotSummaryValue(snapshot, 'reviewDone')),
    formatCount(getSnapshotSummaryValue(snapshot, 'reviewPending')),
    formatCount(getSnapshotSummaryValue(snapshot, 'reviewTotal')),
    formatCount(getSnapshotSummaryValue(snapshot, 'newDone')),
    formatCount(getSnapshotSummaryValue(snapshot, 'newPending')),
    formatCount(getSnapshotSummaryValue(snapshot, 'newTotal')),
    formatMsToSeconds(getSnapshotSummaryValue(snapshot, 'studyTimeMs', 0)),
  ]
}

function buildTodayWorkspaceSummarySection(snapshots) {
  const headers = [
    '时间点', '原始快照', '已完成', '总数', '未完成', '记忆临界点(<=0天)', '逾期',
    '认识(已完成)', '模糊(已完成)', '忘记(已完成)',
    '认识(全部)', '模糊(全部)', '忘记(全部)',
    '已复习', '待复习', '复习总任务', '已新学', '待新学', '新学总任务', '学习时长(秒)',
  ]
  return [
    '## 时间点列表',
    snapshots.length ? markdownTable(headers, snapshots.map(workspaceProgressRow)) : '_暂无时间点数据_',
  ].join('\n')
}

function buildWorkspaceSingleSection(snapshot, options = {}) {
  if (!snapshot) return '## 查看单个时间点\n暂无时间点。'
  const selected = { progress: true, allItems: false, doneItems: false, todoItems: false, ...(options || {}) }
  const parts = [
    '## 查看单个时间点',
    `时间点：${workspaceSnapshotLabel(snapshot)}`,
  ]

  if (selected.progress) {
    const row = workspaceProgressCopyRow(snapshot)
    parts.push('', '### 今日学习进度', rowsToCSV(Object.keys(row), [row]))
  }
  if (selected.allItems) {
    const items = Array.isArray(snapshot.allItems) ? snapshot.allItems : []
    parts.push('', `### 今日全部单词（${items.length}）`, workspaceItemsCsv(snapshot, 'all'))
  }
  if (selected.doneItems) {
    const items = Array.isArray(snapshot.doneItems) ? snapshot.doneItems : []
    parts.push('', `### 今日已完成单词（${items.length}）`, workspaceItemsCsv(snapshot, 'done'))
  }
  if (selected.todoItems) {
    const items = Array.isArray(snapshot.todoItems) ? snapshot.todoItems : []
    parts.push('', `### 今日未完成单词（${items.length}）`, workspaceItemsCsv(snapshot, 'todo'))
  }
  if (parts.length <= 2) parts.push('', '_未勾选任何单时间点复制内容_')
  return parts.join('\n')
}

function buildWorkspaceCompareSection(a, b, options = {}) {
  if (!a || !b) return '## 自由对比两个时间点\n快照不足两个，无法对比。'
  const selected = { progress: true, added: true, removed: true, statusChanged: true, responseChanged: true, ...(options || {}) }
  const aItems = Array.isArray(a.allItems) ? a.allItems : []
  const bItems = Array.isArray(b.allItems) ? b.allItems : []
  const aMap = new Map(aItems.map(item => [workspaceItemKey(item), item]))
  const bMap = new Map(bItems.map(item => [workspaceItemKey(item), item]))
  const aKeys = new Set(aMap.keys())
  const bKeys = new Set(bMap.keys())
  const added = []
  const removed = []
  const statusChanged = []
  const responseChanged = []

  for (const key of bKeys) if (!aKeys.has(key)) added.push(simplifyWorkspaceItem(bMap.get(key)))
  for (const key of aKeys) if (!bKeys.has(key)) removed.push(simplifyWorkspaceItem(aMap.get(key)))
  for (const key of aKeys) {
    if (!bKeys.has(key)) continue
    const x = aMap.get(key)
    const y = bMap.get(key)
    if (Boolean(x?.is_finished) !== Boolean(y?.is_finished)) {
      statusChanged.push({
        单词: y?.voc_spelling || x?.voc_spelling || '',
        学习顺序: y?.order ?? x?.order ?? '',
        之前是否完成: x?.is_finished ? '是' : '否',
        现在是否完成: y?.is_finished ? '是' : '否',
        之前学习次数: x?.study_count ?? '',
        现在学习次数: y?.study_count ?? '',
      })
    }
    if ((x?.first_response || '') !== (y?.first_response || '')) {
      responseChanged.push({
        单词: y?.voc_spelling || x?.voc_spelling || '',
        学习顺序: y?.order ?? x?.order ?? '',
        之前首次反应: respCn(x?.first_response),
        现在首次反应: respCn(y?.first_response),
        之前学习次数: x?.study_count ?? '',
        现在学习次数: y?.study_count ?? '',
      })
    }
  }

  const parts = [
    '## 自由对比两个时间点',
    `对比：${workspaceSnapshotLabel(a)} -> ${workspaceSnapshotLabel(b)}`,
  ]

  if (selected.progress) {
    const progressHeaders = ['指标', '之前', '现在', '变化']
    const metricRows = [
      ['已完成', getSnapshotSummaryValue(a, 'finished', 0), getSnapshotSummaryValue(b, 'finished', 0)],
      ['总数', getSnapshotSummaryValue(a, 'total', 0), getSnapshotSummaryValue(b, 'total', 0)],
      ['今日首次忘记数', getSnapshotSummaryValue(a, 'firstForgetDone', getSnapshotSummaryValue(a, 'forget', 0)), getSnapshotSummaryValue(b, 'firstForgetDone', getSnapshotSummaryValue(b, 'forget', 0))],
      ['今日全部首次忘记数', getSnapshotSummaryValue(a, 'firstForgetAll', getSnapshotSummaryValue(a, 'allForget', 0)), getSnapshotSummaryValue(b, 'firstForgetAll', getSnapshotSummaryValue(b, 'allForget', 0))],
      ['今日模糊数(已完成)', getSnapshotSummaryValue(a, 'doneVague', getSnapshotSummaryValue(a, 'vague', 0)), getSnapshotSummaryValue(b, 'doneVague', getSnapshotSummaryValue(b, 'vague', 0))],
      ['今日模糊数(全部)', getSnapshotSummaryValue(a, 'allVague', 0), getSnapshotSummaryValue(b, 'allVague', 0)],
      ['今日认识数', getSnapshotSummaryValue(a, 'familiar', getSnapshotSummaryValue(a, 'known', 0)), getSnapshotSummaryValue(b, 'familiar', getSnapshotSummaryValue(b, 'known', 0))],
      ['今日新学数', getSnapshotSummaryValue(a, 'newWords', getSnapshotSummaryValue(a, 'newTotal', 0)), getSnapshotSummaryValue(b, 'newWords', getSnapshotSummaryValue(b, 'newTotal', 0))],
      ['今日复习数', getSnapshotSummaryValue(a, 'reviewWords', getSnapshotSummaryValue(a, 'reviewTotal', 0)), getSnapshotSummaryValue(b, 'reviewWords', getSnapshotSummaryValue(b, 'reviewTotal', 0))],
      ['今日待新学数', getSnapshotSummaryValue(a, 'pendingNew', getSnapshotSummaryValue(a, 'newPending', 0)), getSnapshotSummaryValue(b, 'pendingNew', getSnapshotSummaryValue(b, 'newPending', 0))],
      ['学习时长(秒)', Number(getSnapshotSummaryValue(a, 'studyTimeMs', 0)) / 1000, Number(getSnapshotSummaryValue(b, 'studyTimeMs', 0)) / 1000],
    ].map(([label, av, bv]) => [label, formatValue(av, 0), formatValue(bv, 0), formatDecimal(Number(bv || 0) - Number(av || 0), 1)])
    parts.push('', '### 进度变化', markdownTable(progressHeaders, metricRows))
  }

  function appendRows(title, rows, enabled) {
    if (!enabled) return
    parts.push('', `### ${title}`, `数量：${rows.length}`)
    if (rows.length) parts.push(rowsToCSV(Object.keys(rows[0]), rows))
  }
  appendRows('新增条目', added, selected.added)
  appendRows('消失条目', removed, selected.removed)
  appendRows('完成状态变化', statusChanged, selected.statusChanged)
  appendRows('首次反应变化', responseChanged, selected.responseChanged)
  if (parts.length <= 2) parts.push('', '_未勾选任何时间点对比复制内容_')
  return parts.join('\n')
}

function buildTodayWorkspaceMarkdown(dataStore, cardStore) {
  const { workspace, snapshots } = getTodayWorkspaceSnapshots(dataStore)
  const state = getTodayWorkspaceCopyState()
  const latest = snapshots[snapshots.length - 1] || null
  const previous = snapshots[snapshots.length - 2] || null
  const selectedSnapshot = findWorkspaceSnapshot(snapshots, state.selectedSnapshotName, latest)
  const compareStart = findWorkspaceSnapshot(snapshots, state.compareA, previous || snapshots[0] || null)
  const compareEnd = findWorkspaceSnapshot(snapshots, state.compareB, latest)
  const sections = [`# 当日时间点查看`, `日期：${workspace.date || '-'}，时间点数量：${snapshots.length}`]

  if (!snapshots.length) {
    sections.push('_暂无当日时间点数据_')
    return sections.join('\n\n')
  }

  if (getCopyDetailEnabled(cardStore, '当日时间点查看', 'summary')) sections.push(buildTodayWorkspaceSummarySection(snapshots))
  if (getCopyDetailEnabled(cardStore, '当日时间点查看', 'latest')) sections.push(buildWorkspaceSingleSection(selectedSnapshot, state.singleOptions))
  if (getCopyDetailEnabled(cardStore, '当日时间点查看', 'compare')) sections.push(buildWorkspaceCompareSection(compareStart, compareEnd, state.compareOptions))

  if (sections.length === 2) sections.push('_未勾选任何当日时间点查看复制明细_')
  return sections.join('\n\n')
}

function buildStatusSection(rows) {
  return [
    '## 当前状态数据',
    markdownTable(
      ['日期', '总词数', '熟知', '顽固', '认识状态', '模糊状态', '忘记状态', '逾期', '平均学习次数'],
      rows.map(row => {
        const overall = getStatusSource(row)
        return [
          row.date || '-',
          formatCount(formatValue(overall.totalWords, row.totalWords)),
          formatCount(formatValue(overall.wellKnown, row['熟知'])),
          formatCount(formatValue(overall.sticking, row['顽固'])),
          formatCount(formatValue(overall.knownState, row['认识'])),
          formatCount(formatValue(overall.vagueState, row['模糊'])),
          formatCount(formatValue(overall.forgetState, row['忘记'])),
          formatCount(formatValue(overall.overdue, row['逾期'])),
          formatDecimal(formatValue(overall.avgStudyCount, row.avgStudyCount), 2),
        ]
      })
    ),
  ].join('\n')
}

function buildTodaySection(rows) {
  return [
    '## 今日学习进度',
    '校验公式：今日认识 + 今日模糊 + 今日忘记 = 今日已复习 + 今日已新学 = 今日已完成；今日总数 = 今日新学总任务 + 今日复习总任务。',
    markdownTable(
      [
        '日期',
        '快照时间',
        '今日认识',
        '今日模糊',
        '今日忘记',
        '今日已完成',
        '今日未完成',
        '今日总数',
        '今日已复习',
        '今日待复习',
        '今日复习总任务',
        '今日已新学',
        '今日待新学',
        '今日新学总任务',
        '今日全部认识',
        '今日全部模糊',
        '今日全部忘记',
        '学习时长(分钟)',
        '公式校验',
      ],
      rows.map(row => {
        const today = getTodaySource(row)
        const known = toNumber(formatValue(today.known, row.todayKnownCount))
        const vague = toNumber(formatValue(today.vague, row.todayVagueCount))
        const forget = toNumber(formatValue(today.forget, row.todayForgetCount))
        const finished = toNumber(formatValue(today.finished, row.finished))
        const reviewDone = toNumber(formatValue(today.reviewDone, row.dailyReviewedCount))
        const newDone = toNumber(formatValue(today.newDone, row.dailyNewLearnedCount))
        const total = toNumber(formatValue(today.total, row.total))
        const reviewTotal = toNumber(formatValue(today.reviewTotal, row.dailyReviewTaskCount))
        const newTotal = toNumber(formatValue(today.newTotal, row.dailyNewTaskCount))
        const ok = (known + vague + forget === finished)
          && (reviewDone + newDone === finished)
          && (reviewTotal + newTotal === total)
        return [
          row.date || '-',
          formatValue(row.studyStatus?.capturedAt, row.studyStatus?.snapshot, row.capturedAt, row.snapshot, '-'),
          formatCount(known),
          formatCount(vague),
          formatCount(forget),
          formatCount(finished),
          formatCount(formatValue(today.unfinished, row.unfinishedCount, Math.max(total - finished, 0))),
          formatCount(total),
          formatCount(reviewDone),
          formatCount(formatValue(today.reviewPending, row.dailyPendingReviewCount)),
          formatCount(reviewTotal),
          formatCount(newDone),
          formatCount(formatValue(today.newPending, row.dailyPendingNewCount)),
          formatCount(newTotal),
          formatCount(formatValue(today.allKnown, row.todayAllKnownCount)),
          formatCount(formatValue(today.allVague, row.todayAllVagueCount)),
          formatCount(formatValue(today.allForget, row.todayAllForgetCount)),
          formatDecimal((Number(formatValue(today.studyTimeMs, row.studyTimeMs, 0)) || 0) / 60000, 1),
          ok ? '通过' : '异常',
        ]
      })
    ),
  ].join('\n')
}

function buildCriticalSection(rows, settings) {
  const futureDays = Math.max(0, Math.min(365, Number(settings.criticalFutureDays) || 0))
  const dueHeaders = ['待复习(<=0天)']
  for (let day = 1; day <= futureDays; day += 1) {
    dueHeaders.push(day === 1 ? '明天临界' : `${day}天后临界`)
  }

  return [
    '## 记忆临界点统计',
    `未来记忆临界点天数：${futureDays}`,
    markdownTable(
      ['日期', ...dueHeaders],
      rows.map(row => [
        row.date || '-',
        formatCount(countDueToday(row)),
        ...Array.from({ length: futureDays }, (_, index) => formatCount(countDueOffset(row, index + 1))),
      ])
    ),
  ].join('\n')
}

function buildStudyStatusMarkdown(rows, settings, cardStore) {
  const cleanRows = displayRowsOnly(rows)
  const sections = ['# 复习情况复制数据']

  if (getCopyDetailEnabled(cardStore, STUDY_STATUS_CARD_ID, 'status')) sections.push(buildStatusSection(cleanRows))
  if (getCopyDetailEnabled(cardStore, STUDY_STATUS_CARD_ID, 'today')) sections.push(buildTodaySection(cleanRows))
  if (getCopyDetailEnabled(cardStore, STUDY_STATUS_CARD_ID, 'critical')) sections.push(buildCriticalSection(cleanRows, settings))

  if (sections.length === 1) sections.push('_未勾选任何复习情况复制明细_')
  return sections.join('\n\n')
}

export function buildSingleCardMarkdown(copyKey) {
  const dataStore = useDataStore()
  const prediction = usePredictionStore()
  const settings = useSettingsStore()
  const cardStore = useCardStore()
  const rows = getDisplayRows()

  const parts = []
  const now = new Date().toLocaleString('zh-CN', { hour12: false })

  if (copyKey === 'summary') {
    parts.push(`# 按日期统计表\n生成时间：${now}\n`)
    if (getCopyDetailEnabled(cardStore, '按日期统计表', 'table')) {
      parts.push(buildSummaryMarkdown(rows, settings.memoryThresholds))
    } else {
      parts.push('_未勾选按日期统计表复制明细_')
    }
  } else if (copyKey === 'overviewChart') {
    parts.push(buildStudyStatusMarkdown(rows, settings, cardStore))
  } else if (copyKey === 'studyTime') {
    parts.push(`# 学习时长数据\n`)
    let selected = 0
    if (getCopyDetailEnabled(cardStore, '每日学习时长统计', 'chart')) {
      const headers = ['日期', '学习时长(分钟)']
      const tableRows = displayRowsOnly(rows).map(row => [row.date || '-', ((Number(row.studyTimeMs) || 0) / 60000).toFixed(1)])
      parts.push('## 每日学习时长', markdownTable(headers, tableRows))
      selected += 1
    }
    if (getCopyDetailEnabled(cardStore, '每日学习时长统计', 'summary')) {
      parts.push(buildStudyTimeSummary(rows))
      selected += 1
    }
    if (!selected) parts.push('_未勾选学习时长复制明细_')
  } else if (copyKey === 'memory') {
    parts.push(`# 记忆持久度统计\n`)
    if (!getCopyDetailEnabled(cardStore, '记忆持久度统计', 'chart')) {
      parts.push('_未勾选记忆持久度复制明细_')
    } else {
      const thresholds = getReviewSpanThresholds(settings.memoryThresholds)
      if (thresholds.length) {
        const headers = ['日期', ...thresholds.map(spec => `记忆持久度${spec.headerLabel}`)]
        const tableRows = displayRowsOnly(rows)
          .filter(row => row.hasOverviewData)
          .map(row => [
            row.date || '-',
            ...thresholds.map(spec => formatCount(countReviewSpanForThreshold(row, spec))),
          ])
        parts.push(markdownTable(headers, tableRows))
      } else {
        parts.push('_无数据_\n')
      }
    }
  } else if (copyKey === 'prediction') {
    parts.push(`# 未来每日学习量预测\n`)
    const result = prediction.result
    let selected = 0
    if (getCopyDetailEnabled(cardStore, '未来每日学习量预测', 'forecast')) {
      parts.push(buildPredictionForecastSummary(result))
      selected += 1
    }
    if (getCopyDetailEnabled(cardStore, '未来每日学习量预测', 'table')) {
      parts.push('## 预测表格', buildPredictionMarkdown(result))
      selected += 1
    }
    if (getCopyDetailEnabled(cardStore, '未来每日学习量预测', 'probability')) {
      parts.push(buildProbabilityMarkdown(result?.probabilityModel))
      selected += 1
    }
    if (!selected) parts.push('_未勾选预测复制明细_')
  } else if (copyKey === 'predictionTarget') {
    parts.push(`# 目标达标与复习概率\n`)
    const result = prediction.result
    let selected = 0
    if (getCopyDetailEnabled(cardStore, '目标达标与复习概率', 'chart')) {
      parts.push(markdownTable(
        ['几天后', '日期', '目标达标词数', '目标词数', '目标天数', '目标口径'],
        (result?.rows || []).map(row => [row.predictionDay, row.date, row.dailyTargetMatchedCount, row.targetCount || '-', row.targetDays, row.targetMetric])
      ))
      selected += 1
    }
    if (getCopyDetailEnabled(cardStore, '目标达标与复习概率', 'probability')) {
      parts.push(buildProbabilityMarkdown(result?.probabilityModel))
      selected += 1
    }
    if (!selected) parts.push('_未勾选目标达标与复习概率复制明细_')
  } else if (copyKey === 'todayWorkspace') {
    parts.push(buildTodayWorkspaceMarkdown(dataStore, cardStore))
  } else if (copyKey === 'wordList') {
    if (getCopyDetailEnabled(cardStore, '查看单词列表', 'list')) {
      parts.push(dataStore.wordList?.lastText || '# 单词列表\n\n未查询单词列表。')
    } else {
      parts.push('# 单词列表\n\n_未勾选单词列表复制明细_')
    }
  } else if (copyKey === 'todayWordStats') {
    parts.push(`# 当日单词统计\n`)
    if (getCopyDetailEnabled(cardStore, '当日单词统计', 'snapshots')) {
      parts.push(buildTodayWordStatsSection(dataStore))
    } else {
      parts.push('_未勾选当日单词统计复制明细_')
    }
  } else if (copyKey === 'notes') {
    if (getCopyDetailEnabled(cardStore, '字段注释', 'notes')) {
      parts.push(buildFieldNotesMarkdown())
    } else {
      parts.push('# 字段说明\n\n_未勾选字段说明复制明细_')
    }
  }

  return parts.join('\n')
}

export function buildAllMarkdown() {
  const cardStore = useCardStore()
  const rows = getDisplayRows()

  const selectedCards = cardStore.getCopyableCards()
    .filter(card => cardStore.copyEnabled[card.id] !== false)
  const now = new Date().toLocaleString('zh-CN', { hour12: false })
  const cleanRows = displayRowsOnly(rows)
  const rangeText = cleanRows.length ? `${cleanRows[0].date} ~ ${cleanRows[cleanRows.length - 1].date}` : '无范围'
  const parts = ['# 墨墨历史统计数据', '', `生成时间：${now}`, `当前范围：${rangeText}`, `显示数据日：${cleanRows.length} 天`, '']
  const emittedCopyKeys = new Set()
  let sectionCount = 0

  for (const card of selectedCards) {
    const copyKey = card?.copyKey
    if (!copyKey || emittedCopyKeys.has(copyKey)) continue
    if (!rows.length && ROW_DEPENDENT_COPY_KEYS.has(copyKey)) continue
    const markdown = buildSingleCardMarkdown(copyKey)
    if (!hasUsefulCopyContent(copyKey, markdown)) continue
    parts.push(markdown)
    emittedCopyKeys.add(copyKey)
    sectionCount += 1
  }

  if (!sectionCount) {
    parts.push('_未勾选任何可复制内容，或当前选中卡片暂无可复制数据。_')
  }

  return parts.filter(Boolean).join('\n\n')
}

export async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.style.position = 'fixed'
    textarea.style.left = '-9999px'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
  }
}
