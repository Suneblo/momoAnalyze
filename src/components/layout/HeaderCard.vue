<template>
  <div class="card header-card">
    <div>
      <h1>墨墨历史统计</h1>
      <div class="info-text">数据来源：SQLite 本地数据库</div>
    </div>
    <div class="header-actions">
      <el-button @click="$emit('reload')">重新加载</el-button>
      <el-button @click="$emit('copy-all')">复制全部选中内容</el-button>
      <el-button @click="$emit('sync')">联网获取最新数据</el-button>
      <el-button type="warning" @click="showSettings = !showSettings">⚙ 设置</el-button>
      <span class="status-badge ok">就绪</span>
      <el-button type="danger" plain @click="clearCache">清空缓存并刷新</el-button>
    </div>

    <!-- Settings Panel -->
    <div v-show="showSettings" class="settings-panel">
      <div class="toolbar">
        <label>范围
          <select v-model="settings.rangePreset" @change="settings.save()">
            <option value="7">最近 7 天</option>
            <option value="30">最近 30 天</option>
            <option value="90">最近 90 天</option>
            <option value="all">全部</option>
          </select>
        </label>
        <label>自定义天数 <input v-model.number="settings.customDays" type="number" min="1" style="width:70px" @change="settings.save()"></label>
        <label>横轴间距系数 <input v-model.number="settings.pointSpacing" type="number" step="0.1" style="width:70px" @change="settings.save()"></label>
        <label>学习时长 <input v-model.number="settings.studyTimeWindowDays" type="number" min="1" style="width:70px" @change="settings.save()"></label>
        <label><input v-model="settings.hideEmptyDays" type="checkbox" @change="settings.save()"> 跳过无数据日</label>
        <el-button type="primary" size="small" @click="$emit('reload'); settings.save()">应用范围</el-button>
      </div>

      <div class="section">
        <div class="settings-section-title">复习新学堆叠顺序（从上到下）</div>
        <div class="settings-help-text">控制“复习情况 → 复习新学”模式中单根柱子的纵向堆叠顺序。</div>
        <div class="stack-order-list">
          <div v-for="(item, index) in reviewPlanStackItems" :key="item.key" class="stack-order-item">
            <button :disabled="index === 0" @click="settings.moveReviewPlanStackItem(index, -1)">↑</button>
            <button :disabled="index === reviewPlanStackItems.length - 1" @click="settings.moveReviewPlanStackItem(index, 1)">↓</button>
            <span class="stack-color" :style="{ background: item.color }"></span>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </div>

      <div class="section">
        <div class="settings-section-title">卡片顺序（按页面调整）</div>
        <div class="settings-page-grid">
          <section v-for="group in pageGroups" :key="'order-' + group.key" class="settings-page-block">
            <div class="settings-page-title">{{ group.label }}</div>
            <div class="card-order-list vertical">
              <div v-for="(card, index) in group.cards" :key="card.id" class="card-order-item">
                <button :disabled="!canMoveUp(group, card, index)" @click="cardStore.moveCardInPage(group.key, card.id, -1)">↑</button>
                <button :disabled="!canMoveDown(group, card, index)" @click="cardStore.moveCardInPage(group.key, card.id, 1)">↓</button>
                <span>{{ card.id }}</span>
              </div>
            </div>
          </section>
        </div>
      </div>

      <div class="section">
        <div class="settings-section-title">显示控制</div>
        <div class="settings-page-grid">
          <section v-for="group in pageGroups" :key="'visible-' + group.key" class="settings-page-block">
            <div class="settings-page-title">{{ group.label }}</div>
            <div class="check-list vertical">
              <label v-for="card in group.cards" :key="'vis-' + card.id" class="check-item">
                <input type="checkbox" :checked="cardStore.visible[card.id]" @change="e => { cardStore.visible[card.id] = e.target.checked; cardStore.save() }"> {{ card.id }}
              </label>
            </div>
          </section>
        </div>
      </div>

      <div class="section">
        <div class="settings-section-title">总复制（按页面分组，只有可复制卡片会显示）</div>
        <div class="settings-page-grid">
          <section v-for="group in copyPageGroups" :key="'copy-' + group.key" class="settings-page-block">
            <div class="settings-page-title">{{ group.label }}</div>
            <div v-if="group.cards.length" class="check-list vertical">
              <label v-for="card in group.cards" :key="'copy-' + card.id" class="check-item">
                <input type="checkbox" :checked="cardStore.copyEnabled[card.id]" @change="e => { cardStore.copyEnabled[card.id] = e.target.checked; cardStore.save() }"> {{ card.id }}
              </label>
            </div>
            <div v-else class="empty-copy-tip">暂无可参与总复制的卡片</div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useSettingsStore, REVIEW_PLAN_STACK_ITEMS, normalizeReviewPlanStackOrder } from '@/stores/settingsStore'
import { useCardStore, PAGE_REGISTRY, FIXED_FIRST_CARD_ID } from '@/stores/cardStore'

defineEmits(['reload', 'copy-all', 'sync'])

const settings = useSettingsStore()
const cardStore = useCardStore()
const showSettings = ref(false)

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

const copyPageGroups = computed(() => PAGE_REGISTRY.map(page => ({
  ...page,
  cards: cardStore.getCopyableCards(page.key),
})))

function canMoveUp(group, card, index) {
  if (card.id === FIXED_FIRST_CARD_ID) return false
  if (index <= 0) return false
  if (group.key === 'today' && group.cards[index - 1]?.id === FIXED_FIRST_CARD_ID) return false
  return true
}

function canMoveDown(group, card, index) {
  if (card.id === FIXED_FIRST_CARD_ID) return false
  return index >= 0 && index < group.cards.length - 1
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
  padding-top: 10px;
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
.section {
  margin-top: 12px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
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
.settings-page-block {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: #f8fafc;
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
  grid-template-columns: 24px 24px minmax(0, 1fr);
  align-items: center;
  gap: 4px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 6px;
  font-size: 13px;
}
.card-order-item span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-order-item button {
  width: 22px;
  height: 22px;
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: #64748b;
  border-radius: 4px;
}
.card-order-item button:hover { background: #e2e8f0; }
.card-order-item button:disabled { opacity: .3; cursor: not-allowed; }
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
  grid-template-columns: 24px 24px 14px minmax(0, 1fr);
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
}
.stack-order-item button {
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #64748b;
  cursor: pointer;
}
.stack-order-item button:hover { background: #e2e8f0; }
.stack-order-item button:disabled { opacity: .3; cursor: not-allowed; }
.stack-color {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  box-shadow: inset 0 0 0 1px rgba(15, 23, 42, .15);
}
</style>
