export const MEMORY_MODE_REVIEW_SPAN = 'review_span'
export const MEMORY_MODE_CRITICAL_POINT = 'critical_point'
export const MEMORY_MODE_OVERDUE = 'overdue'
export const FALLBACK_MEMORY_THRESHOLDS = '1,2,3,4,5,6,7,15,30'

export const MEMORY_SERIES_COLORS = [
  '#2563eb',
  '#10b981',
  '#f59e0b',
  '#ef4444',
  '#8b5cf6',
  '#14b8a6',
  '#ec4899',
  '#6b7280',
]

function makeThresholdSpec(id, lower, lowerInclusive, upper, upperInclusive, headerLabel) {
  return { id, lower, lowerInclusive, upper, upperInclusive, headerLabel }
}

function formatThresholdNumber(value) {
  if (!Number.isFinite(value)) return ''
  return Number.isInteger(value) ? String(value) : String(value).replace(/\.0+$/, '')
}

function formatSignedThresholdLabel(lower, upper) {
  if (lower === upper) {
    return `${formatThresholdNumber(lower)}天`
  }
  return `${formatThresholdNumber(lower)}-${formatThresholdNumber(upper)}天`
}

export function parseMemoryThresholdSpecsFromText(text) {
  const rawParts = String(text || '')
    .replace(/，/g, ',')
    .replace(/；/g, ',')
    .replace(/;/g, ',')
    .split(',')
    .map(part => part.trim())
    .filter(Boolean)

  const specs = []
  const seen = new Set()

  for (const part of rawParts) {
    const normalized = part
      .replace(/～/g, '-')
      .replace(/—/g, '-')
      .replace(/–/g, '-')
      .replace(/\s+/g, '')

    let spec = null

    const cmpMatch = normalized.match(/^(<=|>=|<|>)(-?\d+(?:\.\d+)?)$/)
    if (cmpMatch) {
      const op = cmpMatch[1]
      const value = Number(cmpMatch[2])
      if (!Number.isFinite(value)) continue

      if (op === '>') {
        spec = makeThresholdSpec(`gt:${value}`, value, false, Infinity, true, `>${formatThresholdNumber(value)}天`)
      } else if (op === '>=') {
        spec = makeThresholdSpec(`gte:${value}`, value, true, Infinity, true, `≥${formatThresholdNumber(value)}天`)
      } else if (op === '<') {
        spec = makeThresholdSpec(`lt:${value}`, -Infinity, true, value, false, value === 0 ? '逾期' : `<${formatThresholdNumber(value)}天`)
      } else if (op === '<=') {
        spec = makeThresholdSpec(`lte:${value}`, -Infinity, true, value, true, `≤${formatThresholdNumber(value)}天`)
      }
    }

    if (!spec) {
      const rangeMatch = normalized.match(/^(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)$/)
      if (rangeMatch) {
        const start = Number(rangeMatch[1])
        const end = Number(rangeMatch[2])
        if (!Number.isFinite(start) || !Number.isFinite(end)) continue

        const lower = Math.min(start, end)
        const upper = Math.max(start, end)
        spec = makeThresholdSpec(`range:${lower}-${upper}`, lower, true, upper, true, formatSignedThresholdLabel(lower, upper))
      }
    }

    if (!spec) {
      const value = Number(normalized)
      if (!Number.isFinite(value)) continue
      spec = makeThresholdSpec(`exact:${value}`, value, true, value, true, formatSignedThresholdLabel(value, value))
    }

    if (seen.has(spec.id)) continue
    seen.add(spec.id)
    specs.push(spec)
  }

  return specs
}

export function thresholdIntersectsNonPositiveCriticalPoint(spec) {
  return thresholdIntersectsIntegerRange(spec, -Infinity, 0)
}

export function thresholdIntersectsReviewSpan(spec) {
  return thresholdIntersectsIntegerRange(spec, 1, Infinity) &&
    !thresholdIntersectsNonPositiveCriticalPoint(spec)
}

export function thresholdIntersectsCriticalPoint(spec) {
  return thresholdIntersectsIntegerRange(spec, 0, Infinity)
}

export function thresholdIntersectsOverdueAge(spec) {
  if (!spec) return false
  // <0 / <=0 这类阈值表达的是临界点已经为负，属于逾期区间。
  if (Number.isFinite(spec.upper) && spec.upper <= 0) return true
  // <7 / <=30 这类阈值在逾期图里按“逾期天数”解释。
  return thresholdIntersectsIntegerRange(spec, 1, Infinity)
}

