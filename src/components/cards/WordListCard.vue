<template>
  <div>
    <p class="info-text">默认读取当前范围内最新一天。列、筛选、排序和分页都可以调整。</p>

    <div class="word-list-layout">
      <div class="word-list-controls">
        <label>日期
          <select v-model="form.date">
            <option v-for="date in dateOptions" :key="date" :value="date">{{ date }}</option>
          </select>
        </label>
        <label>搜索单词 <input v-model.trim="form.q" type="search" placeholder="完整或部分单词"></label>
        <label>状态
          <select v-model="form.state">
            <option value="">全部</option>
            <option value="认识">认识</option>
            <option value="模糊">模糊</option>
            <option value="忘记">忘记</option>
            <option value="逾期">逾期</option>
          </select>
        </label>
        <label>标签
          <select v-model="form.tag">
            <option value="">全部</option>
            <option value="熟知">熟知</option>
            <option value="顽固">顽固</option>
          </select>
        </label>
        <label>学习次数
          <select v-model="form.studyPreset" @change="applyStudyPreset">
            <option value="">全部</option>
            <option value="1">1</option>
            <option value="2-5">2-5</option>
            <option value=">=10">&gt;=10</option>
            <option value="custom">自定义</option>
          </select>
        </label>
        <label>次数最小 <input v-model="form.minStudyCount" type="number" min="0" step="1"></label>
        <label>次数最大 <input v-model="form.maxStudyCount" type="number" min="0" step="1"></label>
        <label>下次复习
          <select v-model="form.due">
            <option value="">全部</option>
            <option value="today">今天待复习</option>
            <option value="tomorrow">明天</option>
            <option value="next7">未来7天</option>
            <option value="overdue">已逾期</option>
            <option value="overdue_gt">逾期超过N天</option>
          </select>
        </label>
        <label>逾期N天 <input v-model="form.overdueDays" type="number" min="1" step="1"></label>
        <label>加入日期
          <select v-model="form.addRange" @change="applyAddRangePreset">
            <option value="">全部</option>
            <option value="recent7">最近7天新增</option>
            <option value="recent30">最近30天新增</option>
            <option value="custom">自定义</option>
          </select>
        </label>
        <label>加入起始 <input v-model="form.addFrom" type="date"></label>
        <label>加入结束 <input v-model="form.addTo" type="date"></label>
        <label>今日联动
          <select v-model="form.todayFilter">
            <option value="">不筛选</option>
            <option value="all">今日全部单词</option>
            <option value="done">今日已完成</option>
            <option value="todo">今日未完成</option>
            <option value="new">今日新学</option>
            <option value="review">今日复习</option>
            <option value="forget">今日首次忘记</option>
            <option value="vague">今日模糊</option>
            <option value="familiar">今日认识</option>
          </select>
        </label>
        <label>每页
          <select v-model="form.pageSize">
            <option value="200">200</option>
            <option value="500">500</option>
            <option value="1000">1000</option>
            <option value="2000">2000</option>
            <option value="all">全部</option>
          </select>
        </label>
      </div>

      <div class="word-list-columns">
        <label v-for="column in WORD_LIST_COLUMNS" :key="column.key">
          <input v-model="visibleColumns" type="checkbox" :value="column.key">
          {{ column.label }}
        </label>
      </div>

      <div class="word-list-sortbar">
        <button
          v-for="sortDef in sortDefs"
          :key="sortDef.sort"
          type="button"
          class="word-sort-btn"
          :class="{ active: sort === sortDef.sort }"
          @click="setSort(sortDef.sort)"
        >
          {{ sortDef.label }}{{ sort === sortDef.sort ? (dir === 'asc' ? ' ↑' : ' ↓') : '' }}
        </button>
      </div>

      <div class="inline-actions word-list-actions">
        <el-button size="small" type="primary" :loading="loading" @click="loadWordList(1)">查询单词列表</el-button>
        <el-button size="small" :disabled="page <= 1 || loading" @click="prevPage">上一页</el-button>
        <el-button size="small" :disabled="page >= pageCount || loading" @click="nextPage">下一页</el-button>
        <el-button size="small" :disabled="!output" @click="copyPage">复制当前页</el-button>
        <el-button size="small" :loading="copyingAll" @click="copyAll">复制全部筛选结果</el-button>
        <el-button size="small" :loading="copyingWords" @click="copyWordsOnly">只复制单词</el-button>
        <el-button size="small" type="warning" :loading="advancing" @click="advanceCurrentWordList">当前筛选结果提前复习</el-button>
        <span class="info-text">{{ status }}</span>
      </div>

      <textarea v-model="output" class="word-list-output" placeholder="点击“查询单词列表”后显示文本"></textarea>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useApi } from '@/composables/useApi'
