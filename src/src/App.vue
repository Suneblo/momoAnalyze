<template>
  <div class="container">
    <HeaderCard @reload="reloadData" @copy-all="copyAll" @sync="syncNow" />
    <LogCard ref="logRef" />

    <nav class="page-tabs" aria-label="页面切换">
      <button
        v-for="page in pages"
        :key="page.key"
        type="button"
        :class="{ active: activePage === page.key }"
        @click="activePage = page.key"
      >
        {{ page.label }}
      </button>
    </nav>

    <CardGrid ref="gridRef" :page-key="activePage" />

    <div
      class="floating-action-panel floating-main-actions"
      aria-label="全局操作"
      :class="{ dragging: draggingPanel === 'main' }"
      :style="floatingPanelStyle('main')"
    >
      <button
        type="button"
        class="floating-control drag-handle"
        title="拖动调整位置，双击恢复默认"
        aria-label="拖动悬浮按钮"
        @pointerdown.prevent="startPanelDrag('main', $event)"
        @dblclick.prevent="resetPanelPosition('main')"
      >
        ⋮⋮
      </button>
      <div class="floating-action-group" aria-label="全局缩放">
        <button type="button" class="floating-control zoom-button" @click="stepZoom(-0.05)">－</button>
        <button type="button" class="floating-control zoom-value" title="点击重置为 100%" @click="resetZoom">
          {{ zoomPercent }}%
        </button>
        <button type="button" class="floating-control zoom-button" @click="stepZoom(0.05)">＋</button>
      </div>
      <div class="floating-action-divider" aria-hidden="true"></div>
      <div class="floating-action-group" aria-label="数据操作">
        <el-button class="floating-el-button" type="primary" :loading="isUpdating" @click="syncNow">数据更新</el-button>
        <el-button class="floating-el-button" :loading="isReloading" @click="reloadData">重新加载</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount, provide } from 'vue'
import { useSettingsStore } from './stores/settingsStore'
import { useCardStore, PAGE_REGISTRY } from './stores/cardStore'
import { useDataStore } from './stores/dataStore'
import { usePredictionStore } from './stores/predictionStore'
import { useApi } from './composables/useApi'
import { buildAllMarkdown, copyToClipboard } from './composables/useCopy'
import HeaderCard from './components/layout/HeaderCard.vue'
import LogCard from './components/layout/LogCard.vue'
import CardGrid from './components/layout/CardGrid.vue'

const settings = useSettingsStore()
const cardStore = useCardStore()
const dataStore = useDataStore()
const prediction = usePredictionStore()
const api = useApi()
const logRef = ref(null)
const gridRef = ref(null)
const pages = PAGE_REGISTRY
const activePage = ref('today')
const isUpdating = ref(false)
const isReloading = ref(false)
const ZOOM_STORAGE_KEY = 'momo-global-zoom'
const FLOATING_PANEL_STORAGE_KEY = 'momo-floating-panel-positions'
const globalZoom = ref(readSavedZoom())
const floatingPositions = ref(readSavedFloatingPositions())
const draggingPanel = ref('')
let dragState = null
provide('momoGlobalZoom', globalZoom)
const zoomPercent = computed(() => Math.round(globalZoom.value * 100))

function log(msg) {
  logRef.value?.log(msg)
}
provide('log', log)

function clampZoom(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 1
  return Math.min(1.4, Math.max(0.65, numeric))
}

function readSavedZoom() {
  try {
    return clampZoom(localStorage.getItem(ZOOM_STORAGE_KEY) || 1)
  } catch {
    return 1
  }
}

function applyZoom(value) {
  const next = clampZoom(value)
  globalZoom.value = next
  document.documentElement.style.setProperty('--momo-global-zoom', next.toFixed(2))
  window.dispatchEvent(new CustomEvent('momo:zoom-change', { detail: { zoom: next } }))
  try {
    localStorage.setItem(ZOOM_STORAGE_KEY, String(next))
  } catch {
    // ignore private browsing or storage permission errors
  }
  window.dispatchEvent(new Event('resize'))
}

function stepZoom(delta) {
  applyZoom(Math.round((globalZoom.value + delta) * 100) / 100)
}

function resetZoom() {
  applyZoom(1)
}

