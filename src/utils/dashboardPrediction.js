import { formatPredictionCount } from '@/utils/dashboardParity'

function pctFromCell(cell, rating) {
  if (!cell) return 0
  if (cell.probs && Number.isFinite(Number(cell.probs[rating]))) {
    return Math.round(Number(cell.probs[rating]) * 100)
  }
  const n = Number(cell.n || cell.total || cell.counts?.n || 0)
  if (!n) return 0
  return Math.round((Number((cell[rating] ?? cell.counts?.[rating]) || 0) / n) * 100)
}

export function formatReviewProbabilityCellText(cell) {
  if (!cell) return '-'
  const n = Number(cell.n || 0)
  if (!Number.isFinite(n) || n <= 0) return '-'
  return `${Math.max(0, Math.round(n))}/${pctFromCell(cell, 'again')}/${pctFromCell(cell, 'hard')}/${pctFromCell(cell, 'good')}/${pctFromCell(cell, 'easy')}`
}

function emptyCounts() {
  return { n: 0, again: 0, hard: 0, good: 0, easy: 0 }
}

function addCellCounts(target, cell) {
  const counts = cell?.modelCounts || cell?.counts || cell || {}
  target.n += Number(cell?.n ?? cell?.total ?? counts.n ?? 0) || 0
  for (const rating of ['again', 'hard', 'good', 'easy']) {
    if (cell?.probs && Number.isFinite(Number(cell.probs[rating]))) {
      target[rating] += Number(cell.probs[rating]) * (Number(cell.n || cell.total || 0) || 0)
    } else {
      target[rating] += Number(counts[rating] ?? cell?.[rating] ?? 0) || 0
    }
  }
}

function aggregateCells(cells) {
  const counts = emptyCounts()
  for (const cell of cells) addCellCounts(counts, cell)
  const n = counts.n
  const probs = {
    again: n > 0 ? counts.again / n : 0,
    hard: n > 0 ? counts.hard / n : 0,
    good: n > 0 ? counts.good / n : 0,
    easy: n > 0 ? counts.easy / n : 0,
  }
  return {
    n,
    total: n,
    counts,
    modelCounts: counts,
    probs,
    again: counts.again,
    hard: counts.hard,
    good: counts.good,
    easy: counts.easy,
  }
}

function memoryRangeLabel(memoryRows, startIndex, endIndex) {
  const first = memoryRows[startIndex]
  const last = memoryRows[endIndex]
  if (!first || !last) return '-'
  const lower = Number(first.lower)
  const upper = Number(last.upper)
  if (Number.isFinite(lower) && Number.isFinite(upper)) {
    return lower === upper ? `${Math.round(lower)}天` : `${Math.round(lower)}-${Math.round(upper)}天`
  }
  return first.label === last.label ? first.label : `${first.label}-${last.label}`
}

function probabilityCellTitle(cell) {
  if (cell?.skip) return ''
  if (!cell?.raw || Number(cell.raw.n || 0) <= 0) return '该区域没有真实样本。'
  const rows = cell.rowspan > 1 ? `；向下合并 ${cell.rowspan} 个记忆持久度桶` : ''
  if (cell.insufficient) {
    return `统计区域：${cell.memoryRangeLabel} × ${cell.label}；真实样本 ${Math.round(Number(cell.raw.n || 0))} 个，低于目标样本数，未展示概率${rows}。`
  }
  return `统计区域：${cell.memoryRangeLabel} × ${cell.label}；真实样本：${Math.round(Number(cell.raw.n || 0))} 个${rows}。`
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
          className: '',
          title: cell ? `统计区域：${memory.label}；真实样本：${Math.round(Number(cell.n || 0))} 个。` : '该区域没有真实样本。',
          rowspan: 1,
          raw: cell,
        }],
      }
    })
  }

  const minSamples = Math.max(1, Math.round(Number(model?.settings?.memoryBucketSize || model?.minCoordinateSamples || 10) || 10))
  const visibleStudyRows = studyRows.filter(study => {
    const counts = aggregateCells(memoryRows.map(memory => mapValue(model.byKey, `${memory.key}|${study.key}`)).filter(Boolean))
    return counts.n >= minSamples
  })
  const rows = memoryRows.map(memory => ({ memory, cells: [] }))

  for (const study of visibleStudyRows) {
    const segments = []
    let rowIndex = 0
    while (rowIndex < memoryRows.length) {
      const collected = []
      const startIndex = rowIndex
      let endIndex = rowIndex
      while (endIndex < memoryRows.length) {
        const memory = memoryRows[endIndex]
        const cell = mapValue(model.byKey, `${memory.key}|${study.key}`)
        if (cell) collected.push(cell)
        const aggregate = aggregateCells(collected)
        if (aggregate.n >= minSamples || endIndex === memoryRows.length - 1) break
        endIndex += 1
      }

      const aggregate = aggregateCells(collected)
      const rowspan = Math.max(1, endIndex - startIndex + 1)
      segments.push({ startIndex, endIndex, rowspan, collected, aggregate })
      rowIndex = endIndex + 1
    }

    if (segments.length > 1) {
      const last = segments[segments.length - 1]
      if (last.aggregate.n > 0 && last.aggregate.n < minSamples) {
        const previous = segments[segments.length - 2]
        previous.endIndex = last.endIndex
        previous.rowspan = Math.max(1, previous.endIndex - previous.startIndex + 1)
        previous.collected = [...previous.collected, ...last.collected]
        previous.aggregate = aggregateCells(previous.collected)
        segments.pop()
      }
    }

    for (const segment of segments) {
      const insufficient = segment.aggregate.n > 0 && segment.aggregate.n < minSamples
      const cell = {
        key: `${study.key}:${segment.startIndex}-${segment.endIndex}`,
        label: study.label,
        text: insufficient ? '-' : formatReviewProbabilityCellText(segment.aggregate),
        className: '',
        title: '',
        rowspan: segment.rowspan,
        raw: segment.aggregate,
        insufficient,
        memoryRangeLabel: memoryRangeLabel(memoryRows, segment.startIndex, segment.endIndex),
      }
      cell.title = probabilityCellTitle(cell)
      rows[segment.startIndex].cells.push(cell)
      for (let i = segment.startIndex + 1; i <= segment.endIndex; i += 1) {
        rows[i].cells.push({
          key: `${study.key}:skip:${i}`,
          label: study.label,
          text: '',
          className: '',
          title: '',
          rowspan: 0,
          skip: true,
        })
      }
    }
  }

  rows.studyRows = visibleStudyRows
  return rows
}
