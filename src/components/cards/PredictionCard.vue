<template>
  <div>
    <p class="info-text">柱高表示预测总量；绿色为新学，黄色为复习。概率分桶默认按 n/忘/模/认/Easy 统计；新词模拟时默认先进入次日复习。</p>

    <div class="prediction-toolbar">
      <label>预测天数 <input v-model.number="settings.predictionDays" type="number" min="1" step="1"></label>
      <label>初始化/补全方式
        <select v-model="settings.predictionInitMode">
          <option value="fsrs_estimate_by_count">按学习次数估算并跑 FSRS</option>
          <option value="first_seen_as_first_learning">数据库首次出现作为第一次学习</option>
        </select>
      </label>
      <label>每日新学方式
        <select v-model="settings.predictionDailyMode">
          <option value="fixed_new">固定每日新学</option>
          <option value="fixed_total">固定每日总量，新学=总量-复习</option>
        </select>
      </label>
      <label v-if="settings.predictionDailyMode === 'fixed_total'" class="switch-label">
        <input v-model="settings.predictionDeferOverflow" type="checkbox"> 超出部分顺延明天
      </label>
      <label>新学/总量数值 <input v-model="settings.predictionDailyValue" type="number" min="0" step="1" placeholder="0"></label>
      <label>复习数量上限 <input v-model="settings.predictionReviewLimit" type="number" min="0" step="1" placeholder="不限制"></label>
      <label class="switch-label"><input v-model="settings.predictionUseStudyCountDimension" type="checkbox" @change="settings.save()"> 使用学习次数维度</label>
      <label>记忆每桶目标词数 <input v-model.number="settings.predictionProbMemoryBucketSize" type="number" min="1" step="1" @change="settings.save()"></label>
      <label v-if="settings.predictionUseStudyCountDimension">学习次数桶宽 <input v-model.number="settings.predictionProbStudyCountBucketSize" type="number" min="1" step="1" @change="settings.save()"></label>
      <label>认识→
        <select v-model="settings.predictionRatingKnown" @change="settings.save()">
          <option value="good">Good/认识</option><option value="easy">Easy</option><option value="hard">Hard/模糊</option><option value="again">Again/忘记</option>
        </select>
      </label>
      <label>模糊→
        <select v-model="settings.predictionRatingVague" @change="settings.save()">
          <option value="hard">Hard/模糊</option><option value="good">Good/认识</option><option value="again">Again/忘记</option><option value="easy">Easy</option>
        </select>
      </label>
      <label>忘记→
        <select v-model="settings.predictionRatingForget" @change="settings.save()">
          <option value="again">Again/忘记</option><option value="hard">Hard/模糊</option><option value="good">Good/认识</option><option value="easy">Easy</option>
        </select>
      </label>
      <el-button type="primary" size="small" :loading="isRunning" @click="runPrediction">
        {{ isRunning ? `计算中 ${progressText}` : '应用预测配置并重新预测' }}
      </el-button>
    </div>

    <BaseChart v-if="predChartOption" :option="predChartOption" :height="400" @chart-click="openDayDetailFromChart" />

    <div v-if="prediction.result" class="summary-panel">
      <div class="summary-card">
        <strong>预测范围</strong>
        <span>未来 {{ prediction.result.rows.length }} 天</span>
        <small>{{ dateRange }}</small>
      </div>
      <div class="summary-card">
        <strong>预测总学习量</strong>
        <span>{{ totalStudy }}</span>
        <small>新学 {{ totalNew }}；复习 {{ totalReview }}</small>
      </div>
      <div class="summary-card">
        <strong>模拟词量</strong>
        <span>{{ prediction.result.simulationWordCount || 0 }}</span>
        <small>参考日：{{ prediction.result.referenceDate || '-' }}</small>
      </div>
      <div class="summary-card">
        <strong>概率样本</strong>
        <span>{{ prediction.result.distSummary?.sampleCount || 0 }}</span>
        <small>{{ prediction.result.distSummary?.global || '' }}</small>
      </div>
    </div>

    <div v-if="prediction.result?.rows?.length" class="stat-table-scroll">
      <el-table :data="prediction.result.rows" size="small" border stripe max-height="400" class="no-squeeze-table clickable-table" :fit="false" style="width:1030px" :row-style="{ cursor: 'pointer' }" @row-click="openDayDetailFromRow">
        <el-table-column prop="predictionDay" label="几天后" width="70" />
        <el-table-column prop="date" label="日期" width="110" />
        <el-table-column prop="predictedNew" label="预测新学" width="85" />
        <el-table-column prop="predictedReview" label="预测复习" width="85" />
        <el-table-column prop="predictedTotal" label="预测总量" width="85" />
        <el-table-column prop="knownOldReview" label="已知旧词复习" width="110" />
        <el-table-column prop="generatedReview" label="预测回流复习" width="110" />
        <el-table-column prop="deferredReview" label="顺延复习" width="85" />
        <el-table-column prop="cumulativeNew" label="累计新学" width="85" />
        <el-table-column prop="cumulativeReview" label="累计复习" width="85" />
        <el-table-column prop="dailyTargetMatchedCount" label="目标达标词数" width="110" />
      </el-table>
    </div>

    <el-dialog v-model="dayDetailVisible" :title="dayDetailTitle" width="min(960px, 94vw)" class="prediction-day-dialog">
      <div v-if="selectedDayDetail" class="day-detail-panel">
        <div class="day-detail-summary">
          <span>日期：<strong>{{ selectedDayDetail.date }}</strong></span>
          <span>第 {{ selectedDayDetail.day }} 天</span>
          <span>全部 {{ selectedDayDetail.words.length }} 个</span>
          <span>库存复习 {{ selectedDayKnownReviewCount }}</span>
          <span>模拟词复习 {{ selectedDayGeneratedReviewCount }}</span>
          <span>模拟新学 {{ selectedDayNewCount }}</span>
        </div>
        <el-table :data="selectedDayDetail.words" size="small" border stripe max-height="520" class="no-squeeze-table detail-word-table" :fit="false" style="width:100%">
          <el-table-column prop="sourceLabel" label="来源" width="90" />
          <el-table-column prop="actionLabel" label="动作" width="110" />
          <el-table-column prop="word" label="单词" min-width="140" />
          <el-table-column prop="rating" label="结果" width="80">
            <template #default="scope">{{ ratingLabel(scope.row.rating) }}</template>
          </el-table-column>
          <el-table-column prop="studyCount" label="学习次数" width="90" />
          <el-table-column prop="probabilityBucket" label="概率分桶" min-width="170" />
          <el-table-column prop="probabilityCellText" label="分桶概率" width="125" />
          <el-table-column prop="probabilitySource" label="概率来源" min-width="135" />
          <el-table-column prop="prevStability" label="原持久度" width="90">
            <template #default="scope">{{ formatDays(scope.row.prevStability) }}</template>
          </el-table-column>
          <el-table-column prop="nextStability" label="新持久度" width="90">
            <template #default="scope">{{ formatDays(scope.row.nextStability) }}</template>
          </el-table-column>
          <el-table-column prop="nextDueDay" label="下次第几天" width="100" />
          <el-table-column prop="key" label="ID" min-width="140" />
        </el-table>
      </div>
    </el-dialog>

    <div class="prediction-trace">
      <div class="prediction-trace-head">
        <div>
          <h3>单词预测追踪</h3>
          <p class="info-text">输入库存单词，查看它在本次预测中被排到哪天、抽到什么反应以及下次复习间隔。</p>
        </div>
        <div class="trace-controls">
          <input v-model.trim="traceWord" type="search" placeholder="输入单词">
          <el-button size="small" :loading="traceLoading" @click="renderTrace">查看</el-button>
        </div>
      </div>
      <div class="prediction-trace-result">
        <span v-if="!traceResult" class="summary-muted">输入单词后查看预测路径。</span>
        <template v-else>
          <div class="trace-title">{{ traceResult.title }}</div>
          <div v-if="traceResult.rows.length" class="trace-list">
            <div v-for="(row, index) in traceResult.rows" :key="`${row.key || row.word}-${row.day}-${row.nextDueDay}-${row.rating}-${index}`" class="trace-row">
              第 {{ row.day }} 天 / {{ row.date }}：{{ row.word || row.key || '-' }}，{{ row.actionLabel || (row.source === 'generated' ? '模拟词复习' : '库存词复习') }}，结果 {{ ratingLabel(row.rating) }}，概率分桶 {{ row.probabilityBucket || '-' }}，记忆持久度 {{ Math.round(row.prevStability) }} → {{ Math.round(row.nextStability) }} 天，下次第 {{ row.nextDueDay }} 天。
            </div>
          </div>
          <div v-else class="info-text">本次预测周期内没有排到这个词，或没有找到匹配记录。</div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settingsStore'
