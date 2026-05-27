// Display rows computation - filters rawRows by range and settings
import { useSettingsStore } from '@/stores/settingsStore'

export function computeDisplayRows(rows) {
  const settings = useSettingsStore()
  if (!rows?.length) return []

  const hideEmpty = settings.hideEmptyDays
  const refDate = new Date()
  refDate.setHours(4, 0, 0, 0)

  let filtered = [...rows].sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')))

  // Filter by range preset
  const preset = settings.rangePreset || '30'
  const customDays = settings.customDays || 30

  if (preset === '30') {
    const cutoff = refDate.getTime() - 30 * 86400000
    const cutoffStr = new Date(cutoff).toISOString().slice(0, 10)
    filtered = filtered.filter(r => r.date >= cutoffStr)
  } else if (preset === '7') {
    const cutoff = refDate.getTime() - 7 * 86400000
    const cutoffStr = new Date(cutoff).toISOString().slice(0, 10)
    filtered = filtered.filter(r => r.date >= cutoffStr)
  } else if (preset === '90') {
    const cutoff = refDate.getTime() - 90 * 86400000
    const cutoffStr = new Date(cutoff).toISOString().slice(0, 10)
    filtered = filtered.filter(r => r.date >= cutoffStr)
  } else if (preset === 'custom') {
    const cutoff = refDate.getTime() - customDays * 86400000
    const cutoffStr = new Date(cutoff).toISOString().slice(0, 10)
    filtered = filtered.filter(r => r.date >= cutoffStr)
  }
  // 'all' keeps everything

  if (hideEmpty) {
    filtered = filtered.filter(r => r.hasData || r.hasOverviewData || r.hasProgressData)
  }

  return filtered.sort((a, b) => String(a?.date || '').localeCompare(String(b?.date || '')))
}
