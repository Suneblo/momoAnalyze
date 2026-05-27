<template>
  <div class="card log-card">
    <div class="log-header">
      <h2>运行日志</h2>
      <div class="inline-actions">
        <el-button size="small" @click="copyLog">复制日志</el-button>
        <el-button size="small" @click="clearLog">清空日志</el-button>
      </div>
    </div>
    <div ref="logEl" class="log-content"></div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const logEl = ref(null)
const logs = ref([])

function log(msg) {
  const time = new Date().toLocaleTimeString()
  logs.value.push(`[${time}] ${msg}`)
  if (logEl.value) {
    const line = `[${time}] ${msg}`
    logEl.value.innerHTML += (logEl.value.innerHTML ? '\n' : '') + line
    logEl.value.scrollTop = logEl.value.scrollHeight
  }
}

function copyLog() {
  navigator.clipboard.writeText(logs.value.join('\n'))
}

function clearLog() {
  logs.value = []
  if (logEl.value) logEl.value.innerHTML = ''
}

defineExpose({ log, copyLog, clearLog })
</script>

<style scoped>
.log-card { margin-bottom: 20px; }
.log-content {
  font-size: .8125rem;
  color: var(--text-muted);
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
}
</style>