import { usePredictionStore } from '@/stores/predictionStore'
import { buildBarOption, COLORS } from '@/utils/chartOptions'
import BaseChart from '@/components/charts/BaseChart.vue'
import { useApi } from '@/composables/useApi'

defineProps({ card: Object })

const settings = useSettingsStore()
const prediction = usePredictionStore()
const api = useApi()
const log = inject('log', console.log)
const isRunning = ref(false)
const progress = ref({ current: 0, total: 0 })
const traceWord = ref('')
const traceResult = ref(null)
const detailLoading = ref(false)
const traceLoading = ref(false)
const dayDetailVisible = ref(false)
const selectedDayDetail = ref(null)

const progressText = computed(() => progress.value.total ? `${progress.value.current}/${progress.value.total}` : '')

const dateRange = computed(() => {
  const rows = prediction.result?.rows || []
  if (!rows.length) return '-'
  return `${rows[0].date} ~ ${rows[rows.length - 1].date}`
})

const totalNew = computed(() => (prediction.result?.rows || []).reduce((sum, row) => sum + (Number(row.predictedNew) || 0), 0))
const totalReview = computed(() => (prediction.result?.rows || []).reduce((sum, row) => sum + (Number(row.predictedReview) || 0), 0))
const totalStudy = computed(() => totalNew.value + totalReview.value)

