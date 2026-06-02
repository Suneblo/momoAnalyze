<template>
  <div class="probability-3d-wrapper">
    <div ref="plotRef" class="probability-3d-plot" :style="{ height: `${scaledHeight}px` }"></div>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { buildProbabilityTableRows } from '@/utils/dashboardPrediction'

const props = defineProps({
  model: { type: Object, default: null },
  rating: { type: String, default: 'good' },
  ratings: { type: Array, default: () => [] },
  height: { type: Number, default: 460 },
})

const plotRef = ref(null)
const injectedGlobalZoom = inject('momoGlobalZoom', null)
let Plotly = null
let resizeObserver = null

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

const currentZoom = computed(() => {
  const injected = injectedGlobalZoom && typeof injectedGlobalZoom === 'object' && 'value' in injectedGlobalZoom
    ? injectedGlobalZoom.value
    : undefined
  return clampZoom(injected ?? readGlobalZoom())
})

const scaledHeight = computed(() => Math.max(280, Math.round((Number(props.height) || 460) * currentZoom.value)))

function ratingLabel(rating) {
  return { again: '忘记', hard: '模糊', good: '认识', easy: 'Easy' }[rating] || rating
}

function activeRatings() {
  const selected = Array.isArray(props.ratings) && props.ratings.length ? props.ratings : [props.rating]
  return selected.filter(rating => ['again', 'hard', 'good', 'easy'].includes(String(rating)))
}

function ratingsLabel(ratings) {
  return ratings.map(ratingLabel).join('+') || '认识'
}

function cellProbability(cell, rating) {
  if (!cell) return 0
  if (cell.probs && Number.isFinite(Number(cell.probs[rating]))) return Math.max(0, Math.min(1, Number(cell.probs[rating])))
  const dist = Array.isArray(cell.distribution) ? cell.distribution : []
  const found = dist.find(item => item.rating === rating)
  if (found) return Math.max(0, Math.min(1, Number(found.prob) || 0))
  const n = Number(cell.n || cell.total || cell.counts?.n || 0)
  if (!n) return 0
  return Math.max(0, Math.min(1, Number(cell[rating] ?? cell.counts?.[rating] ?? 0) / n))
}

function pct(cell, rating) {
  return Math.round(cellProbability(cell, rating) * 1000) / 10
}

function combinedPct(cell, ratings) {
  const value = ratings.reduce((sum, rating) => sum + cellProbability(cell, rating), 0)
  return Math.round(Math.max(0, Math.min(1, value)) * 1000) / 10
}

function buildPlotData() {
  const model = props.model
  const memoryBuckets = model?.memoryBuckets || []
  const tableRows = buildProbabilityTableRows(model)
  const studyBuckets = tableRows.studyRows || model?.studyBuckets || []
  if (!model || !memoryBuckets.length || !studyBuckets.length) return null

  const x = studyBuckets.map((_, index) => index)
  const y = memoryBuckets.map((_, index) => index)
  const xLabels = studyBuckets.map(item => item.label)
  const yLabels = memoryBuckets.map(item => item.label)
  const z = memoryBuckets.map(() => studyBuckets.map(() => null))
  const hovertext = memoryBuckets.map(() => studyBuckets.map(() => ''))
  const ratings = activeRatings()
  const label = ratingsLabel(ratings)
  const probabilityValues = []

  tableRows.forEach((row, rowIndex) => {
    row.cells.forEach((cell, colIndex) => {
      if (!cell || cell.skip || cell.insufficient) return
      const raw = cell.raw || cell
      const n = Number(raw?.n || raw?.total || 0)
      if (!Number.isFinite(n) || n <= 0) return
      const value = combinedPct(raw, ratings)
      if (Number.isFinite(value)) probabilityValues.push(value)
      const text = [
        `记忆持久度：${cell.memoryRangeLabel || row.memory?.label || yLabels[rowIndex]}`,
        `学习次数：${cell.label || xLabels[colIndex]}`,
        `当前 ${label}合并概率：${value.toFixed(1)}%`,
        `n：${Number.isFinite(n) ? Math.round(n) : 0}`,
        `忘/模/认/Easy：${pct(raw, 'again').toFixed(1)} / ${pct(raw, 'hard').toFixed(1)} / ${pct(raw, 'good').toFixed(1)} / ${pct(raw, 'easy').toFixed(1)}%`,
        cell.rowspan > 1 ? `区域：向下合并 ${cell.rowspan} 个记忆持久度桶` : '区域：单格',
      ].join('<br>')
      for (let offset = 0; offset < Math.max(1, Number(cell.rowspan) || 1); offset += 1) {
        const yIndex = rowIndex + offset
        if (!z[yIndex]) continue
        z[yIndex][colIndex] = value
        hovertext[yIndex][colIndex] = text
      }
    })
  })

  const probabilityMin = probabilityValues.length ? Math.min(...probabilityValues) : 0
  const probabilityMax = probabilityValues.length ? Math.max(...probabilityValues) : 100
  const rangePad = probabilityMin === probabilityMax ? Math.max(0.5, Math.abs(probabilityMin) * 0.04) : 0
  const probabilityRange = [
    Math.max(0, probabilityMin - rangePad),
    Math.min(100, probabilityMax + rangePad),
  ]
  if (probabilityRange[0] === probabilityRange[1]) {
    probabilityRange[0] = Math.max(0, probabilityRange[0] - 0.5)
    probabilityRange[1] = Math.min(100, probabilityRange[1] + 0.5)
  }

  return {
    x,
    y,
    xLabels,
    yLabels,
    z,
    hovertext,
    label,
    probabilityMin,
    probabilityMax,
    probabilityRange,
    sampleCount: model.reviewSampleCount || model.globalCounts?.n || 0,
  }
}

