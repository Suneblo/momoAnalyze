import { formatPredictionCount } from '@/utils/dashboardParity'

const RATING_LABELS = {
  again: '忘',
  hard: '模',
  good: '认',
  easy: 'Easy',
}

const DEFAULT_DISTRIBUTION = [
  { rating: 'again', prob: 1 },
  { rating: 'hard', prob: 0 },
  { rating: 'good', prob: 0 },
  { rating: 'easy', prob: 0 },
]

function todayDateOnly() {
  const d = new Date()
  d.setHours(4, 0, 0, 0)
  return d.toISOString().slice(0, 10)
}

function addDays(dateText, days) {
  const base = dateText ? new Date(`${dateText}T04:00:00`) : new Date()
  base.setDate(base.getDate() + Number(days || 0))
  return base.toISOString().slice(0, 10)
}

function deterministicNoise(seed) {
  const x = Math.sin(Number(seed || 1) * 12.9898 + 78.233) * 43758.5453
  return x - Math.floor(x)
}

function normalizeState(state) {
  const s = String(state || '').trim()
  if (['认识', '熟知', 'KNOWN', 'FAMILIAR', 'known', 'good', 'easy'].includes(s)) return 'known'
  if (['模糊', 'VAGUE', 'BLUR', 'vague', 'hard'].includes(s)) return 'vague'
  if (['忘记', 'FORGET', 'AGAIN', 'forget', 'again'].includes(s)) return 'forget'
  return ''
}

export function ratingFromState(state, mapping = {}) {
  const normalized = normalizeState(state)
  if (normalized === 'known') return mapping.known || 'good'
  if (normalized === 'vague') return mapping.vague || 'hard'
  if (normalized === 'forget') return mapping.forget || 'again'
  return null
}

function itemKey(item, fallback = '') {
  return String(item?.vocId || item?.word || fallback)
}

function itemWord(item, fallback = '') {
  return String(item?.word || item?.vocId || fallback)
}

function normalizeStudyCount(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : fallback
}

function normalizeDays(value, fallback = 1) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.round(n) : fallback
}

function applySimpleFsrsReview(state, rating, observedDays) {
  const prev = Math.max(0.5, Number(state.stability || 1))
  const observed = Math.max(1, Math.round(Number(observedDays) || prev))
  let next = prev
  if (rating === 'again') next = 1
  else if (rating === 'hard') next = Math.max(1, Math.round(Math.max(prev * 1.15, observed * 0.8)))
  else if (rating === 'easy') next = Math.max(2, Math.round(Math.max(prev * 3.2, observed * 1.35)))
  else next = Math.max(1, Math.round(Math.max(prev * 2.2, observed)))
  state.stability = next
  state.reps = Math.max(0, Math.round(Number(state.reps) || 0)) + 1
  return next
}

function bucketMemory(days, settings) {
  const value = Math.max(1, Math.round(Number(days) || 1))
  const base = Math.max(1.1, Number(settings.logBase) || 2)
  const width = Math.max(0.1, Number(settings.bucketSize) || 1)
  const log = Math.log(value) / Math.log(base)
  const startExp = Math.floor(log / width) * width
  const endExp = startExp + width
  const lower = Math.max(1, Math.floor(Math.pow(base, startExp)))
  const upper = Math.max(lower, Math.max(lower, Math.ceil(Math.pow(base, endExp)) - 1))
  const key = `${lower}-${upper}`
  return { key, label: lower === upper ? `${lower}天` : `${lower}-${upper}天`, lower, upper, center: (lower + upper) / 2 }
}

function bucketStudyCount(count, settings) {
  const value = Math.max(0, Math.round(Number(count) || 0))
  const width = Math.max(1, Math.round(Number(settings.studyCountBucketSize) || 5))
  const lower = Math.floor(value / width) * width
  const upper = lower + width - 1
  const key = `${lower}-${upper}`
  return { key, label: lower === upper ? String(lower) : `${lower}-${upper}`, lower, upper, center: (lower + upper) / 2 }
}

function makeEmptyCounts() {
  return { n: 0, again: 0, hard: 0, good: 0, easy: 0 }
}

function countsToDistribution(counts, fallback = null) {
  const n = Number(counts?.n || 0)
  if (!n) return fallback || DEFAULT_DISTRIBUTION
  return ['again', 'hard', 'good', 'easy'].map(rating => ({ rating, prob: Number(counts[rating] || 0) / n }))
}

function pushUniqueBucket(list, map, bucket) {
  if (!map.has(bucket.key)) {
    map.set(bucket.key, bucket)
    list.push(bucket)
  }
}

