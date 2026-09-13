// Unit tests for the board's pure helpers:
//   src/dashboard/web/src/lib/board.js  (per-model collapse, vendor, projection, headline + secondary series)
//
// Run directly (`node tests/js/board.test.mjs`) or through tests/test_live_feed_js.py,
// which globs every suite in this directory. No svelte, no DOM.
//
// Rows are the `toRun` shape from api.js after stampPerfScore: completion 0–100,
// perfScore 0–150 (100–150 = fewest turns among clears), rank order preserved,
// gateTurns = {gate id → turn stamped}.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  baseModel, collapseBest, vendorOf, headlineSeries, secondarySeries, turnsPerMinute, costPer10, PERF_LINE,
  legTurns, typicalTurnsPerLeg, projectRun, perTaskSeries, PROJECT_FROM_GATE, fmtMinutes,
} from '../../src/dashboard/web/src/lib/board.js'

const GATES = ['left_bedroom', 'left_house', 'oaks_lab_entered', 'starter_chosen', 'rival1_done', 'route1_reached',
  'viridian_reached', 'parcel_delivered', 'pokedex_received', 'viridian_forest_reached', 'pewter_reached', 'brock_defeated']
// Cumulative stamps from per-leg turns.
const stamps = (legs) => { let t = 0; const out = {}; legs.forEach((n, i) => { t += n; out[GATES[i]] = t }); return out }

const row = (model, resolved, completion, perfScore, turns, sPerTurn, costPerTurn, legs) =>
  ({ runId: `${model}-run`, model, modelResolved: resolved, completion, perfScore, turns, avgSPerTurn: sPerTurn, avgCostPerTurn: costPerTurn,
     durationS: turns * sPerTurn, totalCostUsd: turns * costPerTurn, totalGates: 12, gateTurns: legs ? stamps(legs) : null })

// Ranked like the live board: clears by turns, then completion desc. Leg turns
// are the 2026-09-13 board's real numbers where a run is named after one.
const FULL_A = [5, 2, 4, 18, 11, 4, 15, 22, 25, 45, 153, 65]          // opus: 369 turns
const FULL_B = [5, 2, 2, 3, 8, 8, 26, 8, 31, 52, 166, 57]             // gemini low: 368
const FULL_C = [2, 2, 3, 2, 4, 2, 13, 7, 15, 32, 53, 15]              // astra medium: 150
const FULL_D = [1, 3, 2, 3, 8, 3, 13, 14, 17, 39, 112, 22]            // gemini medium: 237
const TEN = [6, 6, 13, 16, 17, 18, 35, 20, 30, 51]                    // glm high: 212 on ten gates, 300 burned after
const SEVEN = [4, 3, 30, 7, 14, 6, 53]                                // qwen: 117 on seven, 50 burned after
const FOUR = [4, 4, 4, 6]                                             // luna: 18 on four, 30 burned after

const RANKED = [
  row('gpt-6-astra(medium)', 'openai/gpt-6-astra', 100, 150, 150, 32.0, 0.056, FULL_C),
  row('gemini-3.8-flash(medium)', 'google/gemini-3.8-flash', 100, 130, 237, 12.0, 0.018, FULL_D),
  row('gemini-3.8-flash(low)', 'google/gemini-3.8-flash', 100, 100.2, 368, 17.9, 0.009, FULL_B),
  row('claude-opus-5(high)', 'anthropic/claude-opus-5', 100, 100, 369, 31.3, 0.06, FULL_A),
  row('glm-5.3-flash(high)', 'z-ai/glm-5.3-flash', 87, 87, 512, 20.0, 0.0017, TEN),
  row('qwen3.8-flash(thinking)', 'alibaba/qwen3.8-flash', 60, 60, 167, 30.0, 0.003, SEVEN),
  row('gpt-5.6-luna(max)', 'openai/gpt-5.6-luna', 35, 35, 48, 10.0, 0.002, FOUR),
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
    'gpt-6-astra(medium)', 'gemini-3.8-flash(medium)', 'claude-opus-5(high)', 'glm-5.3-flash(high)', 'qwen3.8-flash(thinking)', 'gpt-5.6-luna(max)',
  ])
  assert.equal(out.length, RANKED.length - 1)   // mutation control: the low gemini is the one dropped
})

