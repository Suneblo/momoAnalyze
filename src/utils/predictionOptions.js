export function buildPredictionOptions(settings) {
  return {
    horizonDays: settings.predictionDays,
    initMode: settings.predictionInitMode,
    dailyMode: settings.predictionDailyMode,
    dailyBaseValue: settings.predictionDailyValue,
    reviewLimit: settings.predictionReviewLimit,
    deferOverflow: settings.predictionDeferOverflow,
    ratingMapping: {
      known: settings.predictionRatingKnown,
      vague: settings.predictionRatingVague,
      forget: settings.predictionRatingForget,
    },
    targetSettings: {
      metric: settings.predictionTargetMetric,
      days: settings.predictionTargetDays,
      count: settings.predictionTargetCount,
    },
    modelFrom: settings.predictionModelFrom,
    modelTo: settings.predictionModelTo,
    probabilitySettings: {
      memoryBucketSize: settings.predictionProbMemoryBucketSize,
      studyCountBucketSize: settings.predictionProbStudyCountBucketSize,
      useStudyCountDimension: settings.predictionUseStudyCountDimension,
    },
  }
}

export function hasPredictionRows(result) {
  return Array.isArray(result?.rows) && result.rows.length > 0
}

export function hasProbabilityModel(result) {
  const model = result?.probabilityModel
  if (!model || typeof model !== 'object') return false
  if (Number(model.reviewSampleCount || model.globalCounts?.n || 0) > 0) return true
  return Boolean((model.memoryBuckets || []).length || (model.studyBuckets || []).length)
}