export function formatReviewProbabilityCellText(cell) {
  if (!cell) return '-'
  const n = Number(cell.n || 0)
  const radius = Number(cell.diffusionRadius || 0)
  const hasDiffusedProbability = n <= 0 && radius > 0 && cell.probs && ['again', 'hard', 'good', 'easy'].every(rating => Number.isFinite(Number(cell.probs?.[rating])))
  if (!Number.isFinite(n) || (n <= 0 && !hasDiffusedProbability)) return '-'
  const pct = rating => {
    if (cell.probs && Number.isFinite(Number(cell.probs[rating]))) return Math.round(Number(cell.probs[rating]) * 100)
    return Math.round((Number((cell[rating] ?? cell.counts?.[rating]) || 0) / Math.max(1, n)) * 100)
  }
  const diffusionMark = hasDiffusedProbability ? '*'.repeat(Math.max(1, Math.round(radius))) : ''
  return `${Math.max(0, Math.round(n))}${diffusionMark}/${pct('again')}/${pct('hard')}/${pct('good')}/${pct('easy')}`
}

export function probabilityCellStatus(cell, model = null) {
  if (!cell || cell.fallbackMethod === 'noData') return 'noData'
  const n = Number(cell.n || 0)
  const radius = Number(cell.diffusionRadius || 0)
  if (n <= 0 && radius > 0) return 'diffused'
  const minSamples = Number(model?.minCoordinateSamples || model?.settings?.minCoordinateSamples || 0)
  if (n > 0 && minSamples > 0 && n < minSamples) return 'lowSample'
  return 'direct'
}

export function probabilityCellClassName(cell, model = null) {
  const status = probabilityCellStatus(cell, model)
  if (status === 'noData') return 'probability-cell-no-data'
  if (status === 'diffused' || status === 'lowSample') return 'probability-cell-insufficient'
  return ''
}

export function probabilityCellTitle(cell, model = null) {
  const status = probabilityCellStatus(cell, model)
  if (status === 'noData') return '无数据格子：所在行或列没有真实样本支撑，不扩散。'
  if (status === 'diffused') return `数据不足：本格真实样本为 0，概率来自距离 ${Math.round(Number(cell.diffusionRadius || 0))} 的真实格子扩散。`
  if (status === 'lowSample') return `数据偏少：本格真实样本 ${Math.round(Number(cell.n || 0))} 个，低于建议阈值 ${Math.round(Number(model?.minCoordinateSamples || model?.settings?.minCoordinateSamples || 0))}。`
  return `真实样本：${Math.round(Number(cell?.n || 0))} 个。`
}

