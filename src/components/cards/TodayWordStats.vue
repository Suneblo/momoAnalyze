<template>
  <div class="today-word-stats-card">
    <div class="info-text">
      “当日”以东八区 04:00 为分界线；横轴按小时刻度显示，数据点按数据库原始快照时间落点。
    </div>

    <div class="prediction-toolbar">
      <label>日期 <input v-model="selectedDate" type="date" style="width:150px"></label>
      <el-button size="small" :loading="loading" @click="loadDate">查看</el-button>
      <span class="info-text">{{ statusText }}</span>
    </div>

    <div class="field-toolbar">
      <span class="field-title">图表字段</span>
      <label v-for="field in chartFieldDefs" :key="field.key" class="check-item">
        <input v-model="enabledFields[field.key]" type="checkbox">
        {{ field.name }}
      </label>
      <el-button size="small" @click="selectCoreFields">核心字段</el-button>
      <el-button size="small" @click="selectAllFields">全选</el-button>
      <el-button size="small" @click="clearFields">清空</el-button>
    </div>

    <BaseChart v-if="chartOption" :option="chartOption" :height="360" />
    <p v-else class="info-text">暂无可绘制快照点。</p>

    <div class="duration-chart-section">
      <div class="section-header">
        <h3>累计学习时长</h3>
        <span class="info-text">按快照时间展示当天累计学习时长。</span>
      </div>
      <BaseChart v-if="studyTimeChartOption" :option="studyTimeChartOption" :height="260" />
      <p v-else class="info-text">暂无学习时长快照数据。</p>
    </div>

    <div class="snapshot-data-section">
      <div class="section-header">
        <h3>快照实时数据</h3>
        <span class="info-text">包含认识、模糊、忘记、临界、未完成等实时字段。</span>
      </div>
      <div class="table-wrapper stat-table-scroll snapshot-table-wrap">
        <table class="mini-table snapshot-table">
          <thead>
            <tr>
              <th>快照时间</th>
              <th>已完成</th>
              <th>总数</th>
              <th>未完成</th>
              <th>记忆临界点(≤0天)</th>
              <th>逾期</th>
              <th>今日认识(已完成)</th>
              <th>今日模糊(已完成)</th>
              <th>今日忘记(已完成)</th>
              <th>今日认识(全部)</th>
              <th>今日模糊(全部)</th>
              <th>今日忘记(全部)</th>
              <th>今日已复习</th>
              <th>今日待复习</th>
              <th>今日复习总任务</th>
              <th>今日已新学</th>
              <th>今日待新学</th>
              <th>今日新学总任务</th>
              <th>当前总词数</th>
              <th>认识状态</th>
              <th>模糊状态</th>
              <th>忘记状态</th>
              <th>学习时长</th>
              <th>原始快照</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!orderedSnapshots.length">
              <td colspan="24" class="empty-cell">暂无快照数据。</td>
            </tr>
            <tr v-for="snapshot in orderedSnapshots" :key="snapshot.name || snapshot.displayName">
              <td>{{ snapshot.displayName || snapshot.timeLabel || snapshot.name || '-' }}</td>
              <td>{{ valueOf(snapshot, 'finished') }}</td>
              <td>{{ valueOf(snapshot, 'total') }}</td>
              <td>{{ valueOf(snapshot, 'unfinished') }}</td>
              <td>{{ valueOf(snapshot, 'criticalDueToday') }}</td>
              <td>{{ valueOf(snapshot, 'overdue') }}</td>
              <td>{{ valueOf(snapshot, 'known') }}</td>
              <td>{{ valueOf(snapshot, 'vague') }}</td>
              <td>{{ valueOf(snapshot, 'forget') }}</td>
              <td>{{ valueOf(snapshot, 'allKnown') }}</td>
              <td>{{ valueOf(snapshot, 'allVague') }}</td>
              <td>{{ valueOf(snapshot, 'allForget') }}</td>
              <td>{{ valueOf(snapshot, 'reviewDone') }}</td>
              <td>{{ valueOf(snapshot, 'reviewPending') }}</td>
              <td>{{ valueOf(snapshot, 'reviewTotal') }}</td>
              <td>{{ valueOf(snapshot, 'newDone') }}</td>
              <td>{{ valueOf(snapshot, 'newPending') }}</td>
              <td>{{ valueOf(snapshot, 'newTotal') }}</td>
              <td>{{ valueOf(snapshot, 'totalWords') }}</td>
              <td>{{ valueOf(snapshot, 'knownState') }}</td>
              <td>{{ valueOf(snapshot, 'vagueState') }}</td>
              <td>{{ valueOf(snapshot, 'forgetState') }}</td>
              <td>{{ formatDurationCN(valueOf(snapshot, 'studyTimeMs', 0)) }}</td>
              <td>{{ snapshot.name || '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useApi } from '@/composables/useApi'
import BaseChart from '@/components/charts/BaseChart.vue'

defineProps({ card: Object })

const dataStore = useDataStore()
const api = useApi()
const loading = ref(false)
const selectedDate = ref(localDateString())

const chartFieldDefs = [
  { key: 'known', name: '认识', color: '#2563eb', getter: s => valueOf(s, 'allKnown', 0) },
  { key: 'vague', name: '模糊', color: '#f59e0b', getter: s => valueOf(s, 'allVague', 0) },
  { key: 'forget', name: '忘记', color: '#dc2626', getter: s => valueOf(s, 'allForget', 0) },
  { key: 'criticalDueToday', name: '临界', color: '#7c3aed', getter: s => valueOf(s, 'criticalDueToday', 0) },
  { key: 'unfinished', name: '未完成', color: '#6b7280', getter: s => valueOf(s, 'unfinished', 0) },
  { key: 'finished', name: '已完成', color: '#22c55e', getter: s => valueOf(s, 'finished', 0) },
  { key: 'total', name: '当日总数', color: '#14b8a6', getter: s => valueOf(s, 'total', 0) },
  { key: 'reviewTotal', name: '今日复习', color: '#f97316', getter: s => valueOf(s, 'reviewTotal', 0) },
  { key: 'newTotal', name: '今日新学', color: '#0ea5e9', getter: s => valueOf(s, 'newTotal', 0) },
  { key: 'overdue', name: '逾期', color: '#991b1b', getter: s => valueOf(s, 'overdue', 0) },
]

const enabledFields = reactive({
  known: true,
  vague: true,
  forget: true,
  criticalDueToday: true,
  unfinished: true,
  finished: false,
  total: true,
  reviewTotal: false,
  newTotal: false,
  overdue: false,
})

const HOUR_MS = 60 * 60 * 1000
const DAY_MS = 24 * HOUR_MS
const HOURLY_POINT_WIDTH = 52
const HOURLY_CHART_MIN_WIDTH = 24 * HOURLY_POINT_WIDTH
const HOURLY_SPLIT_AREA = {
  show: true,
  areaStyle: { color: ['rgba(15, 23, 42, .035)', 'rgba(255,255,255,0)'] },
}
const HOURLY_GRID = { left: 52, right: 22, top: 18, bottom: 55 }
const HOURLY_X_AXIS_LABEL = {
  formatter: value => formatHourTick(value),
  fontSize: 11,
  hideOverlap: true,
}
const HOURLY_Y_AXIS_LABEL = { fontSize: 11 }

const workspace = computed(() => dataStore.todayWorkspace || { date: '', snapshots: [], allProgressText: '', error: '' })

const orderedSnapshots = computed(() => {
  const items = Array.isArray(workspace.value.snapshots) ? workspace.value.snapshots : []
  return [...items]
    .filter(snapshot => Number.isFinite(Number(snapshot?.timeMs)) || snapshot?.name || snapshot?.displayName)
    .sort((a, b) => snapshotMs(a) - snapshotMs(b))
})

const statusText = computed(() => {
  if (workspace.value.error) return workspace.value.error
  return orderedSnapshots.value.length ? `已载入 ${orderedSnapshots.value.length} 个原始快照点` : '暂无可绘制快照点'
})

const chartOption = computed(() => {
  const snapshots = orderedSnapshots.value.filter(snapshot => Number.isFinite(snapshotMs(snapshot)))
  if (!snapshots.length) return null

  const date = workspace.value.date || selectedDate.value || localDateString()
  const startMs = getStudyDayStartMs(date)
  const endMs = startMs + DAY_MS
  const seriesDefs = chartFieldDefs.filter(field => enabledFields[field.key])
  if (!seriesDefs.length) return null

  return {
    __minWidth: HOURLY_CHART_MIN_WIDTH,
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const items = Array.isArray(params) ? params : [params]
        const first = items[0]
        const time = first?.value?.[0] ? formatFullTime(first.value[0]) : ''
        let text = `<b>${time}</b><br/>`
        for (const item of items) {
          const value = Array.isArray(item.value) ? item.value[1] : item.value
          text += `${item.marker}${item.seriesName}: ${value}<br/>`
        }
        return text
      },
    },
    legend: {
      data: seriesDefs.map(field => field.name),
      bottom: 0,
      type: 'scroll',
      textStyle: { fontSize: 12 },
    },
    grid: HOURLY_GRID,
    xAxis: {
      type: 'time',
      min: startMs,
      max: endMs,
      interval: HOUR_MS,
      minInterval: HOUR_MS,
      maxInterval: HOUR_MS,
      name: '时间',
      splitArea: HOURLY_SPLIT_AREA,
      splitLine: { show: true, lineStyle: { color: 'rgba(148, 163, 184, .18)' } },
      axisLabel: HOURLY_X_AXIS_LABEL,
    },
    yAxis: { type: 'value', name: '词数', minInterval: 1, axisLabel: HOURLY_Y_AXIS_LABEL },
    series: seriesDefs.map(field => ({
      name: field.name,
      type: 'line',
      smooth: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: snapshots.length > 36 ? 4 : 6,
      data: snapshots.map(snapshot => [snapshotMs(snapshot), field.getter(snapshot)]),
      itemStyle: { color: field.color },
      lineStyle: { color: field.color, width: 2 },
    })),
  }
})