async function ensurePlotly() {
  if (Plotly) return Plotly
  const mod = await import('plotly.js-dist-min')
  Plotly = mod.default || mod
  return Plotly
}

function makeLayout(data) {
  const zoom = currentZoom.value
  const fontSize = Math.max(9, Math.round(12 * zoom))
  const axisFontSize = Math.max(8, Math.round(10 * zoom))
  return {
    autosize: true,
    margin: {
      l: Math.max(0, Math.round(8 * zoom)),
      r: Math.max(8, Math.round(16 * zoom)),
      t: Math.max(8, Math.round(12 * zoom)),
      b: Math.max(8, Math.round(12 * zoom)),
    },
    paper_bgcolor: 'rgba(255,255,255,0)',
    plot_bgcolor: 'rgba(255,255,255,0)',
    font: { size: fontSize, color: '#334155' },
    scene: {
      xaxis: {
        title: { text: '学习次数', font: { size: fontSize } },
        tickfont: { size: axisFontSize },
        tickmode: 'array',
        tickvals: data.x,
        ticktext: data.xLabels,
        backgroundcolor: 'rgba(248,250,252,.75)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      yaxis: {
        title: { text: '记忆持久度', font: { size: fontSize } },
        tickfont: { size: axisFontSize },
        tickmode: 'array',
        tickvals: data.y,
        ticktext: data.yLabels,
        backgroundcolor: 'rgba(248,250,252,.75)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      zaxis: {
        title: { text: `${data.label}% / n`, font: { size: fontSize } },
        tickfont: { size: axisFontSize },
        range: data.probabilityRange,
        backgroundcolor: 'rgba(248,250,252,.55)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      camera: {
        eye: { x: -1.7, y: -1.65, z: 0.82 },
      },
      aspectratio: { x: 1.45, y: 1.05, z: 0.62 },
    },
  }
}

function makeTrace(data) {
  return {
    type: 'surface',
    x: data.x,
    y: data.y,
    z: data.z,
    hovertext: data.hovertext,
    hoverinfo: 'text',
    connectgaps: false,
    colorscale: 'Blues',
    cmin: data.probabilityRange[0],
    cmax: data.probabilityRange[1],
    opacity: 0.92,
    contours: {
      z: {
        show: true,
        usecolormap: true,
        highlightcolor: '#1d4ed8',
        project: { z: true },
      },
      x: { show: true, color: 'rgba(37,99,235,.22)' },
      y: { show: true, color: 'rgba(37,99,235,.22)' },
    },
    colorbar: {
      title: `${data.label}% / n`,
      titleside: 'top',
      len: 0.74,
      thickness: Math.max(10, Math.round(14 * currentZoom.value)),
      tickfont: { size: Math.max(8, Math.round(10 * currentZoom.value)) },
    },
  }
}

async function renderPlot() {
  const el = plotRef.value
  const data = buildPlotData()
  if (!el || !data) return
  const plotly = await ensurePlotly()
  const config = {
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'],
  }
  await plotly.react(el, [makeTrace(data)], makeLayout(data), config)
}

function resizePlot() {
  if (Plotly && plotRef.value) Plotly.Plots.resize(plotRef.value)
}

watch(() => [props.model, props.rating, props.ratings, currentZoom.value, scaledHeight.value], () => nextTick(renderPlot), { deep: true })

onMounted(() => {
  nextTick(renderPlot)
  if (typeof ResizeObserver !== 'undefined' && plotRef.value) {
    resizeObserver = new ResizeObserver(() => resizePlot())
    resizeObserver.observe(plotRef.value)
  }
  window.addEventListener('resize', resizePlot)
  window.addEventListener('momo:zoom-change', renderPlot)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizePlot)
  window.removeEventListener('momo:zoom-change', renderPlot)
  resizeObserver?.disconnect()
  if (Plotly && plotRef.value) Plotly.purge(plotRef.value)
})
</script>

<style scoped>
.probability-3d-wrapper {
  width: 100%;
  overflow: hidden;
  border-radius: 10px;
  background: #fff;
  border: 1px solid rgba(148, 163, 184, .25);
}
.probability-3d-plot {
  width: 100%;
  min-height: 280px;
}
</style>
