// Unit tests for the board's pure helpers:
//   src/dashboard/web/src/lib/board.js  (per-model collapse, vendor, headline series)
//
// Run directly (`node tests/js/board.test.mjs`) or through tests/test_live_feed_js.py,
// which globs every suite in this directory. No svelte, no DOM.
//
// Rows are the `toRun` shape from api.js after stampPerfScore: completion 0–100,
// perfScore 0–150 (100–150 = fewest turns among clears), rank order preserved.
import test from 'node:test'
import assert from 'node:assert/strict'
import { baseModel, collapseBest, vendorOf, headlineSeries, turnsPerMinute, costPer10, PERF_LINE } from '../../src/dashboard/web/src/lib/board.js'

const row = (model, resolved, completion, perfScore, turns, sPerTurn, costPerTurn) =>
  ({ runId: `${model}-run`, model, modelResolved: resolved, completion, perfScore, turns, avgSPerTurn: sPerTurn, avgCostPerTurn: costPerTurn })

// Ranked like the live board on 2026-09-12: clears by turns, then completion desc.
const RANKED = [
  row('gpt-6-astra(low)', 'openai/gpt-6-astra', 100, 150, 145, 30.8, 0.055),
  row('gpt-6-astra(medium)', 'openai/gpt-6-astra', 100, 148.9, 150, 32.0, 0.06),
  row('gemini-3.8-flash(medium)', 'google/gemini-3.8-flash', 100, 129.4, 237, 12.0, 0.012),
  row('gemini-3.8-flash(low)', 'google/gemini-3.8-flash', 100, 100, 368, 17.9, 0.009),
  row('gemini-3.8-flash(high)', 'google/gemini-3.8-flash', 95, 95, 417, 40.0, 0.034),
  row('glm-5.3-flash(max)', 'z-ai/glm-5.3-flash', 95, 95, 557, 60.0, 0.004),
  row('deepseek-v4.1-flash(high)', 'deepseek/deepseek-v4.1-flash', 89, 89, 519, 45.0, 0.006),
  row('muse-spark-1.3(high)', 'meta/muse-spark-1.3', 58, 58, 144, 20.0, 0.02),
]

test('baseModel strips the thinking level and nothing else', () => {
  assert.equal(baseModel('gemini-3.8-flash(medium)'), 'gemini-3.8-flash')
  assert.equal(baseModel('claude-fable-5.1(max)'), 'claude-fable-5.1')
  assert.equal(baseModel('muse-spark-1.3'), 'muse-spark-1.3')
  assert.equal(baseModel(null), '')
})

test('collapseBest keeps the first (best-ranked) row per model, in rank order', () => {
  const out = collapseBest(RANKED)
  assert.deepEqual(out.map((r) => r.model), [
    'gpt-6-astra(low)', 'gemini-3.8-flash(medium)', 'glm-5.3-flash(max)', 'deepseek-v4.1-flash(high)', 'muse-spark-1.3(high)',
  ])
  // Mutation control: the medium astra and the low/high gemini rows are the ones dropped.
  assert.equal(out.length, RANKED.length - 3)
})

test('vendorOf reads the OpenRouter prefix first and falls back to the alias', () => {
  assert.equal(vendorOf(row('gemini-3.8-flash(low)', 'google/gemini-3.8-flash')).key, 'google')
  assert.equal(vendorOf(row('glm-5.3-flash(max)', 'z-ai/glm-5.3-flash')).key, 'z-ai')
  assert.equal(vendorOf({ model: 'claude-opus-5(high)', modelResolved: null }).key, 'anthropic')
  assert.equal(vendorOf({ model: 'mystery-model(high)', modelResolved: 'nobody/mystery' }).key, 'other')
})

test('performance bars: partials scale to the 100% line, clears rise above it by perfScore', () => {
  const { performance } = headlineSeries(collapseBest(RANKED))
  const byModel = Object.fromEntries(performance.map((s) => [s.row.model, s]))
  // The fastest clear (perfScore 150) touches the top of the plot.
  assert.ok(Math.abs(byModel['gpt-6-astra(low)'].height - 1) < 1e-9)
  // A clear at perfScore 100 (the slowest clear) sits exactly on the line.
  const slow = headlineSeries([row('x(low)', 'openai/x', 100, 100, 400, 10, 0.01)]).performance[0]
  assert.ok(Math.abs(slow.height - PERF_LINE) < 1e-9)
  // A partial never crosses the line: 95% of the way to it.
  assert.ok(Math.abs(byModel['glm-5.3-flash(max)'].height - 0.95 * PERF_LINE) < 1e-9)
  assert.ok(byModel['glm-5.3-flash(max)'].height < PERF_LINE)
  assert.equal(byModel['glm-5.3-flash(max)'].label, '95%')
  assert.equal(byModel['gpt-6-astra(low)'].complete, true)
  // Rank order is preserved on the performance card.
  assert.deepEqual(performance.map((s) => s.row.model), collapseBest(RANKED).map((r) => r.model))
})

test('speed is turns per minute, fastest first, tallest = fastest', () => {
  const { speed } = headlineSeries(collapseBest(RANKED))
  assert.equal(speed[0].row.model, 'gemini-3.8-flash(medium)')   // 12 s/turn → 5 turns/min
  assert.equal(speed[0].label, '5.0')
  assert.equal(speed[0].height, 1)
  assert.equal(speed[speed.length - 1].row.model, 'glm-5.3-flash(max)')   // 60 s/turn → 1/min
  assert.equal(turnsPerMinute(0), 0)
  assert.equal(turnsPerMinute(6), 10)
})

test('cost is USD per ten turns, cheapest first, tallest = dearest', () => {
  const { cost } = headlineSeries(collapseBest(RANKED))
  assert.equal(cost[0].row.model, 'glm-5.3-flash(max)')
  assert.equal(cost[0].label, '$0.040')
  assert.equal(cost[cost.length - 1].row.model, 'gpt-6-astra(low)')
  assert.equal(cost[cost.length - 1].label, '$0.55')
  assert.equal(cost[cost.length - 1].height, 1)
  assert.equal(costPer10(0.0123), 0.123)
})

test('empty board yields empty series without dividing by zero', () => {
  assert.deepEqual(headlineSeries([]), { performance: [], speed: [], cost: [] })
})
