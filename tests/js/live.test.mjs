// Unit tests for the LIVE spectate feed's pure logic:
//   src/dashboard/web/src/lib/live.js  (event → box, compaction blocks)
//   src/dashboard/web/src/lib/feed.js  (windowing + interleave)
//
// Run directly (`node tests/js/live.test.mjs`) or through
// tests/test_live_feed_js.py, which is what CI/pytest does. No svelte, no DOM,
// no browser: both modules are deliberately import-free so the rules below are
// checkable without a run.
//
// Event shapes are taken from the producers, not invented:
//   compaction_start    src/agent/append_agent.py  {turn, after_turn, segment, reason}
//   compaction_complete   "                        {turn, after_turn, segment, handover, previous_memory}
//   compaction_thinking   "                        {turn, content}
//   llm_request_usage     "                        {turn, phase, segment, attempt, request_id,
//                                                   continuity:{...}, cache_read_fraction,
//                                                   reasoning_tokens, provider, ...}
//   endpoint_warning      "                        {endpoint, kind, message}   (NO turn)
//   memory_update_output  src/agent/turn.py        {turn, content}  (JSON string)
//   budget_exhausted      src/agent/turn.py        {turn, spent_usd, max_spend_usd}

import assert from 'node:assert/strict'
import test from 'node:test'

import { LiveTrace, memoryHint, usageBox, compactionElapsedS, compactionLabel, turnPreview } from '../../src/dashboard/web/src/lib/live.js'
import { windowFeed } from '../../src/dashboard/web/src/lib/feed.js'

// ── helpers ──────────────────────────────────────────────────────────────
const usage = (over = {}) => ({
  type: 'llm_request_usage',
  turn: 21,
  phase: 'gameplay',
  segment: 1,
  attempt: 1,
  request_id: 'r1',
  cache_read_fraction: 0.9312,
  reasoning_tokens: 0,
  provider: 'DeepInfra',
  continuity: { replayed_blocks: 3, expected_blocks: 3, local_replay: 'intact', provider_feedback: 'accepted' },
  ...over,
})

const compStart = (over = {}) => ({
  type: 'compaction_start', turn: 21, after_turn: 20, segment: 1, reason: 'turn_interval', ...over,
})
const compComplete = (over = {}) => ({
  type: 'compaction_complete',
  turn: 21,
  after_turn: 20,
  segment: 2,
  handover: { continuation_summary: 'Heading north out of Pewter.', memory: { current_goal: 'Mt Moon' } },
  previous_memory: { current_goal: 'Pewter gym' },
  ...over,
})

/** Feed a whole append-style turn-21 compaction sequence, in producer order. */
function compactionSequence(trace, { thinking = true, reasoningTokens = 0 } = {}) {
  trace.ingest({ type: 'turn_start', turn: 21 })
  trace.ingest(compStart())
  trace.ingest(usage({ turn: 21, phase: 'compaction', request_id: 'c1', reasoning_tokens: reasoningTokens }))
  if (thinking) trace.ingest({ type: 'compaction_thinking', turn: 21, content: 'What matters for the next segment…' })
  trace.ingest(compComplete())
}

function feedOf(trace) {
  return windowFeed({
    turnBoxes: trace.turnBoxes,
    masterCards: new Map(),
    compactionsByTurn: trace.compactionsByTurn,
  }).feed
}

// ── #2 — model requests are NOT "Screen settling" ────────────────────────

test('llm_request_usage becomes a Request/diagnostics box, never a settle box', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 7 })
  trace.ingest(usage({ turn: 7 }))
  const boxes = trace.turnBoxes.get(7)
  assert.equal(boxes.length, 1)
  assert.equal(boxes[0].k, 'diag')
  assert.notEqual(boxes[0].k, 'settle')
  assert.match(boxes[0].t, /cache 93\.1% of input/)
  assert.match(boxes[0].t, /reasoning replay 3\/3 \(intact\)/)
  assert.match(boxes[0].t, /provider feedback: accepted/)
  // provider + phase ride on the meta line
  assert.match(boxes[0].meta, /gameplay/)
  assert.match(boxes[0].meta, /DeepInfra/)
})

