<template>
  <div class="base-chart-shell">
    <div v-if="externalLegendItems.length" class="chart-fixed-legend">
      <span
        v-for="item in externalLegendItems"
        :key="item.name"
        class="chart-fixed-legend-item"
      >
        <span class="chart-fixed-legend-marker" :style="{ background: item.color }"></span>
        <span class="chart-fixed-legend-name">{{ item.name }}</span>
      </span>
    </div>

    <div v-if="useFixedYAxis" class="chart-fixed-y-shell">
      <div
        ref="axisChartRef"
        class="chart-y-axis-canvas"
        :style="{ width: fixedAxisWidth + 'px', height: scaledHeight + 'px' }"
      ></div>
      <div class="chart-scroll-shell chart-scroll-with-fixed-y">
        <div
          ref="chartRef"
          class="chart-canvas chart-body-canvas"
          :style="{ width: scrollBodyWidth, height: scaledHeight + 'px' }"
        ></div>
      </div>
    </div>
    <div v-else class="chart-scroll-shell">
      <div
        ref="chartRef"
        class="chart-canvas"
        :style="{ width: chartWidth, height: scaledHeight + 'px' }"
      ></div>
    </div>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { useSettingsStore } from '@/stores/settingsStore'

const props = defineProps({
  option: { type: Object, required: true },
  height: { type: Number, default: 350 },
  theme: { type: String, default: '' }
})

const emit = defineEmits(['chart-click'])

const chartRef = ref(null)
const axisChartRef = ref(null)
const injectedGlobalZoom = inject('momoGlobalZoom', null)
const settings = useSettingsStore()
const currentZoom = ref(readReactiveZoom())
let chart = null
let axisChart = null

function clampZoom(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 1
  return Math.min(1.4, Math.max(0.65, numeric))
}

function readGlobalZoom() {
  if (typeof window === 'undefined') return 1
  const raw = window.getComputedStyle(document.documentElement).getPropertyValue('--momo-global-zoom')
  return clampZoom(Number.parseFloat(raw) || 1)
}


function readReactiveZoom() {
  const injected = injectedGlobalZoom && typeof injectedGlobalZoom === 'object' && 'value' in injectedGlobalZoom
    ? injectedGlobalZoom.value
    : undefined
  return clampZoom(injected ?? readGlobalZoom())
}

function scaleNumber(value, zoom, min = 1) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return value
  return Math.max(min, Math.round(value * zoom))
}

function scaleCssSize(value, zoom, min = 1) {
  if (typeof value === 'number') return scaleNumber(value, zoom, min)
  if (typeof value !== 'string') return value
  const trimmed = value.trim()
  if (trimmed.endsWith('%')) return value
  const match = trimmed.match(/^(-?\d+(?:\.\d+)?)(px)?$/)
  if (!match) return value
  return `${Math.max(min, Math.round(Number(match[1]) * zoom))}${match[2] || ''}`
}

function scalePadding(value, zoom) {
  if (Array.isArray(value)) return value.map(item => scaleCssSize(item, zoom, 0))
  return scaleCssSize(value, zoom, 0)
}

function withFontSize(style, zoom, base = 12) {
  const next = { ...(style || {}) }
  next.fontSize = scaleNumber(typeof next.fontSize === 'number' ? next.fontSize : base, zoom, 8)
  return next
}

function scaleTextStyleContainer(target, key, zoom, base = 12) {
  if (!target) return
  target[key] = withFontSize(target[key], zoom, base)
}

function scaleLabelContainer(target, key, zoom, base = 11) {
  if (!target?.[key]) return
  target[key] = {
    ...target[key],
    fontSize: scaleNumber(typeof target[key].fontSize === 'number' ? target[key].fontSize : base, zoom, 8)
  }
}

function getPointCount(option) {
  const xAxis = Array.isArray(option?.xAxis) ? option.xAxis[0] : option?.xAxis
  const data = xAxis?.data
  if (Array.isArray(data)) return data.length

  const series = Array.isArray(option?.series) ? option.series : []
  let count = 0
  for (const item of series) {
    if (Array.isArray(item?.data)) count = Math.max(count, item.data.length)
  }
  return count
}


