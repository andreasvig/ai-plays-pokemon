// Pure half of the shared model selection (the runes store in
// selection.svelte.js wraps these). A selection is a list of run ALIASES —
// "gpt-5.6-sol(high)", model plus thinking level, one board row each — or null
// for the default. Aliases rather than run ids so a link survives a run being
// republished.
//
// THE DEFAULT (Andreas 2026-09-16) is the union of two things:
//
//   1. the best row per LAB — OpenAI, Google, Anthropic, … — not per model
//      family, so every lab on the board is represented exactly once by its
//      strongest row, and
//   2. every row, at any thinking level, that sits on a Pareto frontier —
//      performance against cost per task AND performance against time per task.
//
// It was "one row per model, its best level", which hid the interesting cases:
// a cheap low-effort level that nothing beats on price, or a slow max level that
// nothing beats on performance, both lost to a sibling that merely ranked
// higher. A frontier row is by definition one that nothing else dominates, which
// is exactly the row a reader should see.
import { collapseBest, vendorOf, perTaskSeries } from './board.js'
import { GATES } from './gates.js'

export const URL_KEY = 'models'

/**
 * The upper-left envelope of {x, y} points: cheapest first, keep every point
 * that beats everything cheaper than it. Ties on x keep the first, which is the
 * better-ranked row — the same rule the scatter plots draw their dashed line by.
 */
export function paretoFront(points) {
  const sorted = points.filter((p) => p.x != null && p.y != null).sort((a, b) => a.x - b.x)
  const keep = []
  let best = -Infinity
  for (const p of sorted) if (p.y > best) { keep.push(p); best = p.y }
  return keep
}

/** The best-ranked row of each lab. `rows` arrive in board rank order. */
export function bestPerLab(rows) {
  const seen = new Set()
  const out = []
  for (const r of rows) {
    const key = vendorOf(r).key
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

/** The default selection: best per lab, plus every row on either frontier. */
export function defaultPicked(rows) {
  if (!rows.length) return []
  const gateIds = GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id)
  // Only a row with a per-task figure can sit on a frontier; the rest are the
  // runs the board cannot project at all.
  const series = perTaskSeries(rows, gateIds, rows).filter((s) => s.eligible)
  const front = (pick) => paretoFront(series.map((s) => ({ x: pick(s), y: s.row.perfScore ?? 0, alias: s.row.model })))
  const keep = new Set(bestPerLab(rows).map((r) => r.model))
  for (const p of front((s) => s.costPerTask)) keep.add(p.alias)
  for (const p of front((s) => s.minutesPerTask)) keep.add(p.alias)
  return rows.filter((r) => keep.has(r.model)).map((r) => r.model)
}

/** Rows of the selection, in board rank order. `picked` null → the default. */
export function applySelection(rows, picked) {
  const want = new Set(picked ?? defaultPicked(rows))
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
    { key: 'default', label: 'Best per lab + frontier', picked: null },
    { key: 'best', label: 'Best per model', picked: collapseBest(rows).map((r) => r.model) },
    { key: 'all', label: 'All levels', picked: rows.map((r) => r.model) },
    { key: 'complete', label: 'Completed only', picked: rows.filter((r) => r.completion >= 100).map((r) => r.model) },
    { key: 'oss', label: 'Open-weights', picked: rows.filter((r) => r.openSource).map((r) => r.model) },
    { key: 'none', label: 'None', picked: [] },
  ]
}