export function buildReviewProbabilityModel(rows, probabilitySettings = {}, mapping = {}) {
  const settings = {
    logBase: Math.max(1.1, Number(probabilitySettings.logBase) || 2),
    bucketSize: Math.max(0.1, Number(probabilitySettings.bucketSize) || 1),
    studyCountBucketSize: Math.max(1, Math.round(Number(probabilitySettings.studyCountBucketSize) || 5)),
    useStudyCountDimension: probabilitySettings.useStudyCountDimension !== false,
    minCoordinateSamples: Math.max(4, Math.round(Number(probabilitySettings.minCoordinateSamples) || 4)),
    nearestNeighborCount: Math.max(1, Math.round(Number(probabilitySettings.nearestNeighborCount) || 5)),
  }

  const memoryBuckets = []
  const studyBuckets = []
  const memoryMap = new Map()
  const studyMap = new Map()
  const rawByKey = new Map()
  const byMemory = new Map()
  const global = makeEmptyCounts()
  let reviewSampleCount = 0
  let excludedNewSampleCount = 0

  function addRating(target, rating, amount = 1) {
    const n = Math.max(0, Number(amount) || 0)
    if (!n) return
    target.n += n
    if (Object.prototype.hasOwnProperty.call(target, rating)) target[rating] += n
  }

  function addSample(item, fallbackIndex) {
    const days = normalizeDays(item.days ?? item.reviewSpanDays ?? item.stability, NaN)
    if (!Number.isFinite(days) || days < 1) return
    const rawStudyCount = normalizeStudyCount(item.studyCount, 0)
    // FSRS 样本的 study_count 是“答完这次以后”的累计次数。
    // 例如 study_count=1 且结果=认识，本质是学习次数 0 时做出的首次结果；
    // 因此建模坐标必须使用本次作答前的次数 rawStudyCount - 1。
    if (rawStudyCount < 1) {
      excludedNewSampleCount += 1
      return
    }
    const modelStudyCount = Math.max(0, rawStudyCount - 1)
    const rating = ratingFromState(item.response || item.lastResponse || item.state || item.currentState, mapping)
    if (!rating) return

    const memoryBucket = bucketMemory(days, settings)
    const studyBucket = bucketStudyCount(modelStudyCount, settings)
    pushUniqueBucket(memoryBuckets, memoryMap, memoryBucket)
    pushUniqueBucket(studyBuckets, studyMap, studyBucket)

    const memoryCounts = byMemory.get(memoryBucket.key) || makeEmptyCounts()
    addRating(memoryCounts, rating)
    byMemory.set(memoryBucket.key, memoryCounts)

    const coordKey = `${memoryBucket.key}|${studyBucket.key}`
    const coord = rawByKey.get(coordKey) || {
      key: coordKey,
      memoryKey: memoryBucket.key,
      memoryLabel: memoryBucket.label,
      memorySort: memoryBucket.lower,
      studyKey: studyBucket.key,
      studyLabel: studyBucket.label,
      studySort: studyBucket.lower,
      counts: makeEmptyCounts(),
      n: 0,
      samples: [],
    }
    addRating(coord.counts, rating)
    coord.n = coord.counts.n
    if (coord.samples.length < 12) coord.samples.push(itemWord(item, `样本${fallbackIndex}`))
    rawByKey.set(coordKey, coord)

    addRating(global, rating)
    reviewSampleCount += 1
  }

  let sampleIndex = 0
  for (const row of rows || []) {
    if (!row || row.isGapMarker) continue
    const outcomeItems = Array.isArray(row.memoryReviewOutcomeItems) ? row.memoryReviewOutcomeItems : []
    const fallbackItems = outcomeItems.length
      ? []
      : Array.isArray(row.memoryReviewSpanItems)
        ? row.memoryReviewSpanItems
        : Array.isArray(row.memoryDiffItems)
          ? row.memoryDiffItems
          : []
    for (const item of [...outcomeItems, ...fallbackItems]) {
      sampleIndex += 1
      addSample(item, sampleIndex)
    }
  }

  memoryBuckets.sort((a, b) => a.lower - b.lower)
  studyBuckets.sort((a, b) => a.lower - b.lower)

  const fallbackDistribution = countsToDistribution(global, [
    { rating: 'again', prob: 0.15 },
    { rating: 'hard', prob: 0.25 },
    { rating: 'good', prob: 0.5 },
    { rating: 'easy', prob: 0.1 },
  ])
  const fallbackObj = Object.fromEntries(fallbackDistribution.map(x => [x.rating, x.prob]))

  const memoryRows = memoryBuckets.map(bucket => {
    const counts = byMemory.get(bucket.key) || makeEmptyCounts()
    const distribution = countsToDistribution(counts, fallbackDistribution)
    const probs = Object.fromEntries(distribution.map(x => [x.rating, x.prob]))
    return {
      ...bucket,
      ...counts,
      total: counts.n,
      probs,
      distribution,
    }
  })

  function countTotal(counts) {
    return ['again', 'hard', 'good', 'easy'].reduce((sum, key) => sum + Math.max(0, Number(counts?.[key]) || 0), 0)
  }

  function mergeWeightedCounts(target, source, weight) {
    const w = Math.max(0, Number(weight) || 0)
    if (!w) return target
    for (const rating of ['again', 'hard', 'good', 'easy']) {
      target[rating] += Math.max(0, Number(source?.[rating]) || 0) * w
    }
    target.n += Math.max(0, Number(source?.n) || 0) * w
    return target
  }

  function coordinateKey(memorySort, studySort) {
    return `${Number(memorySort)}|${Number(studySort)}`
  }

  function buildCoordinateIndex(rows) {
    const rowByCoord = new Map()
    const memorySet = new Set()
    const studySet = new Set()
    const memoryRealSamples = new Map()
    const studyRealSamples = new Map()
    const seeds = []

    for (const row of rows || []) {
      if (!row) continue
      const memorySort = Number(row.memorySort)
      const studySort = Number(row.studySort)
      if (!Number.isFinite(memorySort) || !Number.isFinite(studySort)) continue
      const realN = Math.max(0, Number(row.counts?.n ?? row.total ?? row.n) || 0)
      memorySet.add(memorySort)
      studySet.add(studySort)
      rowByCoord.set(coordinateKey(memorySort, studySort), row)
      if (realN > 0) {
        seeds.push(row)
        memoryRealSamples.set(memorySort, (memoryRealSamples.get(memorySort) || 0) + realN)
        studyRealSamples.set(studySort, (studyRealSamples.get(studySort) || 0) + realN)
      }
    }

    const memorySorts = [...memorySet].sort((a, b) => a - b)
    const studySorts = [...studySet].sort((a, b) => a - b)
    const memoryIndex = new Map(memorySorts.map((value, index) => [value, index]))
    const studyIndex = new Map(studySorts.map((value, index) => [value, index]))

    return {
      rowByCoord,
      memorySorts,
      studySorts,
      memoryIndex,
      studyIndex,
      memoryRealSamples,
      studyRealSamples,
      seeds,
    }
  }

  function compareDiffusionRows(a, b) {
    return (a.dist - b.dist)
      || (Math.max(0, Number(b.row?.counts?.n ?? b.row?.total ?? b.row?.n) || 0) - Math.max(0, Number(a.row?.counts?.n ?? a.row?.total ?? a.row?.n) || 0))
      || (a.memoryDelta - b.memoryDelta)
      || (a.studyDelta - b.studyDelta)
      || (Number(a.row.memorySort || 0) - Number(b.row.memorySort || 0))
      || (Number(a.row.studySort || 0) - Number(b.row.studySort || 0))
  }

  function isDirectProbabilitySeed(row) {
    const n = Number(row?.counts?.n ?? row?.total ?? row?.n)
    return row && Number.isFinite(n) && n > 0
  }

  function countsToProbObject(counts) {
    const n = Number(counts?.n || 0)
    if (!n) return null
    const out = {}
    for (const rating of ['again', 'hard', 'good', 'easy']) {
      out[rating] = Math.max(0, Number(counts?.[rating]) || 0) / n
    }
    return out
  }

  function probObjectToDistribution(probs) {
    return ['again', 'hard', 'good', 'easy'].map(rating => ({ rating, prob: Math.max(0, Number(probs?.[rating]) || 0) }))
  }

  function normalizedProbabilityFromSources(sources) {
    const scores = { again: 0, hard: 0, good: 0, easy: 0 }
    let used = 0
    const neighborLabels = []
    for (const item of sources || []) {
      const probs = countsToProbObject(item.row?.counts)
      if (!probs) continue
      const distance = Math.max(1, Number(item.dist) || 1)
      for (const rating of ['again', 'hard', 'good', 'easy']) {
        // 用户规则：扩散概率只从真实格子的 p 出发，按 p / 距离 累加。
        // 最后归一化成合法概率，避免四个概率和小于 1。
        scores[rating] += probs[rating] / distance
      }
      used += 1
      if (neighborLabels.length < 20) neighborLabels.push(item.row.label || `${item.row.memoryLabel}/${item.row.studyLabel}`)
    }
    if (!used) return null
    const total = ['again', 'hard', 'good', 'easy'].reduce((sum, rating) => sum + scores[rating], 0)
    if (!(total > 0)) return null
    const probs = Object.fromEntries(['again', 'hard', 'good', 'easy'].map(rating => [rating, scores[rating] / total]))
    return { probs, distribution: probObjectToDistribution(probs), neighborLabels }
  }

  function buildDiffusedCoordinateDistribution(memoryBucket, studyBucket, original, coordinateIndex) {
    if (isDirectProbabilitySeed(original)) {
      const counts = mergeWeightedCounts(makeEmptyCounts(), original.counts, 1)
      const distribution = countsToDistribution(counts, fallbackDistribution)
      const probs = Object.fromEntries(distribution.map(x => [x.rating, x.prob]))
      return {
        counts,
        probs,
        distribution,
        rawSamples: Number(original.counts?.n || 0),
        weightedSamples: Number(original.counts?.n || 0),
        source: 'self',
        radius: 0,
        neighborLabels: [],
      }
    }

    if (!coordinateIndex?.seeds?.length) return null

    const ownMemory = Number(memoryBucket.lower)
    const ownStudy = Number(studyBucket.lower)
    const ownMemoryIndex = coordinateIndex.memoryIndex?.get(ownMemory)
    const ownStudyIndex = coordinateIndex.studyIndex?.get(ownStudy)
    if (!Number.isFinite(ownMemoryIndex) || !Number.isFinite(ownStudyIndex)) return null

    // 只对“有真实样本的行 × 有真实样本的列”中的空格子补概率。
    // 整行或整列真实样本为 0 时，不做扩散，避免边缘空桶被一圈圈填满。
    const memoryHasRealSamples = Math.max(0, Number(coordinateIndex.memoryRealSamples?.get(ownMemory)) || 0) > 0
    const studyHasRealSamples = Math.max(0, Number(coordinateIndex.studyRealSamples?.get(ownStudy)) || 0) > 0
    if (!memoryHasRealSamples || !studyHasRealSamples) return null

    const sources = []
    for (const seed of coordinateIndex.seeds) {
      const seedMemoryIndex = coordinateIndex.memoryIndex?.get(Number(seed.memorySort))
      const seedStudyIndex = coordinateIndex.studyIndex?.get(Number(seed.studySort))
      if (!Number.isFinite(seedMemoryIndex) || !Number.isFinite(seedStudyIndex)) continue
      const memoryDelta = Math.abs(seedMemoryIndex - ownMemoryIndex)
      const studyDelta = Math.abs(seedStudyIndex - ownStudyIndex)
      const dist = memoryDelta + studyDelta
      if (dist <= 0) continue
      sources.push({ row: seed, dist, memoryDelta, studyDelta })
    }

    if (!sources.length) return null
    sources.sort(compareDiffusionRows)

    // 从真实格子作为种子逐圈扩散；目标格子只取第一次到达它的那一圈。
    // 不遍历空格子，也不让上一轮扩散结果继续作为下一轮概率来源。
    const radius = Math.min(...sources.map(item => item.dist).filter(dist => dist > 0))
    const ringSources = sources.filter(item => item.dist === radius)
    const estimated = normalizedProbabilityFromSources(ringSources)
    if (!estimated) return null

    const counts = makeEmptyCounts()
    for (const rating of ['again', 'hard', 'good', 'easy']) counts[rating] = estimated.probs[rating]
    counts.n = 0

    return {
      counts,
      probs: estimated.probs,
      distribution: estimated.distribution,
      rawSamples: ringSources.reduce((sum, item) => sum + Number(item.row.counts?.n || 0), 0),
      weightedSamples: ringSources.reduce((sum, item) => sum + Number(item.row.counts?.n || 0) / Math.max(1, item.dist), 0),
      source: 'diffusion-fill',
      radius,
      neighborLabels: estimated.neighborLabels,
    }
  }

  const byKey = new Map()
  const rawRows = new Map(rawByKey)
  const coordinateIndex = buildCoordinateIndex([...rawByKey.values()])
  if (settings.useStudyCountDimension !== false) {
    for (const memoryBucket of memoryBuckets) {
      for (const studyBucket of studyBuckets) {
        const key = `${memoryBucket.key}|${studyBucket.key}`
        const original = rawByKey.get(key) || null
        const filled = buildDiffusedCoordinateDistribution(memoryBucket, studyBucket, original, coordinateIndex) || {
          counts: makeEmptyCounts(),
          probs: null,
          distribution: null,
          rawSamples: 0,
          weightedSamples: 0,
          source: 'noData',
          radius: null,
          neighborLabels: [],
        }
        const distribution = filled.distribution || null
        const probs = filled.probs || { again: 0, hard: 0, good: 0, easy: 0 }
        const realN = Number(original?.counts?.n || 0)
        byKey.set(key, {
          key,
          memoryKey: memoryBucket.key,
          memoryLabel: memoryBucket.label,
          studyKey: studyBucket.key,
          studyLabel: studyBucket.label,
          memorySort: memoryBucket.lower,
          studySort: studyBucket.lower,
          n: realN,
          total: realN,
          counts: original?.counts || makeEmptyCounts(),
          modelCounts: filled.counts,
          again: probs.again * realN,
          hard: probs.hard * realN,
          good: probs.good * realN,
          easy: probs.easy * realN,
          probs,
          distribution,
          usedNearestFallback: filled.source !== 'self',
          fallbackMethod: filled.source,
          fallbackRawSamples: filled.rawSamples,
          fallbackWeightedSamples: filled.weightedSamples,
          fallbackNeighborLabels: filled.neighborLabels,
          diffusionRadius: filled.radius,
          samples: original?.samples || [],
        })
      }
    }
  }

  return {
    settings,
    useStudyCountDimension: settings.useStudyCountDimension,
    memoryBuckets: memoryRows,
    studyBuckets,
    byKey,
    rawByKey: rawRows,
    byMemory,
    memoryByKey: new Map(memoryRows.map(row => [row.key, row])),
    globalCounts: global,
    globalDistribution: fallbackDistribution,
    reviewSampleCount,
    excludedNewSampleCount,
    minCoordinateSamples: settings.minCoordinateSamples,
    nearestNeighborCount: settings.nearestNeighborCount,
    excludedNewWordDate: settings.useStudyCountDimension
      ? '建模时使用本次作答前的学习次数：study_count=1 的结果归入学习次数 0；预测应用时当前次数 0 也使用学习次数 0 的概率。'
      : '建模时使用本次作答前的学习次数：study_count=1 的结果归入学习次数 0；关闭学习次数维度时仅按记忆持久度概率估计。',
  }
}


