// Pure half of the shared model selection (the runes store in
// selection.svelte.js wraps these). A selection is a list of run ALIASES —
// "gpt-5.6-sol(high)", model plus thinking level, one board row each — or null
// for the default: the best-ranked level per model (Andreas 2026-09-14, "2a").
// Aliases rather than run ids so a link survives a run being republished.
import { collapseBest } from './board.js'

export const URL_KEY = 'models'

/** The default selection: one row per model, its best-ranked level. */
export function defaultPicked(rows) {
  return collapseBest(rows).map((r) => r.model)
}

/** Rows of the selection, in board rank order. `picked` null → the default. */
export function applySelection(rows, picked) {
  if (picked == null) return collapseBest(rows)
  const want = new Set(picked)
  return rows.filter((r) => want.has(r.model))
}

/** `?models=a,b` → ['a', 'b']; absent → null (default); present but empty → []. */
export function parsePicked(search) {
  const raw = new URLSearchParams(search || '').get(URL_KEY)
  if (raw == null) return null
  return raw.split(',').map((s) => decodeURIComponent(s.trim())).filter(Boolean)
}

/** The query string for a selection; '' when it is the default. */
export function serializePicked(picked) {
  if (picked == null) return ''
  return `${URL_KEY}=${picked.map((s) => encodeURIComponent(s)).join(',')}`
}

/** The picker's preset lists, each a list of aliases (null = default). */
export function presets(rows) {
  return [
    { key: 'best', label: 'Best per model', picked: null },
    { key: 'all', label: 'All levels', picked: rows.map((r) => r.model) },
    { key: 'complete', label: 'Completed only', picked: rows.filter((r) => r.completion >= 100).map((r) => r.model) },
    { key: 'oss', label: 'Open-weights', picked: rows.filter((r) => r.openSource).map((r) => r.model) },
    { key: 'none', label: 'None', picked: [] },
  ]
}