const dayDetailTitle = computed(() => {
  const detail = selectedDayDetail.value
  if (!detail) return '预测日单词明细'
  return `预测日单词明细：第 ${detail.day} 天 / ${detail.date}`
})

const selectedDayKnownReviewCount = computed(() => (selectedDayDetail.value?.words || []).filter(item => item.action === 'review' && item.source !== 'generated').length)
const selectedDayGeneratedReviewCount = computed(() => (selectedDayDetail.value?.words || []).filter(item => item.action === 'review' && item.source === 'generated').length)
const selectedDayNewCount = computed(() => (selectedDayDetail.value?.words || []).filter(item => item.action === 'new').length)

const predChartOption = computed(() => {
  const rows = prediction.result?.rows || []
  if (!rows.length) return null
  const labels = rows.map(row => String(row.predictionDay))
  return buildBarOption(labels, [
    { name: '预测新学', data: rows.map(row => Number(row.predictedNew) || 0), color: COLORS.green, stack: 'total' },
    { name: '预测复习', data: rows.map(row => Number(row.predictedReview) || 0), color: COLORS.yellow, stack: 'total' },
  ], { yUnit: '词数' })
})

function predictionOptions() {
  return {
    horizonDays: settings.predictionDays,
    initMode: settings.predictionInitMode,
    dailyMode: settings.predictionDailyMode,
    dailyBaseValue: settings.predictionDailyValue,
    reviewLimit: settings.predictionReviewLimit,
    deferOverflow: settings.predictionDeferOverflow,
    ratingMapping: {
      known: settings.predictionRatingKnown,
      vague: settings.predictionRatingVague,
      forget: settings.predictionRatingForget,
    },
    targetSettings: {
      metric: settings.predictionTargetMetric,
      days: settings.predictionTargetDays,
      count: settings.predictionTargetCount,
    },
    modelFrom: settings.predictionModelFrom,
    modelTo: settings.predictionModelTo,
    probabilitySettings: {
      memoryBucketSize: settings.predictionProbMemoryBucketSize,
      studyCountBucketSize: settings.predictionProbStudyCountBucketSize,
      useStudyCountDimension: settings.predictionUseStudyCountDimension,
    },
  }
}

async function runPrediction() {
  isRunning.value = true
  progress.value = { current: 0, total: Number(settings.predictionDays) || 30 }
  settings.save()
  try {
    const data = await api.fetchFsrsPrediction(predictionOptions())
    if (!data?.success) throw new Error(data?.error || data?.result?.message || '后端 FSRS 预测失败')
    prediction.result = data.result
    prediction.saveCache()
    traceResult.value = null
    if (prediction.result?.message && !prediction.result?.rows?.length) {
      ElMessage.warning(prediction.result.message)
      log?.(`预测计算完成但无结果：${prediction.result.message}`)
    } else {
      ElMessage.success('预测计算完成')
      log?.('预测计算完成')
    }
  } catch (error) {
    ElMessage.error(error.message || String(error))
    log?.(`预测计算失败：${error.message || error}`)
  } finally {
    progress.value = { current: Number(settings.predictionDays) || 30, total: Number(settings.predictionDays) || 30 }
    isRunning.value = false
  }
}

function ratingLabel(rating) {
  return { again: '忘', hard: '模', good: '认', easy: 'Easy', new: '新学' }[rating] || rating || '-'
}