test('only screen_settled produces a settle box', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 7 })
  trace.ingest(usage({ turn: 7 }))
  trace.ingest(compStart({ turn: 7, after_turn: 6 }))
  trace.ingest({ type: 'screen_settling', turn: 7 })
  trace.ingest({ type: 'screen_settled', turn: 7, duration: 1.4 })
  const settles = trace.turnBoxes.get(7).filter((b) => b.k === 'settle')
  assert.equal(settles.length, 1)
  assert.match(settles[0].t, /Settled in 1\.4s/)
})

test('unknown cache fraction reads "unknown", not 0%', () => {
  const box = usageBox(usage({ cache_read_fraction: null }))
  assert.match(box.t, /cache unknown/)
})

// ── the user requirement — a compaction is its OWN unnumbered row ─────────

test('a compaction is its own feed row: no turn number, its own counter', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 20 })
  trace.ingest({ type: 'llm_output', turn: 20, args: {} })   // not claimed here
  compactionSequence(trace)

  const feed = feedOf(trace)
  const kinds = feed.map((e) => e.kind)
  // turn 20, then the compaction, then turn 21 — the report's placement.
  assert.deepEqual(kinds, ['turn', 'compaction', 'turn'])
  assert.deepEqual(feed.filter((e) => e.kind === 'turn').map((e) => e.turn), [20, 21])

  const block = feed[1]
  assert.equal(block.number, 1)
  assert.equal(block.afterTurn, 20)
  assert.equal(block.beforeTurn, 21)
  // A compaction row has NO turn of its own — that is the whole point.
  assert.equal(block.turn, undefined)
  assert.equal(compactionLabel(block), 'Compaction 1 · after turn 20')
})

test('gameplay turns keep their numbers and the compaction adds none', () => {
  const trace = new LiveTrace()
  for (const t of [19, 20]) trace.ingest({ type: 'turn_start', turn: t })
  compactionSequence(trace)
  trace.ingest({ type: 'turn_start', turn: 22 })
  const turns = feedOf(trace).filter((e) => e.kind === 'turn').map((e) => e.turn)
  assert.deepEqual(turns, [19, 20, 21, 22])
})

test('compaction numbering increments per compaction, independent of turns', () => {
  const trace = new LiveTrace()
  compactionSequence(trace)
  trace.ingest({ type: 'turn_start', turn: 41 })
  trace.ingest(compStart({ turn: 41, after_turn: 40, segment: 2 }))
  trace.ingest(compComplete({ turn: 41, after_turn: 40, segment: 3 }))
  const blocks = feedOf(trace).filter((e) => e.kind === 'compaction')
  assert.deepEqual(blocks.map((b) => [b.number, b.afterTurn]), [[1, 20], [2, 40]])
})

test('compaction thinking and the compaction request go INSIDE the block, not on the turn', () => {
  const trace = new LiveTrace()
  compactionSequence(trace)
  const block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.deepEqual(block.boxes.map((b) => b.k), ['diag', 'thinking'])
  assert.equal(block.boxes[0].meta.includes('compaction'), true)
  // nothing from the compaction landed on turn 21
  assert.deepEqual(trace.turnBoxes.get(21), [])
})

test('a running compaction is open with a live timer; complete closes it with the handover', () => {
  let clock = 1_000
  const trace = new LiveTrace({ now: () => clock })
  trace.ingest({ type: 'turn_start', turn: 21 })
  trace.ingest(compStart())
  let block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.equal(block.complete, false)
  assert.equal(block.endedAtMs, null)
  // still running → elapsed is measured against the caller's clock
  assert.equal(compactionElapsedS(block, 8_500), 7.5)

  clock = 95_000
  trace.ingest(compComplete())
  block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.equal(block.complete, true)
  // finished → frozen at its own duration, whatever `now` says afterwards
  assert.equal(compactionElapsedS(block, 999_999), 94)
  assert.equal(block.summary, 'Heading north out of Pewter.')
  assert.deepEqual(block.memory, { current_goal: 'Mt Moon' })
  assert.deepEqual(block.previousMemory, { current_goal: 'Pewter gym' })
})

