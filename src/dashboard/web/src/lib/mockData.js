// Mock data for the frontend prototype. Field shapes mirror the REAL
// run_summary.json (session / cost / referee) so this maps 1:1 onto the API
// when we wire up. Seeded from real model aliases (configs/models.yaml) and the
// real pokebench-v1 gate ladder. Numbers are illustrative.
import { GATE_INDEX, TOTAL_GATES, gate, gatesReached } from './gates.js'
import { runSlug } from './router.svelte.js'

export const MODELS = [
  'claude-opus-4.8(xhigh)', 'claude-opus-4.8(high)', 'claude-opus-4.8(medium)', 'claude-opus-4.8(low)', 'claude-opus-4.8(minimal)',
  'claude-opus-4.7(xhigh)', 'claude-opus-4.7(high)', 'claude-opus-4.7(medium)', 'claude-opus-4.7(low)', 'claude-opus-4.7(minimal)',
  'claude-sonnet-4.6(high)', 'claude-sonnet-4.6(medium)', 'claude-sonnet-4.6(low)', 'claude-sonnet-4.6(minimal)',
  'claude-haiku-4.5(high)', 'claude-haiku-4.5(medium)', 'claude-haiku-4.5(low)', 'claude-haiku-4.5(minimal)',
  'gpt-5.5(medium)', 'gpt-5.5(low)', 'gpt-5.4-nano(xhigh)',
  'gemini-3.1-pro(high)', 'gemini-3.1-pro(medium)', 'gemini-3.1-pro(low)', 'gemini-3.1-pro(minimal)',
  'gemini-3.5-flash(high)', 'gemini-3.5-flash(medium)', 'gemini-3.5-flash(low)', 'gemini-3.5-flash(minimal)',
  'gemini-3-flash(high)', 'gemini-3-flash(medium)', 'gemini-3-flash(low)', 'gemini-3-flash(minimal)',
  'gemini-3.1-flash-lite(high)', 'gemini-3.1-flash-lite(medium)', 'gemini-3.1-flash-lite(low)', 'gemini-3.1-flash-lite(minimal)',
  'grok-4.3', 'grok-build-0.1(medium)',
  'kimi-k2.6(thinking)', 'kimi-k2.6(non-thinking)',
  'qwen3.6-plus(thinking)', 'qwen3.6-plus(non-thinking)',
  'mimo-v2.5-pro(thinking)', 'mimo-v2.5-pro(non-thinking)', 'mimo-v2.5(thinking)', 'mimo-v2.5(non-thinking)',
  'gemma-4-31b(thinking)', 'gemma-4-31b(non-thinking)', 'gemma-4-26b-a4b(thinking)', 'gemma-4-26b-a4b(non-thinking)',
  'perceptron-mk1(thinking)', 'perceptron-mk1(non-thinking)',
]
export const CONFIGS = ['config-3.13', 'config-3.12', 'config-tm-smoke', 'config-3.11']

// open-weight families (for the All / Open-source filter)
const OSS = /^(kimi|qwen|mimo|gemma|perceptron)/
export const isOpenSource = (m) => OSS.test(m)

const day = 86400
const BASE = Date.parse('2026-06-14T20:00:00Z') / 1000

let _id = 1000
function mkRun(o) {
  const reached = gatesReached(o.furthest)
  const completion = Math.round((reached / TOTAL_GATES) * 100)
  const cpt = o.cpt          // cost per turn (usd)
  const spt = o.spt          // seconds per turn
  const totalCostUsd = +(cpt * o.turns).toFixed(2)
  const durationS = Math.round(spt * o.turns)
  const startedAt = new Date((BASE - o.daysAgo * day - (o.hour ?? 0) * 3600) * 1000).toISOString()
  const r = {
    runId: o.runId ?? `2026-06-${String(15 - o.daysAgo).padStart(2, '0')}_run-${_id++}`,
    kind: o.kind ?? 'official',
    model: o.model,
    openSource: isOpenSource(o.model),
    config: o.config ?? (o.kind === 'casual' ? 'config-3.13' : 'pokebench-v1'),
    benchmarkVersion: o.kind === 'casual' ? null : 'pokebench-v1',
    status: o.status,
    startedAt,
    turns: o.turns,
    durationS,
    totalCostUsd,
    avgCostPerTurn: cpt,
    avgSPerTurn: spt,
    furthestGate: o.furthest,
    furthestGateName: o.furthest ? gate(o.furthest).name : null,
    gatesReached: reached,
    completion,
    terminationReason: o.status === 'terminated' ? `missed_gate:${nextGateId(o.furthest)}` : null,
    continuedFrom: o.continuedFrom ?? null,
    maxTurns: o.kind === 'casual' ? (o.maxTurns ?? 1500) : null,
  }
  r.slug = runSlug(r)
  return r
}
function nextGateId(furthest) {
  const idx = GATE_INDEX[furthest]
  const ids = Object.keys(GATE_INDEX)
  return ids[idx + 1] ?? furthest
}