function probabilitySourceLabel(cell, fallbackType = '') {
  if (cell?.fallbackMethod === 'self' || cell?.usedNearestFallback === false) return '本分桶真实样本'
  if (cell?.fallbackMethod === 'diffusion-fill') return `真实格子扩散${cell.diffusionRadius ? `（${Math.round(Number(cell.diffusionRadius))}圈）` : ''}`
  if (fallbackType === 'memory') return '同记忆持久度真实样本'
  if (fallbackType === 'global') return '全局真实样本'
  if (cell?.fallbackMethod === 'noData') return '无二维分桶数据'
  return cell?.fallbackMethod || '-'
}

function probabilityInfoForWord(model, word) {
  if (!model) {
    return {
      distribution: DEFAULT_DISTRIBUTION,
      memoryBucket: null,
      studyBucket: null,
      cell: null,
      bucketLabel: '-',
      cellText: '-',
      sourceLabel: '默认概率',
      diffusionRadius: null,
      n: 0,
    }
  }

  const memoryBucket = bucketMemory(word.stability, model.settings)
  const memoryCounts = mapValue(model.byMemory, memoryBucket.key)
  const memoryDistribution = countsToDistribution(memoryCounts, model.globalDistribution)

  if (model.useStudyCountDimension === false) {
    return {
      distribution: memoryDistribution,
      memoryBucket,
      studyBucket: null,
      cell: memoryCounts,
      bucketLabel: `${memoryBucket.label} / 全部学习次数`,
      cellText: formatReviewProbabilityCellText(memoryCounts),
      sourceLabel: '记忆持久度真实样本',
      diffusionRadius: null,
      n: Number(memoryCounts?.n || 0),
    }
  }

  const studyBucket = bucketStudyCount(word.studyCount, model.settings)
  const key = `${memoryBucket.key}|${studyBucket.key}`
  const cell = mapValue(model.byKey, key)

  if (cell?.distribution && cell.fallbackMethod !== 'noData') {
    return {
      distribution: cell.distribution,
      memoryBucket,
      studyBucket,
      cell,
      bucketLabel: `${memoryBucket.label} × ${studyBucket.label}次`,
      cellText: formatReviewProbabilityCellText(cell),
      sourceLabel: probabilitySourceLabel(cell),
      diffusionRadius: cell.diffusionRadius ?? null,
      n: Number(cell.n || 0),
    }
  }

  if (memoryCounts?.n) {
    return {
      distribution: memoryDistribution,
      memoryBucket,
      studyBucket,
      cell,
      bucketLabel: `${memoryBucket.label} × ${studyBucket.label}次`,
      cellText: cell ? formatReviewProbabilityCellText(cell) : '-',
      sourceLabel: probabilitySourceLabel(cell, 'memory'),
      diffusionRadius: cell?.diffusionRadius ?? null,
      n: Number(cell?.n || 0),
    }
  }

  return {
    distribution: model.globalDistribution || DEFAULT_DISTRIBUTION,
    memoryBucket,
    studyBucket,
    cell,
    bucketLabel: `${memoryBucket.label} × ${studyBucket.label}次`,
    cellText: cell ? formatReviewProbabilityCellText(cell) : '-',
    sourceLabel: probabilitySourceLabel(cell, 'global'),
    diffusionRadius: cell?.diffusionRadius ?? null,
    n: Number(cell?.n || 0),
  }
}

