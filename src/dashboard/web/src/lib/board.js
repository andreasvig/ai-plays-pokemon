// Pure helpers for the public board's headline cards and the per-model collapse.
// Import-free so tests/js/board.test.mjs can run them under plain node.
//
// Rows are the `toRun` shape from api.js: model ("gemini-3.8-flash(medium)"),
// modelResolved ("google/gemini-3.8-flash"), completion (0–100), perfScore
// (0–100 completion, 100–150 = fewest turns among clears), turns, durationS,
// totalCostUsd, avgSPerTurn, avgCostPerTurn, gateTurns ({gate id → turn the
// gate was stamped}). The leaderboard arrives already ranked (gates desc, turns
// asc), and every helper here keeps that order unless it says otherwise.

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

// ───────────── projection to a full clear (Andreas 2026-09-13) ─────────────
//
// The Speed and Cost cards read "what would it take this model to beat Brock",
// not "what did a turn cost". A run that cleared every gate is measured; a
// partial run is PROJECTED: its turns on the legs it did clear, divided by the
// field's mean turns on those legs, is its pace; each leg it never reached
// costs pace × the field's mean for that leg; the leg it died on costs at least
// the turns it already burned there (the floor — an estimate below what was
// spent would be a lie). Cost and time to finish are the run's own totals plus
// the estimated extra turns at its own per-turn rates. Only a run that reached
// PROJECT_FROM_GATE is projected: the six gates before it cost cents for every
// model and say nothing about pace.

/** A run counts on the per-task cards once it has reached this gate. */
export const PROJECT_FROM_GATE = 'viridian_reached'
/** A leg has a typical value once this many runs cleared it. */
export const MIN_CLEARS_FOR_TYPICAL = 3

/** Turns spent on each cleared leg, in ladder order (stops at the first uncleared gate). */
export function legTurns(row, gateIds) {
  const stamps = row?.gateTurns || {}
  const out = []
  let prev = 0
  for (const g of gateIds) {
    const t = stamps[g]
    if (typeof t !== 'number') break
    out.push(t - prev)
    prev = t
  }
  return out
}

/** Mean turns per leg over the runs that cleared it (≥ MIN_CLEARS_FOR_TYPICAL), gate id → turns. */
export function typicalTurnsPerLeg(pool, gateIds) {
  const legsByRow = pool.map((r) => legTurns(r, gateIds))
  const out = {}
  gateIds.forEach((g, i) => {
    const vals = legsByRow.map((legs) => legs[i]).filter((v) => v != null)
    if (vals.length >= MIN_CLEARS_FOR_TYPICAL) out[g] = vals.reduce((a, b) => a + b, 0) / vals.length
  })
  return out
}

/**
 * Project one run to a full clear. Returns {eligible, complete, cleared, pace,
 * projected, estimated, floored, failedGate}. `projected` is turns to beat the
 * last gate; `estimated` the part of it not played. Ineligible when the run has
 * not reached PROJECT_FROM_GATE or a missing leg has no typical value yet.
 */
export function projectRun(row, typical, gateIds) {
  const legs = legTurns(row, gateIds)
  const cleared = legs.length
  const played = row.turns ?? 0
  const complete = cleared >= gateIds.length && gateIds.length > 0
  const base = { cleared, complete, played }
  if (complete) return { ...base, eligible: true, pace: paceOf(legs, typical, gateIds), projected: played, estimated: 0, floored: false, failedGate: null }
  const reached = gateIds.indexOf(PROJECT_FROM_GATE)
  const pace = paceOf(legs, typical, gateIds)
  const remaining = gateIds.slice(cleared)
  if (reached < 0 || cleared <= reached || pace == null || remaining.some((g) => typical[g] == null)) {
    return { ...base, eligible: false, pace, projected: null, estimated: null, floored: false, failedGate: remaining[0] ?? null }
  }
  const lastStamp = row.gateTurns[gateIds[cleared - 1]]
  const tail = Math.max(0, played - lastStamp)          // turns burned on the leg never finished
  const failedGate = remaining[0]
  const atPace = pace * typical[failedGate]
  const failTurns = Math.max(tail, atPace)
  const rest = remaining.slice(1).reduce((a, g) => a + pace * typical[g], 0)
  const projected = lastStamp + failTurns + rest
  return { ...base, eligible: true, pace, projected, estimated: projected - played, floored: tail > atPace, failedGate }
}

