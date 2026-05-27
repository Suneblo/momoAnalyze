// FSRS (Free Spaced Repetition Scheduler) core algorithms
// Extracted from original history_dashboard.js

/**
 * Deterministic pseudo-random noise function
 */
export function deterministicNoise(seed) {
  const x = Math.sin(Number(seed || 1) * 12.9898 + 78.233) * 43758.5453
  return x - Math.floor(x)
}

/**
 * Pick a rating based on a probability distribution
 */
export function pickRatingByDistribution(dist, seed) {
  const r = deterministicNoise(seed)
  let acc = 0
  for (const item of dist || []) {
    acc += Number(item.prob || 0)
    if (r <= acc) return item.rating
  }
  return 'good'
}

/**
 * Apply a single FSRS review step
 * @returns {number} new stability (interval in days)
 */
export function applySimpleFsrsReview(state, rating, observedDays) {
  const prev = Math.max(0.5, Number(state.stability || 1))
  const observed = Math.max(1, Math.round(Number(observedDays) || prev))
  let next
  if (rating === 'again') next = 1
  else if (rating === 'hard') next = Math.max(1, Math.round(Math.max(prev * 1.15, observed * 0.8)))
  else if (rating === 'easy') next = Math.max(2, Math.round(Math.max(prev * 3.2, observed * 1.35)))
  else next = Math.max(1, Math.round(Math.max(prev * 2.2, observed)))
  state.stability = next
  state.reps = Math.max(0, Math.round(Number(state.reps) || 0)) + 1
  return next
}

/**
 * Normalize MoMo state to FSRS rating
 */
export function normalizePredictionState(state) {
  const s = String(state || '').trim()
  if (['认识', '熟知', 'KNOWN', 'FAMILIAR'].includes(s)) return 'known'
  if (['模糊', 'VAGUE', 'BLUR'].includes(s)) return 'vague'
  if (['忘记', 'FORGET', 'AGAIN'].includes(s)) return 'forget'
  return 'unknown'
}

/**
 * Map state to FSRS rating using user-defined mapping
 */
export function ratingFromState(state, mapping = {}) {
  const normalized = normalizePredictionState(state)
  if (normalized === 'known') return mapping.known || 'good'
  if (normalized === 'vague') return mapping.vague || 'hard'
  if (normalized === 'forget') return mapping.forget || 'again'
  return null
}

/**
 * Build FSRS rating profile from items
 */
export function buildFsrsRatingProfile(items, mapping = {}) {
  const counts = { again: 0, hard: 0, good: 0, easy: 0 }
  for (const item of items || []) {
    const rating = ratingFromState(item.state, mapping)
    if (rating && Object.prototype.hasOwnProperty.call(counts, rating)) {
      counts[rating] += 1
    }
  }
  const total = Object.values(counts).reduce((a, b) => a + b, 0)
  const fallback = { again: 0.15, hard: 0.25, good: 0.5, easy: 0.10 }
  const dist = Object.keys(counts).map(rating => ({
    rating,
    prob: total ? counts[rating] / total : fallback[rating]
  }))
  return { counts, total, ratingDistribution: dist }
}

/**
 * Compute synthetic estimate intervals for missing review history
 */
export function syntheticBackfillIntervals(item, missingCount, observedDays) {
  if (!missingCount || missingCount <= 0) return []
  const intervals = []
  let remaining = observedDays
  for (let i = 0; i < missingCount; i++) {
    const frac = (i + 1) / (missingCount + 1)
    const d = Math.max(1, Math.round(remaining * frac / (missingCount - i + 1)))
    intervals.push(d)
    remaining -= d
    if (remaining <= 0) break
  }
  // If we didn't produce enough intervals, fill with 1-day intervals
  while (intervals.length < missingCount) {
    intervals.push(1)
  }
  return intervals.slice(0, missingCount)
}

/**
 * Normalize study count value
 */
export function normalizeStudyCountValue(val, fallback = 0) {
  const n = Number(val)
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : Number(fallback || 0)
}

/**
 * Simulate initial FSRS state for a word item
 */
export function simulateInitialFsrsState(item, profile, initMode = 'fsrs_estimate_by_count', mapping = {}, seedBase = 1) {
  const observedDays = Math.max(1, Math.round(Number(item?.days) || 1))
  const rawCount = normalizeStudyCountValue(item?.studyCount, 0)
  const state = { stability: 1, reps: 0 }

  if (rawCount <= 0) return state

  const observedResultCount = Math.max(0, Math.floor(Number(item?.observedReviewResultCount) || 0))
  const rawMissingResultCount = Number(item?.missingReviewResultCount)
  const missingResultCount = Math.max(0, Math.floor(
    Number.isFinite(rawMissingResultCount)
      ? rawMissingResultCount
      : Math.max(0, rawCount - observedResultCount)
  ))

  const syntheticIntervals = syntheticBackfillIntervals(item, missingResultCount, observedDays)
  for (const interval of syntheticIntervals) {
    applySimpleFsrsReview(state, 'again', interval)
  }

  const observedPartCount = Math.max(0, rawCount - syntheticIntervals.length)
  if (observedPartCount <= 0) return state

  const reps = initMode === 'first_seen_as_first_learning'
    ? Math.min(1, observedPartCount)
    : observedPartCount

  for (let i = 1; i <= reps; i++) {
    const isLatest = i === reps
    const knownRating = isLatest ? ratingFromState(item.state, mapping) : null
    const rating = knownRating || 'again'
    const syntheticObserved = isLatest
      ? observedDays
      : Math.max(1, Math.round(observedDays * (0.4 + deterministicNoise(seedBase + syntheticIntervals.length + i) * 0.9)))
    applySimpleFsrsReview(state, rating, syntheticObserved)
  }
  return state
}

/**
 * Build FSRS model from items
 */
export function buildFsrsModelFromItems(items, initMode = 'fsrs_estimate_by_count', mapping = {}) {
  const profile = buildFsrsRatingProfile(items, mapping)
  const states = (items || []).map((item, idx) =>
    simulateInitialFsrsState(item, profile, initMode, mapping, idx + 1)
  )
  const meanStability = states.length
    ? states.reduce((sum, st) => sum + Number(st.stability || 0), 0) / states.length
    : 1
  return { profile, meanStability, sampleStates: states }
}

/**
 * Prediction word seed (unified across full prediction and word trace)
 */
export function predictionWordSeed(item) {
  const count = normalizeStudyCountValue(item?.studyCount, 0)
  const days = Math.max(1, Math.round(Number(item?.days) || 1))
  return Math.max(1, count) * 1009 + Math.max(1, days)
}

/**
 * Rating seed for a specific review step
 */
export function predictionRatingSeed(word, day, reviewStep) {
  return word.seed + reviewStep * 7919 + day * 97
}

export default {
  deterministicNoise,
  pickRatingByDistribution,
  applySimpleFsrsReview,
  normalizePredictionState,
  ratingFromState,
  buildFsrsRatingProfile,
  syntheticBackfillIntervals,
  simulateInitialFsrsState,
  buildFsrsModelFromItems,
  predictionWordSeed,
  predictionRatingSeed,
  normalizeStudyCountValue,
}