test('vendorOf reads the OpenRouter prefix first and falls back to the alias', () => {
  assert.equal(vendorOf(row('gemini-3.8-flash(low)', 'google/gemini-3.8-flash')).key, 'google')
  assert.equal(vendorOf(row('glm-5.3-flash(max)', 'z-ai/glm-5.3-flash')).key, 'z-ai')
  assert.equal(vendorOf({ model: 'claude-opus-5(high)', modelResolved: null }).key, 'anthropic')
  assert.equal(vendorOf({ model: 'mystery-model(high)', modelResolved: 'nobody/mystery' }).key, 'other')
})

test('legTurns walks the stamps in ladder order and stops at the first uncleared gate', () => {
  assert.deepEqual(legTurns(RANKED[4], GATES), TEN)
  assert.deepEqual(legTurns(RANKED[6], GATES), FOUR)
  assert.deepEqual(legTurns({ gateTurns: null }, GATES), [])
  // A gap in the stamps ends the walk even if a later gate carries a turn.
  assert.deepEqual(legTurns({ gateTurns: { left_bedroom: 3, oaks_lab_entered: 9 } }, GATES), [3])
})

test('typicalTurnsPerLeg is the MEAN over runs that cleared the gate, and needs three of them', () => {
  const typical = typicalTurnsPerLeg(RANKED, GATES)
  // Seven runs cleared left_bedroom: (2+1+5+5+6+4+4)/7.
  assert.ok(Math.abs(typical.left_bedroom - 27 / 7) < 1e-9)
  // Four clears on the Brock leg: (15+22+57+65)/4 = 39.75 — a mean, not the median 39.5.
  assert.ok(Math.abs(typical.brock_defeated - 39.75) < 1e-9)
  // Only two runs cleared a gate → no typical value.
  const two = typicalTurnsPerLeg(RANKED.slice(0, 2), GATES)
  assert.equal(two.brock_defeated, undefined)
})

test('projectRun: pace on cleared legs, missing legs at that pace, failed leg floored at turns burned', () => {
  const typical = typicalTurnsPerLeg(RANKED, GATES)
  const glm = projectRun(RANKED[4], typical, GATES)
  assert.equal(glm.eligible, true)
  assert.equal(glm.complete, false)
  const typTen = GATES.slice(0, 10).reduce((a, g) => a + typical[g], 0)
  assert.ok(Math.abs(glm.pace - 212 / typTen) < 1e-9)
  // Pewter at pace vs the 300 turns already burned there: the floor wins.
  const atPace = glm.pace * typical.pewter_reached
  assert.ok(atPace < 300, `at-pace estimate ${atPace} should be below the burned 300`)
  assert.equal(glm.floored, true)
  assert.equal(glm.failedGate, 'pewter_reached')
  const expected = 212 + 300 + glm.pace * typical.brock_defeated
  assert.ok(Math.abs(glm.projected - expected) < 1e-9)
  assert.ok(Math.abs(glm.estimated - (expected - 512)) < 1e-9)
  // Mutation control: without the floor the projection would be lower than the turns played on that leg.
  assert.ok(212 + atPace + glm.pace * typical.brock_defeated < expected)
  // A clear is measured, not projected.
  const opus = projectRun(RANKED[3], typical, GATES)
  assert.deepEqual([opus.eligible, opus.complete, opus.projected, opus.estimated], [true, true, 369, 0])
})

test(`a run that has not reached ${PROJECT_FROM_GATE} is not projected; one that just reached it is`, () => {
  const typical = typicalTurnsPerLeg(RANKED, GATES)
  const luna = projectRun(RANKED[6], typical, GATES)
  assert.equal(luna.eligible, false)
  assert.equal(luna.projected, null)
  const qwen = projectRun(RANKED[5], typical, GATES)
  assert.equal(qwen.eligible, true)
  assert.equal(qwen.failedGate, 'parcel_delivered')
  assert.ok(qwen.projected > 167)
})

test('perTaskSeries: cost and time to finish are the run\'s own totals plus estimated turns at its own rates', () => {
  const per = perTaskSeries(RANKED, GATES)
  const glm = per.find((s) => s.row.model === 'glm-5.3-flash(high)')
  assert.ok(Math.abs(glm.costToFinish - (512 * 0.0017 + glm.estimated * 0.0017)) < 1e-9)
  assert.ok(Math.abs(glm.minutesToFinish - (512 * 20 + glm.estimated * 20) / 60) < 1e-9)
  assert.ok(Math.abs(glm.costPerTask - glm.costToFinish / 12) < 1e-12)
  assert.ok(Math.abs(glm.turnsPerTask - glm.projected / 12) < 1e-12)
  const opus = per.find((s) => s.row.model === 'claude-opus-5(high)')
  assert.ok(Math.abs(opus.costToFinish - 369 * 0.06) < 1e-9)
  assert.ok(Math.abs(opus.turnsPerTask - 369 / 12) < 1e-12)
  const luna = per.find((s) => s.row.model === 'gpt-5.6-luna(max)')
  assert.equal(luna.costPerTask, null)
})