function paceOf(legs, typical, gateIds) {
  let mine = 0, typ = 0
  legs.forEach((t, i) => { const ref = typical[gateIds[i]]; if (ref != null) { mine += t; typ += ref } })
  return typ > 0 ? mine / typ : null
}

/**
 * Per-task figures for each row: {row, ...projection, costToFinish,
 * minutesToFinish, costPerTask, minutesPerTask, turnsPerTask}. `pool` is the set
 * the typical leg turns are computed over (every leaderboard row, so a collapsed
 * card view still uses every clear); it defaults to `rows`.
 */
export function perTaskSeries(rows, gateIds, pool = rows) {
  const typical = typicalTurnsPerLeg(pool, gateIds)
  const n = gateIds.length || 1
  return rows.map((r) => {
    const p = projectRun(r, typical, gateIds)
    if (!p.eligible) return { row: r, ...p, costToFinish: null, minutesToFinish: null, costPerTask: null, minutesPerTask: null, turnsPerTask: null }
    const costToFinish = (r.totalCostUsd ?? 0) + p.estimated * (r.avgCostPerTurn ?? 0)
    const minutesToFinish = ((r.durationS ?? 0) + p.estimated * (r.avgSPerTurn ?? 0)) / 60
    return { row: r, ...p, costToFinish, minutesToFinish, costPerTask: costToFinish / n, minutesPerTask: minutesToFinish / n, turnsPerTask: p.projected / n }
  })
}

// Bars for a "lower is better" card: eligible entries sorted ascending, tallest
// = the largest value, then the ineligible ones (height 0, label "—") in rank order.
function lowerIsBetter(series, pick, fmt) {
  const ok = series.filter((s) => s.eligible && pick(s) != null).map((s) => ({ ...s, value: pick(s) })).sort((a, b) => a.value - b.value)
  const max = ok.length ? Math.max(...ok.map((s) => s.value)) || 1 : 1
  const rest = series.filter((s) => !(s.eligible && pick(s) != null)).map((s) => ({ ...s, value: null, height: 0, label: '—' }))
  return ok.map((s) => ({ ...s, height: s.value / max, label: fmt(s.value) })).concat(rest)
}

/**
 * The three headline series. Each entry: {row, value, height (0–1), label, complete, eligible, ...}.
 *
 * performance — rank order. Bars reach the 100% line in proportion to
 *   completion; clears rise above it in proportion to perfScore's 100–150 band
 *   (fewest turns = tallest), so the best clear touches the top of the plot.
 * time — minutes per task (projected minutes to beat Brock ÷ gates), fastest
 *   first; a partial run's bar is a projection (`complete` false → hatched).
 * cost — USD per task, cheapest first, same projection rule.
 */
export function headlineSeries(rows, gateIds = [], pool = rows) {
  const performance = rows.map((r) => {
    const complete = (r.completion ?? 0) >= 100
    const above = complete ? Math.max(0, Math.min(1, ((r.perfScore ?? 100) - 100) / 50)) : 0
    const height = complete
      ? PERF_LINE + above * (1 - PERF_LINE)
      : Math.max(0, Math.min(1, (r.completion ?? 0) / 100)) * PERF_LINE
    return { row: r, value: r.completion ?? 0, height, label: `${r.completion ?? 0}%`, complete }
  })
  // If several clears tie at perfScore 125 (maxC === minC) they all sit at the same height.
  const per = perTaskSeries(rows, gateIds, pool)
  const time = lowerIsBetter(per, (s) => s.minutesPerTask, fmtMinutes)
  const cost = lowerIsBetter(per, (s) => s.costPerTask, fmtUsd)
  return { performance, time, cost }
}