function distributionForWord(model, word) {
  return probabilityInfoForWord(model, word).distribution
}

function pickRating(distribution, seed) {
  const r = deterministicNoise(seed)
  let acc = 0
  for (const item of distribution || []) {
    acc += Number(item.prob || 0)
    if (r <= acc) return item.rating
  }
  return 'good'
}

function addToQueue(queues, day, word) {
  const key = Math.max(1, Math.round(Number(day) || 1))
  if (!queues.has(key)) queues.set(key, [])
  queues.get(key).push(word)
}

function buildReferenceWords(referenceRow, mapping) {
  const byKey = new Map()
  const reviewItems = Array.isArray(referenceRow?.memoryReviewSpanItems)
    ? referenceRow.memoryReviewSpanItems
    : Array.isArray(referenceRow?.memoryDiffItems)
      ? referenceRow.memoryDiffItems
      : []
  for (const item of reviewItems) {
    const key = itemKey(item, `review-${byKey.size}`)
    if (!key) continue
    byKey.set(key, {
      key,
      word: itemWord(item, key),
      source: 'known',
      stability: Math.max(1, normalizeDays(item.days, 1)),
      studyCount: normalizeStudyCount(item.studyCount, 0),
      state: item.state || item.currentState || item.lastResponse || '',
      rating: ratingFromState(item.state || item.currentState || item.lastResponse, mapping),
      seed: Math.max(1, byKey.size + 1) * 1009 + Math.max(1, normalizeDays(item.days, 1)),
    })
  }
  return byKey
}

