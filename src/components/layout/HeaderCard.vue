<template>
  <div class="card header-card">
    <div>
      <h1>墨墨历史统计</h1>
      <div class="info-text">数据来源：SQLite 本地数据库</div>
    </div>
    <div class="header-actions">
      <el-button @click="$emit('reload')">重新加载</el-button>
      <el-button @click="$emit('copy-all')">复制全部选中内容</el-button>
      <el-button @click="$emit('copy-latest-diff')">复制最近时间点变化</el-button>
      <el-button @click="$emit('sync')">联网获取最新数据</el-button>
      <el-button type="warning" plain @click="showSettings = !showSettings">设置</el-button>
    </div>

    <!-- Settings Panel -->
    <div v-show="showSettings" class="settings-panel">
      <details class="settings-group" open>
        <summary>范围与图表</summary>
        <div class="toolbar compact-settings-toolbar">
          <label>范围
            <select v-model="settings.rangePreset" @change="settings.save()">
              <option value="7">最近 7 天</option>
              <option value="30">最近 30 天</option>
              <option value="90">最近 90 天</option>
              <option value="all">全部</option>
            </select>
          </label>
          <label>自定义天数 <input v-model.number="settings.customDays" type="number" min="1" @change="settings.save()"></label>
          <label>横轴间距 <input v-model.number="settings.pointSpacing" type="number" step="0.1" @change="settings.save()"></label>
          <label>学习时长窗口 <input v-model.number="settings.studyTimeWindowDays" type="number" min="1" @change="settings.save()"></label>
          <label><input v-model="settings.hideEmptyDays" type="checkbox" @change="settings.save()"> 跳过无数据日</label>
          <el-button type="primary" size="small" @click="$emit('reload'); settings.save()">应用</el-button>
        </div>
      </details>

      <details class="settings-group">
        <summary>卡片顺序</summary>
        <div class="settings-page-grid compact-grid">
          <section v-for="group in pageGroups" :key="'order-' + group.key" class="settings-page-block">
            <div class="settings-page-title">{{ group.label }}</div>
            <div class="card-order-list vertical">
              <div
                v-for="card in group.cards"
                :key="card.id"
                class="card-order-item draggable-row"
                :class="{ dragging: draggedCard?.id === card.id, fixed: !canDragCard(card) }"
                :draggable="canDragCard(card)"
                @dragstart="startCardDrag(group.key, card.id, $event)"
                @dragover.prevent
                @drop="dropCard(group.key, card.id)"
                @dragend="endCardDrag"
              >
                <span class="drag-grip" title="拖动排序">⋮⋮</span>
                <span>{{ card.id }}</span>
              </div>
            </div>
          </section>
        </div>
      </details>

      <details class="settings-group">
        <summary>显示与总复制</summary>
        <div class="settings-page-grid compact-grid">
          <section v-for="group in pageGroups" :key="'visible-copy-' + group.key" class="settings-page-block">
            <div class="settings-page-title">{{ group.label }}</div>
            <div class="visibility-copy-list">
              <div v-for="card in group.cards" :key="'vc-' + card.id" class="visibility-copy-row">
                <span class="card-name">{{ card.id }}</span>
                <label><input type="checkbox" :checked="cardStore.visible[card.id]" @change="e => { cardStore.visible[card.id] = e.target.checked; cardStore.save() }"> 显示</label>
                <label :class="{ muted: !canCopyCard(card) }">
                  <input
                    type="checkbox"
                    :disabled="!canCopyCard(card)"
                    :checked="cardStore.copyEnabled[card.id]"
                    @change="e => { cardStore.copyEnabled[card.id] = e.target.checked; cardStore.save() }"
                  >
                  总复制
                </label>
              </div>
            </div>
          </section>
        </div>
      </details>

      <details class="settings-group">
        <summary>复习新学堆叠顺序</summary>
        <div class="stack-order-list">
          <div
            v-for="item in reviewPlanStackItems"
            :key="item.key"
            class="stack-order-item draggable-row"
            :class="{ dragging: draggedStackKey === item.key }"
            draggable="true"
            @dragstart="startStackDrag(item.key, $event)"
            @dragover.prevent
            @drop="dropStackItem(item.key)"
            @dragend="endStackDrag"
          >
            <span class="drag-grip" title="拖动排序">⋮⋮</span>
            <span class="stack-color" :style="{ background: item.color }"></span>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </details>

      <div class="settings-footer">
        <el-button type="danger" plain size="small" @click="clearCache">清空缓存并刷新</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useSettingsStore, REVIEW_PLAN_STACK_ITEMS, normalizeReviewPlanStackOrder } from '@/stores/settingsStore'
import { useCardStore, PAGE_REGISTRY, FIXED_FIRST_CARD_ID } from '@/stores/cardStore'

defineEmits(['reload', 'copy-all', 'copy-latest-diff', 'sync'])

const settings = useSettingsStore()
const cardStore = useCardStore()
const showSettings = ref(false)
const draggedCard = ref(null)
const draggedStackKey = ref('')

const reviewPlanStackItems = computed(() => {
  const order = normalizeReviewPlanStackOrder(settings.reviewPlanStackOrder)
  return order
    .map(key => REVIEW_PLAN_STACK_ITEMS.find(item => item.key === key))
    .filter(Boolean)
})