test('performance bars: partials scale to the 100% line, clears rise above it by perfScore', () => {
  const { performance } = headlineSeries(collapseBest(RANKED), GATES, RANKED)
  const byModel = Object.fromEntries(performance.map((s) => [s.row.model, s]))
  assert.ok(Math.abs(byModel['gpt-6-astra(medium)'].height - 1) < 1e-9)
  const slow = headlineSeries([row('x(low)', 'openai/x', 100, 100, 400, 10, 0.01, FULL_A)], GATES).performance[0]
  assert.ok(Math.abs(slow.height - PERF_LINE) < 1e-9)
  assert.ok(Math.abs(byModel['glm-5.3-flash(high)'].height - 0.87 * PERF_LINE) < 1e-9)
  assert.equal(byModel['glm-5.3-flash(high)'].label, '87%')
  assert.equal(byModel['gpt-6-astra(medium)'].complete, true)
  assert.deepEqual(performance.map((s) => s.row.model), collapseBest(RANKED).map((r) => r.model))
})

test('time and cost cards: per task, cheapest first, projected bars flagged, ineligible rows last with no bar', () => {
  const { time, cost } = headlineSeries(collapseBest(RANKED), GATES, RANKED)
  // The pool (every row) supplies the typical values even though the low gemini is collapsed away.
  assert.equal(cost[0].row.model, 'glm-5.3-flash(high)')
  assert.equal(cost[0].complete, false)          // → hatched
  assert.equal(cost[cost.length - 1].row.model, 'gpt-5.6-luna(max)')
  assert.equal(cost[cost.length - 1].eligible, false)
  assert.equal(cost[cost.length - 1].height, 0)
  assert.equal(cost[cost.length - 1].label, '—')
  const dearest = cost.filter((s) => s.eligible).at(-1)
  assert.equal(dearest.row.model, 'claude-opus-5(high)')
  assert.equal(dearest.height, 1)
  assert.equal(dearest.label, '$' + (369 * 0.06 / 12).toFixed(2))
  assert.equal(time[0].row.model, 'gemini-3.8-flash(medium)')   // 237 turns × 12 s / 12 gates = 4.0 min per task, ahead of astra's 6.7
  assert.equal(time[0].label, fmtMinutes(237 * 12 / 60 / 12))
  assert.equal(time[1].row.model, 'gpt-6-astra(medium)')
  assert.equal(fmtMinutes(6.67), '6.7m')
  assert.equal(fmtMinutes(95), '1.6h')
})

test('secondary strip keeps the per-turn measurements and adds turns per task', () => {
  const { speed, cost10, turnsPerTask } = secondarySeries(collapseBest(RANKED), GATES, RANKED)
  assert.equal(speed[0].row.model, 'gpt-5.6-luna(max)')          // 10 s/turn → 6 turns/min
  assert.equal(speed[0].label, '6.0')
  assert.equal(speed[0].height, 1)
  assert.equal(turnsPerMinute(0), 0)
  assert.equal(turnsPerMinute(6), 10)
  assert.equal(cost10[0].row.model, 'glm-5.3-flash(high)')
  assert.equal(cost10[0].label, '$0.017')
  assert.equal(cost10.filter((s) => s.eligible).at(-1).height, 1)
  assert.equal(costPer10(0.0123), 0.123)
  assert.equal(turnsPerTask[0].row.model, 'gpt-6-astra(medium)')  // 150 / 12
  assert.equal(turnsPerTask[0].label, '12.5')
  assert.equal(turnsPerTask.at(-1).eligible, false)
})

test('empty board yields empty series without dividing by zero', () => {
  assert.deepEqual(headlineSeries([], GATES), { performance: [], time: [], cost: [] })
  assert.deepEqual(secondarySeries([], GATES), { speed: [], cost10: [], turnsPerTask: [] })
})