function pointSpacingFactor() {
  const value = Number(settings.pointSpacing)
  if (!Number.isFinite(value)) return 1
  return value
}

function pointStep(base, zoom) {
  const raw = base * zoom * pointSpacingFactor()
  if (!Number.isFinite(raw)) return Math.round(base * zoom)
  return Math.round(raw)
}

function optionPointBase(option, fallback) {
  const value = Number(option?.__pointBaseWidth)
  return Number.isFinite(value) && value > 0 ? value : fallback
}

function isTimeAxis(option) {
  const xAxis = Array.isArray(option?.xAxis) ? option.xAxis[0] : option?.xAxis
  return xAxis?.type === 'time'
}

function hasCartesianAxis(option) {
  return Boolean(option?.xAxis && option?.yAxis)
}

function cloneOption(value) {
  if (Array.isArray(value)) return value.map(cloneOption)
  if (value && typeof value === 'object') {
    const out = {}
    for (const [key, item] of Object.entries(value)) out[key] = cloneOption(item)
    return out
  }
  return value
}

function cleanOptionValue(value) {
  if (Array.isArray(value)) return value.map(cleanOptionValue)
  if (value && typeof value === 'object') {
    const out = {}
    for (const [key, item] of Object.entries(value)) {
      if (item !== undefined) out[key] = cleanOptionValue(item)
    }
    return out
  }
  return value
}

function normalizeLegendOption(item, zoom, forceHidden = false) {
  const next = {
    ...(item || {}),
    textStyle: withFontSize(item?.textStyle, zoom, 11),
    itemWidth: scaleNumber(item?.itemWidth ?? 18, zoom, 8),
    itemHeight: scaleNumber(item?.itemHeight ?? 10, zoom, 6),
    itemGap: scaleNumber(item?.itemGap ?? 10, zoom, 4),
    padding: scalePadding(item?.padding ?? 0, zoom),
    borderRadius: scalePadding(item?.borderRadius ?? 0, zoom),
  }
  if (next.backgroundColor === undefined) next.backgroundColor = 'rgba(0,0,0,0)'
  if (forceHidden) next.show = false
  return cleanOptionValue(next)
}

function toArray(value) {
  if (Array.isArray(value)) return value
  if (value === undefined || value === null) return []
  return [value]
}
function resolveColor(value) {
  if (!value) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'function') return ''
  if (typeof value === 'object') {
    if (typeof value.color === 'string') return value.color
    if (Array.isArray(value.colorStops) && value.colorStops[0]?.color) return value.colorStops[0].color
  }
  return ''
}

function seriesColor(series, index) {
  const fromItem = resolveColor(series?.itemStyle?.color)
  if (fromItem) return fromItem
  const fromLine = resolveColor(series?.lineStyle?.color)
  if (fromLine) return fromLine
  const fromArea = resolveColor(series?.areaStyle?.color)
  if (fromArea) return fromArea
  const palette = props.option?.color
  if (Array.isArray(palette) && palette[index]) return resolveColor(palette[index]) || '#94a3b8'
  return '#94a3b8'
}

function legendNames(option) {
  const legends = toArray(option?.legend)
  const names = []
  for (const legend of legends) {
    const data = Array.isArray(legend?.data) ? legend.data : []
    for (const item of data) {
      const name = typeof item === 'string' ? item : item?.name
      if (name && !names.includes(name)) names.push(name)
    }
  }
  return names
}


function parseGridLeft(grid) {
  const firstGrid = Array.isArray(grid) ? grid[0] : grid
  const raw = firstGrid?.left
  if (typeof raw === 'number') return raw
  if (typeof raw === 'string') {
    const parsed = Number.parseFloat(raw)
    if (Number.isFinite(parsed)) return parsed
  }
  return scaleNumber(52, currentZoom.value, 34)
}

function scaleLineStyle(style, zoom, baseWidth = 1) {
  const next = { ...(style || {}) }
  next.width = scaleNumber(typeof next.width === 'number' ? next.width : baseWidth, zoom, 1)
  return next
}