import { copyToClipboard } from '@/composables/useCopy'
import { useDataStore } from '@/stores/dataStore'
import {
  WORD_LIST_COLUMNS,
  buildWordListText,
  displayRowsOnly,
  formatWordValue,
} from '@/utils/dashboardParity'

defineProps({ card: Object })

const api = useApi()
const dataStore = useDataStore()
const loading = ref(false)
const copyingAll = ref(false)
const copyingWords = ref(false)
const advancing = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const status = ref('')
const output = ref('')
const sort = ref('word')
const dir = ref('asc')
const visibleColumns = ref(WORD_LIST_COLUMNS.filter(col => col.default).map(col => col.key))

const form = reactive({
  date: '',
  q: '',
  state: '',
  tag: '',
  studyPreset: '',
  minStudyCount: '',
  maxStudyCount: '',
  due: '',
  overdueDays: '7',
  addRange: '',
  addFrom: '',
  addTo: '',
  todayFilter: '',
  pageSize: '1000',
})

const dateOptions = computed(() => {
  const rawDates = dataStore.rawRows.map(row => row.date).filter(Boolean).sort()
  const displayDates = displayRowsOnly(dataStore.displayRows).map(row => row.date).filter(Boolean).sort()
  const dates = rawDates.length ? rawDates : displayDates
  return Array.from(new Set(dates))
})

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / Math.max(1, Number(form.pageSize) || 1000))))

const sortDefs = computed(() => {
  const seen = new Set()
  return WORD_LIST_COLUMNS
    .filter(column => column.sort)
    .concat([{ label: '逾期优先', sort: 'overdue_first' }])
    .filter(item => {
      if (seen.has(item.sort)) return false
      seen.add(item.sort)
      return true
    })
})

watch(dateOptions, dates => {
  if (!dates.length) return
  const displayDates = displayRowsOnly(dataStore.displayRows).map(row => row.date).filter(Boolean).sort()
  const defaultDate = displayDates.length ? displayDates[displayDates.length - 1] : dates[dates.length - 1]
  if (!form.date || !dates.includes(form.date)) form.date = defaultDate
}, { immediate: true })

onMounted(() => {
  if (form.date) loadWordList(1)
})

function addDays(dateText, days) {
  const date = dateText ? new Date(`${dateText}T04:00:00`) : new Date()
  date.setDate(date.getDate() + Number(days || 0))
  return date.toISOString().slice(0, 10)
}

function applyStudyPreset() {
  if (form.studyPreset === '1') {
    form.minStudyCount = '1'
    form.maxStudyCount = '1'
  } else if (form.studyPreset === '2-5') {
    form.minStudyCount = '2'
    form.maxStudyCount = '5'
  } else if (form.studyPreset === '>=10') {
    form.minStudyCount = '10'
    form.maxStudyCount = ''
  } else if (form.studyPreset === '') {
    form.minStudyCount = ''
    form.maxStudyCount = ''
  }
}

function applyAddRangePreset() {
  const day = form.date || dateOptions.value[dateOptions.value.length - 1] || new Date().toISOString().slice(0, 10)
  if (form.addRange === 'recent7') {
    form.addFrom = addDays(day, -6)
    form.addTo = day
  } else if (form.addRange === 'recent30') {
    form.addFrom = addDays(day, -29)
    form.addTo = day
  } else if (form.addRange === '') {
    form.addFrom = ''
    form.addTo = ''
  }
}

function buildParams(targetPage = page.value, pageSize = null) {
  const params = new URLSearchParams()
  if (form.date) params.set('date', form.date)
  if (form.q) params.set('q', form.q)
  if (form.state) params.set('state', form.state)
  if (form.tag) params.set('tag', form.tag)
  if (form.minStudyCount) params.set('minStudyCount', form.minStudyCount)
  if (form.maxStudyCount) params.set('maxStudyCount', form.maxStudyCount)
  if (form.due) params.set('due', form.due)
  if (form.due === 'overdue_gt' && form.overdueDays) params.set('overdueDays', form.overdueDays)
  if (form.addFrom) params.set('addFrom', form.addFrom)
  if (form.addTo) params.set('addTo', form.addTo)
  if (form.todayFilter) params.set('todayFilter', form.todayFilter)
  params.set('sort', sort.value)
  params.set('dir', dir.value)
  params.set('page', String(targetPage))
  params.set('pageSize', pageSize || form.pageSize || '1000')
  return params
}

async function fetchWordList(targetPage = 1, pageSize = null) {
  const params = buildParams(targetPage, pageSize)
  const payload = await api.fetchJson(`/api/words?${params.toString()}`)
  if (!payload || payload.success === false) throw new Error(payload?.error || '单词列表接口返回失败')
  return payload
}

