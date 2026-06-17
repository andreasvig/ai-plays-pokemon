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
    benchmark: s.benchmark ?? null,
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
  // GET /api/models → collapsed rows, one per model with a thinking-level axis:
  // [{model, openrouter_id, reasoning_type, default_level,
  //   levels:[{level, observed, run_count}], observed, run_count}, ...].
  // The dialog picks a model, then a thinking level (default = highest); the
  // submitted identity is `model(level)` (or bare `model` for type none).
  const models = await getJSON('/api/models')
  return models.map((m) => ({
    model: m.model,
    openrouter_id: m.openrouter_id ?? null,
    reasoning_type: m.reasoning_type ?? 'none',
    default_level: m.default_level ?? null,
    levels: Array.isArray(m.levels)
      ? m.levels.map((l) => ({ level: l.level, observed: l.observed ?? null, run_count: l.run_count ?? 0 }))
      : [],
    observed: m.observed ?? null,
    run_count: m.run_count ?? 0,
  }))
}

export async function fetchConfigs() {
  // GET /api/configs → ["config-3.13", ...]
  return getJSON('/api/configs')
}

export async function fetchBenchmarks() {
  // GET /api/benchmarks → [{id, name, goal, ladder, default}, ...] (registry order).
  return getJSON('/api/benchmarks')
}

export async function fetchLeaderboard(benchmark = null) {
  // GET /api/leaderboard[?benchmark=] → best official run per model for that
  // benchmark, gates desc / turns asc. Add displayed rank + perfScore (the same
  // derivations the mock baked in).
  const path = benchmark ? `/api/leaderboard?benchmark=${encodeURIComponent(benchmark)}` : '/api/leaderboard'
  const rows = (await getJSON(path)).map(toRun)
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
  // GET /api/emulator/status → {configured, process_up, connected, busy, active_run_id}.
  // active_run_id (Plan §P6) is the run-dir name of the live run, or null in
  // headless / between runs — Spectate opens /runs/{active_run_id}/ws/* with it.
  // Never throws on an unconfigured control plane (returns configured:false).
  try {
    return await getJSON('/api/emulator/status')
  } catch {
    return { configured: false, process_up: false, connected: false, busy: false, active_run_id: null }
  }
}

export async function fetchRunSummary(runId) {
  // GET /api/runs/{id}/summary → the RAW nested run_summary.json
  // ({session, cost:{…, per_turn}, turns, referee:{gates, furthest, …}}).
  // The Report view renders the scorecard + per-turn trace from this (Plan §P6).
  return getJSON(`/api/runs/${encodeURIComponent(runId)}/summary`)
}

export async function fetchRunTrace(runId) {
  // GET /api/runs/{id}/trace → the two-level master→player trace
  // ({run_id, has_tasks, task_count, turn_count, tasks: [{…master node…, turns:[…]}]}).
  // Casual / no-TaskMaster runs collapse to a single implicit group
  // (task_index:null, empty master_model, no master images). The Report view's
  // master-as-top-level task tree + image traces render from this (Round 8 B1/B2).
  return getJSON(`/api/runs/${encodeURIComponent(runId)}/trace`)
}

// ───────────────────────────── live sockets (Plan §P6) ─────────────────────
// WebSocket helpers. Each returns the WebSocket so the caller owns teardown
// (close on unmount). Built against the EXISTING spectate streams + the new
// control hub — same URL shapes the legacy dashboard used.

function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${path}`
}

export function openControlSocket(onMessage) {
  // WS /api/ws/control — pushes {type:"control", active, queue_len,
  // leaderboard_dirty} on every state change (refetch-on-ping, locked #7).
  // Auto-reconnects on drop. Returns a handle with close() that stops retries.
  let ws = null
  let closed = false
  let timer = null
  function connect() {
    if (closed) return
    ws = new WebSocket(wsUrl('/api/ws/control'))
    ws.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)) } catch { /* ignore malformed */ }
    }
    ws.onclose = () => { if (!closed) timer = setTimeout(connect, 2000) }
    ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
  }
  connect()
  return { close() { closed = true; if (timer) clearTimeout(timer); try { ws && ws.close() } catch { /* ignore */ } } }
}

export function openEventSocket(runId, onMsg) {
  // WS /runs/{id}/ws/events — pushes {type: event|state_update|stats, data};
  // the server replays the full backlog from cursor 0 on every (re)connect.
  // onMsg receives the parsed {type, data} envelope.
  let ws = null
  let closed = false
  let timer = null
  function connect() {
    if (closed) return
    ws = new WebSocket(wsUrl(`/runs/${encodeURIComponent(runId)}/ws/events`))
    ws.onmessage = (e) => {
      try { onMsg(JSON.parse(e.data)) } catch { /* ignore */ }
    }
    ws.onclose = (e) => { if (!closed && e.code !== 1008) timer = setTimeout(connect, 2000) }
    ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
  }
  connect()
  return { close() { closed = true; if (timer) clearTimeout(timer); try { ws && ws.close() } catch { /* ignore */ } } }
}

export function openScreenSocket(runId, onFrame) {
  // WS /runs/{id}/ws/screen — binary PNG frames. onFrame receives an object URL
  // for an <img src>; the caller revokes the PREVIOUS url it held.
  let ws = null
  let closed = false
  let timer = null
  function connect() {
    if (closed) return
    ws = new WebSocket(wsUrl(`/runs/${encodeURIComponent(runId)}/ws/screen`))
    ws.binaryType = 'arraybuffer'
    ws.onmessage = (e) => {
      const blob = new Blob([e.data], { type: 'image/png' })
      onFrame(URL.createObjectURL(blob))
    }
    ws.onclose = (e) => { if (!closed && e.code !== 1008) timer = setTimeout(connect, 2000) }
    ws.onerror = () => { try { ws.close() } catch { /* ignore */ } }
  }
  connect()
  return { close() { closed = true; if (timer) clearTimeout(timer); try { ws && ws.close() } catch { /* ignore */ } } }
}

export async function fetchRunConfig(runId) {
  // GET /runs/{id}/api/config → {referee:{enforce, ladder:[{id,name,deadline_turn,group?}]}}.
  // The spectate gate HUD reads the real ladder from here (never hardcoded).
  return getJSON(`/runs/${encodeURIComponent(runId)}/api/config`)
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
  } else if (spec.benchmark != null) {
    // Official: send WHICH benchmark (ladder + goal). config/max_turns are
    // ignored server-side (frozen wiring).
    body.benchmark = spec.benchmark
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

export function setEmulatorMute(mute) {
  return send('POST', '/api/emulator/mute', { mute })
}