function scaleRichText(rich, zoom, base = 11) {
  if (!rich || typeof rich !== 'object') return rich
  const next = {}
  for (const [key, value] of Object.entries(rich)) {
    next[key] = withFontSize(value || {}, zoom, base)
    if (next[key].padding) next[key].padding = scalePadding(next[key].padding, zoom)
  }
  return next
}

function scaleAxis(axis, zoom) {
  const next = { ...(axis || {}) }
  next.axisLabel = withFontSize(next.axisLabel, zoom, 11)
  if (next.axisLabel?.rich) next.axisLabel.rich = scaleRichText(next.axisLabel.rich, zoom, 11)
  next.nameTextStyle = withFontSize(next.nameTextStyle, zoom, 11)
  next.nameGap = scaleCssSize(next.nameGap, zoom, 0)
  if (next.axisTick) {
    next.axisTick = {
      ...next.axisTick,
      length: scaleNumber(next.axisTick.length ?? 5, zoom, 2),
      lineStyle: scaleLineStyle(next.axisTick.lineStyle, zoom, 1),
    }
  }
  if (next.axisLine) next.axisLine = { ...next.axisLine, lineStyle: scaleLineStyle(next.axisLine.lineStyle, zoom, 1) }
  if (next.splitLine) next.splitLine = { ...next.splitLine, lineStyle: scaleLineStyle(next.splitLine.lineStyle, zoom, 1) }
  if (next.axisPointer?.label) {
    next.axisPointer = {
      ...next.axisPointer,
      label: withFontSize(next.axisPointer.label, zoom, 11)
    }
  }
  return next
}

function scaleGrid(grid, zoom) {
  const next = { ...(grid || {}) }
  for (const key of ['left', 'right', 'top', 'bottom']) {
    if (next[key] !== undefined) next[key] = scaleCssSize(next[key], zoom, key === 'left' ? 30 : 0)
  }
  return next
}

function scaleSeries(series, zoom) {
  const next = { ...series }

  next.label = withFontSize(next.label, zoom, 11)
  if (next.label?.rich) next.label.rich = scaleRichText(next.label.rich, zoom, 11)
  if (next.endLabel) next.endLabel = withFontSize(next.endLabel, zoom, 11)
  if (next.endLabel?.rich) next.endLabel.rich = scaleRichText(next.endLabel.rich, zoom, 11)
  if (next.emphasis?.label) {
    next.emphasis = {
      ...next.emphasis,
      label: withFontSize(next.emphasis.label, zoom, 11)
    }
    if (next.emphasis.label?.rich) next.emphasis.label.rich = scaleRichText(next.emphasis.label.rich, zoom, 11)
  }

  if (next.itemStyle?.borderWidth !== undefined) {
    next.itemStyle = { ...next.itemStyle, borderWidth: scaleNumber(next.itemStyle.borderWidth, zoom, 1) }
  }

  if (next.type === 'bar') {
    if (next.barWidth !== undefined) next.barWidth = scaleCssSize(next.barWidth, zoom, 3)
    else next.barMaxWidth = scaleCssSize(next.barMaxWidth ?? 24, zoom, 4)
    if (next.barMaxWidth !== undefined) next.barMaxWidth = scaleCssSize(next.barMaxWidth, zoom, 4)
    if (next.barMinWidth !== undefined) next.barMinWidth = scaleCssSize(next.barMinWidth, zoom, 1)
    if (next.barCategoryGap !== undefined) next.barCategoryGap = scaleCssSize(next.barCategoryGap, zoom, 0)
    if (next.barGap !== undefined) next.barGap = scaleCssSize(next.barGap, zoom, 0)
  }

  if (next.type === 'line') {
    next.symbolSize = scaleCssSize(next.symbolSize ?? 5, zoom, 2)
    next.lineStyle = scaleLineStyle(next.lineStyle, zoom, 2)
  }

  if (next.type === 'scatter' || next.type === 'effectScatter') {
    next.symbolSize = scaleCssSize(next.symbolSize ?? 6, zoom, 2)
  }

  return next
}

