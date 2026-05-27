import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const CACHE_KEY = 'momoPredictionCache'

export const usePredictionStore = defineStore('prediction', () => {
  const result = ref(null)
  const isCalculating = ref(false)
  const progress = ref({ current: 0, total: 0 })

  function configHash(settings) {
    return [
      settings.predictionDays,
      settings.predictionDailyValue,
      settings.predictionDailyMode,
      settings.predictionReviewLimit,
      settings.predictionInitMode,
      settings.predictionRatingKnown + ',' + settings.predictionRatingVague + ',' + settings.predictionRatingForget,
      settings.predictionTargetMetric + '|' + settings.predictionTargetDays + '|' + settings.predictionTargetCount,
      settings.predictionProbLogBase + ',' + settings.predictionProbBucketSize + ',' + settings.predictionProbStudyCountBucketSize,
      settings.predictionUseStudyCountDimension ? '1' : '0',
    ].join('|')
  }

  function loadCache() {
    try { localStorage.removeItem(CACHE_KEY) } catch (e) { /* ignore */ }
    return false
  }

  function saveCache() {
    try { localStorage.removeItem(CACHE_KEY) } catch (e) { /* ignore */ }
  }

  return {
    result, isCalculating, progress,
    configHash, loadCache, saveCache
  }
})
