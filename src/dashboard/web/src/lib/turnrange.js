// Pure half of the per-level turn range (the runes store in turnrange.svelte.js
// wraps these). Andreas 2026-09-28: "I only want to see the graph from turn
// 100-150" — two handles on the walk map, and a link that opens on that slice.
//
// The URL carries `?turns=<alias>:<from>-<to>`, comma-separated, because a model
// page can have several thinking levels expanded at once and each one draws its
// own run:
//
//     ?turns=gpt-6-sol(high):100-150                     one level
//     ?turns=gpt-6-sol(high):100-150,gpt-6-sol(low):1-40 two
//     ?turns=pareto:100-150                              a model with no levels
//
// Keyed on the board ALIAS (`model(level)`) rather than the bare level, so a
// range cannot follow you onto another model's page and silently re-scope a
// different run. ModelPage opens the levels whose alias belongs to the model
// being shown: the parameter says both WHICH levels are open and what each is
// showing, which it has to, because the expanded state is otherwise local and a
// link to turns 100-150 of a level nobody opened would land on a closed panel.
export const URL_KEY = 'turns'

/** `?turns=high:100-150` → {high: [100, 150]}; absent or unparseable → {}. */
export function parseRanges(search) {
  const raw = new URLSearchParams(search || '').get(URL_KEY)
  if (!raw) return {}
  const out = {}
  for (const part of raw.split(',')) {
    const m = /^([^:]+):(\d+)-(\d+)$/.exec(part.trim())
    if (!m) continue
    const from = Number(m[2]), to = Number(m[3])
    if (to < from) continue
    out[decodeURIComponent(m[1])] = [from, to]
  }
  return out
}

/** {high: [100, 150]} → 'high:100-150'; an empty map → '' (the key comes out). */
export function serializeRanges(ranges) {
  const parts = Object.entries(ranges || {})
    .filter(([, r]) => Array.isArray(r) && r.length === 2 && r[0] != null && r[1] != null)
    .map(([level, [from, to]]) => `${encodeURIComponent(level)}:${Math.round(from)}-${Math.round(to)}`)
  return parts.join(',')
}

/**
 * `range` clamped into [min, max] and ordered, or null when it covers the whole
 * span — the whole span is the default, and a default does not belong in a URL.
 */
export function normalizeRange(range, min, max) {
  if (!Array.isArray(range)) return null
  const lo = Math.max(min, Math.min(max, Math.round(Math.min(range[0], range[1]))))
  const hi = Math.max(min, Math.min(max, Math.round(Math.max(range[0], range[1]))))
  if (lo <= min && hi >= max) return null
  return [lo, hi]
}