const studyTimeChartOption = computed(() => {
  const snapshots = orderedSnapshots.value.filter(snapshot => Number.isFinite(snapshotMs(snapshot)))
  if (!snapshots.length) return null

  const hasStudyTime = snapshots.some(snapshot => Number(valueOf(snapshot, 'studyTimeMs', 0)) > 0)
  if (!hasStudyTime) return null

  const date = workspace.value.date || selectedDate.value || localDateString()
  const startMs = getStudyDayStartMs(date)
  const endMs = startMs + DAY_MS
  const data = snapshots.map(snapshot => [
    snapshotMs(snapshot),
    Math.round((Number(valueOf(snapshot, 'studyTimeMs', 0)) || 0) / 6000) / 10,
  ])

  return {
    __minWidth: 960,
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const item = Array.isArray(params) ? params[0] : params
        const time = item?.value?.[0] ? formatFullTime(item.value[0]) : ''
        const minutes = Array.isArray(item?.value) ? item.value[1] : 0
        return `<b>${time}</b><br/>累计学习时长：${minutes} 分钟`
      },
    },
    grid: { left: 58, right: 28, top: 20, bottom: 42 },
    xAxis: {
      type: 'time',
      min: startMs,
      max: endMs,
      interval: 60 * 60 * 1000,
      name: '时间',
      axisLabel: { formatter: value => formatHourTick(value) },
    },
    yAxis: {
      type: 'value',
      name: '分钟',
      min: 0,
      minInterval: 1,
    },
    series: [{
      name: '累计学习时长',
      type: 'line',
      smooth: false,
      showSymbol: true,
      symbol: 'circle',
      symbolSize: snapshots.length > 36 ? 4 : 6,
      data,
      areaStyle: { opacity: 0.08 },
    }],
  }
})

