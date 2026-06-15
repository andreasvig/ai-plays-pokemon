// Real API client. Swaps in for `mockData.js` at wiring time (Plan §P5,
// "Contract mapping: mock module → real API"). The backend serves FLAT,
// snake_case `RunSummary` JSON (`model_dump(mode="json")`); the components
// consume camelCase fields plus a handful of CLIENT-SIDE derivations
// (`completion`, `avgCostPerTurn`, `avgSPerTurn`, `perfScore`, `slug`,
// `openSource`, `furthestGateName`). This module maps snake→camel and keeps
// the SAME derivation formulas the mock used so the components don't change.
import { gate, GATE_INDEX } from './gates.js'
import { runSlug } from './router.svelte.js'

// open-weight families (for the All / Open-source filter) — same regex as the mock
const OSS = /^(kimi|qwen|mimo|gemma|perceptron)/
export const isOpenSource = (m) => OSS.test(m)

async function getJSON(path) {
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json()
}
async function send(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail ?? '' } catch { /* ignore */ }
    throw new Error(`${method} ${path} → ${res.status}${detail ? ': ' + detail : ''}`)
  }
  return res.status === 204 ? null : res.json()
}

// flat snake_case RunSummary → the camelCase run shape the components consume.
// Mirrors mockData's mkRun() output (minus the seeded illustrative bits).
export function toRun(s) {
  const total = s.total_gates || 0
  const reached = s.gates_reached || 0
  const completion = total > 0 ? Math.round((reached / total) * 100) : 0
  const furthestGate = s.furthest_gate ?? null
  const r = {
    runId: s.run_id,
    kind: s.kind,
    model: s.model,
    openSource: isOpenSource(s.model),
    config: s.config_stem ?? (s.kind === 'casual' ? null : 'pokebench-v1'),
    benchmarkVersion: s.benchmark_version ?? null,
    status: s.status,
    startedAt: s.started_at,
    endedAt: s.ended_at,
    turns: s.turns ?? 0,
    durationS: s.duration_s ?? 0,
    totalCostUsd: s.total_cost_usd ?? 0,
    avgCostPerTurn: s.avg_cost_per_turn_usd ?? 0,
    avgSPerTurn: s.avg_s_per_turn ?? 0,
    furthestGate,
    furthestGateName: furthestGate && GATE_INDEX[furthestGate] != null ? gate(furthestGate).name : null,
    gatesReached: reached,
    totalGates: total,
    completion,
    terminationReason: s.termination_reason ?? null,
    continuedFrom: s.continued_from ?? null,
    maxTurns: s.max_turns ?? null,
  }
  r.slug = runSlug(r)
  return r
}

// perfScore: 0–100 = completion% (partial); 100–150 = turn-efficiency among
// completers (slowest completer → 100, fastest → 150). Computed across the SET
// (same as mockData) so the charts' y-axis matches. Mutates rows in place.
function stampPerfScore(rows) {
  const C = rows.filter((r) => r.completion >= 100)
  if (C.length) {
    const maxC = Math.max(...C.map((r) => r.turns))
    const minC = Math.min(...C.map((r) => r.turns))
    for (const r of rows) {
      r.perfScore = r.completion >= 100
        ? (maxC === minC ? 125 : 100 + (50 * (maxC - r.turns)) / (maxC - minC))
        : r.completion
    }
  } else {
    for (const r of rows) r.perfScore = r.completion
  }
  return rows
}

// ───────────────────────────── reads ─────────────────────────────

export async function fetchModels() {
  // GET /api/models → [{alias, openrouter_id, observed|null}, ...]; the dialog
  // only needs the alias list (MODELS in the mock was a string[]).
  const models = await getJSON('/api/models')
  return models.map((m) => m.alias)
}

export async function fetchConfigs() {
  // GET /api/configs → ["config-3.13", ...]
  return getJSON('/api/configs')
}

export async function fetchLeaderboard() {
  // GET /api/leaderboard → best official run per model, gates desc / turns asc.
  // Add displayed rank + perfScore (the same derivations the mock baked in).
  const rows = (await getJSON('/api/leaderboard')).map(toRun)
  stampPerfScore(rows)
  return rows.map((r, i) => ({ ...r, rank: i + 1 }))
}

export async function fetchRuns(filters = {}) {
  // GET /api/runs?kind=&status=&q=&sort=&order= → flat history rows.
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v != null && v !== '' && v !== 'all') qs.set(k, v)
  }
  const path = '/api/runs' + (qs.toString() ? `?${qs}` : '')
  const rows = (await getJSON(path)).map(toRun)
  return stampPerfScore(rows)
}

export async function fetchRun(runId) {
  return toRun(await getJSON(`/api/runs/${encodeURIComponent(runId)}`))
}

export async function fetchQueue() {
  // GET /api/queue → {active: <queue_id|null>, items: [QueuedRun, ...]}.
  // QueuedRun is snake_case; map to the camelCase queue-card shape (queueId,
  // continueFrom, maxTurns). `active` is a queue_id, but the running run isn't
  // in `items` — App joins it to the live RunSummary / stats separately.
  const { active, items } = await getJSON('/api/queue')
  return {
    active,
    items: (items || []).map((q) => ({
      queueId: q.queue_id,
      kind: q.kind,
      model: q.model,
      config: q.config ?? null,
      maxTurns: q.max_turns ?? null,
      continueFrom: q.continue_from ?? null,
      enqueuedAt: q.enqueued_at,
    })),
  }
}

export async function fetchEmulatorStatus() {
  // GET /api/emulator/status → {configured, process_up, connected, busy}.
  // Never throws on an unconfigured control plane (returns configured:false).
  try {
    return await getJSON('/api/emulator/status')
  } catch {
    return { configured: false, process_up: false, connected: false, busy: false }
  }
}

// ───────────────────────────── mutations ─────────────────────────────

export function enqueueRun(spec) {
  // spec uses the dialog's camelCase fields; the API expects snake_case. Official
  // ignores config/max_turns server-side, but send only what's relevant.
  const body = { kind: spec.kind, model: spec.model }
  if (spec.kind === 'casual') {
    if (spec.config != null) body.config = spec.config
    if (spec.maxTurns != null) body.max_turns = spec.maxTurns
    if (spec.continueFrom != null) body.continue_from = spec.continueFrom
  }
  return send('POST', '/api/queue', body)
}

export function cancelQueued(queueId) {
  return send('DELETE', `/api/queue/${encodeURIComponent(queueId)}`)
}

export function moveQueued(queueId, toIndex) {
  return send('POST', `/api/queue/${encodeURIComponent(queueId)}/move`, { to_index: toIndex })
}

export function stopRun(runId) {
  return send('POST', `/api/runs/${encodeURIComponent(runId)}/stop`)
}

export function continueRun(runId, maxTurns) {
  const body = maxTurns != null ? { max_turns: maxTurns } : undefined
  return send('POST', `/api/runs/${encodeURIComponent(runId)}/continue`, body)
}
