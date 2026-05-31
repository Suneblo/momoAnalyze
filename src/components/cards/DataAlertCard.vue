<template>
  <div class="data-alert-card">
    <p v-if="!alerts.length" class="info-text">暂无数据减少提醒。</p>

    <div v-for="(alert, index) in alerts" :key="alertKey(alert, index)" class="alert-item">
      <div class="alert-main">{{ alert.message || '检测到数据减少。是否是你主动删除/调整了这些单词？' }}</div>
      <div class="alert-sub">{{ formatDataAlertSub(alert) }}</div>

      <details class="alert-details" open>
        <summary>减少单词列表（{{ alertItems(alert).length }}{{ Number(alert.itemsOmitted || 0) > 0 ? `，另有 ${alert.itemsOmitted} 个未展开` : '' }}）</summary>
        <ol v-if="alertItems(alert).length" class="alert-words">
          <li v-for="(item, itemIndex) in alertItems(alert)" :key="itemIndex">
            <span class="alert-word">{{ itemIndex + 1 }}. {{ wordOf(item) }}</span>
            <span v-if="metaOf(item).length" class="alert-word-meta">{{ metaOf(item).join('；') }}</span>
          </li>
        </ol>
        <div v-else class="alert-empty">没有拿到具体单词列表；请用 SQL 检查相邻快照差异。</div>
      </details>

      <div class="inline-actions">
        <el-button size="small" @click="dismiss(index)">是，知道了</el-button>
        <el-button size="small" type="warning" :loading="loading" @click="syncAgain">否，重新获取一遍</el-button>
      </div>
    </div>

    <p v-if="status" class="status-line">{{ status }}</p>
  </div>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useApi } from '@/composables/useApi'

defineProps({ card: Object })

const dataStore = useDataStore()
const settings = useSettingsStore()
const api = useApi()
const log = inject('log', null)
const loading = ref(false)
const status = ref('')
const alerts = computed(() => Array.isArray(dataStore.alerts) ? dataStore.alerts : [])

function alertKey(alert, index) {
  return `${alert?.type || 'alert'}-${alert?.snapshotTime || ''}-${index}`
}

function alertItems(alert) {
  return Array.isArray(alert?.items) ? alert.items : []
}

function formatDataAlertSub(alert) {
  const parts = [`快照：${alert?.snapshotTime || '-'}`]
  if (alert?.previousSnapshotTime) parts.push(`上次快照：${alert.previousSnapshotTime}`)
  if (alert?.previousCount !== undefined || alert?.currentCount !== undefined) {
    parts.push(`上次数量：${alert.previousCount ?? '-'}`)
    parts.push(`本次数量：${alert.currentCount ?? '-'}`)
  }
  parts.push(`减少：${alert?.count ?? '-'}`)
  return parts.join('；')
}

function wordOf(item) {
  return item?.word || '未知单词'
}

function metaOf(item) {
  const bits = []
  const vocId = item?.vocId
  const currentState = item?.currentState
  const lastResponse = item?.lastResponse
  const nextStudyDate = item?.nextStudyDate
  const studyCount = item?.studyCount
  const firstResponse = item?.firstResponse
  const isNew = item?.isNew
  const isFinished = item?.isFinished

  if (vocId) bits.push(`id=${vocId}`)
  if (currentState) bits.push(`状态=${currentState}`)
  if (lastResponse) bits.push(`反应=${lastResponse}`)
  if (nextStudyDate) bits.push(`下次=${nextStudyDate}`)
  if (studyCount !== undefined && studyCount !== null && studyCount !== '') bits.push(`次数=${studyCount}`)
  if (item?.tags) bits.push(`标签=${item.tags}`)
  if (item?.order !== undefined && item.order !== null && item.order !== '') bits.push(`顺序=${item.order}`)
  if (firstResponse) bits.push(`首次=${firstResponse}`)
  if (isNew !== undefined && isNew !== null) bits.push(`新词=${isNew ? '是' : '否'}`)
  if (isFinished !== undefined && isFinished !== null) bits.push(`完成=${isFinished ? '是' : '否'}`)
  return bits
}

function dismiss(index) {
  dataStore.alerts.splice(index, 1)
  status.value = '已隐藏这条提醒。'
}

async function syncAgain() {
  loading.value = true
  status.value = '正在重新获取数据…'
  log?.('数据减少提醒：开始重新联网获取数据...')
  try {
    const result = await api.syncFromApi()
    if (result?.success === false) throw new Error(result.error || '联网同步失败')
    const data = await api.fetchDashboardPage(0, 60, 'asc', { memoryThresholds: settings.memoryThresholds })
    if (data?.days) dataStore.setDashboardData(data)
    const alertData = await api.fetchAlerts()
    if (alertData?.success) dataStore.setAlerts(alertData.alerts || [])
    status.value = '重新获取完成，已刷新页面数据。'
    log?.('数据减少提醒：重新获取完成。')
  } catch (error) {
    status.value = `重新获取失败：${error.message || error}`
    log?.(status.value)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.alert-item { border: 1px solid #fecaca; background: #fff7ed; border-radius: 10px; padding: 10px; margin-bottom: 10px; }
.alert-main { color: #b45309; font-weight: 600; margin-bottom: 4px; }
.alert-sub { color: #92400e; font-size: 13px; margin-bottom: 8px; }
.alert-details { font-size: 13px; }
.alert-words { margin: 8px 0 0; padding-left: 20px; max-height: 240px; overflow: auto; }
.alert-words li { margin-bottom: 4px; }
.alert-word { font-weight: 600; margin-right: 6px; }
.alert-word-meta { color: var(--text-muted); }
.alert-empty { color: var(--text-muted); margin-top: 6px; }
.status-line { margin: 8px 0 0; color: #2563eb; font-size: 13px; }
</style>