/**
 * The strip under the board: the per-turn measurements the cards used to lead
 * with, plus average turns per task with the same projection rule.
 * speed — turns per minute, fastest first, tallest = fastest.
 * cost10 — USD per 10 turns, cheapest first, tallest = dearest.
 * turnsPerTask — projected turns to beat Brock ÷ gates, fewest first.
 * inputsPerTurn — mean game inputs per turn, most first.
 * outputTokens — mean output tokens (thinking + reply) per turn, fewest first.
 */
export function secondarySeries(rows, gateIds = [], pool = rows) {
  const speedVals = rows.map((r) => ({ row: r, value: turnsPerMinute(r.avgSPerTurn), eligible: true, complete: true }))
    .sort((a, b) => b.value - a.value)
  const speedMax = speedVals.length ? Math.max(...speedVals.map((s) => s.value)) || 1 : 1
  const speed = speedVals.map((s) => ({ ...s, height: s.value / speedMax, label: fmtTpm(s.value) }))

  const costVals = rows.map((r) => ({ row: r, value: costPer10(r.avgCostPerTurn), eligible: true, complete: true }))
    .sort((a, b) => a.value - b.value)
  const costMax = costVals.length ? Math.max(...costVals.map((c) => c.value)) || 1 : 1
  const cost10 = costVals.map((c) => ({ ...c, height: c.value / costMax, label: fmtUsd(c.value) }))

  const turnsPerTask = lowerIsBetter(perTaskSeries(rows, gateIds, pool), (s) => s.turnsPerTask, (v) => v.toFixed(1))

  // Inputs per turn (2026-09-14): mean game inputs a turn carries, most first.
  // A row without the statistic (older harness) is ineligible and left off.
  const inputVals = rows.filter((r) => r.avgInputsPerTurn != null)
    .map((r) => ({ row: r, value: r.avgInputsPerTurn, eligible: true, complete: true }))
    .sort((a, b) => b.value - a.value)
  const inputMax = inputVals.length ? Math.max(...inputVals.map((s) => s.value)) || 1 : 1
  const inputsPerTurn = inputVals.map((s) => ({ ...s, height: s.value / inputMax, label: s.value.toFixed(1) }))
    .concat(rows.filter((r) => r.avgInputsPerTurn == null).map((r) => ({ row: r, value: null, eligible: false, complete: true, height: 0, label: '—' })))
  // Output tokens per turn (2026-09-14): thinking + reply, every call, fewest
  // first. A row without usage data is ineligible and left off.
  const tokVals = rows.filter((r) => r.avgOutputTokensPerTurn != null)
    .map((r) => ({ row: r, value: r.avgOutputTokensPerTurn, eligible: true, complete: true }))
    .sort((a, b) => a.value - b.value)
  const tokMax = tokVals.length ? Math.max(...tokVals.map((s) => s.value)) || 1 : 1
  const outputTokens = tokVals.map((s) => ({ ...s, height: s.value / tokMax, label: fmtTokens(s.value) }))
    .concat(rows.filter((r) => r.avgOutputTokensPerTurn == null).map((r) => ({ row: r, value: null, eligible: false, complete: true, height: 0, label: '—' })))
  return { speed, cost10, turnsPerTask, inputsPerTurn, outputTokens }
}

/**
 * The runs × legs matrix behind the projections, for the Estimation methods
 * page: every run of `pool` (all thinking levels, not collapsed), each leg's
 * actual turns and ratio to the typical value, the estimated turns for each leg
 * the run never cleared (failed leg floored at turns burned), and the per-run
 * totals the board cards use. `typical` is the same MEAN-over-≥3-clears the
 * cards use, so a cell here is exactly what a card's projection is built from.
 */
