// Pure helpers for the public board's headline cards and the per-model collapse.
// Import-free so tests/js/board.test.mjs can run them under plain node.
//
// Rows are the `toRun` shape from api.js: model ("gemini-3.8-flash(medium)"),
// modelResolved ("google/gemini-3.8-flash"), completion (0–100), perfScore
// (0–100 completion, 100–150 = fewest turns among clears), turns, avgSPerTurn,
// avgCostPerTurn. The leaderboard arrives already ranked (gates desc, turns asc),
// and every helper here keeps that order unless it says otherwise.

/** "gemini-3.8-flash(medium)" → "gemini-3.8-flash". A bare alias is its own base. */
export function baseModel(alias) {
  return String(alias ?? '').replace(/\s*\([^)]*\)\s*$/, '')
}

/**
 * One row per model: the FIRST (= best-ranked) row for each base model, in rank
 * order. Andreas 2026-09-12: "only show top score per model not including
 * thinking" — the thinking level is part of the alias, so the collapse is on the
 * alias with its "(level)" removed.
 */
export function collapseBest(rows) {
  const seen = new Set()
  const out = []
  for (const r of rows) {
    const key = baseModel(r.model)
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r)
  }
  return out
}

// Vendor (provider) of a row, keyed off the OpenRouter id's prefix when the
// payload carries it and off the alias otherwise. Colours are muted to sit on
// the cream sheet next to the palette in app.css.
const VENDORS = [
  ['google', /^google\//, /^(gemini|gemma)/, 'Google', '#3f6b46'],
  ['anthropic', /^anthropic\//, /^claude/, 'Anthropic', '#b5694a'],
  ['openai', /^openai\//, /^gpt/, 'OpenAI', '#1f1c17'],
  ['z-ai', /^z-ai\//, /^glm/, 'Z.AI', '#2b5279'],
  ['deepseek', /^deepseek\//, /^deepseek/, 'DeepSeek', '#4a5ea8'],
  ['meta', /^meta\//, /^muse/, 'Meta', '#4a739e'],
  ['x-ai', /^x-ai\//, /^grok/, 'xAI', '#6b6b6b'],
  ['moonshotai', /^moonshotai\//, /^kimi/, 'Moonshot', '#8a6a1d'],
  ['alibaba', /^(alibaba|qwen)\//, /^qwen/, 'Alibaba', '#8f6339'],
  ['minimax', /^minimax\//, /^minimax/, 'MiniMax', '#6b5a94'],
  ['xiaomi', /^xiaomi\//, /^mimo/, 'Xiaomi', '#a75f34'],
  ['sakana', /^sakana\//, /^fugu/, 'Sakana', '#7c5c8a'],
]
const UNKNOWN = { key: 'other', label: 'Other', color: '#9a9184' }

export function vendorOf(row) {
  const resolved = String(row?.modelResolved ?? '')
  const alias = String(row?.model ?? '')
  for (const [key, byId, byAlias, label, color] of VENDORS) {
    if ((resolved && byId.test(resolved)) || (!resolved && byAlias.test(alias))) return { key, label, color }
  }
  for (const [key, , byAlias, label, color] of VENDORS) {
    if (byAlias.test(alias)) return { key, label, color }
  }
  return UNKNOWN
}

/** Where the 100% line sits in the performance card, as a fraction of the plot height. */
export const PERF_LINE = 0.68

/** Turns per minute from seconds per turn; 0 when unknown. */
export function turnsPerMinute(avgSPerTurn) {
  return avgSPerTurn > 0 ? 60 / avgSPerTurn : 0
}

/** Cost of ten turns in USD. */
export function costPer10(avgCostPerTurn) {
  return (avgCostPerTurn ?? 0) * 10
}

/**
 * The three headline series. Each entry: {row, value, height (0–1), label}.
 *
 * performance — rank order. Bars reach the 100% line in proportion to
 *   completion; clears rise above it in proportion to perfScore's 100–150 band
 *   (fewest turns = tallest), so the best clear touches the top of the plot.
 * speed — turns per minute, fastest first. Height relative to the fastest.
 * cost — USD per 10 turns, cheapest first. Height relative to the dearest.
 */
export function headlineSeries(rows) {
  const performance = rows.map((r) => {
    const complete = (r.completion ?? 0) >= 100
    const above = complete ? Math.max(0, Math.min(1, ((r.perfScore ?? 100) - 100) / 50)) : 0
    const height = complete
      ? PERF_LINE + above * (1 - PERF_LINE)
      : Math.max(0, Math.min(1, (r.completion ?? 0) / 100)) * PERF_LINE
    return { row: r, value: r.completion ?? 0, height, label: `${r.completion ?? 0}%`, complete }
  })
  // If several clears tie at perfScore 125 (maxC === minC) they all sit at the same height.

  const speedVals = rows.map((r) => ({ row: r, value: turnsPerMinute(r.avgSPerTurn) }))
    .sort((a, b) => b.value - a.value)
  const speedMax = speedVals.length ? Math.max(...speedVals.map((s) => s.value)) || 1 : 1
  const speed = speedVals.map((s) => ({ ...s, height: s.value / speedMax, label: fmtTpm(s.value) }))

  const costVals = rows.map((r) => ({ row: r, value: costPer10(r.avgCostPerTurn) }))
    .sort((a, b) => a.value - b.value)
  const costMax = costVals.length ? Math.max(...costVals.map((c) => c.value)) || 1 : 1
  const cost = costVals.map((c) => ({ ...c, height: c.value / costMax, label: fmtUsd(c.value) }))

  return { performance, speed, cost }
}

export function fmtTpm(v) {
  if (!(v > 0)) return '—'
  return v >= 10 ? String(Math.round(v)) : v.toFixed(1)
}

export function fmtUsd(n) {
  if (n == null) return '—'
  if (n >= 1) return '$' + n.toFixed(2)
  return '$' + n.toFixed(n < 0.1 ? 3 : 2)
}
