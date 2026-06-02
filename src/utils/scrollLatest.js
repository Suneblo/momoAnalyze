let scrollTimer = 0

function scrollOne(el) {
  if (!el) return
  if (el.scrollHeight > el.clientHeight) el.scrollTop = el.scrollHeight
  if (el.scrollWidth > el.clientWidth) el.scrollLeft = el.scrollWidth
}

function scrollLatestTablesNow(root = document) {
  const scope = root?.querySelectorAll ? root : document
  const wrappers = scope.querySelectorAll('[data-latest-scroll]')

  wrappers.forEach((wrapper) => {
    scrollOne(wrapper)
    wrapper.querySelectorAll('.el-scrollbar__wrap, .el-table__body-wrapper').forEach(scrollOne)
  })
}

export function requestScrollLatestTables(root = document) {
  if (typeof window === 'undefined') return
  window.clearTimeout(scrollTimer)

  const run = () => {
    window.requestAnimationFrame(() => scrollLatestTablesNow(root))
    window.setTimeout(() => scrollLatestTablesNow(root), 80)
    window.setTimeout(() => scrollLatestTablesNow(root), 240)
  }

  scrollTimer = window.setTimeout(run, 0)
}
