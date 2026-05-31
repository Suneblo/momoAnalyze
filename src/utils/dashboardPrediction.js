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
  const radius = Number(cell.diffusionRadius || 0)
  const hasDiffusedProbability = n <= 0 && radius > 0 && cell.probs && ['again', 'hard', 'good', 'easy'].every(rating => Number.isFinite(Number(cell.probs?.[rating])))
  if (!Number.isFinite(n) || (n <= 0 && !hasDiffusedProbability)) return '-'
  const diffusionMark = hasDiffusedProbability ? '*'.repeat(Math.max(1, Math.round(radius))) : ''
  return `${Math.max(0, Math.round(n))}${diffusionMark}/${pctFromCell(cell, 'again')}/${pctFromCell(cell, 'hard')}/${pctFromCell(cell, 'good')}/${pctFromCell(cell, 'easy')}`
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
