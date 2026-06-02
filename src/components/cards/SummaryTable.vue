<template>
  <div>
    <div class="summary-tools">
      <span class="info-text">已去重：当前状态、今日学习进度、记忆临界点和学习时长由对应卡片复制；这里仅保留日期、更新时间、当日复习平均学习次数和记忆持久度明细。</span>
      <el-button size="small" @click="copySummary">复制表格</el-button>
    </div>

    <div v-if="rows.length" class="stat-table-scroll" data-latest-scroll>
      <el-table
        :data="rows"
        size="small"
        border
        stripe
        max-height="520"
        :style="{ width: tableMinWidth + 'px' }"
        class="summary-table no-squeeze-table"
        :fit="false"
      >
      <el-table-column
        v-for="column in columns"
        :key="column.key"
        :label="column.label"
        :min-width="columnWidth(column)"
        :fixed="column.key === 'date' ? 'left' : false"
        show-overflow-tooltip
      >
        <template #header>
          <div class="column-head">
            <span>{{ column.label }}</span>
            <small v-if="column.subLabel">{{ column.subLabel }}</small>
          </div>
        </template>
        <template #default="{ row }">
          {{ column.formatter(row) }}
        </template>
      </el-table-column>
      </el-table>
    </div>
    <p v-else class="info-text">暂无数据</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useDataStore } from '@/stores/dataStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { buildSummaryColumns, buildSummaryMarkdown, displayRowsOnly } from '@/utils/dashboardParity'
import { copyToClipboard } from '@/composables/useCopy'

defineProps({ card: Object })

const dataStore = useDataStore()
const settings = useSettingsStore()

const rows = computed(() => {
  const source = dataStore.displayRows?.length ? dataStore.displayRows : dataStore.rawRows
  return displayRowsOnly(source).filter(row => row.hasData !== false)
})

const columns = computed(() => buildSummaryColumns(settings.memoryThresholds))
const tableMinWidth = computed(() => columns.value.reduce((sum, column) => sum + Number(columnWidth(column) || 100), 0))

function columnWidth(column) {
  if (column.key === 'date') return 105
  if (column.key.includes('memory') || column.key.includes('critical_point') || column.subLabel) return 150
  if (column.label.length >= 6) return 130
  return 86
}

async function copySummary() {
  await copyToClipboard(`# 按日期统计表\n\n${buildSummaryMarkdown(rows.value, settings.memoryThresholds)}`)
  ElMessage.success('已复制按日期统计表')
}
</script>

<style scoped>
.summary-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.column-head {
  display: flex;
  flex-direction: column;
  line-height: 1.25;
}
.column-head small {
  color: #94a3b8;
  font-weight: 400;
  font-size: 11px;
}
:deep(.summary-table .el-table__cell) {
  font-size: 12px;
}
</style>