function thresholdIntersectsIntegerRange(spec, minValue, maxValue = Infinity) {
  if (!spec) return false

  const lowerBound = Number.isFinite(spec.lower)
    ? (spec.lowerInclusive ? Math.ceil(spec.lower) : Math.floor(spec.lower) + 1)
    : -Infinity
  const upperBound = Number.isFinite(spec.upper)
    ? (spec.upperInclusive ? Math.floor(spec.upper) : Math.ceil(spec.upper) - 1)
    : Infinity

  const rangeMin = Number.isFinite(minValue) ? Math.ceil(minValue) : -Infinity
  const rangeMax = Number.isFinite(maxValue) ? Math.floor(maxValue) : Infinity

  return Math.max(lowerBound, rangeMin) <= Math.min(upperBound, rangeMax)
}

export function getReviewSpanThresholds(inputText) {
  const parsed = parseMemoryThresholdSpecsFromText(inputText)
  const specs = parsed.length ? parsed : parseMemoryThresholdSpecsFromText(FALLBACK_MEMORY_THRESHOLDS)
  return specs.filter(thresholdIntersectsReviewSpan)
}

export function memoryThresholdMatches(spec, days, mode = MEMORY_MODE_REVIEW_SPAN) {
  const rawValue = Number(days)
  if (!spec || !Number.isFinite(rawValue)) return false

  if (mode === MEMORY_MODE_REVIEW_SPAN) {
    if (rawValue < 1) return false
    return thresholdValueMatches(spec, rawValue)
  }

  if (mode === MEMORY_MODE_CRITICAL_POINT) {
    if (rawValue < 0) return false
    return thresholdValueMatches(spec, rawValue)
  }

  if (mode === MEMORY_MODE_OVERDUE) {
    if (rawValue >= 0) return false
    if (Number.isFinite(spec.upper) && spec.upper <= 0) return thresholdValueMatches(spec, rawValue)
    const overdueAge = Math.max(1, Math.abs(Math.floor(rawValue)))
    return thresholdValueMatches(spec, overdueAge)
  }

  return thresholdValueMatches(spec, rawValue)
}

function thresholdValueMatches(spec, value) {
  const lowerOk = !Number.isFinite(spec.lower) || (spec.lowerInclusive ? value >= spec.lower : value > spec.lower)
  const upperOk = !Number.isFinite(spec.upper) || (spec.upperInclusive ? value <= spec.upper : value < spec.upper)
  return lowerOk && upperOk
}

export function countReviewSpanForThreshold(row, spec) {
  const items = Array.isArray(row?.memoryReviewSpanItems)
    ? row.memoryReviewSpanItems
    : Array.isArray(row?.memoryDiffItems)
      ? row.memoryDiffItems
      : []

  if (items.length) {
    return items.filter(item => memoryThresholdMatches(spec, item.days, MEMORY_MODE_REVIEW_SPAN)).length
  }

  if (Array.isArray(row?.memoryDiffs)) {
    return row.memoryDiffs.filter(days => memoryThresholdMatches(spec, days, MEMORY_MODE_REVIEW_SPAN)).length
  }

  return 0
}

export function sortRowsByDateAsc(rows) {
  return [...(rows || [])].sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')))
}


export function countCriticalPointForThreshold(row, spec) {
  const items = Array.isArray(row?.memoryNextDueItems) ? row.memoryNextDueItems : []
  return items.filter(item => memoryThresholdMatches(spec, item.days, MEMORY_MODE_CRITICAL_POINT)).length
}

export function countOverdueForThreshold(row, spec) {
  const items = Array.isArray(row?.memoryNextDueItems) ? row.memoryNextDueItems : []
  return items.filter(item => memoryThresholdMatches(spec, item.days, MEMORY_MODE_OVERDUE)).length
}

export function getCriticalPointThresholds(inputText) {
  const parsed = parseMemoryThresholdSpecsFromText(inputText)
  const specs = parsed.length ? parsed : parseMemoryThresholdSpecsFromText(FALLBACK_MEMORY_THRESHOLDS)
  return specs.filter(thresholdIntersectsCriticalPoint)
}

export function getOverdueThresholds(inputText) {
  const parsed = parseMemoryThresholdSpecsFromText(inputText)
  const specs = parsed.length ? parsed : parseMemoryThresholdSpecsFromText('<7,<=30')
  return specs.filter(thresholdIntersectsOverdueAge)
}