function buildDueQueues(referenceRow, knownWords) {
  const queues = new Map()
  const dueItems = Array.isArray(referenceRow?.memoryNextDueItems) ? referenceRow.memoryNextDueItems : []
  for (const item of dueItems) {
    const key = itemKey(item, `due-${queues.size}`)
    const existing = knownWords.get(key) || {
      key,
      word: itemWord(item, key),
      source: 'known',
      stability: Math.max(1, normalizeDays(item.reviewSpanDays || item.stability || 1, 1)),
      studyCount: normalizeStudyCount(item.studyCount, 0),
      seed: Math.max(1, knownWords.size + 1) * 1009,
    }
    const dueDay = Math.max(1, normalizeDays(item.days, 1))
    addToQueue(queues, dueDay, existing)
    if (!knownWords.has(key)) knownWords.set(key, existing)
  }
  return queues
}

function countTargetWords(words, targetSettings, currentDay, queues) {
  const targetDays = Math.max(0, Number(targetSettings.days) || 0)
  const metric = targetSettings.metric || 'review_span'
  let count = 0
  if (metric === 'critical_point') {
    for (const word of words.values()) {
      let nextDue = Infinity
      for (const [day, list] of queues.entries()) {
        if (day < currentDay) continue
        if (list.some(x => x.key === word.key)) nextDue = Math.min(nextDue, day - currentDay)
      }
      if (nextDue > targetDays) count += 1
    }
    return count
  }
  for (const word of words.values()) {
    if (Number(word.stability || 0) > targetDays) count += 1
  }
  return count
}

function coercePositiveInt(value, fallback = 0) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : fallback
}

