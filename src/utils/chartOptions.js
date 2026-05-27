// ECharts option builder helpers
import * as echarts from 'echarts'

export const COLORS = {
  green: '#22c55e',
  yellow: '#f59e0b',
  red: '#dc2626',
  blue: '#2563eb',
  orange: '#f97316',
  teal: '#14b8a6'
}

/**
 * Build a multi-series line chart option
 * @param {string[]} labels - X-axis labels
 * @param {{name:string, data:number[], color:string}[]} seriesList
 * @param {object} opts - { title, yUnit, xUnit, tooltipFormatter }
 */
export function buildLineOption(labels, seriesList, opts = {}) {
  return {
    tooltip: {
      trigger: 'axis',
      formatter: opts.tooltipFormatter
    },
    legend: {
      data: seriesList.map(s => s.name),
      bottom: 0
    },
    grid: {
      left: 50, right: 20, top: 20, bottom: 50
    },
    xAxis: {
      type: 'category',
      data: labels,
      name: opts.xUnit || '',
      axisLabel: { rotate: labels.length > 15 ? 45 : 0 }
    },
    yAxis: {
      type: 'value',
      name: opts.yUnit || '',
      minInterval: 1
    },
    series: seriesList.map(s => ({
      name: s.name,
      type: 'line',
      data: s.data,
      smooth: false,
      itemStyle: { color: s.color },
      lineStyle: { color: s.color, width: 2 },
      symbol: 'circle',
      symbolSize: 6
    }))
  }
}

/**
 * Build a stacked bar chart option
 * @param {string[]} labels
 * @param {{name:string, data:number[], color:string, stack?:string}[]} seriesList
 */
export function buildBarOption(labels, seriesList, opts = {}) {
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: opts.tooltipFormatter
    },
    legend: {
      data: seriesList.map(s => s.name),
      bottom: 0
    },
    grid: {
      left: 50, right: 20, top: 20, bottom: 50
    },
    xAxis: {
      type: 'category',
      data: labels,
      axisLabel: { rotate: labels.length > 15 ? 45 : 0 }
    },
    yAxis: {
      type: 'value',
      name: opts.yUnit || '',
      minInterval: 1
    },
    series: seriesList.map(s => ({
      name: s.name,
      type: 'bar',
      data: s.data,
      stack: s.stack || 'total',
      itemStyle: { color: s.color },
      barMaxWidth: 40
    }))
  }
}

/**
 * Build a probability table option (heatmap-like)
 */
export function buildProbabilityHeatmap(memoryRows, studyRows, dataMatrix) {
  return {
    tooltip: {
      formatter: (params) => {
        const { value, name } = params
        return `${name}<br/>概率: ${(value * 100).toFixed(1)}%`
      }
    },
    grid: { left: 120, top: 10, right: 10, bottom: 10 },
    xAxis: {
      type: 'category',
      data: studyRows.map(r => r.label),
      position: 'top'
    },
    yAxis: {
      type: 'category',
      data: memoryRows.map(r => r.label),
      inverse: true
    },
    visualMap: {
      min: 0,
      max: 1,
      calculable: true,
      orient: 'vertical',
      left: 10,
      bottom: 10,
      inRange: { color: ['#f0f9ff', '#bae6fd', '#7dd3fc', '#38bdf8', '#0ea5e9'] }
    },
    series: [{
      type: 'heatmap',
      data: dataMatrix,
      label: { show: true, formatter: (p) => (p.value * 100).toFixed(0) + '%' },
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } }
    }]
  }
}