export function estimationMatrix(pool, gateIds) {
  const typical = typicalTurnsPerLeg(pool, gateIds)
  const clears = {}, slowest = {}
  for (const g of gateIds) { clears[g] = 0; slowest[g] = null }
  pool.forEach((r) => legTurns(r, gateIds).forEach((t, i) => {
    const g = gateIds[i]; clears[g] += 1; slowest[g] = slowest[g] == null ? t : Math.max(slowest[g], t)
  }))
  const n = gateIds.length || 1
  const rows = pool.map((r) => {
    const legs = legTurns(r, gateIds).map((t, i) => {
      const g = gateIds[i]; const ref = typical[g]
      return { gate: g, turns: t, ratio: ref ? t / ref : null }
    })
    const p = projectRun(r, typical, gateIds)
    const lastStamp = legs.length ? r.gateTurns[gateIds[legs.length - 1]] : 0
    const tail = p.complete ? null : { gate: gateIds[legs.length], turns: Math.max(0, (r.turns ?? 0) - lastStamp) }
    const estimates = p.eligible && !p.complete
      ? gateIds.slice(legs.length).map((g, i) => {
          const atPace = p.pace * typical[g]
          const turns = i === 0 ? Math.max(tail.turns, atPace) : atPace
          return { gate: g, turns, ratio: turns / typical[g], floored: i === 0 && p.floored }
        })
      : []
    const costToFinish = p.eligible ? (r.totalCostUsd ?? 0) + p.estimated * (r.avgCostPerTurn ?? 0) : null
    const minutesToFinish = p.eligible ? ((r.durationS ?? 0) + p.estimated * (r.avgSPerTurn ?? 0)) / 60 : null
    return {
      row: r, legs, tail, estimates, ...p,
      costToFinish, minutesToFinish,
      costPerTask: costToFinish == null ? null : costToFinish / n,
      minutesPerTask: minutesToFinish == null ? null : minutesToFinish / n,
      turnsPerTask: p.projected == null ? null : p.projected / n,
      playedShare: p.projected ? p.played / p.projected : null,
    }
  })
  rows.sort((a, b) => (b.cleared - a.cleared) || ((a.projected ?? a.played) - (b.projected ?? b.played)))
  return { typical, clears, slowest, rows }
}

export function fmtTpm(v) {
  if (!(v > 0)) return '—'
  return v >= 10 ? String(Math.round(v)) : v.toFixed(1)
}

/**
 * Battles + movement (2026-09-14, artifacts/battle-and-movement-fidelity/plan.md).
 * movement — shortest path ÷ overworld steps over closed map legs, best first.
 * wildTurns — turns per wild battle (rule A: turns that STARTED in one), fewest
 *   first; only runs that cleared Route 1 — before that no wild grass is
 *   reachable and a run has nothing to say (Andreas 2026-09-14).
 * trainerTurns — turns spent in trainer battles (attempts summed per trainer) PLUS,
 *   for every trainer the run did not fight, a projection built like the leg
 *   projection (projectRun): the run's PACE — its turns on the trainers it
 *   fought ÷ the field's typical turns on those same trainers — times the
 *   field's typical turns for the missing trainer, one fight each. A trainer's
 *   typical is the mean over the runs that fought it, used
 *   once ≥ MIN_BATTLE_OBSERVATIONS runs have (threshold 4 and optional
 *   trainers projected: Andreas 2026-09-14). A bar with a projected part is not
 *   `complete` (hatched). Rows without the statistic are
 *   ineligible and appended with height 0.
 */
export const MIN_BATTLE_OBSERVATIONS = 4
/**
 * The first-badge trainer roster in encounter order (src/referee/battles.py
 * TRAINER_GROUP).
 */
export const TRAINER_ROSTER = [
  { group: 'rival_oaks_lab', name: "Rival (Oak's Lab)", short: 'Rival 1', mandatory: true },
  { group: 'rival_route22', name: 'Rival (Route 22)', short: 'Rival 22', mandatory: false },
  { group: '102', name: 'Bug Catcher Rick', short: 'Rick', mandatory: false },
  { group: '103', name: 'Bug Catcher Doug', short: 'Doug', mandatory: false },
  { group: '104', name: 'Bug Catcher Sammy', short: 'Sammy', mandatory: false },
  { group: '531', name: 'Bug Catcher Anthony', short: 'Anthony', mandatory: false },
  { group: '532', name: 'Bug Catcher Charlie', short: 'Charlie', mandatory: false },
  { group: '142', name: 'Camper Liam', short: 'Liam', mandatory: false },
  { group: '414', name: 'Leader Brock', short: 'Brock', mandatory: true },
]