function setOutputText(payload, mode = 'page') {
  const text = buildWordListText(payload.items || [], payload, visibleColumns.value, sort.value, dir.value, mode)
  output.value = text
  dataStore.wordList = {
    items: payload.items || [],
    total: Number(payload.total || 0),
    page: Number(payload.page || page.value || 1),
    date: payload.date || form.date || '',
    lastText: text,
    visibleColumns: [...visibleColumns.value],
    sort: sort.value,
    dir: dir.value,
  }
}

async function loadWordList(targetPage = 1) {
  loading.value = true
  status.value = '正在查询…'
  try {
    const payload = await fetchWordList(targetPage)
    page.value = Number(payload.page || targetPage || 1)
    form.pageSize = String(payload.pageSize || form.pageSize || '1000')
    form.date = payload.date || form.date || ''
    total.value = Number(payload.total || 0)
    items.value = Array.isArray(payload.items) ? payload.items : []
    setOutputText(payload, 'page')
    const pages = Math.max(1, Math.ceil(total.value / Math.max(1, Number(payload.pageSize || form.pageSize) || 1000)))
    status.value = `共 ${total.value} 条，第 ${page.value}/${pages} 页`
  } catch (error) {
    status.value = `查询失败：${error.message || error}`
    output.value = error.stack || String(error)
  } finally {
    loading.value = false
  }
}

function setSort(sortKey) {
  if (sort.value === sortKey) {
    dir.value = dir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sort.value = sortKey
    dir.value = ['study_count', 'overdue_first'].includes(sortKey) ? 'desc' : 'asc'
  }
  loadWordList(1)
}

function prevPage() {
  if (page.value <= 1) return
  loadWordList(page.value - 1)
}

function nextPage() {
  if (page.value >= pageCount.value) return
  loadWordList(page.value + 1)
}

async function copyPage() {
  await copyToClipboard(output.value || '')
  ElMessage.success('已复制当前页单词列表')
}

async function copyAll() {
  copyingAll.value = true
  try {
    const payload = await fetchWordList(1, 'all')
    const text = buildWordListText(payload.items || [], payload, visibleColumns.value, sort.value, dir.value, 'all')
    await copyToClipboard(text)
    ElMessage.success('已复制全部筛选结果')
  } catch (error) {
    ElMessage.error(error.message || String(error))
  } finally {
    copyingAll.value = false
  }
}

async function copyWordsOnly() {
  copyingWords.value = true
  try {
    const payload = await fetchWordList(1, 'all')
    const words = (payload.items || []).map(item => formatWordValue(item, 'word')).filter(Boolean).join('\n')
    await copyToClipboard(words)
    ElMessage.success('已复制筛选结果中的单词')
  } catch (error) {
    ElMessage.error(error.message || String(error))
  } finally {
    copyingWords.value = false
  }
}

async function advanceCurrentWordList() {
  advancing.value = true
  status.value = '正在准备提前复习…'
  try {
    const payload = await fetchWordList(1, 'all')
    const list = Array.isArray(payload.items) ? payload.items : []
    const vocIds = list.map(item => item.vocId).filter(Boolean)
    const words = list.filter(item => !item.vocId).map(item => formatWordValue(item, 'word')).filter(Boolean)
    if (!vocIds.length && !words.length) throw new Error('当前筛选结果没有可提前复习的单词')
    await ElMessageBox.confirm(
      `将通过云端 API 提前复习 ${vocIds.length + words.length} 个单词。该操作会修改墨墨云端复习安排，确认继续？`,
      '确认提前复习当前筛选结果',
      { type: 'warning', confirmButtonText: '确认执行', cancelButtonText: '取消' }
    )
    const result = await api.advanceStudyWords({ vocIds, words })
    if (!result || result.success === false) throw new Error(result?.error || '提前复习接口返回失败')
    status.value = `已请求提前复习：${result.advancedCount ?? 0}/${result.requestedCount ?? (vocIds.length + words.length)} 个。`
    ElMessage.success(status.value)
  } catch (error) {
    if (error === 'cancel') {
      status.value = '已取消提前复习'
    } else {
      status.value = `提前复习失败：${error.message || error}`
      ElMessage.error(error.message || String(error))
    }
  } finally {
    advancing.value = false
  }
}
</script>

<style scoped>
.word-list-layout {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.word-list-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
}
.word-list-controls label,
.word-list-columns label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #334155;
}
.word-list-controls input,
.word-list-controls select {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 7px;
  font-size: 13px;
  max-width: 160px;
}
.word-list-columns {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding: 8px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.word-list-sortbar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.word-sort-btn {
  border: 1px solid var(--border);
  border-radius: 999px;
  background: #fff;
  padding: 4px 9px;
  font-size: 12px;
  cursor: pointer;
}
.word-sort-btn.active {
  background: #e0f2fe;
  border-color: #38bdf8;
  color: #0369a1;
}
.word-list-actions {
  flex-wrap: wrap;
}
.word-list-output {
  width: 100%;
  min-height: 260px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.55;
  resize: vertical;
  background: #fff;
}
</style>