watch(() => workspace.value.date, value => {
  if (value) selectedDate.value = value
}, { immediate: true })

onMounted(() => {
  if (workspace.value.date) selectedDate.value = workspace.value.date
})

function localDateString() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function getStudyDayStartMs(dateText) {
  const date = String(dateText || localDateString()).slice(0, 10)
  const value = Date.parse(`${date}T04:00:00+08:00`)
  return Number.isFinite(value) ? value : Date.now()
}

function snapshotMs(snapshot) {
  const direct = Number(snapshot?.timeMs)
  if (Number.isFinite(direct) && direct > 0) return direct
  const parsed = Date.parse(snapshot?.name || snapshot?.displayName || '')
  return Number.isFinite(parsed) ? parsed : 0
}

function summaryOf(snapshot) {
  return snapshot?.summary || {}
}

function statusToday(snapshot) {
  return snapshot?.studyStatus?.today || {}
}

function statusOverall(snapshot) {
  return snapshot?.studyStatus?.overall || {}
}

function statusCritical(snapshot) {
  return snapshot?.studyStatus?.critical || {}
}

function valueOf(snapshot, key, fallback = '-') {
  const s = summaryOf(snapshot)
  const today = statusToday(snapshot)
  const overall = statusOverall(snapshot)
  const critical = statusCritical(snapshot)
  const map = {
    finished: [today.finished, s.finished],
    total: [today.total, s.total],
    unfinished: [today.unfinished, s.unfinished, s.todoItemsCount, Number(s.total || 0) - Number(s.finished || 0)],
    known: [today.known, s.known, s.familiar],
    vague: [today.vague, s.vague, s.doneVague],
    forget: [today.forget, s.forget, s.firstForgetDone],
    allKnown: [today.allKnown, s.allKnown, s.allFamiliar, s.familiar],
    allVague: [today.allVague, s.allVague],
    allForget: [today.allForget, s.allForget, s.firstForgetAll],
    reviewDone: [today.reviewDone, s.reviewDone, s.doneReviewWords],
    reviewPending: [today.reviewPending, s.reviewPending, s.pendingReview],
    reviewTotal: [today.reviewTotal, s.reviewTotal, s.reviewWords],
    newDone: [today.newDone, s.newDone, s.doneNewWords],
    newPending: [today.newPending, s.newPending, s.pendingNew],
    newTotal: [today.newTotal, s.newTotal, s.newWords],
    studyTimeMs: [today.studyTimeMs, s.studyTimeMs],
    criticalDueToday: [critical.dueToday, s.criticalDueToday, s.dueToday],
    overdue: [overall.overdue, s.overdue],
    totalWords: [overall.totalWords, s.totalWords],
    knownState: [overall.knownState, s.knownState],
    vagueState: [overall.vagueState, s.vagueState],
    forgetState: [overall.forgetState, s.forgetState],
  }
  const values = map[key] || [s[key]]
  for (const value of values) {
    if (value !== undefined && value !== null && value !== '' && Number.isFinite(Number(value))) return Math.round(Number(value))
  }
  return fallback
}

