<template>
  <div class="custom-words-card">
    <div class="toolbar compact-toolbar">
      <span>日期：{{ payload.date || workspaceDate || '-' }}</span>
      <span>未补全：{{ payload.unresolvedCount || 0 }}</span>
      <span v-if="payload.snapshot">快照：{{ displayTime(payload.snapshot) }}</span>
      <el-button size="small" :loading="loading" @click="loadCustomWords">刷新</el-button>
    </div>

    <p v-if="status" class="status-line">{{ status }}</p>

    <div v-if="!loading && !items.length" class="empty-line">
      暂无需要补全的自定义词。
    </div>

    <div v-else class="table-wrap">
      <table class="custom-table">
        <thead>
          <tr>
            <th>顺序</th>
            <th>前 5 个词</th>
            <th>占位</th>
            <th>后 5 个词</th>
            <th>状态</th>
            <th>拼写</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.vocId">
            <td>#{{ item.order }}</td>
            <td>
              <span v-for="word in item.before" :key="`b-${item.vocId}-${word.order}`" class="word-chip">
                #{{ word.order }} {{ word.word || '-' }}
              </span>
            </td>
            <td>
              <div class="placeholder-word">{{ item.display || `[custom:${item.vocIdTail}]` }}</div>
              <div class="muted">尾号 {{ item.vocIdTail }}</div>
            </td>
            <td>
              <span v-for="word in item.after" :key="`a-${item.vocId}-${word.order}`" class="word-chip">
                #{{ word.order }} {{ word.word || '-' }}
              </span>
            </td>
            <td>
              <div>{{ item.dueState || '-' }}</div>
              <div class="muted">学习 {{ item.studyCount ?? '-' }} 次，{{ item.lastResponse || '-' }}</div>
            </td>
            <td>
              <input
                v-model="drafts[item.vocId]"
                class="spelling-input"
                placeholder="填写自定义词"
                @keydown.enter="saveItem(item)"
              >
            </td>
            <td>
              <el-button size="small" type="primary" :loading="saving[item.vocId]" @click="saveItem(item)">保存</el-button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useApi } from '@/composables/useApi'
import { useDataStore } from '@/stores/dataStore'

const api = useApi()
const dataStore = useDataStore()

const payload = ref({ date: '', snapshot: '', items: [], unresolvedCount: 0, resolvedCount: 0 })
const loading = ref(false)
const status = ref('')
const drafts = reactive({})
const saving = reactive({})

const workspaceDate = computed(() => dataStore.todayWorkspace?.date || '')
const items = computed(() => Array.isArray(payload.value.items) ? payload.value.items : [])

function displayTime(value) {
  return String(value || '').replace('T', ' ').split('+')[0]
}

async function loadCustomWords() {
  loading.value = true
  status.value = ''
  try {
    const data = await api.fetchTodayCustomWords(workspaceDate.value || undefined)
    if (!data?.success) {
      status.value = data?.error || '读取自定义词失败'
      return
    }
    payload.value = data.customWords || { date: workspaceDate.value || '', items: [] }
    for (const item of items.value) {
      if (!Object.prototype.hasOwnProperty.call(drafts, item.vocId)) drafts[item.vocId] = ''
    }
  } catch (e) {
    status.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}

async function saveItem(item) {
  const spelling = String(drafts[item.vocId] || '').trim()
  if (!spelling) {
    ElMessage.warning('请先填写拼写')
    return
  }
  saving[item.vocId] = true
  try {
    const data = await api.saveCustomWord({ vocId: item.vocId, spelling })
    if (!data?.success) {
      ElMessage.error(data?.error || '保存失败')
      return
    }
    ElMessage.success(`已保存：${spelling}`)
    delete drafts[item.vocId]
    await loadCustomWords()
  } catch (e) {
    ElMessage.error(e?.message || String(e))
  } finally {
    saving[item.vocId] = false
  }
}

watch(workspaceDate, () => {
  loadCustomWords()
})

onMounted(loadCustomWords)
</script>

<style scoped>
.custom-words-card { font-size: 13px; }
.compact-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.status-line { color: #b45309; margin: 8px 0 0; }
.empty-line { color: var(--text-muted); margin-top: 10px; }
.table-wrap { overflow-x: auto; margin-top: 10px; }
.custom-table { width: 100%; border-collapse: collapse; min-width: 980px; }
.custom-table th,
.custom-table td { border: 1px solid var(--border); padding: 6px 8px; text-align: left; vertical-align: top; }
.custom-table th { background: #f8fafc; color: #334155; font-weight: 600; }
.custom-table th:nth-child(5),
.custom-table td:nth-child(5) { min-width: 92px; }
.word-chip { display: inline-block; margin: 0 4px 4px 0; padding: 2px 6px; border: 1px solid #dbeafe; background: #eff6ff; border-radius: 6px; white-space: nowrap; }
.placeholder-word { font-family: ui-monospace, SFMono-Regular, Consolas, monospace; color: #b45309; white-space: nowrap; }
.muted { color: var(--text-muted); font-size: 12px; margin-top: 3px; }
.spelling-input { width: 180px; max-width: 100%; padding: 4px 7px; border: 1px solid #cbd5e1; border-radius: 6px; }
</style>