export async function buildDashboardPrediction(rows, options = {}, onProgress = null) {
  const cleanRows = [...(rows || [])].filter(r => r && !r.isGapMarker && r.date).sort((a, b) => String(a.date).localeCompare(String(b.date)))
  const referenceRow = [...cleanRows].reverse().find(r => r.hasOverviewData) || cleanRows[cleanRows.length - 1]
  const latestDate = referenceRow?.date || todayDateOnly()
  const horizonDays = Math.max(1, coercePositiveInt(options.horizonDays, 30))
  const dailyBase = coercePositiveInt(options.dailyBaseValue, 0)
  const dailyMode = options.dailyMode === 'fixed_total' ? 'fixed_total' : 'fixed_new'
  const reviewLimit = options.reviewLimit === null || options.reviewLimit === undefined || options.reviewLimit === ''
    ? null
    : coercePositiveInt(options.reviewLimit, 0)
  const deferOverflow = options.deferOverflow !== false
  const mapping = options.ratingMapping || { known: 'good', vague: 'hard', forget: 'again' }
  const targetSettings = options.targetSettings || { metric: 'review_span', days: 30, count: '' }
  const modelRows = cleanRows.filter(row => {
    if (options.modelFrom && row.date < options.modelFrom) return false
    if (options.modelTo && row.date > options.modelTo) return false
    return true
  })
  const probabilityModel = buildReviewProbabilityModel(modelRows.length ? modelRows : cleanRows, options.probabilitySettings || {}, mapping)

  const knownWords = buildReferenceWords(referenceRow, mapping)
  const queues = buildDueQueues(referenceRow, knownWords)
  const activeWords = new Map(knownWords)
  const rowsOut = []
  const dayWordRatings = []
  const dayWordDetails = []
  let cumulativeNew = 0
  let cumulativeReview = 0
  let generatedIndex = 0
  let carriedReview = 0

  for (let day = 1; day <= horizonDays; day += 1) {
    const currentDate = addDays(latestDate, day)
    const dayEvents = []
    const due = queues.get(day) || []
    queues.delete(day)
    let reviewCapacity = due.length
    let predictedNew = dailyBase

    if (dailyMode === 'fixed_total') {
      const totalCapacity = dailyBase
      reviewCapacity = Math.min(due.length, totalCapacity)
      predictedNew = Math.max(0, totalCapacity - reviewCapacity)
    } else if (reviewLimit !== null) {
      reviewCapacity = Math.min(due.length, reviewLimit)
    }

    const reviewsToday = due.slice(0, reviewCapacity)
    const overflow = due.slice(reviewCapacity)
    if (deferOverflow || dailyMode === 'fixed_new') {
      for (const word of overflow) addToQueue(queues, day + 1, word)
    }
    carriedReview = overflow.length

    let knownOldReview = 0
    let generatedReview = 0
    for (const word of reviewsToday) {
      if (word.source === 'generated') generatedReview += 1
      else knownOldReview += 1
      const probabilityInfo = probabilityInfoForWord(probabilityModel, word)
      const distribution = probabilityInfo.distribution
      const rating = pickRating(distribution, word.seed + day * 97 + normalizeStudyCount(word.studyCount, 0) * 7919)
      const prevStability = word.stability
      const nextStability = applySimpleFsrsReview(word, rating, prevStability)
      word.studyCount = normalizeStudyCount(word.studyCount, 0) + 1
      word.lastRating = rating
      const nextDueDay = day + Math.max(1, Math.round(nextStability))
      addToQueue(queues, nextDueDay, word)
      const event = {
        day,
        date: currentDate,
        action: 'review',
        actionLabel: word.source === 'generated' ? '模拟词复习' : '库存词复习',
        word: word.word,
        key: word.key,
        source: word.source,
        sourceLabel: word.source === 'generated' ? '模拟词' : '库存词',
        rating,
        prevStability,
        nextStability,
        nextDueDay,
        studyCount: word.studyCount,
        probabilityBucket: probabilityInfo.bucketLabel,
        probabilityCellText: probabilityInfo.cellText,
        probabilitySource: probabilityInfo.sourceLabel,
        probabilityN: probabilityInfo.n,
        probabilityDiffusionRadius: probabilityInfo.diffusionRadius,
      }
      dayEvents.push(event)
      dayWordRatings.push(event)
    }

    for (let i = 0; i < predictedNew; i += 1) {
      generatedIndex += 1
      const word = {
        key: `new-${generatedIndex}`,
        word: `新词${generatedIndex}`,
        source: 'generated',
        stability: 1,
        studyCount: 1,
        seed: 1000003 + generatedIndex * 17,
      }
      activeWords.set(word.key, word)
      addToQueue(queues, day + 1, word)
      const event = {
        day,
        date: currentDate,
        action: 'new',
        actionLabel: '模拟词新学',
        word: word.word,
        key: word.key,
        source: word.source,
        sourceLabel: '模拟词',
        rating: 'new',
        prevStability: 0,
        nextStability: word.stability,
        nextDueDay: day + 1,
        studyCount: word.studyCount,
        probabilityBucket: '新学：无复习概率分桶',
        probabilityCellText: '-',
        probabilitySource: '新学不抽复习概率',
        probabilityN: 0,
        probabilityDiffusionRadius: null,
      }
      dayEvents.push(event)
      dayWordRatings.push(event)
    }

    cumulativeNew += predictedNew
    cumulativeReview += reviewsToday.length
    const targetMatched = countTargetWords(activeWords, targetSettings, day, queues)
    const targetCount = coercePositiveInt(targetSettings.count, 0)

    rowsOut.push({
      predictionDay: day,
      futureDayLabel: day,
      futureDayTitle: `第${day}天`,
      date: currentDate,
      predictedNew,
      predictedReview: reviewsToday.length,
      predictedTotal: predictedNew + reviewsToday.length,
      cumulativeNew,
      cumulativeReview,
      knownOldReview,
      generatedReview,
      deferredReview: carriedReview,
      carriedReview,
      reviewCapacity: reviewLimit,
      reviewLimit,
      dailyTargetMatchedCount: targetMatched,
      familiarAboveTarget: targetMatched,
      criticalAboveTarget: targetMatched,
      targetCount,
      targetDays: targetSettings.days,
      targetMetric: targetSettings.metric,
      words: dayEvents,
    })

    dayWordDetails.push({
      day,
      date: currentDate,
      words: dayEvents,
    })

    if (typeof onProgress === 'function') onProgress(day, horizonDays)
    if (day % 10 === 0) await Promise.resolve()
  }

  const dist = probabilityModel.globalDistribution || DEFAULT_DISTRIBUTION
  const distSummary = {
    global: dist.map(x => `${RATING_LABELS[x.rating] || x.rating}:${Math.round(Number(x.prob || 0) * 100)}%`).join(' / '),
    sampleCount: probabilityModel.globalCounts?.n || 0,
  }

  return {
    rows: rowsOut,
    dailyBase,
    dailyMode,
    reviewLimit,
    initMode: options.initMode || 'fsrs_estimate_by_count',
    ratingMapping: mapping,
    targetSettings,
    simulationWordCount: activeWords.size,
    availableSimulationWordCount: knownWords.size,
    probabilityModel,
    distSummary,
    fsrsProfile: { ratingDistribution: dist },
    dayWordRatings,
    dayWordDetails,
    referenceDate: latestDate,
  }
}