function readSavedFloatingPositions() {
  try {
    const raw = localStorage.getItem(FLOATING_PANEL_STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return {}
    return parsed
  } catch {
    return {}
  }
}

function saveFloatingPositions() {
  try {
    localStorage.setItem(FLOATING_PANEL_STORAGE_KEY, JSON.stringify(floatingPositions.value))
  } catch {
    // ignore private browsing or storage permission errors
  }
}

function floatingPanelStyle(panelKey) {
  const pos = floatingPositions.value?.[panelKey]
  if (!pos || !Number.isFinite(pos.right) || !Number.isFinite(pos.bottom)) return {}
  return {
    right: `${Math.max(0, pos.right)}px`,
    bottom: `${Math.max(0, pos.bottom)}px`,
  }
}

function clampFloatingPosition(right, bottom, width = 0, height = 0) {
  const margin = 8
  const maxRight = Math.max(margin, window.innerWidth - width - margin)
  const maxBottom = Math.max(margin, window.innerHeight - height - margin)
  return {
    right: Math.min(maxRight, Math.max(margin, right)),
    bottom: Math.min(maxBottom, Math.max(margin, bottom)),
  }
}

function startPanelDrag(panelKey, event) {
  const panelEl = event.currentTarget?.closest?.('.floating-action-panel')
  if (!panelEl) return
  const rect = panelEl.getBoundingClientRect()
  const startRight = window.innerWidth - rect.right
  const startBottom = window.innerHeight - rect.bottom
  dragState = {
    panelKey,
    startX: event.clientX,
    startY: event.clientY,
    startRight,
    startBottom,
    width: rect.width,
    height: rect.height,
  }
  draggingPanel.value = panelKey
  window.addEventListener('pointermove', onPanelDragMove, { passive: false })
  window.addEventListener('pointerup', stopPanelDrag, { once: true })
  window.addEventListener('pointercancel', stopPanelDrag, { once: true })
}

function onPanelDragMove(event) {
  if (!dragState) return
  event.preventDefault()
  const dx = event.clientX - dragState.startX
  const dy = event.clientY - dragState.startY
  const next = clampFloatingPosition(
    dragState.startRight - dx,
    dragState.startBottom - dy,
    dragState.width,
    dragState.height,
  )
  floatingPositions.value = {
    ...floatingPositions.value,
    [dragState.panelKey]: next,
  }
}

function stopPanelDrag() {
  if (dragState) saveFloatingPositions()
  dragState = null
  draggingPanel.value = ''
  window.removeEventListener('pointermove', onPanelDragMove)
}

function resetPanelPosition(panelKey) {
  const next = { ...floatingPositions.value }
  delete next[panelKey]
  floatingPositions.value = next
  saveFloatingPositions()
}

function clampSavedFloatingPositions() {
  const panels = document.querySelectorAll('.floating-action-panel')
  let changed = false
  const nextPositions = { ...floatingPositions.value }
  panels.forEach((panelEl) => {
    const panelKey = 'main'
    const current = nextPositions[panelKey]
    if (!current) return
    const rect = panelEl.getBoundingClientRect()
    const clamped = clampFloatingPosition(current.right, current.bottom, rect.width, rect.height)
    if (clamped.right !== current.right || clamped.bottom !== current.bottom) {
      nextPositions[panelKey] = clamped
      changed = true
    }
  })
  if (changed) {
    floatingPositions.value = nextPositions
    saveFloatingPositions()
  }
}

onMounted(async () => {
  applyZoom(globalZoom.value)
  window.addEventListener('resize', clampSavedFloatingPositions)
  setTimeout(clampSavedFloatingPositions, 0)
  log('正在加载配置...')
  settings.load()
  cardStore.load()
  prediction.loadCache(settings)

  log('正在加载数据...')
  await reloadData()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', clampSavedFloatingPositions)
  window.removeEventListener('pointermove', onPanelDragMove)
})

async function reloadData() {
  isReloading.value = true
  try {
    const data = await api.fetchDashboardPage(0, 60, false, 'asc')
    if (data?.days) {
      dataStore.setDashboardData(data)
      log(`加载完成：扫描 ${data.total || 0} 天，当前范围 ${dataStore.renderMeta.start || '-'} ~ ${dataStore.renderMeta.end || '-'}，显示 ${dataStore.renderMeta.dataDays || 0} 天`)
    }
  } finally {
    isReloading.value = false
  }
}

async function copyAll() {
  const md = buildAllMarkdown()
  await copyToClipboard(md, '已复制全部选中内容')
  log('已复制全部选中内容')
}

async function syncNow() {
  if (isUpdating.value) return
  isUpdating.value = true
  log('正在联网获取最新数据...')
  try {
    const result = await api.syncFromApi()
    if (result?.success) {
      log('联网同步完成，正在刷新...')
      await reloadData()
    } else {
      log('联网同步失败：' + (result?.error || '未知错误'))
    }
  } catch (e) {
    log('联网同步失败：' + e.message)
  } finally {
    isUpdating.value = false
  }
}
</script>

<style>
@import './assets/main.css';

:root {
  --bg: #f3f4f6;
  --card-bg: #ffffff;
  --text-main: #1f2937;
  --text-muted: #6b7280;
  --border: #e5e7eb;
  --primary: #3b82f6;
  --primary-hover: #2563eb;
  --shadow: 0 4px 6px -1px rgba(0,0,0,.05), 0 2px 4px -2px rgba(0,0,0,.05);
}

.container {
  max-width: 100%;
  margin: 0 auto;
  padding: calc(16px * var(--momo-global-zoom, 1));
}

.page-tabs {
  position: sticky;
  top: 0;
  z-index: 20;
  display: flex;
  gap: calc(8px * var(--momo-global-zoom, 1));
  align-items: center;
  margin: 0 0 calc(16px * var(--momo-global-zoom, 1));
  padding: calc(8px * var(--momo-global-zoom, 1));
  background: rgba(243, 244, 246, .92);
  backdrop-filter: blur(8px);
  border: 1px solid var(--border);
  border-radius: calc(14px * var(--momo-global-zoom, 1));
  overflow-x: auto;
}

.page-tabs button {
  border: 1px solid var(--border);
  background: var(--card-bg);
  color: var(--text-muted);
  border-radius: 999px;
  padding: calc(7px * var(--momo-global-zoom, 1)) calc(16px * var(--momo-global-zoom, 1));
  font-size: calc(14px * var(--momo-global-zoom, 1));
  cursor: pointer;
  white-space: nowrap;
}

.page-tabs button.active {
  color: #0f766e;
  border-color: #14b8a6;
  background: rgba(20, 184, 166, .1);
  font-weight: 700;
}



.floating-action-panel {
  position: fixed;
  right: calc(18px * var(--momo-global-zoom, 1));
  bottom: calc(18px * var(--momo-global-zoom, 1));
  z-index: 3000;
  display: flex;
  align-items: center;
  gap: calc(8px * var(--momo-global-zoom, 1));
  padding: calc(8px * var(--momo-global-zoom, 1));
  border: 1px solid rgba(148, 163, 184, .35);
  border-radius: 999px;
  background: rgba(255, 255, 255, .94);
  box-shadow: 0 14px 28px rgba(15, 23, 42, .18);
  backdrop-filter: blur(8px);
}

.floating-main-actions {
  max-width: calc(100vw - 24px);
}

.floating-action-panel.dragging {
  cursor: grabbing;
  user-select: none;
}

.floating-action-group {
  display: flex;
  align-items: center;
  gap: calc(6px * var(--momo-global-zoom, 1));
}

.floating-action-divider {
  width: 1px;
  height: calc(24px * var(--momo-global-zoom, 1));
  background: rgba(148, 163, 184, .35);
  flex: 0 0 auto;
}

.floating-control,
.floating-el-button.el-button {
  height: var(--momo-control-height);
  min-height: var(--momo-control-height);
  border-radius: 999px;
  font-size: var(--momo-small-font-size);
  font-weight: 700;
  line-height: 1;
}

.drag-handle {
  min-width: calc(30px * var(--momo-global-zoom, 1));
  border: 1px dashed rgba(148, 163, 184, .55);
  background: rgba(248, 250, 252, .88);
  color: #64748b;
  cursor: grab;
  touch-action: none;
  user-select: none;
  padding: 0 calc(8px * var(--momo-global-zoom, 1));
}

.drag-handle:hover {
  border-color: #14b8a6;
  color: #0f766e;
  background: rgba(20, 184, 166, .08);
}

.dragging .drag-handle {
  cursor: grabbing;
}

.zoom-button,
.zoom-value {
  min-width: var(--momo-control-height);
  border: 1px solid rgba(148, 163, 184, .45);
  background: #fff;
  color: var(--text-main);
  cursor: pointer;
  padding: 0 calc(10px * var(--momo-global-zoom, 1));
}

.zoom-button:hover,
.zoom-value:hover {
  border-color: #14b8a6;
  color: #0f766e;
  background: rgba(20, 184, 166, .08);
}

.zoom-value {
  min-width: calc(58px * var(--momo-global-zoom, 1));
  color: #0f766e;
}

.floating-el-button.el-button {
  padding: 0 calc(14px * var(--momo-global-zoom, 1));
  margin-left: 0;
}

@media (max-width: 640px) {
  .floating-action-panel {
    right: calc(10px * var(--momo-global-zoom, 1));
    bottom: calc(10px * var(--momo-global-zoom, 1));
    gap: calc(6px * var(--momo-global-zoom, 1));
    padding: calc(6px * var(--momo-global-zoom, 1));
    border-radius: calc(18px * var(--momo-global-zoom, 1));
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .floating-action-group {
    gap: calc(5px * var(--momo-global-zoom, 1));
  }

  .floating-action-divider {
    display: none;
  }

  .floating-control,
  .floating-el-button.el-button {
    height: calc(30px * var(--momo-global-zoom, 1));
    min-height: calc(30px * var(--momo-global-zoom, 1));
    font-size: calc(12px * var(--momo-global-zoom, 1));
  }

  .drag-handle {
    min-width: calc(28px * var(--momo-global-zoom, 1));
    padding: 0 calc(7px * var(--momo-global-zoom, 1));
  }

  .zoom-button,
  .zoom-value {
    min-width: calc(30px * var(--momo-global-zoom, 1));
    padding: 0 calc(8px * var(--momo-global-zoom, 1));
  }

  .zoom-value {
    min-width: calc(50px * var(--momo-global-zoom, 1));
  }

  .floating-el-button.el-button {
    padding: 0 calc(10px * var(--momo-global-zoom, 1));
  }
}

</style>