function formatDays(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${Math.round(n)}天`
}

function normalizeDayKey(value) {
  return String(value ?? '').trim()
}

function cachedDayDetail(rowOrDay) {
  const result = prediction.result
  if (!result) return null
  const day = normalizeDayKey(rowOrDay?.predictionDay ?? rowOrDay?.day ?? rowOrDay)
  const date = normalizeDayKey(rowOrDay?.date)
  const row = (result.rows || []).find(item => normalizeDayKey(item.predictionDay) === day || (date && item.date === date))
  const detail = (result.dayWordDetails || []).find(item => normalizeDayKey(item.day) === day || (date && item.date === date))
  const words = Array.isArray(row?.words) && row.words.length
    ? row.words
    : Array.isArray(detail?.words)
      ? detail.words
      : []
  return {
    day: Number(row?.predictionDay ?? detail?.day ?? day) || 0,
    date: row?.date || detail?.date || date || '-',
    words,
  }
}

async function findDayDetail(rowOrDay) {
  const cached = cachedDayDetail(rowOrDay)
  if (cached?.words?.length) return cached
  const day = Number(cached?.day || rowOrDay?.predictionDay || rowOrDay?.day || rowOrDay)
  if (!Number.isFinite(day) || day <= 0) return cached
  detailLoading.value = true
  try {
    const data = await api.fetchFsrsPredictionDay({ ...predictionOptions(), day })
    if (!data?.success) throw new Error(data?.error || '后端预测日明细失败')
    const detail = data.detail || { day, date: cached?.date || '-', words: [] }
    const result = prediction.result
    if (result) {
      const existing = Array.isArray(result.dayWordDetails) ? result.dayWordDetails : []
      result.dayWordDetails = [...existing.filter(item => normalizeDayKey(item.day) !== normalizeDayKey(detail.day)), detail]
    }
    return {
      day: Number(detail.day || day) || day,
      date: detail.date || cached?.date || '-',
      words: Array.isArray(detail.words) ? detail.words : [],
    }
  } finally {
    detailLoading.value = false
  }
}

async function openDayDetail(rowOrDay) {
  try {
    const detail = await findDayDetail(rowOrDay)
    if (!detail || !detail.words.length) {
      ElMessage.warning('这一天没有可显示的逐词明细')
      return
    }
    selectedDayDetail.value = detail
    dayDetailVisible.value = true
  } catch (error) {
    ElMessage.error(error.message || String(error))
    log?.(`预测日明细加载失败：${error.message || error}`)
    return
  }
}

function openDayDetailFromRow(row) {
  openDayDetail(row)
}

function openDayDetailFromChart(params) {
  const index = Number(params?.dataIndex)
  const row = Number.isFinite(index) ? prediction.result?.rows?.[index] : null
  if (row) openDayDetail(row)
}


function normalizeSearch(value) {
  return String(value || '').trim().toLowerCase()
}

async function renderTrace() {
  const query = normalizeSearch(traceWord.value)
  if (!query) {
    traceResult.value = null
    return
  }
  traceLoading.value = true
  traceResult.value = { title: '查询中...', rows: [] }
  try {
    const data = await api.fetchFsrsPredictionTrace({ ...predictionOptions(), query })
    if (!data?.success) throw new Error(data?.error || '后端单词追踪失败')
    traceResult.value = data.trace || { title: `未找到：${traceWord.value}`, rows: [] }
  } catch (error) {
    traceResult.value = { title: `查询失败：${error.message || error}`, rows: [] }
    ElMessage.error(error.message || String(error))
  } finally {
    traceLoading.value = false
  }
}
</script>

<style scoped>
.prediction-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  margin: 12px 0 14px;
}
.prediction-toolbar label,
.switch-label {
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.prediction-toolbar input,
.prediction-toolbar select,
.trace-controls input {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.prediction-toolbar input[type='number'] {
  width: 90px;
}
.summary-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
  margin: 12px 0;
}
.summary-card {
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.summary-card strong { font-size: 12px; color: #64748b; }
.summary-card span { font-size: 18px; font-weight: 700; color: #0f172a; }
.summary-card small { color: #94a3b8; }
.prediction-trace {
  margin-top: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px;
  background: #fff;
}
.prediction-trace-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}
.prediction-trace h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.trace-controls {
  display: flex;
  gap: 6px;
  align-items: center;
}
.prediction-trace-result {
  margin-top: 10px;
  font-size: 13px;
}
.trace-title {
  font-weight: 600;
  margin-bottom: 6px;
}
.trace-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  max-height: 260px;
  overflow: auto;
}
.trace-row {
  padding: 6px 8px;
  border-radius: 8px;
  background: #f8fafc;
}
.summary-muted { color: #94a3b8; }

.day-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.day-detail-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #f8fafc;
  font-size: 13px;
}
.detail-word-table {
  font-size: 12px;
}
.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}
</style>
