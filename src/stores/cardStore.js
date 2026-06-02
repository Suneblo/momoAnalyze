import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'

const STORAGE_KEY = 'momoCardStates'
export const FIXED_FIRST_CARD_ID = '复习情况'
const REMOVED_CARD_IDS = new Set(['历史总览', '每日学习进度', '记忆临界点统计', '学习情况'])

export const PAGE_REGISTRY = [
  { key: 'today', label: '当日情况' },
  { key: 'study', label: '学习情况' },
  { key: 'tools', label: '小工具' },
]

export const CARD_REGISTRY = [
  {
    id: '复习情况',
    page: 'today',
    copyKey: 'overviewChart',
    copyDetail: {
      status: '当前状态数据',
      today: '今日学习进度',
      critical: '记忆临界点统计',
    },
  },
  { id: '当日单词统计', page: 'today', copyKey: 'todayWordStats', copyDetail: { snapshots: '快照实时数据' } },
  { id: '当日时间点查看', page: 'today', copyKey: 'todayWorkspace', copyDetail: { summary: '时间点列表', latest: '最近时间点明细', compare: '最近两个时间点对比' } },
  { id: '数据减少提醒', page: 'today', copyKey: 'dataAlert', copyEnabled: false },
  { id: '自定义词', page: 'today', copyKey: 'customWords', copyEnabled: false },

  { id: '记忆持久度统计', page: 'study', copyKey: 'memory', copyDetail: { chart: '图表数据' } },
  { id: '每日学习时长统计', page: 'study', copyKey: 'studyTime', copyDetail: { chart: '图表数据', summary: '汇总统计' } },
  { id: '按日期统计表', page: 'study', copyKey: 'summary', copyDetail: { table: '表格数据' } },
  { id: '字段注释', page: 'study', copyKey: 'notes', copyDetail: { notes: '字段说明' } },

  { id: '未来每日学习量预测', page: 'tools', copyKey: 'prediction', copyDetail: { table: '预测表格', probability: '概率分桶', forecast: '预测数据' } },
  { id: '目标达标与复习概率', page: 'tools', copyKey: 'predictionTarget', copyDetail: { chart: '图表', probability: '概率分桶' } },
  { id: '查看单词列表', page: 'tools', copyKey: 'wordList', copyDetail: { list: '单词列表' } },
  { id: '云词本管理器', page: 'tools', copyKey: 'notepadManager', copyEnabled: false },
  { id: '文章生词筛选', page: 'tools', copyKey: 'articleUnknownWords', copyEnabled: false },
]

function isCopyable(card) {
  return card.copyEnabled !== false && !!card.copyKey
}

function getCardById(id) {
  return CARD_REGISTRY.find(card => card.id === id)
}

function normalizeOrder(savedOrder) {
  const validIds = new Set(CARD_REGISTRY.map(card => card.id))
  const saved = Array.isArray(savedOrder)
    ? savedOrder.filter(id => validIds.has(id) && !REMOVED_CARD_IDS.has(id))
    : []

  const result = []

  for (const page of PAGE_REGISTRY) {
    const pageIds = CARD_REGISTRY
      .filter(card => card.page === page.key)
      .map(card => card.id)

    const orderedPageIds = []

    for (const id of saved) {
      if (pageIds.includes(id) && !orderedPageIds.includes(id)) orderedPageIds.push(id)
    }

    for (const id of pageIds) {
      if (!orderedPageIds.includes(id)) orderedPageIds.push(id)
    }

    // “复习情况”必须保持在当日情况页面第一张卡。
    if (page.key === 'today' && orderedPageIds.includes(FIXED_FIRST_CARD_ID)) {
      orderedPageIds.splice(orderedPageIds.indexOf(FIXED_FIRST_CARD_ID), 1)
      orderedPageIds.unshift(FIXED_FIRST_CARD_ID)
    }

    for (const id of orderedPageIds) {
      if (!result.includes(id)) result.push(id)
    }
  }

  return result
}

