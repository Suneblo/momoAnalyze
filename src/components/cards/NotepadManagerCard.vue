<template>
  <div class="notepad-manager-card">
    <div class="toolbar compact-toolbar">
      <el-button size="small" :loading="loading" @click="loadNotepads">刷新云词本</el-button>
      <el-select v-model="selectedId" size="small" filterable clearable placeholder="选择云词本" class="notepad-select">
        <el-option v-for="item in notepads" :key="notepadId(item)" :label="notepadLabel(item)" :value="notepadId(item)" />
      </el-select>
      <el-button size="small" :disabled="!selectedId" :loading="loading" @click="loadSelectedNotepad">读取内容</el-button>
    </div>

    <p class="info-text">选择云词本后需手动点击“读取内容”，避免误覆盖当前编辑区。</p>
    <p v-if="status" class="status-line">{{ status }}</p>

    <div class="form-grid">
      <label class="field">
        <span>标题</span>
        <el-input v-model="title" placeholder="云词本标题" />
      </label>
      <label class="field">
        <span>简介</span>
        <el-input v-model="brief" type="textarea" :rows="3" placeholder="云词本简介" />
      </label>
      <label class="field field-full">
        <span>正文内容</span>
        <el-input v-model="content" type="textarea" :rows="12" placeholder="云词本正文内容" />
      </label>
    </div>

    <div class="inline-actions">
      <el-button type="primary" :loading="loading" @click="createFromEditor">新建云词本</el-button>
      <el-button type="warning" :disabled="!selectedId" :loading="loading" @click="saveSelected">保存到选中云词本</el-button>
      <span class="info-text">正文长度：{{ content.length }} 字符</span>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useApi } from '@/composables/useApi'

defineProps({ card: Object })

const api = useApi()
const notepads = ref([])
const selectedId = ref('')
const title = ref('')
const brief = ref('')
const content = ref('')
const status = ref('')
const loading = ref(false)

function notepadId(item) {
  return String(item?.id || item?.notepad_id || item?.notepadId || '')
}

function notepadLabel(item) {
  const id = notepadId(item)
  const name = item?.title || item?.name || id || '未命名云词本'
  return id ? `${name}（${id}）` : name
}

function extractError(data, fallback) {
  return data?.error || data?.message || fallback
}

async function loadNotepads() {
  loading.value = true
  status.value = '正在读取云词本列表…'
  try {
    const data = await api.fetchNotepads()
    if (!data?.success) throw new Error(extractError(data, '读取云词本列表失败'))
    notepads.value = Array.isArray(data.notepads) ? data.notepads : []
    status.value = `已读取 ${notepads.value.length} 个云词本。`
  } catch (error) {
    status.value = `读取云词本列表失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

async function loadSelectedNotepad() {
  if (!selectedId.value) {
    status.value = '请先选择云词本。'
    return
  }
  loading.value = true
  status.value = '正在读取云词本内容…'
  try {
    const data = await api.fetchNotepad(selectedId.value)
    if (!data?.success) throw new Error(extractError(data, '读取云词本失败'))
    const notepad = data.notepad || {}
    title.value = notepad.title || ''
    brief.value = notepad.brief || ''
    content.value = notepad.content || ''
    status.value = `已读取：${notepad.title || selectedId.value}`
  } catch (error) {
    status.value = `读取云词本失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

function confirmWrite(type, idLabel) {
  return window.confirm(
    `这是写入/覆盖云端数据的危险操作。\n\n` +
    `操作类型：${type}\n` +
    `云词本 ID：${idLabel}\n` +
    `标题：${title.value || '未命名云词本'}\n` +
    `正文长度：${content.value.length} 字符\n\n` +
    `确认继续吗？`
  )
}

async function createFromEditor() {
  const payload = {
    title: title.value.trim() || '未命名云词本',
    brief: brief.value || '',
    content: content.value || '',
    tags: [],
    status: 'PUBLISHED',
  }
  if (!confirmWrite('新建云词本', '新建')) {
    status.value = '已取消新建云词本。'
    return
  }
  loading.value = true
  status.value = '正在新建云词本…'
  try {
    const data = await api.createNotepad(payload)
    if (!data?.success) throw new Error(extractError(data, '新建云词本失败'))
    status.value = `创建成功：${data.notepad?.title || payload.title}`
    await loadNotepads()
    const newId = notepadId(data.notepad)
    if (newId) selectedId.value = newId
  } catch (error) {
    status.value = `新建失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

async function saveSelected() {
  if (!selectedId.value) {
    status.value = '请先选择云词本。'
    return
  }
  const payload = {
    id: selectedId.value,
    title: title.value.trim() || '未命名云词本',
    brief: brief.value || '',
    content: content.value || '',
    status: 'PUBLISHED',
  }
  if (!confirmWrite('保存到已有云词本', selectedId.value)) {
    status.value = '已取消保存云词本。'
    return
  }
  loading.value = true
  status.value = '正在保存云词本…'
  try {
    const data = await api.updateNotepad(payload)
    if (!data?.success) throw new Error(extractError(data, '保存云词本失败'))
    status.value = `保存成功：${data.notepad?.title || payload.title}`
    await loadNotepads()
  } catch (error) {
    status.value = `保存失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

</script>

<style scoped>
.compact-toolbar { gap: 8px; }
.notepad-select { min-width: 260px; flex: 1; }
.form-grid { display: grid; gap: 10px; margin-top: 10px; }
.field { display: grid; gap: 4px; font-size: 13px; color: var(--text-main); }
.field span { font-weight: 600; }
.field-full { grid-column: 1 / -1; }
.status-line { margin: 8px 0; color: #2563eb; font-size: 13px; }
.inline-actions { flex-wrap: wrap; }
@media (min-width: 900px) {
  .form-grid { grid-template-columns: 1fr 1fr; }
}
</style>