const pageGroups = computed(() => PAGE_REGISTRY.map(page => ({
  ...page,
  cards: cardStore.getCardsByPage(page.key),
})))

function canDragCard(card) {
  if (card.id === FIXED_FIRST_CARD_ID) return false
  return true
}

function canCopyCard(card) {
  return card.copyEnabled !== false && !!card.copyKey
}

function startCardDrag(pageKey, cardId, event) {
  if (cardId === FIXED_FIRST_CARD_ID) return
  draggedCard.value = { pageKey, id: cardId }
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', cardId)
  }
}

function dropCard(pageKey, targetId) {
  const source = draggedCard.value
  if (!source || source.pageKey !== pageKey || source.id === targetId) return
  const ids = cardStore.getOrderedCards(pageKey).map(card => card.id)
  const from = ids.indexOf(source.id)
  const to = ids.indexOf(targetId)
  if (from < 0 || to < 0) return
  ids.splice(from, 1)
  const insertAt = ids.indexOf(targetId)
  ids.splice(insertAt < 0 ? to : insertAt, 0, source.id)
  cardStore.reorderCardsInPage(pageKey, ids)
}

function endCardDrag() {
  draggedCard.value = null
}

function startStackDrag(key, event) {
  draggedStackKey.value = key
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', key)
  }
}

function dropStackItem(targetKey) {
  const sourceKey = draggedStackKey.value
  if (!sourceKey || sourceKey === targetKey) return
  const order = normalizeReviewPlanStackOrder(settings.reviewPlanStackOrder)
  const from = order.indexOf(sourceKey)
  const to = order.indexOf(targetKey)
  if (from < 0 || to < 0) return
  order.splice(from, 1)
  const insertAt = order.indexOf(targetKey)
  order.splice(insertAt < 0 ? to : insertAt, 0, sourceKey)
  settings.reviewPlanStackOrder = normalizeReviewPlanStackOrder(order)
  settings.save()
}

function endStackDrag() {
  draggedStackKey.value = ''
}

function clearCache() {
  if (confirm('清除所有缓存并刷新？')) {
    localStorage.clear()
    location.reload()
  }
}
</script>

<style scoped>
.header-card {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.header-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.settings-panel {
  width: 100%;
  margin-top: 10px;
  border-top: 1px solid var(--border);
  padding-top: 8px;
  max-height: min(70vh, 620px);
  overflow: auto;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  margin-bottom: 8px;
}
.toolbar label { font-size: 13px; display: flex; align-items: center; gap: 4px; }
.toolbar input, .toolbar select {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.compact-settings-toolbar {
  gap: 8px 12px;
  margin: 8px 0 0;
}
.compact-settings-toolbar input[type="number"] {
  width: 72px;
}
.settings-group {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f8fafc;
  margin-top: 8px;
  padding: 0;
}
.settings-group summary {
  cursor: pointer;
  list-style: none;
  padding: 8px 10px;
  color: var(--text-main);
  font-size: 13px;
  font-weight: 700;
}
.settings-group summary::-webkit-details-marker {
  display: none;
}
.settings-group summary::before {
  content: '›';
  display: inline-block;
  margin-right: 7px;
  color: #64748b;
  transform: rotate(0deg);
  transition: transform .15s ease;
}
.settings-group[open] summary::before {
  transform: rotate(90deg);
}
.settings-group > .toolbar,
.settings-group > .settings-page-grid,
.settings-group > .stack-order-list {
  padding: 0 10px 10px;
}
.settings-section-title {
  color: var(--text-main);
  font-weight: 700;
  margin-bottom: 8px;
}
.settings-page-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 10px;
}
.settings-page-grid.compact-grid {
  grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
}
.settings-page-block {
  min-width: 0;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
}
.settings-page-title {
  margin-bottom: 8px;
  color: #0f766e;
  font-size: 13px;
  font-weight: 700;
}
.card-order-list,
.check-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
}
.card-order-list.vertical,
.check-list.vertical {
  flex-direction: column;
  flex-wrap: nowrap;
  align-items: stretch;
}
.card-order-item {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 7px;
  padding: 5px 7px;
  font-size: 13px;
}
.card-order-item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.draggable-row {
  cursor: grab;
  user-select: none;
}
.draggable-row.dragging {
  opacity: .45;
}
.draggable-row.fixed {
  cursor: default;
  background: #f1f5f9;
}
.drag-grip {
  color: #94a3b8;
  font-size: 12px;
  line-height: 1;
  text-align: center;
}
.check-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 24px;
  font-size: 13px;
  cursor: pointer;
}
.empty-copy-tip {
  color: var(--text-muted);
  font-size: 12px;
}
.settings-help-text {
  margin-bottom: 8px;
  color: var(--text-muted);
  font-size: 12px;
}
.stack-order-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 360px;
}
.stack-order-item {
  display: grid;
  grid-template-columns: 20px 14px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}
.stack-color {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, .15);
}
.visibility-copy-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.visibility-copy-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 10px;
  padding: 5px 7px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: #fff;
  font-size: 13px;
}
.visibility-copy-row .card-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.visibility-copy-row label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.muted {
  color: var(--text-muted);
}
.settings-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 8px;
}
</style>