function scaleChartOption(option, zoom) {
  const next = cloneOption(option)
  if (!next || typeof next !== 'object') return next

  scaleTextStyleContainer(next, 'textStyle', zoom, 12)
  if (next.title) {
    next.title = mapMaybeArray(next.title, item => ({
      ...item,
      textStyle: withFontSize(item.textStyle, zoom, 14),
      subtextStyle: withFontSize(item.subtextStyle, zoom, 11),
    }))
  }
  if (next.legend) {
    next.legend = mapMaybeArray(next.legend, item => normalizeLegendOption(item, zoom))
  }
  if (next.tooltip) {
    next.tooltip = mapMaybeArray(next.tooltip, item => ({
      ...item,
      textStyle: withFontSize(item.textStyle, zoom, 12),
      padding: scalePadding(item.padding ?? 10, zoom),
    }))
  }
  if (next.grid) next.grid = mapMaybeArray(next.grid, grid => scaleGrid(grid, zoom))
  if (next.xAxis) next.xAxis = mapMaybeArray(next.xAxis, axis => scaleAxis(axis, zoom))
  if (next.yAxis) next.yAxis = mapMaybeArray(next.yAxis, axis => scaleAxis(axis, zoom))
  if (next.visualMap) {
    next.visualMap = mapMaybeArray(next.visualMap, item => ({
      ...item,
      textStyle: withFontSize(item.textStyle, zoom, 11),
      itemWidth: scaleNumber(item.itemWidth ?? 20, zoom, 8),
      itemHeight: scaleNumber(item.itemHeight ?? 140, zoom, 40),
    }))
  }
  if (next.dataZoom) {
    next.dataZoom = mapMaybeArray(next.dataZoom, item => ({
      ...item,
      height: scaleCssSize(item.height, zoom, 10),
      bottom: scaleCssSize(item.bottom, zoom, 0),
      textStyle: withFontSize(item.textStyle, zoom, 11),
    }))
  }
  if (next.toolbox) {
    next.toolbox = {
      ...next.toolbox,
      itemSize: scaleNumber(next.toolbox.itemSize ?? 15, zoom, 8),
      itemGap: scaleNumber(next.toolbox.itemGap ?? 8, zoom, 3),
      textStyle: withFontSize(next.toolbox.textStyle, zoom, 11),
    }
  }
  if (next.graphic) {
    next.graphic = mapMaybeArray(next.graphic, item => ({
      ...item,
      style: item.style ? withFontSize(item.style, zoom, 12) : item.style,
    }))
  }
  if (Array.isArray(next.series)) next.series = next.series.map(series => scaleSeries(series, zoom))

  return cleanOptionValue(next)
}

function mapMaybeArray(value, mapper) {
  if (Array.isArray(value)) return value.map((item, index) => mapper(item || {}, index))
  return mapper(value || {}, 0)
}

const scaledOption = computed(() => scaleChartOption(props.option, currentZoom.value))

const externalLegendItems = computed(() => {
  if (!useFixedYAxis.value) return []
  const option = scaledOption.value || {}
  const legends = toArray(option.legend).filter(item => item?.show !== false)
  if (!legends.length) return []

  const series = Array.isArray(option.series) ? option.series : []
  const names = legendNames(option)
  const source = names.length ? names : series.map(item => item?.name).filter(Boolean)
  const unique = []
  for (const name of source) {
    if (name && !unique.includes(name)) unique.push(name)
  }

  return unique.map((name, index) => {
    const matchedIndex = series.findIndex(item => item?.name === name)
    const matched = matchedIndex >= 0 ? series[matchedIndex] : null
    return {
      name,
      color: seriesColor(matched, matchedIndex >= 0 ? matchedIndex : index),
    }
  })
})

const scaledHeight = computed(() => {
  const raw = Number(props.height || 350)
  const base = Number.isFinite(raw) && raw > 0 ? raw : 350
  return Math.max(160, Math.round(base * currentZoom.value))
})