const hasPerTurn = (r) => r.battleFidelity === 'live' || r.battleFidelity === 'backfill'

/**
 * Runs × trainers: turns spent on each trainer (attempts summed), the field's
 * typical (mean) turns per trainer once MIN_BATTLE_OBSERVATIONS runs fought it,
 * each run's pace over the trainers it fought, and the projected average the
 * card shows. EVERY trainer the run has not fought — finished runs included,
 * so every run is scored on the same roster (Andreas 2026-09-14) — is
 * projected as one fight at pace × typical; a fight against a trainer whose
 * column is not usable is shown but left out of the average. The
 * Estimation methods page renders it; battleSeries().trainerTurns is built
 * from the same numbers.
 */
export function trainerMatrix(pool, rows = pool) {
  const typicals = trainerTypicals(pool)
  const columns = TRAINER_ROSTER.map((t) => ({ ...t, typical: typicals[t.group]?.typical ?? null, n: typicals[t.group]?.n ?? 0,
    usable: (typicals[t.group]?.n ?? 0) >= MIN_BATTLE_OBSERVATIONS }))
  const col = Object.fromEntries(columns.map((c) => [c.group, c]))
  const out = rows.map((r) => {
    const perTurn = hasPerTurn(r)
    const by = Object.fromEntries((r.trainerBattles || []).filter((g) => g.attempts > 0).map((g) => [g.group, g]))
    // Pace: own turns ÷ typical turns, summed over the fought trainers whose
    // typical is usable (same shape as paceOf for legs).
    let mine = 0, typ = 0
    for (const t of TRAINER_ROSTER) {
      const g = by[t.group]
      if (perTurn && g && g.turns != null && col[t.group].usable) { mine += g.turns; typ += col[t.group].typical }
    }
    const pace = typ > 0 ? mine / typ : null
    const cells = TRAINER_ROSTER.map((t) => {
      const g = by[t.group]
      const c = col[t.group]
      // A fight against a trainer too few runs have met (column not usable) is
      // shown but not counted: nobody else gets that trainer projected, so
      // counting it would score this run on a different roster.
      if (g) return { group: t.group, turns: perTurn ? g.turns : null, attempts: g.attempts, won: g.won, kind: 'fought', counted: c.usable,
        ratio: perTurn && g.turns != null && c.typical > 0 ? g.turns / c.typical : null }
      if (c.usable && pace != null) return { group: t.group, turns: pace * c.typical, attempts: 1, kind: 'projected', ratio: pace }
      return { group: t.group, turns: null, attempts: 0, kind: 'none', ratio: null }
    })
    const fought = cells.filter((c) => c.kind === 'fought' && c.counted)
    const uncounted = cells.filter((c) => c.kind === 'fought' && !c.counted)
    const projected = cells.filter((c) => c.kind === 'projected')
    const measured = perTurn ? fought.reduce((a, c) => a + (c.turns ?? 0), 0) : null
    const attempts = fought.reduce((a, c) => a + c.attempts, 0)
    const projTurns = projected.reduce((a, c) => a + c.turns, 0)
    const eligible = perTurn && attempts > 0   // the first trainer must have been fought
    const avg = eligible ? (measured + projTurns) / (attempts + projected.length) : null
    return { row: r, cells, measured, attempts, pace, projected: projTurns, projectedCount: projected.length, uncounted, eligible, avg, complete: projected.length === 0 }
  })
  return { columns, rows: out }
}