function formatHourTick(value) {
  const d = new Date(Number(value))
  const h = String(d.getHours()).padStart(2, '0')
  return `${h}:00`
}

function formatFullTime(value) {
  const d = new Date(Number(value))
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  const sec = String(d.getSeconds()).padStart(2, '0')
  return `${y}-${m}-${day} ${h}:${min}:${sec}`
}

function formatDurationCN(ms) {
  const sec = Number(ms || 0) / 1000
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  if (h > 0) return `${h}时${m}分${s.toFixed(1)}秒`
  if (m > 0) return `${m}分${s.toFixed(1)}秒`
  return `${s.toFixed(1)}秒`
}

function selectCoreFields() {
  for (const field of chartFieldDefs) enabledFields[field.key] = ['known', 'vague', 'forget', 'criticalDueToday', 'unfinished', 'total'].includes(field.key)
}

function selectAllFields() {
  for (const field of chartFieldDefs) enabledFields[field.key] = true
}

function clearFields() {
  for (const field of chartFieldDefs) enabledFields[field.key] = false
}

async function loadDate() {
  if (!selectedDate.value) return
  loading.value = true
  try {
    const data = await api.fetchTodayWorkspace(selectedDate.value)
    if (data?.todayWorkspace) {
      dataStore.todayWorkspace = data.todayWorkspace
    } else {
      dataStore.todayWorkspace = { date: selectedDate.value, snapshots: [], allProgressText: '', error: '该日期暂无快照数据' }
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.today-word-stats-card { width: 100%; }
.prediction-toolbar,
.field-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin-bottom: 10px;
}
.field-toolbar {
  padding: 8px 10px;
  border: 1px dashed var(--border);
  border-radius: 10px;
  background: rgba(248, 250, 252, .72);
}
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
}
.check-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--text-muted);
  white-space: nowrap;
}
.duration-chart-section,
.snapshot-data-section { margin-top: 14px; }
.section-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 12px;
  margin-bottom: 6px;
}
.section-header h3 {
  margin: 0;
  font-size: 15px;
}
.snapshot-table-wrap { max-height: 420px; overflow: auto; }
.snapshot-table th,
.snapshot-table td {
  padding: 6px 9px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
  text-align: right;
}
.snapshot-table th:first-child,
.snapshot-table td:first-child,
.snapshot-table th:last-child,
.snapshot-table td:last-child {
  text-align: left;
}
.empty-cell { text-align: center !important; color: var(--text-muted); }
</style>