const chartWidth = computed(() => {
  const zoom = currentZoom.value
  const explicitWidth = Number(props.option?.__minWidth || props.option?.customMinWidth)
  if (Number.isFinite(explicitWidth) && explicitWidth > 0) {
    return `${Math.max(1, Math.round(explicitWidth * zoom * pointSpacingFactor()))}px`
  }

  const count = getPointCount(props.option)
  if (!count) return '100%'
  const step = pointStep(optionPointBase(props.option, isTimeAxis(props.option) ? 48 : 52), zoom)
  const width = Math.max(1, count * step)
  return `${width}px`
})

const useFixedYAxis = computed(() => {
  if (props.option?.__fixedYAxis === false) return false
  if (props.option?.__fixedYAxis === true) return hasCartesianAxis(props.option)
  return hasCartesianAxis(props.option) && chartWidth.value !== '100%'
})

const fixedAxisWidth = computed(() => Math.max(34, parseGridLeft(scaledOption.value?.grid)))

const scrollBodyWidth = computed(() => {
  if (!useFixedYAxis.value) return chartWidth.value
  const zoom = currentZoom.value
  const explicitWidth = Number(props.option?.__minWidth || props.option?.customMinWidth)
  if (Number.isFinite(explicitWidth) && explicitWidth > 0) {
    return `${Math.max(1, Math.round(explicitWidth * zoom * pointSpacingFactor()))}px`
  }

  const count = getPointCount(props.option)
  if (!count) return '100%'
  const step = pointStep(optionPointBase(props.option, isTimeAxis(props.option) ? 48 : 52), zoom)
  const width = Math.max(1, count * step)
  return `${width}px`
})

function mapAxis(axis, mapper) {
  const axes = toArray(axis)
  const mapped = axes.map((item, index) => mapper(item || {}, index))
  return Array.isArray(axis) ? mapped : mapped[0]
}

function makeBodyOption(option) {
  const next = cloneOption(option)
  if (next.legend) next.legend = mapMaybeArray(next.legend, item => normalizeLegendOption(item, currentZoom.value, true))
  const grids = toArray(next.grid?.length ? next.grid : (next.grid || {}))
  const mappedGrids = grids.map(grid => ({ ...grid, left: 0 }))
  next.grid = Array.isArray(next.grid) ? mappedGrids : mappedGrids[0]
  next.yAxis = mapAxis(next.yAxis, axis => ({
    ...axis,
    name: '',
    axisLabel: { ...(axis.axisLabel || {}), show: false },
    axisTick: { ...(axis.axisTick || {}), show: false },
    axisLine: { ...(axis.axisLine || {}), show: false },
    splitLine: { ...(axis.splitLine || {}), show: true },
  }))
  return cleanOptionValue(next)
}

function makeAxisOption(option) {
  const next = cloneOption(option)
  const axisWidth = fixedAxisWidth.value
  const grids = toArray(next.grid?.length ? next.grid : (next.grid || {}))
  const mappedGrids = grids.map(grid => ({ ...grid, left: Math.max(28, axisWidth - 8), right: 0 }))
  next.grid = Array.isArray(next.grid) ? mappedGrids : mappedGrids[0]
  next.tooltip = { show: false }
  next.legend = mapMaybeArray(next.legend || {}, item => normalizeLegendOption(item, currentZoom.value, true))
  next.xAxis = mapAxis(next.xAxis, axis => ({
    ...axis,
    name: '',
    axisLabel: { ...(axis.axisLabel || {}), show: false },
    axisTick: { ...(axis.axisTick || {}), show: false },
    axisLine: { ...(axis.axisLine || {}), show: false },
    splitLine: { ...(axis.splitLine || {}), show: false },
  }))
  next.yAxis = mapAxis(next.yAxis, axis => ({
    ...axis,
    axisLabel: { ...(axis.axisLabel || {}), show: true },
    axisTick: { ...(axis.axisTick || {}), show: true },
    axisLine: { ...(axis.axisLine || {}), show: true },
    splitLine: { ...(axis.splitLine || {}), show: false },
  }))
  next.series = (Array.isArray(next.series) ? next.series : []).map(series => ({
    ...series,
    silent: true,
    label: { ...(series.label || {}), show: false },
    itemStyle: { ...(series.itemStyle || {}), opacity: 0 },
    lineStyle: { ...(series.lineStyle || {}), opacity: 0 },
    areaStyle: series.areaStyle ? { ...series.areaStyle, opacity: 0 } : undefined,
    symbol: 'none',
  }))
  return cleanOptionValue(next)
}