// best official run per model -> the leaderboard
const OFFICIAL = [
  // completers (100% — reached thunder_badge), spread by turns
  { model: 'claude-opus-4.8(xhigh)',  furthest: 'thunder_badge', turns: 1153, cpt: 0.085, spt: 62, daysAgo: 1 },
  { model: 'claude-sonnet-4.6(high)', furthest: 'thunder_badge', turns: 1208, cpt: 0.026, spt: 38, daysAgo: 3 },
  { model: 'grok-4.3',                furthest: 'thunder_badge', turns: 1255, cpt: 0.014, spt: 28, daysAgo: 2 },
  { model: 'gpt-5.5(medium)',         furthest: 'thunder_badge', turns: 1290, cpt: 0.041, spt: 50, daysAgo: 2 },
  { model: 'gemini-3.1-pro(high)',    furthest: 'thunder_badge', turns: 1342, cpt: 0.019, spt: 33, daysAgo: 1 },
  // partial runs (terminated at a gate deadline)
  { model: 'kimi-k2.6(thinking)',        furthest: 'vermilion_reached', turns: 1004, cpt: 0.011, spt: 22, daysAgo: 4, status: 'terminated' },
  { model: 'gemini-3.5-flash(high)',     furthest: 'cascade_badge',     turns: 842,  cpt: 0.008, spt: 18, daysAgo: 4, status: 'terminated' },
  { model: 'qwen3.6-plus(thinking)',     furthest: 'mt_moon_cleared',   turns: 712,  cpt: 0.009, spt: 20, daysAgo: 5, status: 'terminated' },
  { model: 'gemini-3-flash(high)',       furthest: 'brock_defeated',    turns: 498,  cpt: 0.005, spt: 10, daysAgo: 6, status: 'terminated' },
  { model: 'gemma-4-31b(thinking)',      furthest: 'pewter_reached',    turns: 410,  cpt: 0.004, spt: 16, daysAgo: 6, status: 'terminated' },
  { model: 'mimo-v2.5-pro(thinking)',    furthest: 'pokedex_received',  turns: 305,  cpt: 0.013, spt: 24, daysAgo: 7, status: 'terminated' },
  { model: 'claude-haiku-4.5(high)',     furthest: 'viridian_reached',  turns: 205,  cpt: 0.006, spt: 14, daysAgo: 8, status: 'terminated' },
  { model: 'gemini-3.1-flash-lite(high)',furthest: 'left_house',        turns: 50,   cpt: 0.0035,spt: 12, daysAgo: 9, status: 'terminated' },
]

export const leaderboard = OFFICIAL
  .map((o) => mkRun({ ...o, status: o.status ?? 'completed' }))
  .sort((a, b) => b.completion - a.completion || a.turns - b.turns)
  .map((r, i) => ({ ...r, rank: i + 1 }))

// perfScore: 0–100 = completion% (partial); 100–150 = turn-efficiency among
// completers (slowest completer -> 100, fastest -> 150). Drives the charts.
{
  const C = leaderboard.filter((r) => r.completion >= 100)
  const maxC = Math.max(...C.map((r) => r.turns))
  const minC = Math.min(...C.map((r) => r.turns))
  for (const r of leaderboard) {
    r.perfScore = r.completion >= 100
      ? (maxC === minC ? 125 : 100 + 50 * (maxC - r.turns) / (maxC - minC))
      : r.completion
  }
}

export const activeRun = {
  ...mkRun({ model: 'gemini-3.1-pro(medium)', furthest: 'mt_moon_entered', turns: 631, cpt: 0.018, spt: 35, daysAgo: 0, status: 'running' }),
  currentTurn: 631,
  currentGateDeadline: 700,
  nextGate: 'mt_moon_cleared',
}

export const queue = [
  { queueId: 'q_01', kind: 'official', model: 'claude-opus-4.8(high)' },
  { queueId: 'q_02', kind: 'casual',   model: 'gemini-3.5-flash(high)', config: 'config-3.13', maxTurns: 1500 },
  { queueId: 'q_03', kind: 'casual',   model: 'grok-4.3', config: 'config-3.13', maxTurns: 800, continueFrom: '2026-06-10_run-204' },
  { queueId: 'q_04', kind: 'official', model: 'kimi-k2.6(thinking)' },
]

export const runs = [
  activeRun,
  ...leaderboard,
  mkRun({ model: 'claude-opus-4.8(xhigh)', furthest: 'cerulean_reached', turns: 705, cpt: 0.072, spt: 58, daysAgo: 2, hour: 4, status: 'cancelled' }),
  mkRun({ model: 'gemini-3.5-flash(medium)', furthest: 'left_house', turns: 10, cpt: 0.044, spt: 33, daysAgo: 3, hour: 6, status: 'terminated', config: 'config-tm-smoke' }),
  mkRun({ kind: 'casual', model: 'claude-sonnet-4.6(high)', furthest: 'mt_moon_cleared', turns: 1500, cpt: 0.019, spt: 36, daysAgo: 4, hour: 2, status: 'completed', maxTurns: 1500 }),
  mkRun({ kind: 'casual', model: 'gemini-3.1-pro(high)', furthest: 'vermilion_reached', turns: 1500, cpt: 0.014, spt: 31, daysAgo: 5, hour: 3, status: 'completed', maxTurns: 1500, continuedFrom: '2026-06-09_run-188' }),
  mkRun({ kind: 'casual', model: 'grok-4.3', furthest: 'brock_defeated', turns: 600, cpt: 0.010, spt: 26, daysAgo: 6, hour: 5, status: 'crashed', maxTurns: 800 }),
  mkRun({ kind: 'casual', model: 'gemini-3-flash(low)', furthest: 'route1_reached', turns: 220, cpt: 0.003, spt: 8, daysAgo: 7, hour: 1, status: 'completed', maxTurns: 300 }),
  mkRun({ kind: 'casual', model: 'claude-haiku-4.5(medium)', furthest: 'starter_chosen', turns: 140, cpt: 0.005, spt: 13, daysAgo: 8, hour: 7, status: 'cancelled', maxTurns: 500 }),
].sort((a, b) => Date.parse(b.startedAt) - Date.parse(a.startedAt))

export const runBySlug = Object.fromEntries(runs.map((r) => [r.slug, r]))

export const stats = {
  modelsRanked: leaderboard.length,
  completers: leaderboard.filter((r) => r.completion >= 100).length,
  totalRuns: runs.length,
  benchmarkVersion: 'pokebench-v1',
}