/** Per trainer group: {name, mandatory, n, typical (mean turns), median} over runs with per-turn battle state. */
export function trainerTypicals(pool) {
  const turnsBy = new Map()
  for (const r of pool) {
    if (!hasPerTurn(r)) continue
    for (const g of r.trainerBattles || []) {
      if (!(g.attempts > 0) || g.turns == null) continue
      if (!turnsBy.has(g.group)) turnsBy.set(g.group, { name: g.name, mandatory: !!g.mandatory, turns: [] })
      turnsBy.get(g.group).turns.push(g.turns)
    }
  }
  const out = {}
  for (const [group, v] of turnsBy) {
    const s = [...v.turns].sort((a, b) => a - b)
    const median = s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2
    out[group] = { name: v.name, mandatory: v.mandatory, n: s.length, typical: s.reduce((a, b) => a + b, 0) / s.length, median }
  }
  return out
}

function rank(vals, fmt, { desc = false } = {}) {
  vals.sort((a, b) => (desc ? b.value - a.value : a.value - b.value))
  const max = vals.length ? Math.max(...vals.map((s) => s.value)) || 1 : 1
  return vals.map((s) => ({ ...s, height: s.value / max, label: fmt(s.value) }))
}
const OFF = (r) => ({ row: r, value: null, eligible: false, complete: true, height: 0, label: '—' })

export function battleSeries(rows, pool = rows) {
  const movement = rank(rows.filter((r) => r.movementEfficiency != null)
    .map((r) => ({ row: r, value: r.movementEfficiency, eligible: true, complete: true, fidelity: r.stepsFidelity })),
    (v) => Math.round(v * 100) + '%', { desc: true })
    .concat(rows.filter((r) => r.movementEfficiency == null).map(OFF))

  const wildOk = (r) => r.wildBattles > 0 && r.wildBattleTurns != null && r.gateTurns != null && r.gateTurns.route1_reached != null
  const wildTurns = rank(rows.filter(wildOk)
    .map((r) => ({ row: r, value: r.wildBattleTurns / r.wildBattles, eligible: true, complete: true })),
    (v) => v.toFixed(1))
    .concat(rows.filter((r) => !wildOk(r)).map(OFF))

  // Turns per trainer battle: (measured turns + projected mandatory trainers)
  // ÷ (attempts + projected battles). A run qualifies once it fought its first
  // trainer (Andreas 2026-09-14); the projection is pace-based, see trainerMatrix.
  const matrix = trainerMatrix(pool, rows)
  const byId = new Map(matrix.rows.map((m) => [m.row, m]))
  const trainerVals = []
  const trainerOff = []
  for (const r of rows) {
    const m = byId.get(r)
    if (!m || !m.eligible) { trainerOff.push(OFF(r)); continue }
    trainerVals.push({ row: r, value: m.avg, measured: m.measured, attempts: m.attempts, projected: m.projected, pace: m.pace,
      uncounted: m.uncounted.map((c) => ({ ...c, ...matrix.columns.find((x) => x.group === c.group) })),
      missing: m.cells.filter((c) => c.kind === 'projected').map((c) => ({ ...c, ...matrix.columns.find((x) => x.group === c.group) })),
      eligible: true, complete: m.complete })
  }
  const trainerTurns = rank(trainerVals, (v) => v.toFixed(1)).concat(trainerOff)
  return { movement, wildTurns, trainerTurns }
}

/** Token counts as "480" / "1.2k" / "12k". */
export function fmtTokens(v) {
  if (v == null || !(v >= 0)) return '—'
  if (v >= 10000) return Math.round(v / 1000) + 'k'
  if (v >= 1000) return (v / 1000).toFixed(1) + 'k'
  return String(Math.round(v))
}

export function fmtUsd(n) {
  if (n == null) return '—'
  if (n >= 1) return '$' + n.toFixed(2)
  return '$' + n.toFixed(n < 0.1 ? 3 : 2)
}

/** Minutes as "4.2m" / "12m" / "1.3h". */
export function fmtMinutes(v) {
  if (v == null || !(v >= 0)) return '—'
  if (v >= 90) return (v / 60).toFixed(1) + 'h'
  return v >= 10 ? Math.round(v) + 'm' : v.toFixed(1) + 'm'
}
