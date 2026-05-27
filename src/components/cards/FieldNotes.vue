<template>
  <div class="field-notes-editor">
    <el-input
      v-model="draft"
      type="textarea"
      :autosize="{ minRows: 12, maxRows: 28 }"
      resize="vertical"
      spellcheck="false"
      placeholder="请输入字段注释，支持 Markdown 列表。"
      class="field-notes-input"
      @keydown.ctrl.s.prevent="saveNotes"
      @keydown.meta.s.prevent="saveNotes"
    />

    <div class="field-notes-toolbar">
      <span class="field-notes-status" :class="{ dirty: isDirty }">{{ statusText }}</span>
      <div class="field-notes-actions">
        <el-button size="small" @click="reloadNotes">重新载入</el-button>
        <el-button size="small" @click="resetDefault">恢复默认</el-button>
        <el-button size="small" type="primary" :disabled="!isDirty" @click="saveNotes">保存</el-button>
      </div>
    </div>

    <div class="info-text">快捷键：Ctrl + S 保存。字段注释会保存到本机浏览器缓存。</div>
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

defineProps({ card: Object })

const STORAGE_KEY = 'momoFieldNotesText'

const DEFAULT_NOTES = `- 每日范围：东八区 04:00 为分界线，04:00 前属前一天。
- 逾期：下次复习日期早于到达日，不等于忘记。
- 忘记：上次反应为“忘记”，且未逾期。
- 模糊：上次反应为“模糊”，且未逾期。
- 认识：上次反应为“认识”，且未逾期。
- 熟知、顽固：墨墨标签，独立于状态。
- 当日已新学：当天已完成的新词数。
- 当日已复习：当天已完成的复习词数。
- 今日首次忘记数：当天首次作答“忘记”的数量。
- 按日期统计表的熟悉度范围列按“数量/平均学习次数”展示。`

const draft = ref(DEFAULT_NOTES)
const saved = ref(DEFAULT_NOTES)
const savedAt = ref('')

const isDirty = computed(() => draft.value !== saved.value)
const statusText = computed(() => {
  if (isDirty.value) return '有未保存修改'
  if (savedAt.value) return `已保存 ${savedAt.value}`
  return '已保存'
})

function nowText() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function readSavedNotes() {
  try {
    const text = localStorage.getItem(STORAGE_KEY)
    return text && text.trim() ? text : DEFAULT_NOTES
  } catch (e) {
    return DEFAULT_NOTES
  }
}

function applyText(text) {
  draft.value = text
  saved.value = text
}

function reloadNotes() {
  applyText(readSavedNotes())
  ElMessage.success('已重新载入字段注释')
}

function saveNotes() {
  try {
    localStorage.setItem(STORAGE_KEY, draft.value)
    saved.value = draft.value
    savedAt.value = nowText()
    ElMessage.success('字段注释已保存')
  } catch (e) {
    ElMessage.error(`字段注释保存失败：${e?.message || e}`)
  }
}

function resetDefault() {
  draft.value = DEFAULT_NOTES
  saveNotes()
}

onMounted(() => applyText(readSavedNotes()))
</script>

<style scoped>
.field-notes-editor {
  display: grid;
  gap: 10px;
}

.field-notes-input :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  line-height: 1.75;
  color: var(--text-main);
  border-radius: 12px;
}

.field-notes-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.field-notes-status {
  color: var(--text-muted);
  font-size: 12px;
}

.field-notes-status.dirty {
  color: #b45309;
  font-weight: 700;
}

.field-notes-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
