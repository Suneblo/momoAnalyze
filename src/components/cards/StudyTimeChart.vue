<template>
  <div>
    <BaseChart v-if="chartOption" :option="chartOption" :height="350" />
    <p v-else class="info-text">暂无数据</p>

    <div v-if="summary" class="summary-grid compact">
      <div class="summary-card summary-card-strong">
        <div class="summary-label">近{{ summary.windowDays }}日统计范围</div>
        <div class="summary-value small">{{ summary.startDate }} ~ {{ summary.endDate }}</div>
        <div class="summary-sub">缺失日期按 0 计算；有进度数据 {{ summary.daysWithData }} 天</div>
      </div>
      <div class="summary-card summary-card-hot">
        <div class="summary-label">近{{ summary.windowDays }}日总时长</div>
        <div class="summary-value">{{ formatDuration(summary.totalMs) }}</div>
        <div class="summary-sub">{{ summary.totalMinutes.toFixed(2) }} 分钟</div>
      </div>
      <div class="summary-card">
        <div class="summary-label">近{{ summary.windowDays }}日平均时长</div>
        <div class="summary-value">{{ formatDuration(summary.avgMs) }}</div>
        <div class="summary-sub">{{ summary.avgMinutes.toFixed(2) }} 分钟/天</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { sortRowsByDateAsc } from '@/utils/memoryThresholds'
import BaseChart from '@/components/charts/BaseChart.vue'

defineProps({ card: Object })
const dataStore = useDataStore()
const settings = useSettingsStore()

function parseDateOnly(date) {
  const match = String(date || '').match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return null
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
}

function formatDateOnly(date) {
  return date.toISOString().slice(0, 10)
}

function addDays(dateStr, delta) {
  const date = parseDateOnly(dateStr)
  if (!date) return ''
  date.setUTCDate(date.getUTCDate() + delta)
  return formatDateOnly(date)
}

function todayDateOnly() {
  const now = new Date()
  return formatDateOnly(new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())))
}

function maxDateOnly(a, b) {
  if (!a) return b || ''
  if (!b) return a || ''
  return String(a) >= String(b) ? String(a) : String(b)
}

function formatDuration(ms) {
  const value = Number(ms) || 0
  const min = value / 60000
  if (min < 60) return `${Math.round(min)}分`
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return m > 0 ? `${h}时${m}分` : `${h}时`
}

function formatMinutes(ms) {
  return Number(((Number(ms) || 0) / 60000).toFixed(1))
}

const studyRows = computed(() => {
  const windowDays = Math.max(1, Number(settings.studyTimeWindowDays) || 30)
  const rows = sortRowsByDateAsc(dataStore.rawRows)
  const lastDataDate = rows.filter(row => row?.date).at(-1)?.date || ''
  const endDate = maxDateOnly(lastDataDate, todayDateOnly())
  if (!endDate) return []

  const byDate = new Map(rows.filter(row => row?.date).map(row => [row.date, row]))
  const out = []

  for (let offset = windowDays - 1; offset >= 0; offset--) {
    const date = addDays(endDate, -offset)
    const src = byDate.get(date)
    out.push({
      ...(src || {}),
      date,
      hasData: Boolean(src && src.hasData),
      hasOverviewData: Boolean(src && src.hasOverviewData),
      hasProgressData: Boolean(src && src.hasProgressData),
      studyTimeMs: src && src.hasProgressData ? Number(src.studyTimeMs || 0) : 0,
      isGapMarker: false,
    })
  }

  return out
})

const summary = computed(() => {
  const rows = studyRows.value
  if (!rows.length) return null

  const windowDays = rows.length
  const totalMs = rows.reduce((sum, row) => sum + Number(row.studyTimeMs || 0), 0)
  const avgMs = totalMs / Math.max(1, windowDays)

  return {
    windowDays,
    daysWithData: rows.filter(row => row.hasProgressData).length,
    startDate: rows[0]?.date || '',
    endDate: rows[rows.length - 1]?.date || '',
    totalMs,
    avgMs,
    totalMinutes: totalMs / 60000,
    avgMinutes: avgMs / 60000,
  }
})

const chartOption = computed(() => {
  const rows = studyRows.value
  if (!rows.length) return null

  const labels = rows.map(row => row.date?.slice(5) || '')
  const data = rows.map(row => formatMinutes(row.studyTimeMs))

  return {
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const index = params[0]?.dataIndex ?? 0
        const value = params[0]?.value || 0
        return `${rows[index]?.date || params[0]?.axisValue}<br/>每日学习时长：${value.toFixed(1)} 分钟<br/>${formatDuration(value * 60000)}`
      }
    },
    legend: { data: ['每日学习时长'], bottom: 0, textStyle: { fontSize: 12 } },
    grid: { left: 55, right: 20, top: 15, bottom: 40 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, rotate: rows.length > 20 ? 45 : 0 } },
    yAxis: { type: 'value', name: '分钟', minInterval: 1, axisLabel: { fontSize: 11 } },
    series: [
      {
        name: '每日学习时长',
        type: 'line',
        data,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 2, color: '#e11d48' },
        itemStyle: { color: '#e11d48' },
      }
    ]
  }
})
</script>

<style scoped>
.summary-grid.compact {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.summary-card {
  background: #f8fafc;
  border-radius: 10px;
  padding: 10px 14px;
}
.summary-card-strong { background: #eef2ff; }
.summary-card-hot { background: #fff1f2; }
.summary-label { font-size: 12px; color: #64748b; }
.summary-value { font-size: 18px; font-weight: 600; color: #1e293b; }
.summary-value.small { font-size: 15px; }
.summary-sub { font-size: 12px; color: #64748b; margin-top: 2px; }
</style>
