<template>
  <div class="article-unknown-card">
    <div class="toolbar compact-toolbar">
      <el-button size="small" :loading="loading" @click="loadNotepads">刷新云词本</el-button>
      <el-select v-model="selectedId" size="small" filterable clearable placeholder="选择云词本" class="notepad-select">
        <el-option v-for="item in notepads" :key="notepadId(item)" :label="notepadLabel(item)" :value="notepadId(item)" />
      </el-select>
    </div>

    <div class="form-grid">
      <label class="field field-full">
        <span>英文文章</span>
        <el-input v-model="articleText" type="textarea" :rows="8" placeholder="粘贴英文文章" />
      </label>

      <label class="field">
        <span>比对目标</span>
        <el-select v-model="compareMode" size="small">
          <el-option label="本地学习计划未包含" value="local" />
          <el-option label="选中云词本未包含" value="notepad" />
          <el-option label="学习计划和云词本都未包含" value="both" />
        </el-select>
      </label>

      <label class="field">
        <span>最短长度</span>
        <el-input-number v-model="minLength" size="small" :min="1" :max="30" />
      </label>

      <label class="field checkbox-field">
        <input v-model="skipStopwords" type="checkbox">
        <span>跳过常见虚词</span>
      </label>

      <label class="field checkbox-field">
        <input v-model="excludeInflections" type="checkbox">
        <span>排除变形词</span>
      </label>

      <label class="field">
        <span>写入章节名</span>
        <el-input v-model="chapter" placeholder="Article 2026-05-18" />
      </label>

      <label class="field">
        <span>新建云词本标题</span>
        <el-input v-model="newTitle" placeholder="文章生词" />
      </label>

      <label class="field">
        <span>新建云词本简介</span>
        <el-input v-model="newBrief" placeholder="从文章分析生成" />
      </label>
    </div>

    <div class="inline-actions">
      <el-button type="primary" :loading="loading" @click="analyzeArticle">分析文章</el-button>
      <el-button :disabled="!missingWords.length" @click="copyMissingWords">复制缺失单词</el-button>
      <el-button type="warning" :disabled="!missingWords.length || !selectedId" :loading="loading" @click="addToSelectedNotepad">写入选中云词本</el-button>
      <el-button type="success" :disabled="!missingWords.length" :loading="loading" @click="createAndWriteNotepad">新建云词本并写入</el-button>
    </div>

    <p v-if="status" class="status-line">{{ status }}</p>

    <div v-if="missingWords.length" class="result-panel">
      <p class="info-text">缺失单词 {{ missingWords.length }} 个。下方文本可直接复制。</p>
      <el-input :model-value="resultText" type="textarea" :rows="10" readonly />
      <div class="word-list">
        <span v-for="item in missingWords.slice(0, 200)" :key="item.word" class="word-tag">
          {{ item.word }}<small>×{{ item.count ?? 1 }}</small>
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useApi } from '@/composables/useApi'

defineProps({ card: Object })

const api = useApi()
const loading = ref(false)
const status = ref('')
const notepads = ref([])
const selectedId = ref('')
const articleText = ref('')
const compareMode = ref('local')
const minLength = ref(2)
const skipStopwords = ref(true)
const excludeInflections = ref(true)
const missingWords = ref([])
const chapter = ref(`Article ${localDateString()}`)
const newTitle = ref(`文章生词 ${localDateString()}`)
const newBrief = ref('从文章分析生成')

const resultText = computed(() => buildMissingText(missingWords.value))