export function buildFutureStudySeries(prediction) {
  const rows = prediction?.rows || []
  return [
    { name: '预测新学', key: 'predictedNew', data: rows.map(r => Number(r.predictedNew) || 0) },
    { name: '预测复习', key: 'predictedReview', data: rows.map(r => Number(r.predictedReview) || 0) },
  ]
}

export function predictionSummaryText(prediction) {
  if (!prediction?.rows?.length) return '暂无预测结果。'
  const totalNew = prediction.rows.reduce((s, r) => s + (Number(r.predictedNew) || 0), 0)
  const totalReview = prediction.rows.reduce((s, r) => s + (Number(r.predictedReview) || 0), 0)
  const total = totalNew + totalReview
  return `未来 ${prediction.rows.length} 天：预测总学习量 ${formatPredictionCount(total)}，新学 ${formatPredictionCount(totalNew)}，复习 ${formatPredictionCount(totalReview)}；概率样本 ${prediction.distSummary?.sampleCount || 0}。`
}

export function buildPredictionMarkdown(prediction) {
  if (!prediction?.rows?.length) return '暂无预测数据。'
  const lines = []
  lines.push(`模型参考日：${prediction.referenceDate || '-'}`)
  lines.push(predictionSummaryText(prediction))
  lines.push('')
  lines.push('几天后\t日期\t预测新学\t预测复习\t预测总量\t已知旧词复习\t预测回流复习\t顺延复习\t累计新学\t累计复习\t目标达标词数')
  for (const row of prediction.rows) {
    lines.push([
      row.predictionDay,
      row.date,
      row.predictedNew,
      row.predictedReview,
      row.predictedTotal,
      row.knownOldReview,
      row.generatedReview,
      row.deferredReview,
      row.cumulativeNew,
      row.cumulativeReview,
      row.dailyTargetMatchedCount,
    ].join('\t'))
  }
  return lines.join('\n')
}

function mapValue(maybeMap, key) {
  if (!maybeMap) return undefined
  if (typeof maybeMap.get === 'function') return maybeMap.get(key)
  return maybeMap[key]
}

export function buildProbabilityTableRows(model) {
  if (!model) return []
  const memoryRows = model.memoryBuckets || []
  const studyRows = model.studyBuckets || []
  if (model.useStudyCountDimension === false) {
    return memoryRows.map(memory => {
      const cell = mapValue(model.byMemory, memory.key)
      return {
        memory,
        cells: [{
          key: 'all',
          label: '全部',
          text: formatReviewProbabilityCellText(cell),
          className: probabilityCellClassName(cell, model),
          title: probabilityCellTitle(cell, model),
          raw: cell,
        }],
      }
    })
  }
  return memoryRows.map(memory => ({
    memory,
    cells: studyRows.map(study => {
      const cell = mapValue(model.byKey, `${memory.key}|${study.key}`)
      return {
        key: study.key,
        label: study.label,
        text: formatReviewProbabilityCellText(cell),
        className: probabilityCellClassName(cell, model),
        title: probabilityCellTitle(cell, model),
        raw: cell,
      }
    }),
  }))
}
