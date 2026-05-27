<template>
  <div class="card-grid">
    <template v-for="card in visibleCards" :key="card.id">
      <div class="card" :class="{ 'wide-card': card.copyKey === 'overviewChart' }">
        <h2>{{ card.id }}</h2>
        <component :is="getComponent(card.copyKey)" :card="card" />
        <div class="inline-actions">
          <el-button v-if="canCopy(card)" size="small" @click="copyCard(card)">复制</el-button>
          <el-button v-if="hasDetailPanel(card)" size="small" type="warning" @click="toggleDetail(card)">⚙</el-button>
        </div>
        <div v-show="detailOpen[card.id]" class="card-detail-panel">
          <!-- Copy detail checkboxes -->
          <div v-if="card.copyDetail && Object.keys(card.copyDetail).length" class="copy-detail-block">
            <div class="info-text">复制时包含：</div>
            <label v-for="(label, key) in card.copyDetail" :key="key" class="check-item copy-detail-item">
              <input type="checkbox" :checked="cardStore.copyDetail[card.id]?.[key]" @change="e => { cardStore.copyDetail[card.id][key] = e.target.checked; cardStore.save() }">
              {{ label }}
            </label>
          </div>
          <!-- Per-card settings -->
          <div v-if="card.id === '记忆持久度统计'" class="card-setting setting-row">
            <label class="setting-label">熟悉度阈值(天)</label>
            <input v-model="settings.memoryThresholds" type="text" class="setting-input wide" placeholder="1,2,3,4,5,6,7,15,30" @change="settings.save()">
            <span class="setting-help">支持 1、2-3、&gt;7、&lt;=30；折线图按这些阈值统计记忆持久度。</span>
          </div>
          <div v-if="card.id === '复习情况'" class="card-setting">
            <label>未来记忆临界点天数 <input v-model.number="settings.criticalFutureDays" type="number" min="0" max="365" style="width:80px" @change="settings.save()"></label>
            <span class="setting-help">复习情况使用新的 studyStatus API：认知情况和复习新学显示为条形图；未来灰色为记忆临界点，逾期单独用折线展示。</span>
          </div>
          <div v-if="card.id === '未来每日学习量预测'" class="card-setting">
            <div class="info-text">模型数据日期范围</div>
            <label>开始 <input v-model="settings.predictionModelFrom" type="date" style="width:140px" @change="settings.save()"></label>
            <label style="margin-left:8px">结束 <input v-model="settings.predictionModelTo" type="date" style="width:140px" @change="settings.save()"></label>
            <div class="info-text" style="margin-top:6px">FSRS 映射</div>
            <label>认识→<select v-model="settings.predictionRatingKnown" @change="settings.save()"><option value="good">Good</option><option value="easy">Easy</option><option value="hard">Hard</option><option value="again">Again</option></select></label>
            <label style="margin-left:8px">模糊→<select v-model="settings.predictionRatingVague" @change="settings.save()"><option value="hard">Hard</option><option value="good">Good</option><option value="again">Again</option></select></label>
            <label style="margin-left:8px">忘记→<select v-model="settings.predictionRatingForget" @change="settings.save()"><option value="again">Again</option><option value="hard">Hard</option><option value="good">Good</option></select></label>
            <label style="margin-left:8px"><input v-model="settings.predictionUseStudyCountDimension" type="checkbox" @change="settings.save()"> 学习次数维度</label>
            <div class="info-text" style="margin-top:6px">概率分桶设置</div>
            <label>底数 <input v-model.number="settings.predictionProbLogBase" type="number" min="1.1" step="0.1" style="width:60px" @change="settings.save()"></label>
            <label style="margin-left:8px">桶宽 <input v-model.number="settings.predictionProbBucketSize" type="number" min="0.1" step="0.1" style="width:60px" @change="settings.save()"></label>
            <label style="margin-left:8px">次数桶宽 <input v-model.number="settings.predictionProbStudyCountBucketSize" type="number" min="1" step="1" style="width:70px" @change="settings.save()"></label>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, defineAsyncComponent } from 'vue'
import { useCardStore, CARD_REGISTRY } from '@/stores/cardStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { buildSingleCardMarkdown, copyToClipboard } from '@/composables/useCopy'

const props = defineProps({ pageKey: { type: String, default: '' } })

const cardStore = useCardStore()
const settings = useSettingsStore()
const detailOpen = reactive({})

const componentMap = {
  overviewChart: defineAsyncComponent(() => import('@/components/cards/OverviewChart.vue')),
  todayWordStats: defineAsyncComponent(() => import('@/components/cards/TodayWordStats.vue')),
  memory: defineAsyncComponent(() => import('@/components/cards/MemoryChart.vue')),
  studyTime: defineAsyncComponent(() => import('@/components/cards/StudyTimeChart.vue')),
  prediction: defineAsyncComponent(() => import('@/components/cards/PredictionCard.vue')),
  predictionTarget: defineAsyncComponent(() => import('@/components/cards/TargetCard.vue')),
  wordList: defineAsyncComponent(() => import('@/components/cards/WordListCard.vue')),
  summary: defineAsyncComponent(() => import('@/components/cards/SummaryTable.vue')),
  notes: defineAsyncComponent(() => import('@/components/cards/FieldNotes.vue')),
  notepadManager: defineAsyncComponent(() => import('@/components/cards/NotepadManagerCard.vue')),
  articleUnknownWords: defineAsyncComponent(() => import('@/components/cards/ArticleUnknownWordsCard.vue')),
  todayWorkspace: defineAsyncComponent(() => import('@/components/cards/TodayWorkspaceCard.vue')),
  dataAlert: defineAsyncComponent(() => import('@/components/cards/DataAlertCard.vue')),
}

function getComponent(copyKey) { return componentMap[copyKey] || null }

const visibleCards = computed(() => {
  return cardStore.getVisibleCards(props.pageKey).map(id => CARD_REGISTRY.find(r => r.id === id)).filter(Boolean)
})

function toggleDetail(card) { detailOpen[card.id] = !detailOpen[card.id] }

function canCopy(card) {
  return card.copyEnabled !== false
}

function hasDetailPanel(card) {
  return canCopy(card) || ['复习情况', '记忆持久度统计', '未来每日学习量预测'].includes(card.id)
}

async function copyCard(card) {
  const md = buildSingleCardMarkdown(card.copyKey)
  await copyToClipboard(md, `已复制 ${card.id}`)
}
</script>

<style scoped>
.card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.wide-card { grid-column: 1 / -1; }
@media (max-width: 768px) { .card-grid { grid-template-columns: 1fr; } }
.inline-actions { margin-top: 8px; display: flex; gap: 8px; align-items: center; }
.card-detail-panel { margin-top: 8px; border-top: 1px solid var(--border); padding-top: 8px; font-size: 13px; }
.check-item { display: inline-flex; align-items: center; gap: 4px; font-size: 13px; cursor: pointer; margin-right: 8px; }
.copy-detail-block { margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px dashed var(--border); }
.copy-detail-item { margin-top: 4px; }
.card-setting { margin-top: 8px; }
.card-setting label { font-size: 13px; margin-right: 8px; }
.card-setting input, .card-setting select { padding: 2px 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; }
.setting-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; }
.setting-label { min-width: 96px; font-size: 13px; color: var(--text-main); }
.setting-input.wide { width: 220px; max-width: 100%; }
.setting-help { font-size: 12px; color: var(--text-muted); }
</style>
