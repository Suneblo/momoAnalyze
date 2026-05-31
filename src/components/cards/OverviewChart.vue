<template>
  <div class="study-status-card">
    <div class="mode-tabs">
      <button :class="{ active: mode === 'cognition' }" @click="mode = 'cognition'">认知情况</button>
      <button :class="{ active: mode === 'plan' }" @click="mode = 'plan'">复习新学</button>
    </div>

    <BaseChart v-if="mainChartOption" :option="mainChartOption" :height="360" />
    <p v-else class="info-text">暂无数据</p>

    <div v-if="formulaWarning" class="status-warning">
      {{ formulaWarning }}
    </div>

    <div v-if="overdueChartOption" class="overdue-section">
      <div class="section-title">逾期统计</div>
      <BaseChart :option="overdueChartOption" :height="220" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useSettingsStore } from '@/stores/settingsStore'
import BaseChart from '@/components/charts/BaseChart.vue'

defineProps({ card: Object })

const dataStore = useDataStore()
const settings = useSettingsStore()
const mode = ref('cognition')

function toNumber(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

function parseDate(dateText) {
  if (!dateText) return null
  const [y, m, d] = String(dateText).slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

function formatDate(date) {
  if (!date) return ''
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function addDays(date, days) {
  const out = new Date(date)
  out.setDate(out.getDate() + days)
  return out
}

function dayDiff(a, b) {
  const ad = parseDate(a)
  const bd = parseDate(b)
  if (!ad || !bd) return null
  return Math.round((ad.getTime() - bd.getTime()) / 86400000)
}

function axisLabel(dateText, latestDateText, futureOffset = 0) {
  if (futureOffset === 1) return '明天'
  if (futureOffset === 2) return '后天'
  if (futureOffset > 2) return `${futureOffset}天后`

  const diff = dayDiff(dateText, latestDateText)
  if (diff === -1) return '昨天'
  if (diff === 0) return '今天'
  return String(dateText || '').slice(5) || '-'
}

function status(row) {
  return row?.studyStatus || {}
}

function today(row) {
  return status(row).today || {}
}

function overall(row) {
  return status(row).overall || {}
}

function critical(row) {
  return status(row).critical || {}
}

function countDueAt(row, offset) {
  const dueByOffset = critical(row).dueByOffset || row?.criticalDueByOffset || {}
  const direct = dueByOffset[String(offset)]
  return direct !== undefined && direct !== null ? toNumber(direct) : 0
}

function actualRows() {
  return (dataStore.displayRows || [])
    .filter(row => row && !row.isGapMarker && (row.studyStatus?.today || row.hasProgressData || row.hasOverviewData))
}

const timeline = computed(() => {
  const rows = actualRows()
  if (!rows.length) return []

  const latest = rows[rows.length - 1]
  const latestDate = latest.date
  const latestDateObj = parseDate(latestDate)
  const futureDays = Math.max(0, Math.min(365, Number(settings.criticalFutureDays) || 0))

  const actual = rows.map(row => ({
    type: 'actual',
    date: row.date,
    label: axisLabel(row.date, latestDate, 0),
    row,
    futureOffset: 0,
  }))

  const future = []
  if (latestDateObj) {
    for (let offset = 1; offset <= futureDays; offset += 1) {
      future.push({
        type: 'future',
        date: formatDate(addDays(latestDateObj, offset)),
        label: axisLabel('', latestDate, offset),
        row: latest,
        futureOffset: offset,
      })
    }
  }

  return [...actual, ...future]
})

function actualValue(point, getter) {
  return point.type === 'actual' ? getter(point.row) : 0
}

const PLAN_STACK_SEGMENTS = {
  reviewPending: { name: '未复习', color: '#cbd5e1' },
  newPending: { name: '未新学', color: '#64748b' },
  reviewDone: { name: '已复习', color: '#f59e0b' },
  newDone: { name: '已新学', color: '#6cc8b6' },
}

const DEFAULT_PLAN_STACK_ORDER = ['reviewPending', 'newPending', 'reviewDone', 'newDone']

const BAR_CHART_POINT_WIDTH = 52
const BAR_CHART_GRID = Object.freeze({ left: 52, right: 22, top: 18, bottom: 55 })
const BAR_CHART_SPLIT_AREA = Object.freeze({
  show: true,
  areaStyle: { color: ['rgba(15, 23, 42, .035)', 'rgba(255,255,255,0)'] },
})
const BAR_CHART_LABEL_FONT_SIZE = 11
const BAR_CHART_LEGEND_TEXT_STYLE = Object.freeze({ fontSize: 12 })
const BAR_CHART_BAR_GAP = '0%'
const BAR_CHART_BAR_CATEGORY_GAP = '30%'

function sharedBarLegend(data) {
  return {
    data,
    bottom: 0,
    textStyle: { ...BAR_CHART_LEGEND_TEXT_STYLE },
  }
}

function sharedBarXAxis(labels, count) {
  return {
    type: 'category',
    data: labels,
    splitArea: {
      show: BAR_CHART_SPLIT_AREA.show,
      areaStyle: { ...BAR_CHART_SPLIT_AREA.areaStyle, color: [...BAR_CHART_SPLIT_AREA.areaStyle.color] },
    },
    axisLabel: { fontSize: BAR_CHART_LABEL_FONT_SIZE, interval: 0, rotate: count > 14 ? 40 : 0 },
  }
}

function sharedBarYAxis(name = '词数') {
  return {
    type: 'value',
    name,
    minInterval: 1,
    axisLabel: { fontSize: BAR_CHART_LABEL_FONT_SIZE },
  }
}

function sharedBarSeries(extra = {}) {
  return {
    type: 'bar',
    barGap: BAR_CHART_BAR_GAP,
    barCategoryGap: BAR_CHART_BAR_CATEGORY_GAP,
    emphasis: { focus: 'series' },
    ...extra,
  }
}

function normalizePlanStackOrder(value) {
  const input = Array.isArray(value) ? value : String(value || '').split(',')
  const unique = []
  for (const key of input) {
    if (PLAN_STACK_SEGMENTS[key] && !unique.includes(key)) unique.push(key)
  }
  for (const key of DEFAULT_PLAN_STACK_ORDER) {
    if (!unique.includes(key)) unique.push(key)
  }
  return unique
}

function clampCount(value, min = 0, max = Number.POSITIVE_INFINITY) {
  const n = toNumber(value, 0)
  return Math.max(min, Math.min(max, n))
}

function planParts(row) {
  const t = today(row)
  const reviewTotal = Math.max(0, toNumber(t.reviewTotal, row.dailyReviewTotalCount))
  const newTotal = Math.max(0, toNumber(t.newTotal, row.dailyNewTotalCount))
  const reviewDone = clampCount(toNumber(t.reviewDone, row.dailyReviewedCount), 0, reviewTotal)
  const newDone = clampCount(toNumber(t.newDone, row.dailyNewLearnedCount), 0, newTotal)
  const reviewPending = Math.max(reviewTotal - reviewDone, 0)
  const newPending = Math.max(newTotal - newDone, 0)

  return {
    reviewPending,
    newPending,
    reviewDone,
    newDone,
    reviewTotal,
    newTotal,
    total: reviewTotal + newTotal,
    done: reviewDone + newDone,
  }
}

function emptyPlanParts() {
  return {
    reviewPending: 0,
    newPending: 0,
    reviewDone: 0,
    newDone: 0,
    reviewTotal: 0,
    newTotal: 0,
    total: 0,
    done: 0,
  }
}

function planPartsForPoint(point) {
  return point?.type === 'actual' ? planParts(point.row) : emptyPlanParts()
}

function tooltipMarker(color) {
  return `<span style="display:inline-block;margin-right:4px;border-radius:50%;width:10px;height:10px;background:${color};"></span>`
}

function futureDueValue(point) {
  return point.type === 'future' ? countDueAt(point.row, point.futureOffset) : 0
}

function cognitionUnfinished(row) {
  const t = today(row)
  return toNumber(t.unfinished, toNumber(row.unfinishedCount, Math.max(toNumber(t.total, row.total) - toNumber(t.finished, row.finished), 0)))
}

function cognitionOption(points) {
  const labels = points.map(point => point.label)
  const dates = points.map(point => point.date)

  return {
    __pointBaseWidth: BAR_CHART_POINT_WIDTH,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const index = params[0]?.dataIndex ?? 0
        const point = points[index]
        let text = `<b>${dates[index] || labels[index]}</b><br/>`
        params.forEach(item => {
          if (item.value !== null && item.value !== undefined) text += `${item.marker}${item.seriesName}: ${item.value}<br/>`
        })
        if (point?.type === 'actual') {
          const t = today(point.row)
          const done = toNumber(t.known) + toNumber(t.vague) + toNumber(t.forget)
          text += `<span style="color:#999">已完成: ${done}，总数: ${toNumber(t.total, point.row.total)}</span>`
        }
        return text
      },
    },
    legend: sharedBarLegend(['今日认识', '今日模糊', '今日忘记', '未完成 / 记忆临界点']),
    grid: { ...BAR_CHART_GRID },
    xAxis: sharedBarXAxis(labels, points.length),
    yAxis: sharedBarYAxis('词数'),
    series: [
      sharedBarSeries({
        name: '今日认识',
        stack: 'cognition',
        data: points.map(point => actualValue(point, row => toNumber(today(row).known, row.todayKnownCount))),
        itemStyle: { color: '#6cc8b6' },
      }),
      sharedBarSeries({
        name: '今日模糊',
        stack: 'cognition',
        data: points.map(point => actualValue(point, row => toNumber(today(row).vague, row.todayVagueCount))),
        itemStyle: { color: '#f59e0b' },
      }),
      sharedBarSeries({
        name: '今日忘记',
        stack: 'cognition',
        data: points.map(point => actualValue(point, row => toNumber(today(row).forget, row.todayForgetCount ?? row.todayFirstForgetCount))),
        itemStyle: { color: '#f9734a' },
      }),
      sharedBarSeries({
        name: '未完成 / 记忆临界点',
        stack: 'cognition',
        data: points.map(point => point.type === 'actual' ? cognitionUnfinished(point.row) : futureDueValue(point)),
        itemStyle: { color: '#74777b' },
      }),
    ],
  }
}

function planOption(points) {
  const labels = points.map(point => point.label)
  const dates = points.map(point => point.date)
  const topToBottomOrder = normalizePlanStackOrder(settings.reviewPlanStackOrder)
  const renderOrder = [...topToBottomOrder].reverse()
  const rows = points.map(point => planPartsForPoint(point))

  return {
    __pointBaseWidth: BAR_CHART_POINT_WIDTH,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: params => {
        const index = params[0]?.dataIndex ?? 0
        const values = rows[index] || {}
        let text = `<b>${dates[index] || labels[index]}</b><br/>`
        for (const key of topToBottomOrder) {
          const segment = PLAN_STACK_SEGMENTS[key]
          text += `${tooltipMarker(segment.color)}${segment.name}: ${toNumber(values[key])}<br/>`
        }
        text += `<span style="color:#999">已完成: ${toNumber(values.done)}；总数: ${toNumber(values.total)}</span><br/>`
        text += `<span style="color:#999">复习: ${toNumber(values.reviewDone)}/${toNumber(values.reviewTotal)}；新学: ${toNumber(values.newDone)}/${toNumber(values.newTotal)}</span>`
        return text
      },
    },
    legend: sharedBarLegend(topToBottomOrder.map(key => PLAN_STACK_SEGMENTS[key].name)),
    grid: { ...BAR_CHART_GRID },
    xAxis: sharedBarXAxis(labels, points.length),
    yAxis: sharedBarYAxis('词数'),
    series: renderOrder.map(key => sharedBarSeries({
      name: PLAN_STACK_SEGMENTS[key].name,
      stack: 'plan-total',
      data: rows.map(values => toNumber(values[key])),
      itemStyle: { color: PLAN_STACK_SEGMENTS[key].color },
    })),
  }
}

