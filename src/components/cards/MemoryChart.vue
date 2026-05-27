<template>
  <div class="memory-chart-card">
    <section v-if="criticalChartOption" class="memory-chart-section">
      <div class="section-title">临界点统计（≥0）</div>
      <BaseChart :option="criticalChartOption" :height="320" />
    </section>
    <p v-else class="info-text">暂无临界点数据</p>

    <section v-if="reviewSpanChartOption" class="memory-chart-section">
      <div class="section-title">记忆持久度统计（＞0）</div>
      <BaseChart :option="reviewSpanChartOption" :height="320" />
    </section>
    <p v-else class="info-text">暂无记忆持久度数据</p>

    <section v-if="overdueChartOption" class="memory-chart-section">
      <div class="section-title">逾期统计（＜0）</div>
      <BaseChart :option="overdueChartOption" :height="320" />
    </section>
    <p v-else class="info-text">暂无逾期数据</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import { useSettingsStore } from '@/stores/settingsStore'
import {
  MEMORY_SERIES_COLORS,
  countCriticalPointForThreshold,
  countOverdueForThreshold,
  countReviewSpanForThreshold,
  getCriticalPointThresholds,
  getOverdueThresholds,
  getReviewSpanThresholds,
  sortRowsByDateAsc,
} from '@/utils/memoryThresholds'
import BaseChart from '@/components/charts/BaseChart.vue'

defineProps({ card: Object })
const dataStore = useDataStore()
const settings = useSettingsStore()

const rows = computed(() => sortRowsByDateAsc(dataStore.displayRows)
  .filter(row => row.hasOverviewData && !row.isGapMarker))

function makeLineChart(title, thresholds, counter) {
  const cleanRows = rows.value
  if (!cleanRows.length || !thresholds.length) return null

  const labels = cleanRows.map(row => row.date?.slice(5) || '')
  const series = thresholds.map((spec, index) => ({
    name: spec.headerLabel,
    type: 'line',
    data: cleanRows.map(row => counter(row, spec)),
    smooth: false,
    connectNulls: false,
    symbol: 'none',
    lineStyle: { width: 2, color: MEMORY_SERIES_COLORS[index % MEMORY_SERIES_COLORS.length] },
    itemStyle: { color: MEMORY_SERIES_COLORS[index % MEMORY_SERIES_COLORS.length] },
  }))

  return {
    __pointBaseWidth: 52,
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        const index = params[0]?.dataIndex ?? 0
        const fullDate = cleanRows[index]?.date || params[0]?.axisValue || ''
        let html = `<b>${fullDate}</b><br/>`
        params.forEach(param => {
          html += `${param.marker}${param.seriesName}: ${param.value}<br/>`
        })
        return html
      },
    },
    legend: {
      data: series.map(item => item.name),
      bottom: 0,
      type: 'scroll',
      textStyle: { fontSize: 11 },
    },
    grid: { left: 55, right: 20, top: 18, bottom: 58 },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { fontSize: 11, rotate: cleanRows.length > 20 ? 45 : 0 },
    },
    yAxis: { type: 'value', name: '词数', minInterval: 1, axisLabel: { fontSize: 11 } },
    series,
  }
}

const criticalChartOption = computed(() => makeLineChart(
  '临界点统计（≥0）',
  getCriticalPointThresholds(settings.memoryThresholds),
  countCriticalPointForThreshold,
))

const reviewSpanChartOption = computed(() => makeLineChart(
  '记忆持久度统计（＞0）',
  getReviewSpanThresholds(settings.memoryThresholds),
  countReviewSpanForThreshold,
))

const overdueChartOption = computed(() => makeLineChart(
  '逾期统计（＜0）',
  getOverdueThresholds(settings.memoryThresholds),
  countOverdueForThreshold,
))
</script>

<style scoped>
.memory-chart-card {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.memory-chart-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.section-title {
  font-weight: 700;
  color: #334155;
}
</style>