test('a compaction event with no open block never falls back onto a turn box', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 5 })
  trace.ingest({ type: 'compaction_thinking', turn: 5, content: 'orphan' })
  assert.deepEqual(trace.turnBoxes.get(5), [])
})

test('a replayed backlog does not duplicate a compaction block', () => {
  // The events WS replays from cursor 0 on every reconnect.
  const trace = new LiveTrace()
  compactionSequence(trace)
  compactionSequence(trace)
  const blocks = feedOf(trace).filter((e) => e.kind === 'compaction')
  assert.equal(blocks.length, 1)
  assert.equal(blocks[0].number, 1)
})

// ── #20 — one Memory box per compaction ──────────────────────────────────

test('append: the memory_update_output that follows compaction_complete is dropped', () => {
  const trace = new LiveTrace()
  compactionSequence(trace)
  // turn.py logs this immediately after compaction_complete, same turn, same memory
  trace.ingest({ type: 'memory_update_output', turn: 21, content: JSON.stringify({ current_goal: 'Mt Moon' }) })
  assert.deepEqual(trace.turnBoxes.get(21).filter((b) => b.k === 'memory'), [])
  const block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.deepEqual(block.memory, { current_goal: 'Mt Moon' })
})

test('legacy: a per-turn memory_update_output still renders its Memory box', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 12 })
  trace.ingest({ type: 'memory_update_output', turn: 12, content: '{"party": ["Charmander"]}' })
  const mem = trace.turnBoxes.get(12).filter((b) => b.k === 'memory')
  assert.equal(mem.length, 1)
  assert.equal(mem[0].t, '{"party":["Charmander"]}')
})

test('legacy: "(no changes)" and "none" pass through unparsed', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 1 })
  trace.ingest({ type: 'memory_update_output', turn: 1, content: 'none' })
  trace.ingest({ type: 'memory_update_output', turn: 1 })
  assert.deepEqual(trace.turnBoxes.get(1).map((b) => b.t), ['none', '(no changes)'])
})

// ── #21 — endpoint_warning reaches the browser ───────────────────────────

test('endpoint_warning renders as a warning box on the turn in flight', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 1 })
  // It carries no `turn` at all (append_agent.py emits endpoint/kind/message).
  trace.ingest({
    type: 'endpoint_warning',
    endpoint: 'deepinfra/turbo',
    kind: 'cache_economics',
    message: 'Endpoint deepinfra/turbo lists no cache-read price: caching cannot save money here',
  })
  const boxes = trace.turnBoxes.get(1)
  assert.equal(boxes.length, 1)
  assert.equal(boxes[0].k, 'warning')
  assert.match(boxes[0].t, /no cache-read price/)
  assert.equal(boxes[0].meta, 'deepinfra/turbo')
})

// ── #22 — reasoning withheld by the endpoint ─────────────────────────────

test('billed reasoning with no thinking text shows a withheld line', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 3 })
  trace.ingest(usage({ turn: 3, reasoning_tokens: 1024 }))
  const kinds = trace.turnBoxes.get(3).map((b) => b.k)
  assert.deepEqual(kinds, ['diag', 'withheld'])
  assert.match(trace.turnBoxes.get(3)[1].t, /Reasoning withheld by endpoint \(1,024 tokens\)/)
})

test('the withheld line disappears when the thinking text actually arrives', () => {
  // append_agent emits llm_request_usage BEFORE the thinking blocks of the same
  // request, so the box is provisional by construction.
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 3 })
  trace.ingest(usage({ turn: 3, reasoning_tokens: 1024 }))
  trace.ingest({ type: 'llm_thinking', turn: 3, content: 'I should go north.' })
  const kinds = trace.turnBoxes.get(3).map((b) => b.k)
  assert.deepEqual(kinds, ['diag', 'thinking'])
})

test('zero reasoning tokens adds no withheld line', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 3 })
  trace.ingest(usage({ turn: 3, reasoning_tokens: 0 }))
  trace.ingest(usage({ turn: 3, reasoning_tokens: null, request_id: 'r2' }))
  assert.deepEqual(trace.turnBoxes.get(3).map((b) => b.k), ['diag', 'diag'])
})