function localDateString() {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

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

function wordsOnly() {
  return missingWords.value.map(item => item.word).filter(Boolean)
}

function buildMissingText(words) {
  const lines = ['# 文章缺失单词', `数量：${words.length}`, '', '单词\t出现次数\t本地学习计划\t选中云词本\t变形/原形']
  for (const item of words) {
    lines.push([
      item.word,
      item.count ?? '',
      item.inLocalPlan ? '已在' : '未在',
      item.inNotepad ? '已在' : '未在',
      [item.localMatchedWord, item.notepadMatchedWord].filter(Boolean).join('/') || '-',
    ].join('\t'))
  }
  return lines.join('\n')
}

async function copyText(text) {
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
    status.value = `读取云词本失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

async function analyzeArticle() {
  if (!articleText.value.trim()) {
    status.value = '请先粘贴文章。'
    return
  }
  if ((compareMode.value === 'notepad' || compareMode.value === 'both') && !selectedId.value) {
    status.value = '当前比对目标需要先选择云词本。'
    return
  }
  loading.value = true
  status.value = '正在分析文章…'
  try {
    const data = await api.analyzeArticle({
      text: articleText.value,
      notepadId: selectedId.value,
      mode: compareMode.value,
      minLength: minLength.value,
      skipStopwords: skipStopwords.value,
      excludeInflections: excludeInflections.value,
    })
    if (!data?.success) throw new Error(extractError(data, '文章分析失败'))
    missingWords.value = Array.isArray(data.missingWords) ? data.missingWords : []
    status.value = `文章唯一词 ${data.uniqueCount || 0} 个，缺失 ${data.missingCount || 0} 个${data.excludeInflections ? `，变形排除 ${data.excludedByInflectionCount || 0} 个` : ''}。`
  } catch (error) {
    status.value = `分析失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

async function copyMissingWords() {
  await copyText(wordsOnly().join('\n'))
  status.value = `已复制 ${wordsOnly().length} 个缺失单词。`
}

function confirmWordWrite(type, idLabel, titleLabel) {
  const words = wordsOnly()
  return window.confirm(
    `这是写入云端数据的危险操作。\n\n` +
    `操作类型：${type}\n` +
    `云词本 ID：${idLabel}\n` +
    `标题：${titleLabel}\n` +
    `写入单词数：${words.length}\n` +
    `前 20 个单词：${words.slice(0, 20).join(', ') || '-'}\n\n` +
    `确认继续吗？`
  )
}

async function addToSelectedNotepad() {
  const words = wordsOnly()
  if (!words.length) {
    status.value = '没有可写入的缺失单词。请先分析文章。'
    return
  }
  if (!selectedId.value) {
    status.value = '请先选择云词本。'
    return
  }
  if (!confirmWordWrite('写入选中云词本', selectedId.value, '选中云词本')) {
    status.value = '已取消写入云词本。'
    return
  }
  loading.value = true
  status.value = '正在写入选中云词本…'
  try {
    const data = await api.addWordsToNotepad({ id: selectedId.value, words, chapter: chapter.value })
    if (!data?.success) throw new Error(extractError(data, '写入云词本失败'))
    status.value = `写入完成：新增 ${data.addedCount || 0} 个，已自动去重。`
  } catch (error) {
    status.value = `写入失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

async function createAndWriteNotepad() {
  const words = wordsOnly()
  if (!words.length) {
    status.value = '没有可写入的缺失单词。请先分析文章。'
    return
  }
  const title = newTitle.value.trim() || `文章生词 ${localDateString()}`
  if (!confirmWordWrite('新建云词本并写入', '新建', title)) {
    status.value = '已取消新建云词本并写入。'
    return
  }
  loading.value = true
  status.value = '正在新建云词本并写入…'
  try {
    const data = await api.addWordsToNotepad({ title, brief: newBrief.value || '从文章分析生成', words, chapter: chapter.value })
    if (!data?.success) throw new Error(extractError(data, '新建并写入失败'))
    status.value = `创建并写入完成：新增 ${data.addedCount || 0} 个。`
    await loadNotepads()
    const newId = notepadId(data.notepad)
    if (newId) selectedId.value = newId
  } catch (error) {
    status.value = `创建/写入失败：${error.message || error}`
  } finally {
    loading.value = false
  }
}

</script>

<style scoped>
.compact-toolbar { gap: 8px; }
.notepad-select { min-width: 260px; flex: 1; }
.form-grid { display: grid; gap: 10px; margin-top: 10px; }
.field { display: grid; gap: 4px; font-size: 13px; }
.field span { font-weight: 600; }
.field-full { grid-column: 1 / -1; }
.checkbox-field { display: flex; align-items: center; gap: 6px; margin-top: 20px; }
.inline-actions { flex-wrap: wrap; margin-top: 10px; }
.status-line { margin: 8px 0; color: #2563eb; font-size: 13px; }
.result-panel { margin-top: 10px; }
.word-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; max-height: 180px; overflow: auto; }
.word-tag { background: #f1f5f9; padding: 3px 8px; border-radius: 6px; font-size: 13px; }
.word-tag small { margin-left: 4px; color: var(--text-muted); }
@media (min-width: 900px) {
  .form-grid { grid-template-columns: 1fr 1fr; }
}
</style>
