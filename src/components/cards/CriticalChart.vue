<template>
  <BaseChart v-if="chartOption" :option="chartOption" :height="350" />
  <p v-else class="info-text">暂无数据</p>
</template>

<script setup>
import { computed } from 'vue'
import { useDataStore } from '@/stores/dataStore'
import BaseChart from '@/components/charts/BaseChart.vue'

defineProps({ card: Object })
const dataStore = useDataStore()

const chartOption = computed(() => {
  const rows = dataStore.displayRows?.filter(r => r.hasOverviewData) || []
  if (!rows.length) return null

  // Use memoryNextDueItems to compute due words by day
  // For now show total words and next-due counts
  const labels = rows.map(r => r.date?.slice(5) || '')
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['总词数', '当日待复习(逾期0天)'], bottom: 0, textStyle: { fontSize: 12 } },
    grid: { left: 55, right: 20, top: 15, bottom: 40 },
    xAxis: { type: 'category', data: labels, axisLabel: { fontSize: 11, rotate: rows.length > 20 ? 45 : 0 } },
    yAxis: { type: 'value', name: '词数', minInterval: 1 },
    series: [
      { name: '总词数', type: 'line', data: rows.map(r => r.totalWords || 0), smooth: false, lineStyle: { width: 2, color: '#3b82f6' }, symbol: 'none' },
      { name: '当日待复习(逾期0天)', type: 'line', data: rows.map(r => {
        // Count items with days <= 0 (overdue or due today) from memoryNextDueItems
        const items = r.memoryNextDueItems || []
        return items.filter(i => Number(i.days) <= 0).length
      }), smooth: false, lineStyle: { width: 2, color: '#ef4444' }, areaStyle: { color: 'rgba(239,68,68,0.1)' }, symbol: 'none' },
    ]
  }
})
</script>