export const useCardStore = defineStore('card', () => {
  const order = ref(normalizeOrder(CARD_REGISTRY.map(card => card.id)))
  const visible = reactive({})
  const copyEnabled = reactive({})
  const copyDetail = reactive({})

  CARD_REGISTRY.forEach(card => {
    visible[card.id] = true
    copyEnabled[card.id] = card.copyEnabled !== false
    copyDetail[card.id] = {}
    if (card.copyDetail) Object.keys(card.copyDetail).forEach(key => { copyDetail[card.id][key] = true })
  })

  function load() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
      order.value = normalizeOrder(saved.order)

      if (saved.visible) {
        Object.assign(visible, saved.visible)
        for (const removed of REMOVED_CARD_IDS) delete visible[removed]
      }

      if (saved.copyEnabled) {
        CARD_REGISTRY.forEach(card => {
          if (card.copyEnabled === false) copyEnabled[card.id] = false
          else if (Object.prototype.hasOwnProperty.call(saved.copyEnabled, card.id)) copyEnabled[card.id] = saved.copyEnabled[card.id]
        })
      }

      if (saved.copyDetail) {
        CARD_REGISTRY.forEach(card => {
          if (saved.copyDetail[card.id]) Object.assign(copyDetail[card.id], saved.copyDetail[card.id])
        })
      }



      // 工具卡只用于显示，不参与总复制；当日时间点查看已支持复制，保留为可复制卡片。
      for (const id of ['云词本管理器', '文章生词筛选', '数据减少提醒', '自定义词']) {
        copyEnabled[id] = false
      }

      if (copyDetail['当日时间点查看']) {
        for (const key of ['summary', 'latest', 'compare']) {
          if (!Object.prototype.hasOwnProperty.call(copyDetail['当日时间点查看'], key)) copyDetail['当日时间点查看'][key] = true
        }
      }

      save()
    } catch (e) { /* ignore */ }
  }

  function save() {
    order.value = normalizeOrder(order.value)
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      order: order.value,
      visible: { ...visible },
      copyEnabled: { ...copyEnabled },
      copyDetail: JSON.parse(JSON.stringify(copyDetail))
    }))
  }

  function getOrderedCards(pageKey = '') {
    // Do not mutate order.value here. This method is used inside computed/template
    // render paths. Mutating a dependency while reading it causes Vue recursive
    // updates in HeaderCard.
    return normalizeOrder(order.value)
      .map(getCardById)
      .filter(card => card && (!pageKey || card.page === pageKey))
  }

  function getCardsByPage(pageKey) {
    return getOrderedCards(pageKey)
  }

  function getVisibleCards(pageKey = '') {
    return getOrderedCards(pageKey)
      .filter(card => visible[card.id] !== false)
      .map(card => card.id)
  }

  function getCopyableCards(pageKey = '') {
    return getOrderedCards(pageKey).filter(isCopyable)
  }

  function moveCardInPage(pageKey, cardId, dir) {
    if (cardId === FIXED_FIRST_CARD_ID) return

    const pageCards = getOrderedCards(pageKey)
    const index = pageCards.findIndex(card => card.id === cardId)
    const nextIndex = index + dir
    if (index < 0 || nextIndex < 0 || nextIndex >= pageCards.length) return

    if (pageKey === 'today' && pageCards[nextIndex]?.id === FIXED_FIRST_CARD_ID) return

    const pageIds = pageCards.map(card => card.id)
    const tmp = pageIds[index]
    pageIds[index] = pageIds[nextIndex]
    pageIds[nextIndex] = tmp

    const nextOrder = []
    for (const page of PAGE_REGISTRY) {
      if (page.key === pageKey) {
        nextOrder.push(...pageIds)
      } else {
        nextOrder.push(...getOrderedCards(page.key).map(card => card.id))
      }
    }

    order.value = normalizeOrder(nextOrder)
    save()
  }

  function reorderCardsInPage(pageKey, pageIds) {
    const validPageIds = getOrderedCards(pageKey).map(card => card.id)
    if (!validPageIds.length) return

    const nextPageIds = []
    for (const id of Array.isArray(pageIds) ? pageIds : []) {
      if (validPageIds.includes(id) && !nextPageIds.includes(id)) nextPageIds.push(id)
    }
    for (const id of validPageIds) {
      if (!nextPageIds.includes(id)) nextPageIds.push(id)
    }

    if (pageKey === 'today' && nextPageIds.includes(FIXED_FIRST_CARD_ID)) {
      nextPageIds.splice(nextPageIds.indexOf(FIXED_FIRST_CARD_ID), 1)
      nextPageIds.unshift(FIXED_FIRST_CARD_ID)
    }

    const nextOrder = []
    for (const page of PAGE_REGISTRY) {
      if (page.key === pageKey) {
        nextOrder.push(...nextPageIds)
      } else {
        nextOrder.push(...getOrderedCards(page.key).map(card => card.id))
      }
    }

    order.value = normalizeOrder(nextOrder)
    save()
  }

  function moveCard(idx, dir) {
    const card = getCardById(order.value[idx])
    if (!card) return
    moveCardInPage(card.page, card.id, dir)
  }

  function getCopyKeys() {
    return getCopyableCards()
      .filter(card => copyEnabled[card.id] !== false)
      .map(card => card.copyKey)
  }

  return {
    order, visible, copyEnabled, copyDetail,
    load, save, moveCard, moveCardInPage, reorderCardsInPage,
    getCardsByPage, getOrderedCards, getVisibleCards, getCopyableCards, getCopyKeys
  }
})