test('withheld reasoning inside a compaction sits in the block and clears on its thinking', () => {
  const trace = new LiveTrace()
  compactionSequence(trace, { thinking: false, reasoningTokens: 512 })
  let block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.deepEqual(block.boxes.map((b) => b.k), ['diag', 'withheld'])

  const trace2 = new LiveTrace()
  compactionSequence(trace2, { thinking: true, reasoningTokens: 512 })
  block = feedOf(trace2).find((e) => e.kind === 'compaction')
  assert.deepEqual(block.boxes.map((b) => b.k), ['diag', 'thinking'])
})

// ── #16 — budget_exhausted is a real terminal box ────────────────────────

test('budget_exhausted renders an explicit terminal box with both amounts', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 33 })
  trace.ingest({ type: 'budget_exhausted', turn: 33, spent_usd: 2.0104, max_spend_usd: 2 })
  const boxes = trace.turnBoxes.get(33)
  assert.equal(boxes.length, 1)
  assert.equal(boxes[0].k, 'terminal')
  assert.match(boxes[0].t, /Spend budget reached/)
  assert.match(boxes[0].t, /\$2\.0104 of \$2\.0000/)
})

// ── #15 — memory panel hint ──────────────────────────────────────────────

test('memoryHint names the turn of the first compaction, and is null without one', () => {
  assert.equal(
    memoryHint({ compaction: { every_n_turns: 20 } }),
    'Memory is written at compaction · first compaction after turn 20'
  )
  assert.equal(memoryHint({}), null)
  assert.equal(memoryHint(null), null)
  assert.equal(memoryHint({ compaction: { every_n_turns: 0 } }), null)
})

// ── #23-adjacent — report-only traces add nothing to the live feed ───────

test('turn_trace / compaction_trace are ignored by the live feed', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 9 })
  assert.equal(trace.ingest({ type: 'turn_trace', turn: 9, messages: [{ role: 'user', content: 'x' }] }), false)
  assert.equal(trace.ingest({ type: 'compaction_trace', turn: 9, messages: [] }), false)
  assert.deepEqual(trace.turnBoxes.get(9), [])
})

// ── windowing keeps working with compaction rows present ─────────────────

test('pruning drops compaction rows below the window with their turns', () => {
  const trace = new LiveTrace()
  compactionSequence(trace)                      // compaction before turn 21
  for (let t = 22; t <= 100; t += 1) trace.ingest({ type: 'turn_start', turn: t })
  const { cutoffTurn } = windowFeed({
    turnBoxes: trace.turnBoxes, masterCards: new Map(),
    compactionsByTurn: trace.compactionsByTurn, fallbackTurns: 40,
  })
  assert.equal(cutoffTurn, 60)
  trace.prune(cutoffTurn)
  assert.equal(trace.compactionsByTurn.size, 0)
  assert.equal(trace.turnBoxes.has(21), false)
  // and a later compaction after pruning still numbers on from where it was
  trace.ingest(compStart({ turn: 101, after_turn: 100, segment: 2 }))
  assert.equal([...trace.compactionsByTurn.values()][0][0].number, 2)
})

test('windowFeed without a compaction map behaves exactly as before', () => {
  const turnBoxes = new Map([[1, [{ k: 'thinking', t: 'a' }]], [2, []]])
  const { feed } = windowFeed({ turnBoxes, masterCards: new Map() })
  assert.deepEqual(feed.map((e) => [e.kind, e.turn]), [['turn', 1], ['turn', 2]])
})

// ── legacy shapes still render ────────────────────────────────────────────

test('legacy events (ocr, tools, retries, settle) map exactly as before', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 4 })
  trace.ingest({ type: 'ocr_flush', turn: 4, n_captures: 2, cleaned: 'PEWTER CITY', duration: 0.4, cost_usd: 0.00012 })
  trace.ingest({ type: 'tool_call', turn: 4, tool: 'press', args: { b: 'a' } })
  trace.ingest({ type: 'tool_response', turn: 4, response: 'ok' })
  trace.ingest({ type: 'agent_retry', turn: 4, attempt: 1, max_attempts: 3, error_type: 'TimeoutError', timeout_s: 360, retryable: true, provider_sort: 'throughput' })
  trace.ingest({ type: 'output_retry', turn: 4, content: 'bad json' })
  trace.ingest({ type: 'llm_text', turn: 4, content: 'hello' })
  trace.ingest({ type: 'agent_error', turn: 4, error: 'boom' })
  assert.deepEqual(
    trace.turnBoxes.get(4).map((b) => b.k),
    ['ocr', 'tool', 'tool', 'retry', 'retry', 'output', 'error']
  )
})

