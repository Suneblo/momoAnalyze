<template>
  <div>
    <div class="prediction-toolbar">
      <label>目标判断方式
        <select v-model="settings.predictionTargetMetric" @change="settings.save()">
          <option value="review_span">熟悉度 / 记忆持久度</option>
          <option value="critical_point">记忆临界点 / 下次复习距今</option>
        </select>
      </label>
      <label>目标天数 &gt; <input v-model.number="settings.predictionTargetDays" type="number" min="0" step="1" @change="settings.save()"> 天</label>
      <label>目标词数 <input v-model="settings.predictionTargetCount" type="number" min="1" step="1" placeholder="可选" @change="settings.save()"></label>
    </div>

    <BaseChart v-if="targetChartOption" :option="targetChartOption" :height="300" />

    <p class="info-text">
      复习概率模型：按全局历史中“记忆持久度 × 学习次数 → 再次复习首次反应”统计；记忆持久度按固定目标词数分桶，相同天数会整组保留在同一桶里，不会为了凑词数拆开。新词不参与复习概率分桶，模拟时默认进入次日复习。
    </p>

    <div class="prediction-toolbar probability-model-toolbar">
      <label class="switch-label"><input v-model="settings.predictionUseStudyCountDimension" type="checkbox" @change="settings.save()"> 使用学习次数维度</label>
      <label>记忆每桶目标词数 <input v-model.number="settings.predictionProbMemoryBucketSize" type="number" min="1" step="1" @change="settings.save()"></label>
      <label v-if="settings.predictionUseStudyCountDimension">学习次数桶宽 <input v-model.number="settings.predictionProbStudyCountBucketSize" type="number" min="1" step="1" @change="settings.save()"></label>
    </div>

    <BaseChart v-if="probabilityChartOption" :option="probabilityChartOption" :height="300" />

    <div v-if="probability3dVisible" class="probability-3d-card">
      <div class="probability-3d-head">
        <div>
          <h3>复习概率三维图</h3>
          <p class="info-text">X 轴为学习次数分桶，Y 轴为记忆持久度分桶，Z 轴为所选反应合并概率；悬浮信息包含该区域样本数 n。拖动可旋转，滚轮或双指可缩放。</p>
        </div>
        <div class="probability-rating-checks">
          <span>显示</span>
          <label v-for="item in probability3dRatingOptions" :key="item.key">
            <input :checked="probability3dRatings.includes(item.key)" type="checkbox" @change="toggleProbability3dRating(item.key, $event.target.checked)">
            {{ item.label }}
          </label>
        </div>
      </div>
      <Probability3DChart :model="probabilityModel" :ratings="probability3dRatings" :height="480" />
      <p class="info-text probability-3d-summary">{{ probability3dSummary }}</p>
    </div>
    <p v-else-if="probabilityModel && probabilityModel.useStudyCountDimension === false" class="info-text">学习次数维度已关闭：三维图不显示，概率模型只按记忆持久度一维估计。</p>

    <div v-if="probabilityRows.length" class="table-wrapper mini-table-wrapper stat-table-scroll">
      <table class="probability-table">
        <thead>
          <tr>
            <th>记忆持久度 \ 学习次数</th>
            <th v-for="study in probabilityStudyColumns" :key="study.key">{{ study.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in probabilityRows" :key="row.memory.key">
            <th>{{ row.memory.label }}</th>
            <template v-for="cell in row.cells" :key="cell.key">
              <td v-if="!cell.skip" :rowspan="cell.rowspan" :title="cell.title">{{ cell.text }}</td>
            </template>
          </tr>
        </tbody>
      </table>
      <div class="info-text table-note">单元格说明：n/忘/模/认/Easy，例如 20/10/25/55/10 表示该区域真实样本 20 个；样本不足时会在同一学习次数列向下合并记忆持久度行，仍不足目标词数则显示 -；空白表示被上方合并区域覆盖。</div>
    </div>
    <p v-else class="info-text">暂无复习概率分桶数据。请先运行“未来每日学习量预测”。</p>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useSettingsStore } from '@/stores/settingsStore'
import { usePredictionStore } from '@/stores/predictionStore'
import { buildLineOption, COLORS } from '@/utils/chartOptions'
import { buildProbabilityTableRows } from '@/utils/dashboardPrediction'
import BaseChart from '@/components/charts/BaseChart.vue'
import Probability3DChart from '@/components/charts/Probability3DChart.vue'

defineProps({ card: Object })

const settings = useSettingsStore()
const prediction = usePredictionStore()
const probability3dRatings = ref(['good'])
const probability3dRatingOptions = [
  { key: 'again', label: '忘' },
  { key: 'hard', label: '模' },
  { key: 'good', label: '认' },
  { key: 'easy', label: 'Easy' },
]

const targetChartOption = computed(() => {
  const rows = prediction.result?.rows || []
  if (!rows.length) return null
  const labels = rows.map(row => String(row.predictionDay))
  const targetCount = Number(settings.predictionTargetCount) || 0
  const series = [{
    name: '目标达标词数',
    data: rows.map(row => Number(row.dailyTargetMatchedCount) || 0),
    color: COLORS.blue,
  }]
  if (targetCount > 0) {
    series.push({ name: '目标词数线', data: rows.map(() => targetCount), color: COLORS.red })
  }
  return buildLineOption(labels, series, { yUnit: '词数', xUnit: '几天后' })
})

const probabilityModel = computed(() => prediction.result?.probabilityModel || null)

const probabilityRows = computed(() => buildProbabilityTableRows(probabilityModel.value))

const probabilityStudyColumns = computed(() => {
  const model = probabilityModel.value
  if (!model) return []
  if (model.useStudyCountDimension === false) return [{ key: 'all', label: '全部' }]
  return probabilityRows.value.studyRows || model.studyBuckets || []
})

const probabilityChartOption = computed(() => {
  const model = probabilityModel.value
  const memoryRows = model?.memoryBuckets || []
  if (!memoryRows.length) return null
  const labels = memoryRows.map(row => row.label)
  const pct = (row, rating) => {
    if (row.probs && Number.isFinite(Number(row.probs[rating]))) return Math.round(Number(row.probs[rating]) * 1000) / 10
    const n = Number(row.n || row.total || 0)
    return n ? Math.round((Number(row[rating] || 0) / n) * 1000) / 10 : 0
  }
  return buildLineOption(labels, [
    { name: '忘记/Again概率', data: memoryRows.map(row => pct(row, 'again')), color: COLORS.red },
    { name: '模糊/Hard概率', data: memoryRows.map(row => pct(row, 'hard')), color: COLORS.yellow },
    { name: '认识/Good概率', data: memoryRows.map(row => pct(row, 'good')), color: COLORS.blue },
    { name: 'Easy概率', data: memoryRows.map(row => pct(row, 'easy')), color: COLORS.green },
  ], { yUnit: '%', xUnit: '记忆持久度分桶' })
})

const probability3dVisible = computed(() => {
  const model = probabilityModel.value
  return !!model && model.useStudyCountDimension !== false && !!(model.memoryBuckets || []).length && !!(model.studyBuckets || []).length && !!model.byKey
})

const probability3dSummary = computed(() => {
  const model = probabilityModel.value
  if (!model) return '暂无三维概率数据。'
  const memoryCount = (model.memoryBuckets || []).length
  const studyCount = (model.studyBuckets || []).length
  const sampleCount = model.reviewSampleCount || model.globalCounts?.n || 0
  const labels = probability3dRatings.value.map(key => probability3dRatingOptions.find(item => item.key === key)?.label || key).join('+')
  return `当前显示：${labels}合并概率和区域样本数 n；拖动可旋转，滚轮或双指可缩放；${memoryCount} 个记忆持久度桶 × ${studyCount} 个学习次数桶；复习样本 ${sampleCount} 个。`
})

function toggleProbability3dRating(key, checked) {
  const current = probability3dRatings.value
  if (checked) {
    probability3dRatings.value = current.includes(key) ? current : [...current, key]
    return
  }
  const next = current.filter(item => item !== key)
  probability3dRatings.value = next.length ? next : [key]
}
</script>

<style scoped>
.prediction-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px 16px;
  margin-bottom: 14px;
}
.probability-model-toolbar {
  margin-top: -4px;
}
.prediction-toolbar label {
  font-size: 13px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.prediction-toolbar input,
.prediction-toolbar select,
.probability-3d-head select {
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.prediction-toolbar input[type='number'] {
  width: 80px;
}
.probability-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.probability-table th,
.probability-table td {
  border: 1px solid var(--border);
  padding: 6px 8px;
  text-align: center;
  white-space: nowrap;
}
.probability-table th {
  background: #f8fafc;
  font-weight: 600;
}

.table-note {
  margin-top: 8px;
}
.probability-3d-card {
  margin: 12px 0;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px;
  background: #f8fafc;
}
.probability-3d-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
  margin-bottom: 8px;
}
.probability-3d-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.probability-3d-head label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  white-space: nowrap;
}
.probability-rating-checks {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 6px 10px;
  font-size: 13px;
}
.probability-rating-checks label {
  margin: 0;
}
.probability-3d-summary {
  margin-top: 8px;
}
@media (max-width: 720px) {
  .probability-3d-head { flex-direction: column; }
}
</style>
