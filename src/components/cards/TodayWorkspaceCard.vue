<template>
  <div class="today-workspace-card">
    <div class="toolbar compact-toolbar">
      <label class="date-field">日期 <input v-model="date" type="date"></label>
      <el-button size="small" :loading="loading" @click="reloadWorkspace">重新加载</el-button>
      <el-button size="small" :disabled="!workspace.allProgressText" @click="copyAllProgress">复制所有时间点进度</el-button>
    </div>

    <div class="info-row">
      <span>当前日期：{{ workspace.date || date || '-' }}</span>
      <span>快照数量：{{ snapshots.length }}</span>
      <span v-if="workspace.error" class="error-text">{{ workspace.error }}</span>
    </div>

    <div class="table-wrapper stat-table-scroll">
      <table class="mini-table">
        <thead>
          <tr>
            <th>时间点</th>
            <th>已完成</th>
            <th>总数</th>
            <th>今日首次忘记数</th>
            <th>今日全部首次忘记数</th>
            <th>今日模糊数(已完成)</th>
            <th>今日模糊数(全部)</th>
            <th>今日认识数</th>
            <th>今日新学数</th>
            <th>今日复习数</th>
            <th>今日待新学数</th>
            <th>学习时长</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!snapshots.length">
            <td colspan="12" class="empty-cell">暂无当日快照；请先在页面点击联网同步写入数据库。</td>
          </tr>
          <tr v-for="snapshot in snapshots" :key="snapshot.name">
            <td><button class="link-button" @click="selectSnapshot(snapshot.name)">{{ snapshot.displayName || snapshot.name }}</button></td>
            <td>{{ summaryOf(snapshot).finished ?? '-' }}</td>
            <td>{{ summaryOf(snapshot).total ?? '-' }}</td>
            <td>{{ summaryOf(snapshot).firstForgetDone ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).firstForgetAll ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).doneVague ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).allVague ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).familiar ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).newWords ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).reviewWords ?? 0 }}</td>
            <td>{{ summaryOf(snapshot).pendingNew ?? 0 }}</td>
            <td>{{ formatDurationCN(summaryOf(snapshot).studyTimeMs || 0) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="workspace-section">
      <h3>查看单个时间点</h3>
      <div class="toolbar compact-toolbar">
        <el-select v-model="selectedSnapshotName" size="small" placeholder="选择时间点" class="snapshot-select" @change="refreshSinglePreview">
          <el-option v-for="snapshot in snapshots" :key="snapshot.name" :label="snapshot.displayName || snapshot.name" :value="snapshot.name" />
        </el-select>
        <el-button size="small" @click="selectSingleAll">全选</el-button>
        <el-button size="small" @click="clearSingleAll">清空</el-button>
        <el-button size="small" @click="refreshSinglePreview">预览</el-button>
        <el-button size="small" :disabled="!singleOutput" @click="copySingleView">复制当前勾选内容</el-button>
      </div>
      <div class="check-list">
        <label><input v-model="singleOptions.progress" type="checkbox" @change="refreshSinglePreview"> 今日学习进度</label>
        <label><input v-model="singleOptions.allItems" type="checkbox" @change="refreshSinglePreview"> 今日全部单词</label>
        <label><input v-model="singleOptions.doneItems" type="checkbox" @change="refreshSinglePreview"> 今日已完成单词</label>
        <label><input v-model="singleOptions.todoItems" type="checkbox" @change="refreshSinglePreview"> 今日未完成单词</label>
      </div>
      <el-input v-model="singleOutput" type="textarea" :rows="8" readonly placeholder="单时间点预览" />
    </div>

    <div class="workspace-section">
      <h3>自由对比两个时间点</h3>
      <div class="toolbar compact-toolbar">
        <el-select v-model="compareA" size="small" placeholder="A 快照" class="snapshot-select">
          <el-option :key="INITIAL_SNAPSHOT_NAME" label="初始状态（0数据）" :value="INITIAL_SNAPSHOT_NAME" />
          <el-option v-for="snapshot in snapshots" :key="'a-'+snapshot.name" :label="snapshot.displayName || snapshot.name" :value="snapshot.name" />
        </el-select>
        <span>→</span>
        <el-select v-model="compareB" size="small" placeholder="B 快照" class="snapshot-select">
          <el-option v-for="snapshot in snapshots" :key="'b-'+snapshot.name" :label="snapshot.displayName || snapshot.name" :value="snapshot.name" />
        </el-select>
        <el-button size="small" @click="compareSnapshots(true)">生成差异</el-button>
        <el-button size="small" :disabled="!compareOutput" @click="copyCompareResult">复制差异结果</el-button>
      </div>
      <div class="check-list">
        <label><input v-model="compareOptions.progress" type="checkbox"> 进度变化</label>
        <label><input v-model="compareOptions.added" type="checkbox"> 新增条目</label>
        <label><input v-model="compareOptions.removed" type="checkbox"> 消失条目</label>
        <label><input v-model="compareOptions.statusChanged" type="checkbox"> 完成状态</label>
        <label><input v-model="compareOptions.responseChanged" type="checkbox"> 首次反应</label>
        <label><input v-model="compareOptions.memoryChanged" type="checkbox"> 记忆持久度</label>
        <label><input v-model="compareOptions.studyCountChanged" type="checkbox"> 学习次数</label>
        <label><input v-model="compareOptions.studyTimeChanged" type="checkbox"> 复习时间</label>
      </div>
      <el-input v-model="compareOutput" type="textarea" :rows="10" readonly placeholder="时间点对比结果" />
    </div>

    <p v-if="status" class="status-line">{{ status }}</p>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useApi } from '@/composables/useApi'

const COPY_STATE_KEY = 'momoTodayWorkspaceCopyState'
const INITIAL_SNAPSHOT_NAME = '__INITIAL_ZERO__'
const REAL_RESPONSES = new Set(['FAMILIAR', 'VAGUE', 'FORGET', '认识', '模糊', '忘记'])

function loadSavedCopyState() {
  try {
    return JSON.parse(localStorage.getItem(COPY_STATE_KEY) || '{}') || {}
  } catch {
    return {}
  }
}

defineProps({ card: Object })

const dataStore = useDataStore()
const api = useApi()
const savedCopyState = loadSavedCopyState()
const loading = ref(false)
const status = ref('')
const date = ref(savedCopyState.date || localDateString())
const selectedSnapshotName = ref(savedCopyState.selectedSnapshotName || '')
const compareA = ref(savedCopyState.compareA || '')
const compareB = ref(savedCopyState.compareB || '')
const singleOutput = ref('')
const compareOutput = ref('')

const singleOptions = reactive({ progress: true, allItems: false, doneItems: false, todoItems: false, ...(savedCopyState.singleOptions || {}) })
const compareOptions = reactive({ progress: true, added: true, removed: true, statusChanged: true, responseChanged: true, memoryChanged: true, studyCountChanged: true, studyTimeChanged: false, ...(savedCopyState.compareOptions || {}) })

const workspace = computed(() => dataStore.todayWorkspace || { date: '', snapshots: [], allProgressText: '', error: '' })
const snapshots = computed(() => {
  const items = Array.isArray(workspace.value.snapshots) ? workspace.value.snapshots : []
  return [...items].sort((a, b) => String(a?.name || a?.displayName || '').localeCompare(String(b?.name || b?.displayName || '')))
})

function localDateString() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function summaryOf(snapshot) {
  return snapshot?.summary || {}
}

function formatDurationCN(ms) {
  const sec = Number(ms || 0) / 1000
  const sign = sec < 0 ? '-' : ''
  const abs = Math.abs(sec)
  const h = Math.floor(abs / 3600)
  const m = Math.floor((abs % 3600) / 60)
  const s = abs % 60
  if (h > 0) return `${sign}${h}时${m}分${s.toFixed(1)}秒`
  if (m > 0) return `${sign}${m}分${s.toFixed(1)}秒`
  return `${sign}${s.toFixed(1)}秒`
}

function rawResponse(item) {
  return String(item?.first_response ?? item?.firstResponse ?? '').trim()
}

function respCn(value) {
  const map = {
    FAMILIAR: '认识',
    VAGUE: '模糊',
    FORGET: '忘记',
    STUDY_RESPONSE_UNSPECIFIED: '未作答',
    '': '未作答',
  }
  return map[value] || value || ''
}

function normalizedResponse(item) {
  return respCn(rawResponse(item)) || '未作答'
}

function hasRealResponse(item) {
  const value = rawResponse(item)
  return REAL_RESPONSES.has(value) || REAL_RESPONSES.has(respCn(value))
}

function itemFinished(item) {
  if (!item) return false
  return Boolean(item.is_finished ?? item.isFinished)
}

function itemIsNew(item) {
  if (!item) return false
  return Boolean(item.is_new ?? item.isNew)
}

function escapeCsv(value) {
  const text = String(value ?? '')
  if (/[",\n\r]/.test(text)) return `"${text.replace(/"/g, '""')}"`
  return text
}

function rowsToCSV(headers, rows) {
  const lines = [headers.map(escapeCsv).join(',')]
  for (const row of rows) lines.push(headers.map(header => escapeCsv(row[header])).join(','))
  return lines.join('\n')
}

function saveCopyState() {
  try {
    localStorage.setItem(COPY_STATE_KEY, JSON.stringify({
      date: date.value,
      selectedSnapshotName: selectedSnapshotName.value,
      compareA: compareA.value,
      compareB: compareB.value,
      singleOptions: { ...singleOptions },
      compareOptions: { ...compareOptions },
      singleOutput: singleOutput.value,
      compareOutput: compareOutput.value,
    }))
  } catch {
    // ignore storage quota/private mode errors; copy still works in current page.
  }
}

function createInitialSnapshot() {
  return {
    name: INITIAL_SNAPSHOT_NAME,
    displayName: '初始状态（0数据）',
    progress: { finished: 0, total: 0 },
    summary: {
      finished: 0,
      total: 0,
      studyTimeMs: 0,
      firstForgetDone: 0,
      firstForgetAll: 0,
      doneVague: 0,
      allVague: 0,
      familiar: 0,
      newWords: 0,
      reviewWords: 0,
      pendingNew: 0,
    },
    allItems: [],
    doneItems: [],
    todoItems: [],
  }
}

function getSnapshotByName(name) {
  if (name === INITIAL_SNAPSHOT_NAME) return createInitialSnapshot()
  return snapshots.value.find(snapshot => snapshot.name === name) || null
}

function todayItemKey(item) {
  return String(item?.voc_id || item?.vocId || '').trim()
}

function wordText(item) {
  return item?.voc_spelling || item?.word || item?.spelling || item?.vocId || item?.voc_id || ''
}

function numberOrNull(value) {
  if (value === undefined || value === null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function memoryDurabilityDays(item) {
  return numberOrNull(
    item?.memory_durability_days
      ?? item?.review_span_days
      ?? item?.memoryReviewSpanDays
      ?? item?.memory_review_span_days
      ?? item?.days
  )
}

function previousMemoryDurabilityDays(item) {
  return numberOrNull(
    item?.previous_memory_durability_days
      ?? item?.previous_review_span_days
      ?? item?.previousMemoryReviewSpanDays
      ?? item?.previous_memory_review_span_days
      ?? item?.previousDays
  )
}

function studyCountValue(item) {
  return numberOrNull(item?.study_count ?? item?.studyCount)
}

function previousStudyCountValue(item) {
  return numberOrNull(item?.previous_study_count ?? item?.previousStudyCount)
}

function lastReviewDate(item) {
  return item?.last_study_date || item?.lastStudyDate || ''
}

function previousLastReviewDate(item) {
  return item?.previous_last_study_date || item?.previousLastStudyDate || ''
}

function nextReviewDate(item) {
  return item?.next_study_date || item?.nextStudyDate || ''
}

function previousNextReviewDate(item) {
  return item?.previous_next_study_date || item?.previousNextStudyDate || ''
}

function createInitialCompareItem(source) {
  const item = source || {}
  const previousMemory = previousMemoryDurabilityDays(item)
  const previousCount = previousStudyCountValue(item)
  const previousLast = previousLastReviewDate(item)
  const previousNext = previousNextReviewDate(item)
  return {
    ...item,
    // 初始时间点是当日作答前的虚拟基线：单词只作为行标识，
    // 当日状态使用默认值；记忆/次数/复习时间使用后端按数据库历史注入的上一次值。
    first_response: 'STUDY_RESPONSE_UNSPECIFIED',
    firstResponse: 'STUDY_RESPONSE_UNSPECIFIED',
    is_finished: false,
    isFinished: false,
    study_count: previousCount,
    studyCount: previousCount,
    last_study_date: previousLast,
    lastStudyDate: previousLast,
    next_study_date: previousNext,
    nextStudyDate: previousNext,
    memory_durability_days: previousMemory,
    review_span_days: previousMemory,
    days: previousMemory,
  }
}

function formatDays(value) {
  const n = numberOrNull(value)
  if (n === null) return ''
  const rounded = Math.round(n * 10) / 10
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1)
}

function formatSignedDays(value) {
  const n = numberOrNull(value)
  if (n === null) return ''
  const text = formatDays(Math.abs(n))
  if (!text) return ''
  return n > 0 ? `+${text}` : n < 0 ? `-${text}` : '0'
}

function simplifyTodayItem(item) {
  return {
    单词: wordText(item),
    学习顺序: item?.order ?? '',
    首次反应: respCn(rawResponse(item)),
    是否新词: itemIsNew(item) ? '是' : '否',
    是否完成: itemFinished(item) ? '是' : '否',
    学习次数: studyCountValue(item) ?? '',
    '记忆持久度(天)': formatDays(memoryDurabilityDays(item)),
    最近复习日期: lastReviewDate(item),
    下次复习日期: nextReviewDate(item),
  }
}

function simplifyCompareItem(item) {
  return {
    单词: wordText(item),
    首次反应: respCn(rawResponse(item)),
    是否新词: itemIsNew(item) ? '是' : '否',
    是否完成: itemFinished(item) ? '是' : '否',
    学习次数: studyCountValue(item) ?? '',
    '记忆持久度(天)': formatDays(memoryDurabilityDays(item)),
    最近复习日期: lastReviewDate(item),
    下次复习日期: nextReviewDate(item),
  }
}

function changeText(beforeValue, afterValue) {
  const beforeText = beforeValue === undefined || beforeValue === null || beforeValue === '' ? '空' : String(beforeValue)
  const afterText = afterValue === undefined || afterValue === null || afterValue === '' ? '空' : String(afterValue)
  return beforeText === afterText ? '' : `${beforeText} -> ${afterText}`
}

function countChangeText(beforeValue, afterValue) {
  const beforeNumber = numberOrNull(beforeValue)
  const afterNumber = numberOrNull(afterValue)
  if (beforeNumber === null && afterNumber === null) return ''
  const beforeText = beforeNumber === null ? '无数据' : formatDays(beforeNumber)
  const afterText = afterNumber === null ? '无数据' : formatDays(afterNumber)
  if (beforeText === afterText) return ''
  if (beforeNumber !== null && afterNumber !== null) {
    return `${beforeText} -> ${afterText} (${formatSignedDays(afterNumber - beforeNumber)})`
  }
  return `${beforeText} -> ${afterText}`
}

function memoryChangeText(beforeValue, afterValue) {
  const beforeNumber = numberOrNull(beforeValue)
  const afterNumber = numberOrNull(afterValue)
  if (beforeNumber === null && afterNumber === null) return ''
  const beforeText = beforeNumber === null ? '无数据' : formatDays(beforeNumber)
  const afterText = afterNumber === null ? '无数据' : formatDays(afterNumber)
  if (beforeText === afterText) return ''
  if (beforeNumber !== null && afterNumber !== null) {
    return `${beforeText} -> ${afterText} (${formatSignedDays(afterNumber - beforeNumber)})`
  }
  return `${beforeText} -> ${afterText}`
}

function actualReviewHappenedBetween(beforeItem, afterItem) {
  if (!afterItem) return false
  const beforeFinished = itemFinished(beforeItem)
  const afterFinished = itemFinished(afterItem)
  if (!beforeFinished && afterFinished) return true

  const beforeResponse = normalizedResponse(beforeItem)
  const afterResponse = normalizedResponse(afterItem)
  if (hasRealResponse(afterItem) && beforeResponse !== afterResponse) return true

  // last_study_date 只能作为辅助证据。没有完成状态或首次反应变化时，
  // 不用 study_count / 记忆持久度单独判定复习，避免把历史基线差异误报为“今日复习”。
  const beforeLast = lastReviewDate(beforeItem)
  const afterLast = lastReviewDate(afterItem)
  const workspaceDate = workspace.value?.date || date.value || ''
  if (afterFinished && afterLast && afterLast !== beforeLast && (!workspaceDate || afterLast.slice(0, 10) === workspaceDate)) return true

  return false
}

function longTermChanged(beforeItem, afterItem) {
  return Boolean(
    memoryChangeText(memoryDurabilityDays(beforeItem), memoryDurabilityDays(afterItem))
      || countChangeText(studyCountValue(beforeItem), studyCountValue(afterItem))
      || changeText(lastReviewDate(beforeItem), lastReviewDate(afterItem))
      || changeText(nextReviewDate(beforeItem), nextReviewDate(afterItem))
  )
}

function progressCsvFromSnapshot(name) {
  const snapshot = getSnapshotByName(name)
  if (!snapshot) return ''
  const m = snapshot.summary || {}
  const row = {
    已完成: m.finished ?? '',
    总数: m.total ?? '',
    '学习时长(秒)': Number(m.studyTimeMs || 0) / 1000,
    今日首次忘记数: m.firstForgetDone ?? 0,
    今日全部首次忘记数: m.firstForgetAll ?? 0,
    '今日模糊数(已完成)': m.doneVague ?? 0,
    '今日模糊数(全部)': m.allVague ?? 0,
    今日认识数: m.familiar ?? 0,
    今日新学数: m.newWords ?? 0,
    今日复习数: m.reviewWords ?? 0,
    今日待新学数: m.pendingNew ?? 0,
  }
  const headers = Object.keys(row)
  return rowsToCSV(headers, [row])
}

function itemCsvFromSnapshot(name, kind) {
  const snapshot = getSnapshotByName(name)
  if (!snapshot) return ''
  const items = kind === 'done' ? snapshot.doneItems : kind === 'todo' ? snapshot.todoItems : snapshot.allItems
  const headers = ['单词', '学习顺序', '首次反应', '是否新词', '是否完成', '学习次数', '记忆持久度(天)', '最近复习日期', '下次复习日期']
  const rows = (items || []).map(simplifyTodayItem)
  return rowsToCSV(headers, rows)
}

function getSingleSelectedText() {
  const name = selectedSnapshotName.value
  if (!name || !getSnapshotByName(name)) return ''
  const parts = []
  if (singleOptions.progress) parts.push('【今日学习进度】\n' + progressCsvFromSnapshot(name))
  if (singleOptions.allItems) parts.push('【今日全部单词】\n' + itemCsvFromSnapshot(name, 'all'))
  if (singleOptions.doneItems) parts.push('【今日已完成单词】\n' + itemCsvFromSnapshot(name, 'done'))
  if (singleOptions.todoItems) parts.push('【今日未完成单词】\n' + itemCsvFromSnapshot(name, 'todo'))
  return parts.join('\n\n')
}

function refreshSinglePreview() {
  singleOutput.value = getSingleSelectedText()
  saveCopyState()
}

function selectSingleAll() {
  singleOptions.progress = true
  singleOptions.allItems = true
  singleOptions.doneItems = true
  singleOptions.todoItems = true
  refreshSinglePreview()
}

function clearSingleAll() {
  singleOptions.progress = false
  singleOptions.allItems = false
  singleOptions.doneItems = false
  singleOptions.todoItems = false
  singleOutput.value = ''
  saveCopyState()
}

function selectSnapshot(name) {
  selectedSnapshotName.value = name
  refreshSinglePreview()
}

function compareSnapshots(showAlert = true) {
  const a = getSnapshotByName(compareA.value)
  const b = getSnapshotByName(compareB.value)
  if (!a || !b) {
    if (showAlert) status.value = '请选择两个时间点。'
    return
  }

  const aProg = a.progress || {}
  const bProg = b.progress || {}
  const aItems = Array.isArray(a.allItems) ? a.allItems : []
  const bItems = Array.isArray(b.allItems) ? b.allItems : []
  const usingInitialA = compareA.value === INITIAL_SNAPSHOT_NAME
  const bMap = new Map(bItems.map(item => [todayItemKey(item), item]).filter(([key]) => key))
  const aMap = usingInitialA
    ? new Map(bItems.map(item => [todayItemKey(item), createInitialCompareItem(item)]).filter(([key]) => key))
    : new Map(aItems.map(item => [todayItemKey(item), item]).filter(([key]) => key))
  const aKeys = new Set(aMap.keys())
  const bKeys = new Set(bMap.keys())
  const allKeys = new Set([...aKeys, ...bKeys])
  const added = []
  const removed = []
  const changedRows = []
  let ignoredLongTermChangeCount = 0

  for (const key of bKeys) if (!aKeys.has(key)) added.push(simplifyCompareItem(bMap.get(key)))
  for (const key of aKeys) if (!bKeys.has(key)) removed.push(simplifyCompareItem(aMap.get(key)))

  for (const key of allKeys) {
    const x = aMap.get(key) || null
    const y = bMap.get(key) || null
    const reviewedHere = actualReviewHappenedBetween(x, y)
    const row = {
      单词: wordText(y) || wordText(x),
    }

    if (compareOptions.statusChanged) {
      const beforeStatus = x ? (itemFinished(x) ? '是' : '否') : '无数据'
      const afterStatus = y ? (itemFinished(y) ? '是' : '否') : '无数据'
      row.完成状态 = beforeStatus === afterStatus ? '' : `${beforeStatus} -> ${afterStatus}`
    }

    if (compareOptions.responseChanged) {
      row.首次反应 = changeText(normalizedResponse(x), normalizedResponse(y))
    }

    const previousDurability = usingInitialA ? previousMemoryDurabilityDays(y) : memoryDurabilityDays(x)
    const currentDurability = memoryDurabilityDays(y)
    const previousCount = usingInitialA ? previousStudyCountValue(y) : studyCountValue(x)
    const currentCount = studyCountValue(y)
    const previousLastDate = usingInitialA ? previousLastReviewDate(y) : lastReviewDate(x)
    const currentLastDate = lastReviewDate(y)
    const previousNextDate = usingInitialA ? previousNextReviewDate(y) : nextReviewDate(x)
    const currentNextDate = nextReviewDate(y)

    const hasSuppressedLongTermChange = !reviewedHere && longTermChanged(
      {
        memory_durability_days: previousDurability,
        study_count: previousCount,
        last_study_date: previousLastDate,
        next_study_date: previousNextDate,
      },
      {
        memory_durability_days: currentDurability,
        study_count: currentCount,
        last_study_date: currentLastDate,
        next_study_date: currentNextDate,
      }
    )
    if (hasSuppressedLongTermChange) ignoredLongTermChangeCount += 1

    if (compareOptions.memoryChanged && reviewedHere) {
      row['记忆持久度(天)'] = memoryChangeText(previousDurability, currentDurability)
    }

    if (compareOptions.studyCountChanged && reviewedHere) {
      row.学习次数 = countChangeText(previousCount, currentCount)
    }

    if (compareOptions.studyTimeChanged && reviewedHere) {
      row.最近复习日期 = changeText(previousLastDate, currentLastDate)
      row.下次复习日期 = changeText(previousNextDate, currentNextDate)
    }

    const hasVisibleChange = Object.keys(row).some(keyName => keyName !== '单词' && row[keyName])
    if (hasVisibleChange) changedRows.push(row)
  }

  const lines = [`对比: ${a.displayName || a.name} -> ${b.displayName || b.name}`]
  const am = a.summary || {}
  const bm = b.summary || {}

  if (compareOptions.progress) {
    lines.push('', '【进度变化】')
    lines.push(`已完成变化: ${(bProg.finished || bm.finished || 0) - (aProg.finished || am.finished || 0)}`)
    lines.push(`总数变化: ${(bProg.total || bm.total || 0) - (aProg.total || am.total || 0)}`)
    lines.push(`今日首次忘记数变化: ${(bm.firstForgetDone || 0) - (am.firstForgetDone || 0)}`)
    lines.push(`今日全部首次忘记数变化: ${(bm.firstForgetAll || 0) - (am.firstForgetAll || 0)}`)
    lines.push(`今日模糊数(已完成)变化: ${(bm.doneVague || 0) - (am.doneVague || 0)}`)
    lines.push(`今日模糊数(全部)变化: ${(bm.allVague || 0) - (am.allVague || 0)}`)
    lines.push(`今日认识数变化: ${(bm.familiar || 0) - (am.familiar || 0)}`)
    lines.push(`今日新学数变化: ${(bm.newWords || 0) - (am.newWords || 0)}`)
    lines.push(`今日复习数变化: ${(bm.reviewWords || 0) - (am.reviewWords || 0)}`)
    lines.push(`今日待新学数变化: ${(bm.pendingNew || 0) - (am.pendingNew || 0)}`)
    lines.push(`学习时长变化: ${formatDurationCN((bm.studyTimeMs || 0) - (am.studyTimeMs || 0))}`)
  }

  appendWordListBlock(lines, '新增条目', added, compareOptions.added)
  appendWordListBlock(lines, '消失条目', removed, compareOptions.removed)
  const showChangeTable = compareOptions.statusChanged || compareOptions.responseChanged || compareOptions.memoryChanged || compareOptions.studyCountChanged || compareOptions.studyTimeChanged
  appendCsvBlock(lines, '单词变化', changedRows, showChangeTable, changeTableHeaders())
  if (ignoredLongTermChangeCount > 0) {
    lines.push('', `已忽略 ${ignoredLongTermChangeCount} 条“无本次复习证据但长期记忆字段不同”的变化，避免把历史基线差异误报为今日复习。`)
  }

  compareOutput.value = lines.join('\n')
  saveCopyState()
}

function changeTableHeaders() {
  const headers = ['单词']
  if (compareOptions.statusChanged) headers.push('完成状态')
  if (compareOptions.responseChanged) headers.push('首次反应')
  if (compareOptions.memoryChanged) headers.push('记忆持久度(天)')
  if (compareOptions.studyCountChanged) headers.push('学习次数')
  if (compareOptions.studyTimeChanged) headers.push('最近复习日期', '下次复习日期')
  return headers
}

function appendWordListBlock(lines, title, rows, enabled) {
  if (!enabled) return
  lines.push('', `【${title}】`, `数量: ${rows.length}`)
  if (rows.length) {
    const words = rows
      .map(row => row?.单词 || row?.voc_spelling || row?.word || '')
      .filter(Boolean)
    if (words.length) lines.push(words.join('，'))
  }
}

function appendCsvBlock(lines, title, rows, enabled, fixedHeaders = null) {
  if (!enabled) return
  lines.push('', `【${title}】`, `数量: ${rows.length}`)
  if (rows.length) {
    const headers = fixedHeaders || Object.keys(rows[0])
    lines.push(rowsToCSV(headers, rows))
  }
}

async function copyText(text, okMsg) {
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
  status.value = okMsg
}

async function copySingleView() {
  refreshSinglePreview()
  await copyText(singleOutput.value, '已复制当前时间点内容。')
}

async function copyCompareResult() {
  await copyText(compareOutput.value, '已复制差异结果。')
}

async function copyAllProgress() {
  await copyText(workspace.value.allProgressText || '', '已复制所有时间点进度。')
}

async function reloadWorkspace() {
  loading.value = true
  status.value = '正在重新加载当日时间点数据…'
  try {
    const data = await api.fetchTodayWorkspace(date.value)
    if (!data?.success) throw new Error(data?.error || '加载当日时间点失败')
    dataStore.todayWorkspace = data.todayWorkspace || { date: date.value, snapshots: [], allProgressText: '', error: '该日期暂无快照数据' }
    status.value = `加载完成：${snapshots.value.length} 个快照。`
    saveCopyState()
  } catch (error) {
    status.value = `加载失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

watch(snapshots, value => {
  const latest = value[value.length - 1]?.name || ''
  const previous = value[value.length - 2]?.name || ''
  const first = value[0]?.name || ''
  if (!selectedSnapshotName.value || !value.some(item => item.name === selectedSnapshotName.value)) selectedSnapshotName.value = latest
  if (!compareA.value || (compareA.value !== INITIAL_SNAPSHOT_NAME && !value.some(item => item.name === compareA.value))) compareA.value = previous || first || INITIAL_SNAPSHOT_NAME
  if (!compareB.value || !value.some(item => item.name === compareB.value)) compareB.value = latest
  if (value.length >= 2 && compareA.value === compareB.value) compareA.value = previous || first || INITIAL_SNAPSHOT_NAME
  refreshSinglePreview()
}, { immediate: true })

watch([date, selectedSnapshotName, compareA, compareB], saveCopyState)
watch(singleOptions, () => { refreshSinglePreview() }, { deep: true })
watch(compareOptions, saveCopyState, { deep: true })

onMounted(() => {
  if (workspace.value.date) date.value = workspace.value.date
  if (!snapshots.value.length) reloadWorkspace()
})
</script>

<style scoped>
.compact-toolbar { gap: 8px; }
.date-field { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.date-field input { padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.info-row { display: flex; flex-wrap: wrap; gap: 8px 16px; margin: 8px 0; font-size: 13px; color: var(--text-muted); }
.error-text { color: #dc2626; }
.table-wrapper { overflow: auto; margin-top: 8px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; min-width: 980px; }
.mini-table th, .mini-table td { border: 1px solid var(--border); padding: 5px 7px; text-align: left; white-space: nowrap; }
.mini-table th { background: #f8fafc; font-weight: 600; }
.empty-cell { text-align: center; color: var(--text-muted); }
.link-button { border: 0; background: transparent; color: #2563eb; cursor: pointer; padding: 0; font-size: 12px; }
.workspace-section { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
h3 { margin: 0 0 8px; font-size: 15px; }
.snapshot-select { min-width: 220px; }
.check-list { display: flex; flex-wrap: wrap; gap: 8px 14px; margin: 8px 0; font-size: 13px; }
.check-list label { display: inline-flex; align-items: center; gap: 4px; }
.status-line { margin: 8px 0 0; color: #2563eb; font-size: 13px; }
</style>
