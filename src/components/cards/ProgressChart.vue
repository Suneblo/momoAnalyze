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
  const rows = dataStore.displayRows?.filter(r => r.hasProgressData) || []
  if (!rows.length) return null
  const labels = rows.map(r => r.date?.slice(5) || '')

  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['已完成', '总数', '当日已新学', '当日已复习'], bottom: 0, textStyle: { fontSize: 12 } },
    grid: { left: 55, right: 20, top: 15, bottom: 40 },
    xAxis: {
      type: 'category', data: labels,
      axisLabel: { fontSize: 11, rotate: rows.length > 20 ? 45 : 0 }
    },
    yAxis: { type: 'value', name: '词数', minInterval: 1, axisLabel: { fontSize: 11 } },
    series: [
      { name: '已完成', type: 'line', data: rows.map(r => r.finished || 0), smooth: false, lineStyle: { width: 2, color: '#22c55e' }, itemStyle: { color: '#22c55e' }, symbol: 'none' },
      { name: '总数', type: 'line', data: rows.map(r => r.total || 0), smooth: false, lineStyle: { width: 2, color: '#3b82f6' }, itemStyle: { color: '#3b82f6' }, symbol: 'none' },
      { name: '当日已新学', type: 'line', data: rows.map(r => r.dailyNewLearnedCount || 0), smooth: false, lineStyle: { width: 1.5, color: '#14b8a6', type: 'dashed' }, itemStyle: { color: '#14b8a6' }, symbol: 'none' },
      { name: '当日已复习', type: 'line', data: rows.map(r => r.dailyReviewedCount || 0), smooth: false, lineStyle: { width: 1.5, color: '#f59e0b', type: 'dashed' }, itemStyle: { color: '#f59e0b' }, symbol: 'none' },
    ]
  }
})
</script>
