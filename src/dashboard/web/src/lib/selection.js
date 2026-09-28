// Pure half of the shared model selection (the runes store in
// selection.svelte.js wraps these). A selection is a list of run ALIASES —
// "gpt-5.6-sol(high)", model plus thinking level, one board row each — or null
// for the default. Aliases rather than run ids so a link survives a run being
// republished.
//
// THE DEFAULT (Andreas 2026-09-16, revised 2026-09-25) is the union of two things:
//
//   1. the best row per lab per CLASS — one opus, one sol, one flash-lite —
//      where the class is the model line with its version dropped
//      (board.js `classOf`), so a lab appears once per line it ships and a
//      superseded generation does not sit next to its successor, and
//   2. every row, at any thinking level, that sits on a Pareto frontier —
//      performance against cost per task AND performance against time per task.
//
// (1) was "one row per LAB" until 2026-09-25, which represented a lab by its
// single strongest row and so hid claude-fable behind claude-opus while letting
// claude-opus-5 and claude-opus-5.5 both onto the cards, which collapse by name.
// Class is the unit that actually matches how labs ship. The frontier is NOT
// filtered by class (Andreas's call): a row nothing dominates on price or speed
// is worth seeing whatever its generation.
//
// Before that, (1) was "one row per model, its best level", which hid the
// interesting cases: a cheap low-effort level that nothing beats on price, or a
// slow max level that nothing beats on performance, both lost to a sibling that
// merely ranked higher. A frontier row is by definition one that nothing else
// dominates, which is exactly the row a reader should see.
import { collapseBest, classKey, perTaskSeries } from './board.js'
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

/** The best-ranked row of each lab+class. `rows` arrive in board rank order. */
export function bestInClass(rows) {
  const seen = new Set()
  const out = []
  for (const r of rows) {
    const key = classKey(r)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

/** The default selection: best in class, plus every row on either frontier. */
export function defaultPicked(rows) {
  if (!rows.length) return []
  const gateIds = GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id)
  // Only a row with a per-task figure can sit on a frontier; the rest are the
  // runs the board cannot project at all.
  const series = perTaskSeries(rows, gateIds, rows).filter((s) => s.eligible)
  const front = (pick) => paretoFront(series.map((s) => ({ x: pick(s), y: s.row.perfScore ?? 0, alias: s.row.model })))
  const keep = new Set(bestInClass(rows).map((r) => r.model))
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
    { key: 'default', label: 'Best in class + frontier', picked: null },
    { key: 'best', label: 'Best per model', picked: collapseBest(rows).map((r) => r.model) },
    { key: 'all', label: 'All levels', picked: rows.map((r) => r.model) },
    { key: 'complete', label: 'Completed only', picked: rows.filter((r) => r.completion >= 100).map((r) => r.model) },
    { key: 'oss', label: 'Open-weights', picked: rows.filter((r) => r.openSource).map((r) => r.model) },
    { key: 'none', label: 'None', picked: [] },
  ]
}