function disposeCharts() {
  chart?.dispose()
  axisChart?.dispose()
  chart = null
  axisChart = null
}

function getRenderOption() {
  return scaledOption.value
}

function bindChartEvents() {
  if (!chart) return
  chart.off('click')
  chart.on('click', params => emit('chart-click', params))
}

function initChart() {
  disposeCharts()
  if (!chartRef.value) return

  const option = getRenderOption()
  chart = echarts.init(chartRef.value, props.theme)
  chart.setOption(useFixedYAxis.value ? makeBodyOption(option) : option)
  bindChartEvents()

  if (useFixedYAxis.value && axisChartRef.value) {
    axisChart = echarts.init(axisChartRef.value, props.theme)
    axisChart.setOption(makeAxisOption(option))
  }
}

function resize() {
  chart?.resize()
  axisChart?.resize()
}

function updateOptions() {
  if (!chart) return
  const option = getRenderOption()
  chart.setOption(useFixedYAxis.value ? makeBodyOption(option) : option, true)
  if (axisChart) axisChart.setOption(makeAxisOption(option), true)
  nextTick(resize)
}

function handleZoomChange(event) {
  currentZoom.value = clampZoom(event?.detail?.zoom ?? readReactiveZoom())
  nextTick(() => {
    initChart()
    resize()
  })
}

watch(() => props.option, updateOptions, { deep: true })

watch(() => readReactiveZoom(), value => {
  const next = clampZoom(value)
  if (Math.abs(next - currentZoom.value) < 0.001) return
  currentZoom.value = next
  nextTick(() => {
    initChart()
    resize()
  })
})

watch([chartWidth, useFixedYAxis, fixedAxisWidth, scrollBodyWidth, scaledHeight, () => settings.pointSpacing], () => {
  nextTick(() => {
    initChart()
    resize()
  })
})

onMounted(() => {
  currentZoom.value = readGlobalZoom()
  initChart()
  window.addEventListener('resize', resize)
  window.addEventListener('momo:zoom-change', handleZoomChange)
  nextTick(resize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  window.removeEventListener('momo:zoom-change', handleZoomChange)
  disposeCharts()
})
</script>

<style scoped>
.base-chart-shell {
  width: 100%;
  max-width: 100%;
}
.chart-fixed-legend {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 6px 14px;
  padding: 0 8px 8px;
  font-size: calc(12px * var(--momo-global-zoom, 1));
  line-height: 1.25;
  color: var(--text-color, #334155);
}
.chart-fixed-legend-item {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  white-space: nowrap;
}
.chart-fixed-legend-marker {
  width: calc(16px * var(--momo-global-zoom, 1));
  height: calc(9px * var(--momo-global-zoom, 1));
  min-width: 9px;
  min-height: 6px;
  margin-right: 5px;
  border-radius: 999px;
}
.chart-fixed-legend-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
.chart-scroll-shell {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
}
.chart-canvas {
  min-width: 0;
  flex: 0 0 auto;
}
.chart-fixed-y-shell {
  width: 100%;
  max-width: 100%;
  display: flex;
  align-items: stretch;
}
.chart-y-axis-canvas {
  flex: 0 0 auto;
  position: sticky;
  left: 0;
  z-index: 2;
  background: var(--card-bg, #fff);
  border-right: 1px solid rgba(148, 163, 184, .18);
}
.chart-scroll-with-fixed-y {
  flex: 1 1 auto;
  min-width: 0;
}
.chart-body-canvas {
  min-width: 0;
  flex: 0 0 auto;
}
</style>