test('an empty ocr_flush adds nothing', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 4 })
  assert.equal(trace.ingest({ type: 'ocr_flush', turn: 4, n_captures: 0, cleaned: '' }), false)
  assert.deepEqual(trace.turnBoxes.get(4), [])
})

test('reset() clears turns, blocks and the compaction counter', () => {
  const trace = new LiveTrace()
  compactionSequence(trace)
  trace.reset()
  assert.equal(trace.turnBoxes.size, 0)
  assert.equal(trace.compactionsByTurn.size, 0)
  compactionSequence(trace)
  assert.equal([...trace.compactionsByTurn.values()][0][0].number, 1)
})

// ── elapsed must come from the producer's clock, not the browser's ────────

test('a finished compaction reports the duration the EVENTS recorded', () => {
  // A reconnect replays the whole backlog at once, so the client clock sees
  // start and complete in the same millisecond.
  let clock = 1_700_000_000_000
  const trace = new LiveTrace({ now: () => clock })
  trace.ingest({ type: 'turn_start', turn: 21 })
  trace.ingest({ ...compStart(), timestamp: 1_700_000_000 })
  trace.ingest({ ...compComplete(), timestamp: 1_700_000_247 })
  const block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.equal(compactionElapsedS(block, clock), 247)
})

test('a running compaction replayed from the backlog counts from ITS start', () => {
  let clock = 1_700_000_500_000
  const trace = new LiveTrace({ now: () => clock })
  trace.ingest({ type: 'turn_start', turn: 21 })
  trace.ingest({ ...compStart(), timestamp: 1_700_000_400 })
  const block = feedOf(trace).find((e) => e.kind === 'compaction')
  // 100s ago by the producer's clock — not 0 just because we just ingested it
  assert.equal(compactionElapsedS(block, 1_700_000_500_000), 100)
})

test('elapsed falls back to the client clock when an event carried no timestamp', () => {
  let clock = 5_000
  const trace = new LiveTrace({ now: () => clock })
  trace.ingest({ type: 'turn_start', turn: 21 })
  trace.ingest(compStart())
  clock = 11_000
  trace.ingest(compComplete())
  const block = feedOf(trace).find((e) => e.kind === 'compaction')
  assert.equal(compactionElapsedS(block, clock), 6)
})

// ── #22 — the collapsed row must say something ───────────────────────────

test('turnPreview falls through thinking → decision prose → withheld → error', () => {
  assert.equal(turnPreview([{ k: 'thinking', t: 'I should go   north' }]), 'I should go north')
  assert.equal(
    turnPreview([{ k: 'output', ok: true, t: 'Last turn: succeeded — Going north', reasoning: 'Going north' }]),
    'Going north'
  )
  assert.equal(
    turnPreview([{ k: 'withheld', t: 'Reasoning withheld by endpoint (1,024 tokens)' }]),
    'Reasoning withheld by endpoint (1,024 tokens)'
  )
  assert.equal(turnPreview([{ k: 'error', t: 'boom' }]), 'boom')
  // nothing to preview → empty, NOT a bare ellipsis
  assert.equal(turnPreview([]), '')
  assert.equal(turnPreview([{ k: 'ocr', t: 'PEWTER' }]), '')
  // long text is elided
  assert.equal(turnPreview([{ k: 'thinking', t: 'x'.repeat(80) }], 10), 'xxxxxxxxxx…')
})

test('a turn whose reasoning was withheld previews the withheld line, not "…"', () => {
  const trace = new LiveTrace()
  trace.ingest({ type: 'turn_start', turn: 3 })
  trace.ingest(usage({ turn: 3, reasoning_tokens: 900 }))
  assert.match(turnPreview(trace.turnBoxes.get(3)), /Reasoning withheld by endpoint/)
})
