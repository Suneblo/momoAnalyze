import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'momoUserSettings'

export const REVIEW_PLAN_STACK_ITEMS = [
  { key: 'reviewPending', label: '未复习', color: '#cbd5e1' },
  { key: 'newPending', label: '未新学', color: '#64748b' },
  { key: 'reviewDone', label: '已复习', color: '#f59e0b' },
  { key: 'newDone', label: '已新学', color: '#6cc8b6' },
]

const REVIEW_PLAN_STACK_KEYS = REVIEW_PLAN_STACK_ITEMS.map(item => item.key)

export function normalizeReviewPlanStackOrder(value) {
  const input = Array.isArray(value) ? value : String(value || '').split(',')
  const unique = []
  for (const key of input) {
    if (REVIEW_PLAN_STACK_KEYS.includes(key) && !unique.includes(key)) unique.push(key)
  }
  for (const key of REVIEW_PLAN_STACK_KEYS) {
    if (!unique.includes(key)) unique.push(key)
  }
  return unique
}

export const useSettingsStore = defineStore('settings', () => {
  // Range & display
  const rangePreset = ref('30')
  const customDays = ref(30)
  const pointSpacing = ref(1)
  const studyTimeWindowDays = ref(30)
  const hideEmptyDays = ref(true)
  const memoryThresholds = ref('0,1,2,3,4,5,6,7,15,30')
  const criticalFutureDays = ref(7)
  // 复习新学堆叠图顺序，表示从上到下的显示顺序。
  const reviewPlanStackOrder = ref(normalizeReviewPlanStackOrder(['reviewPending', 'newPending', 'reviewDone', 'newDone']))

  // Prediction
  const predictionDays = ref(30)
  const predictionInitMode = ref('fsrs_estimate_by_count')
  const predictionDailyMode = ref('fixed_new')
  const predictionDailyValue = ref('')
  const predictionReviewLimit = ref('')
  const predictionRatingKnown = ref('good')
  const predictionRatingVague = ref('hard')
  const predictionRatingForget = ref('again')
  const predictionTargetMetric = ref('review_span')
  const predictionTargetDays = ref(30)
  const predictionTargetCount = ref('')
  const predictionDeferOverflow = ref(false)
  const predictionModelFrom = ref('')
  const predictionModelTo = ref('')
  const predictionUseStudyCountDimension = ref(true)
  const predictionProbLogBase = ref(2)
  const predictionProbBucketSize = ref(1)
  const predictionProbStudyCountBucketSize = ref(5)

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
      for (const [k, v] of Object.entries(saved)) {
        if (k in this) this[k] = v
      }
      reviewPlanStackOrder.value = normalizeReviewPlanStackOrder(reviewPlanStackOrder.value)
    } catch (e) { /* ignore */ }
  }

  function moveReviewPlanStackItem(index, dir) {
    const order = normalizeReviewPlanStackOrder(reviewPlanStackOrder.value)
    const nextIndex = index + dir
    if (index < 0 || nextIndex < 0 || nextIndex >= order.length) return
    const tmp = order[index]
    order[index] = order[nextIndex]
    order[nextIndex] = tmp
    reviewPlanStackOrder.value = order
    save()
  }

  function save() {
    const data = {}
    for (const k of Object.keys(this)) {
      if (typeof this[k] !== 'function' && !k.startsWith('$') && !k.startsWith('_')) {
        data[k] = this[k]
      }
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
  }

  return {
    rangePreset, customDays, pointSpacing, studyTimeWindowDays, hideEmptyDays,
    memoryThresholds, criticalFutureDays, reviewPlanStackOrder,
    predictionDays, predictionInitMode, predictionDailyMode, predictionDailyValue,
    predictionReviewLimit, predictionRatingKnown, predictionRatingVague, predictionRatingForget,
    predictionTargetMetric, predictionTargetDays, predictionTargetCount,
    predictionDeferOverflow, predictionModelFrom, predictionModelTo,
    predictionUseStudyCountDimension, predictionProbLogBase, predictionProbBucketSize, predictionProbStudyCountBucketSize,
    moveReviewPlanStackItem,
    load, save
  }
})