const mainChartOption = computed(() => {
  const points = timeline.value
  if (!points.length) return null
  return mode.value === 'cognition' ? cognitionOption(points) : planOption(points)
})

const formulaWarning = computed(() => {
  const rows = actualRows()
  const latest = rows[rows.length - 1]
  const checks = latest?.studyStatus?.checks || {}
  if (!latest || !Object.keys(checks).length) return ''
  if (checks.responseDoneEqualsTaskDone === false) return '数据校验异常：认识+模糊+忘记 与 已复习+已新学 不一致。'
  if (checks.taskTotalEqualsDbTotal === false) return '数据校验异常：今日新学+今日复习 与 今日总数 不一致。'
  return ''
})

const overdueChartOption = computed(() => {
  const rows = actualRows()
  if (!rows.length) return null
  const labels = rows.map(row => axisLabel(row.date, rows[rows.length - 1]?.date, 0))
  const dates = rows.map(row => row.date)
  const data = rows.map(row => toNumber(overall(row).overdue, row['逾期']))

  return {
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const index = params[0]?.dataIndex ?? 0
        return `<b>${dates[index] || labels[index]}</b><br/>${params[0].marker}逾期: ${params[0].value}`
      },
    },
    grid: { left: 52, right: 22, top: 18, bottom: 35 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 11, interval: 0, rotate: rows.length > 14 ? 40 : 0 },
    },
    yAxis: { type: 'value', name: '逾期', minInterval: 1, axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '逾期',
        type: 'line',
        smooth: false,
        symbol: rows.length > 45 ? 'none' : 'circle',
        data,
        lineStyle: { width: 2, color: '#ef4444' },
        itemStyle: { color: '#ef4444' },
      },
    ],
  }
})
</script>

<style scoped>
.study-status-card { width: 100%; }
.mode-tabs {
  display: flex;
  justify-content: center;
  gap: 8px;
  margin-bottom: 8px;
}
.mode-tabs button {
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text-muted);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 13px;
  cursor: pointer;
}
.mode-tabs button.active {
  color: #0f766e;
  border-color: #14b8a6;
  background: rgba(20, 184, 166, .08);
  font-weight: 600;
}
.status-warning {
  margin-top: 8px;
  color: #b45309;
  background: rgba(251, 191, 36, .12);
  border: 1px solid rgba(251, 191, 36, .35);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}
.overdue-section {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 10px;
}
.section-title {
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 4px;
}
</style>
