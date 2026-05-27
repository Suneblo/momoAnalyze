<template>
  <div class="probability-3d-wrapper">
    <div ref="plotRef" class="probability-3d-plot" :style="{ height: `${scaledHeight}px` }"></div>
  </div>
</template>

<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  model: { type: Object, default: null },
  rating: { type: String, default: 'good' },
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

function mapValue(maybeMap, key) {
  if (!maybeMap) return undefined
  if (typeof maybeMap.get === 'function') return maybeMap.get(key)
  return maybeMap[key]
}

function ratingLabel(rating) {
  return { again: '忘记', hard: '模糊', good: '认识', easy: 'Easy' }[rating] || rating
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

function cellSource(cell) {
  if (!cell) return '-'
  if (cell.fallbackMethod === 'self' || cell.usedNearestFallback === false) return '本分桶'
  if (cell.fallbackMethod === 'diffusion-fill') return `真实格子扩散估计${cell.diffusionRadius ? `（${Math.round(Number(cell.diffusionRadius))}圈）` : ''}`
  if (cell.fallbackMethod === 'memoryFallback') return '同记忆持久度分桶估计'
  if (cell.fallbackMethod === 'globalFallback') return '全局概率估计'
  if (cell.fallbackMethod === 'noData') return '无数据格子'
  return cell.fallbackMethod || '-'
}

function buildPlotData() {
  const model = props.model
  const memoryBuckets = model?.memoryBuckets || []
  const studyBuckets = model?.studyBuckets || []
  if (!model || !memoryBuckets.length || !studyBuckets.length) return null

  const x = studyBuckets.map(item => item.label)
  const y = memoryBuckets.map(item => item.label)
  const z = []
  const customdata = []
  const rating = props.rating
  const label = ratingLabel(rating)

  for (const memory of memoryBuckets) {
    const row = []
    const customRow = []
    for (const study of studyBuckets) {
      const cell = mapValue(model.byKey, `${memory.key}|${study.key}`)
      const value = pct(cell, rating)
      row.push(value)
      const n = Number(cell?.n || cell?.total || 0)
      customRow.push([
        `记忆持久度：${memory.label}`,
        `学习次数：${study.label}`,
        `当前 ${label}：${value.toFixed(1)}%`,
        `n：${Number.isFinite(n) ? Math.round(n) : 0}`,
        `忘/模/认/Easy：${pct(cell, 'again').toFixed(1)} / ${pct(cell, 'hard').toFixed(1)} / ${pct(cell, 'good').toFixed(1)} / ${pct(cell, 'easy').toFixed(1)}%`,
        `来源：${cellSource(cell)}`,
      ].join('<br>'))
    }
    z.push(row)
    customdata.push(customRow)
  }

  return { x, y, z, customdata, label, sampleCount: model.reviewSampleCount || model.globalCounts?.n || 0 }
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
        backgroundcolor: 'rgba(248,250,252,.75)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      yaxis: {
        title: { text: '记忆持久度', font: { size: fontSize } },
        tickfont: { size: axisFontSize },
        backgroundcolor: 'rgba(248,250,252,.75)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      zaxis: {
        title: { text: `${data.label}%`, font: { size: fontSize } },
        tickfont: { size: axisFontSize },
        range: [0, 100],
        backgroundcolor: 'rgba(248,250,252,.55)',
        gridcolor: 'rgba(148,163,184,.32)',
        zerolinecolor: 'rgba(148,163,184,.45)',
      },
      camera: {
        eye: { x: 1.7, y: 1.65, z: 0.82 },
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
    customdata: data.customdata,
    hovertemplate: '%{customdata}<extra></extra>',
    colorscale: 'Blues',
    cmin: 0,
    cmax: 100,
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
      title: `${data.label}%`,
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

watch(() => [props.model, props.rating, currentZoom.value, scaledHeight.value], () => nextTick(renderPlot), { deep: true })

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
