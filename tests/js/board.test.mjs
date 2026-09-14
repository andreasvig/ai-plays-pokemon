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
  baseModel, collapseBest, vendorOf, headlineSeries, secondarySeries, battleSeries, trainerTypicals, trainerMatrix, MIN_BATTLE_OBSERVATIONS, turnsPerMinute, costPer10, fmtTokens, PERF_LINE,
  legTurns, typicalTurnsPerLeg, projectRun, perTaskSeries, estimationMatrix, PROJECT_FROM_GATE, fmtMinutes,
  ofModel, levelOf, modelField, levelRows, runGateRows,
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
  // Inputs per turn: most first; a row without the statistic is ineligible and last.
  const withInputs = RANKED.map((r, i) => ({ ...r, avgInputsPerTurn: i === 0 ? null : 2 + i }))
  const { inputsPerTurn } = secondarySeries(withInputs, GATES, withInputs)
  assert.equal(inputsPerTurn[0].row.model, 'gpt-5.6-luna(max)')
  assert.equal(inputsPerTurn[0].label, '8.0')
  assert.equal(inputsPerTurn[0].height, 1)
  assert.equal(inputsPerTurn.at(-1).row.model, 'gpt-6-astra(medium)')
  assert.equal(inputsPerTurn.at(-1).eligible, false)
  // Output tokens per turn: fewest first; a row without usage data is ineligible and last.
  const withTokens = RANKED.map((r, i) => ({ ...r, avgOutputTokensPerTurn: i === 0 ? null : 400 * i }))
  const { outputTokens } = secondarySeries(withTokens, GATES, withTokens)
  assert.equal(outputTokens[0].row.model, RANKED[1].model)
  assert.equal(outputTokens[0].label, '400')
  assert.equal(outputTokens.filter((s) => s.eligible).at(-1).height, 1)
  assert.equal(outputTokens.at(-1).row.model, RANKED[0].model)
  assert.equal(outputTokens.at(-1).eligible, false)
  assert.deepEqual([fmtTokens(480), fmtTokens(1234), fmtTokens(12345), fmtTokens(null)], ['480', '1.2k', '12k', '—'])
})

test('battle series: rule-A turn costs, means over ≥4 fights, unfought trainers projected as one fight at pace × mean, hatched', () => {
  const brock = (turns, attempts = 1) => ({ group: '414', id: 414, name: 'Leader Brock', attempts, turns, won: true, mandatory: true })
  // Rows 6.. fought only the rival — in two tries, so they clear the two-fight bar (MIN_FIGHTS_FOR_PROJECTION).
  const rival = (turns, attempts = 1) => ({ group: 'rival_oaks_lab', id: 327, name: "Rival (Oak's Lab)", attempts, turns, won: true, mandatory: true })
  const pool = RANKED.map((r, i) => ({ ...r,
    battleFidelity: i === 0 ? null : 'backfill',
    wildBattles: 10, wildBattleTurns: 10 + i, battleTurnShare: 0.1 * (i + 1),
    movementEfficiency: i === 0 ? null : 0.5 + i * 0.05, stepsFidelity: 'bound', shortestSteps: 100, overworldSteps: 150,
    trainerBattles: i === 0 ? null : [rival(2, i <= 5 ? 1 : 2), ...(i <= 5 ? [brock(4 + i)] : [])],
    trainerBattleTurns: i === 0 ? null : 2 + (i <= 5 ? 4 + i : 0),
    completion: i <= 5 ? 100 : 50 }))
  const typ = trainerTypicals(pool)
  assert.equal(typ['414'].n, 5)                       // rows 1..5 fought Brock
  assert.deepEqual([typ['414'].typical, typ['414'].median], [7, 7])   // mean of 5,6,7,8,9
  assert.deepEqual([typ.rival_oaks_lab.n, typ.rival_oaks_lab.typical], [RANKED.length - 1, 2])
  const { movement, wildTurns, trainerTurns } = battleSeries(pool, pool)
  assert.equal(movement[0].row.model, pool.at(-1).model)         // best first
  assert.equal(movement.at(-1).eligible, false)                  // the row without the statistic
  assert.equal(wildTurns[0].label, '1.0')                        // row 0: 10 turns / 10 battles, fewest first
  // Wild battles: only runs that cleared Route 1 (row 6 = luna(max), four gates, is off even with battles).
  assert.equal(wildTurns.find((s) => s.row === pool[6]).eligible, false)
  assert.ok(wildTurns.filter((s) => s.eligible).every((s) => s.row.gateTurns.route1_reached != null))
  const noBrock = trainerTurns.filter((s) => s.eligible && s.projected > 0)
  assert.equal(noBrock.length, RANKED.length - 6)                // rows 6.. never met Brock and did not finish
  // Every projected row fought the rival in exactly the typical 2 turns → pace 1 → Brock at the mean 7.
  assert.ok(noBrock.every((s) => !s.complete && s.pace === 1 && s.projected === 7 && s.missing[0].name === 'Leader Brock'))
  assert.equal(noBrock[0].label, ((2 + 7) / 3).toFixed(1))        // (rival 2T over 2 tries + Brock 7) ÷ 3 fights
  // A slow rival fight (6 turns, 3× typical) projects Brock at 3 × 7 = 21, not the flat mean.
  const slow = battleSeries([{ ...pool[6], trainerBattles: [rival(6, 2)] }], pool).trainerTurns[0]
  assert.deepEqual([slow.pace, slow.projected, slow.missing[0].turns, slow.missing[0].ratio], [3, 21, 21, 3])
  assert.equal(slow.label, ((6 + 21) / 3).toFixed(1))
  // A fast one (1 turn) projects Brock at 3.5; a projected cell carries the pace as its ratio.
  const fast = trainerMatrix(pool, [{ ...pool[6], trainerBattles: [rival(1, 2)] }]).rows[0]
  assert.deepEqual([fast.pace, fast.cells.find((c) => c.kind === 'projected').turns, fast.cells[0].ratio], [0.5, 3.5, 0.5])
  assert.ok(trainerTurns.filter((s) => s.eligible && s.projected === 0).every((s) => s.complete))
  assert.equal(trainerTurns.at(-1).eligible, false)              // the row with no per-turn state
  assert.equal(MIN_BATTLE_OBSERVATIONS, 4)
  const tm = trainerMatrix(pool)
  assert.equal(tm.columns.length, 9)
  assert.equal(tm.columns.find((c) => c.group === '414').typical, 7)
  assert.equal(tm.rows[0].eligible, false)
  assert.deepEqual(tm.rows[6].cells.filter((c) => c.kind !== 'none').map((c) => c.kind), ['fought', 'projected'])
  // A run that fought nobody yet is left off even with per-turn state.
  const virgin = [{ ...pool[1], trainerBattles: [], trainerBattleTurns: 0 }]
  assert.equal(battleSeries(virgin, pool).trainerTurns[0].eligible, false)
  // One fight is not enough either (Andreas 2026-09-14: two before a projection is made); two is.
  const one = [{ ...pool[1], runId: 'one', model: 'one(high)', trainerBattles: [{ group: 'rival_oaks_lab', name: "Rival (Oak's Lab)", attempts: 1, turns: 5, won: true, mandatory: true }] }]
  assert.equal(battleSeries(one, pool).trainerTurns[0].eligible, false)
  const two = [{ ...one[0], trainerBattles: [{ ...one[0].trainerBattles[0], attempts: 2, turns: 9 }] }]
  assert.equal(battleSeries(two, pool).trainerTurns[0].eligible, true)
  // Fewer than 4 fights → nothing projected, even for a mandatory trainer; 4 is enough.
  const thin = pool.map((r, i) => ({ ...r, trainerBattles: i === 0 ? null : [rival(2, 2), ...(i <= 3 ? [brock(4)] : [])] }))   // rival in two tries: every row clears the two-fight bar
  assert.ok(battleSeries(thin, thin).trainerTurns.every((s) => !s.projected))
  const four = pool.map((r, i) => ({ ...r, trainerBattles: i === 0 ? null : [rival(2, 2), ...(i <= 4 ? [brock(4)] : [])] }))
  assert.ok(battleSeries(four, four).trainerTurns.some((s) => s.projected === 4))
  // Optional trainers: rows 1..5 finished; 4 of them fought Rick (10 turns each) → usable. Any run that did
  // not fight Rick — before Pewter, past it, or finished — is charged one Rick fight at pace × 10, so every
  // run is scored on the same roster.
  const rick = (turns) => ({ group: '102', id: 102, name: 'Bug Catcher Rick', attempts: 1, turns, won: true, mandatory: false })
  const withRick = pool.map((r, i) => ({ ...r, trainerBattles: i === 0 ? null : [rival(2, i <= 5 ? 1 : 2), ...(i <= 5 ? [brock(4 + i)] : []), ...(i >= 2 && i <= 5 ? [rick(10)] : [])] }))
  const wm = trainerMatrix(withRick)
  const early = wm.rows[6]                                   // luna(max): four gates, before the forest
  const rickCell = early.cells.find((c) => c.group === '102')
  assert.deepEqual([rickCell.kind, rickCell.turns, rickCell.ratio], ['projected', 10, 1])
  assert.deepEqual([early.projectedCount, early.projected], [2, 17])                    // Brock 7 + Rick 10
  assert.ok(Math.abs(early.avg - (2 + 17) / 4) < 1e-9)        // rival in 2 tries + 2 projected fights
  const finished = wm.rows[1]                                // finished, fought rival + Brock, never Rick
  const fr = finished.cells.find((c) => c.group === '102')
  assert.deepEqual([fr.kind, finished.complete, finished.projectedCount], ['projected', false, 1])
  assert.ok(Math.abs(finished.avg - (2 + 5 + finished.pace * 10) / 3) < 1e-9)
  // A fight against a trainer only one run met (Charlie) is shown but not counted: the run's measured
  // turns, fights and average ignore it, so it is scored on the same roster as everyone else.
  const charlie = { group: '532', id: 532, name: 'Bug Catcher Charlie', attempts: 1, turns: 3, won: true, mandatory: false }
  const solo = trainerMatrix(withRick, [{ ...withRick[1], trainerBattles: [...withRick[1].trainerBattles, charlie] }]).rows[0]
  const cc = solo.cells.find((c) => c.group === '532')
  assert.deepEqual([cc.kind, cc.counted, solo.measured, solo.attempts, solo.uncounted.length], ['fought', false, wm.rows[1].measured, wm.rows[1].attempts, 1])
  assert.equal(solo.avg, wm.rows[1].avg)
  const pastPewter = trainerMatrix(withRick, [{ ...withRick[6], gateTurns: withRick[2].gateTurns, completion: 90, trainerBattles: [rival(2, 2)] }]).rows[0]
  assert.deepEqual(pastPewter.cells.filter((c) => c.kind === 'projected').map((c) => c.group), ['102', '414'])   // past Pewter: still charged
})

test('empty board yields empty series without dividing by zero', () => {
  assert.deepEqual(headlineSeries([], GATES), { performance: [], time: [], cost: [] })
  assert.deepEqual(secondarySeries([], GATES), { speed: [], cost10: [], turnsPerTask: [], inputsPerTurn: [], outputTokens: [] })
  assert.deepEqual(battleSeries([]), { movement: [], wildTurns: [], trainerTurns: [] })
})

test('estimationMatrix: every run, every leg, ratios to the typical leg, estimates that sum to the projection', () => {
  const m = estimationMatrix(RANKED, GATES)
  assert.deepEqual(m.typical, typicalTurnsPerLeg(RANKED, GATES))
  assert.equal(m.clears.left_bedroom, 7)          // every run cleared the first gate
  assert.equal(m.clears.brock_defeated, 4)        // the four full clears
  assert.equal(m.slowest.pewter_reached, 166)     // gemini low's Pewter leg
  // Sorted: most gates first, then fewest projected turns.
  assert.deepEqual(m.rows.slice(0, 4).map((x) => x.row.model),
    ['gpt-6-astra(medium)', 'gemini-3.8-flash(medium)', 'gemini-3.8-flash(low)', 'claude-opus-5(high)'])
  const glm = m.rows.find((x) => x.row.model === 'glm-5.3-flash(high)')
  assert.equal(glm.legs.length, 10)
  assert.equal(glm.legs[0].ratio, 6 / m.typical.left_bedroom)
  // Two estimated legs (Pewter floored at the 300 turns burned, Brock at pace); they sum to the projection.
  assert.deepEqual(glm.estimates.map((e) => e.gate), ['pewter_reached', 'brock_defeated'])
  assert.equal(glm.tail.turns, 300)
  assert.equal(glm.estimates[0].turns, 300)
  assert.ok(glm.estimates[0].floored)
  const played = glm.legs.reduce((a, l) => a + l.turns, 0)
  assert.ok(Math.abs(played + glm.estimates[0].turns + glm.estimates[1].turns - glm.projected) < 1e-9)
  assert.equal(glm.turnsPerTask, glm.projected / 12)
  assert.ok(Math.abs(glm.playedShare - 512 / glm.projected) < 1e-9)
  // A run below the eligibility gate has no estimates and no totals, but its legs are still in the matrix.
  const luna = m.rows.find((x) => x.row.model === 'gpt-5.6-luna(max)')
  assert.equal(luna.eligible, false)
  assert.equal(luna.legs.length, 4)
  assert.deepEqual(luna.estimates, [])
  assert.equal(luna.costPerTask, null)
  assert.deepEqual(luna.tail, { gate: 'rival1_done', turns: 30 })
  // A full clear has no tail and no estimates; played share is 100%.
  const astra = m.rows[0]
  assert.equal(astra.tail, null)
  assert.equal(astra.playedShare, 1)
  // Empty pool: no rows, no division by zero.
  assert.deepEqual(estimationMatrix([], GATES).rows, [])
})

// ───────────── model pages (2026-09-14) ─────────────

test('modelField: every other model at its best level, every level of the page model, rank order kept', () => {
  const field = modelField(RANKED, 'gemini-3.8-flash')
  assert.deepEqual(field.map((r) => r.model), [
    'gpt-6-astra(medium)', 'gemini-3.8-flash(medium)', 'gemini-3.8-flash(low)', 'claude-opus-5(high)', 'glm-5.3-flash(high)', 'qwen3.8-flash(thinking)', 'gpt-5.6-luna(max)',
  ])
  // Mutation control: for another model the second gemini level is collapsed away again.
  assert.equal(modelField(RANKED, 'claude-opus-5').length, RANKED.length - 1)
  assert.ok(modelField(RANKED, 'claude-opus-5').every((r) => r.model !== 'gemini-3.8-flash(low)'))
  assert.equal(ofModel('gemini-3.8-flash')(RANKED[2]), true)
  assert.equal(ofModel('gemini-3.8-flash')(RANKED[0]), false)
})

test('levelOf reads the parenthesised level; a bare alias has none', () => {
  assert.equal(levelOf('gemini-3.8-flash(medium)'), 'medium')
  assert.equal(levelOf('qwen3.8-flash(thinking)'), 'thinking')
  assert.equal(levelOf('muse-spark-1.3'), null)
})

test('levelRows: catalog order highest first, unrun levels absent, unknown levels appended, no-level models one line', () => {
  const entry = { model: 'gemini-3.8-flash', thinking_levels: ['high', 'medium', 'low', 'minimal'] }
  const out = levelRows(RANKED, entry, 'gemini-3.8-flash')
  assert.deepEqual(out.map((l) => [l.level, l.absent, l.row?.model ?? null]), [
    ['high', true, null], ['medium', false, 'gemini-3.8-flash(medium)'], ['low', false, 'gemini-3.8-flash(low)'], ['minimal', true, null],
  ])
  // A level the catalog dropped still shows, after the known ones.
  const renamed = levelRows(RANKED, { thinking_levels: ['high'] }, 'gemini-3.8-flash')
  assert.deepEqual(renamed.map((l) => [l.level, l.absent]), [['high', true], ['medium', false], ['low', false]])
  // No catalog entry: the board's own levels, rank order.
  assert.deepEqual(levelRows(RANKED, null, 'gemini-3.8-flash').map((l) => l.level), ['medium', 'low'])
  // reasoning_type none: one line under level null.
  const bare = [row('muse-spark-1.3', 'meta/muse', 40, 40, 90, 10, 0.01, FOUR)]
  assert.deepEqual(levelRows(bare, { thinking_levels: [] }, 'muse-spark-1.3').map((l) => [l.level, l.absent]), [[null, false]])
  // A model with an entry and no rows: every level absent (the page is not reachable, but the helper is total).
  assert.equal(levelRows(RANKED, entry, 'nobody').every((l) => l.absent), true)
})

test('runGateRows: stamps, time and cost at the stamp, the failed leg from the termination reason, leg efficiency from the legs', () => {
  const gates = [
    { id: 'left_bedroom', name: 'Left the bedroom', cap: 30 }, { id: 'left_house', name: 'Stepped outside', cap: 30 },
    { id: 'oaks_lab_entered', name: "Entered Oak's Lab", cap: 30 }, { id: 'starter_chosen', name: 'Chose a starter', cap: 30 },
  ]
  const r = { turns: 40, status: 'terminated', terminationReason: 'leg_cap:oaks_lab_entered',
    gateTurns: { left_bedroom: 3, left_house: 9 }, gateTimesS: { left_bedroom: 30, left_house: 95 }, gateCostsUsd: { left_bedroom: 0.01, left_house: 0.035 },
    movementLegs: [{ node_id: 'left_bedroom', d_open: 9, steps: 12, source: 'video', status: 'closed' }, { node_id: 'left_house', d_open: 12, steps: 12, source: 'trace', status: 'closed' },
                   { node_id: 'oaks_lab_entered', d_open: 4, steps: 31, source: 'bound', status: 'open' }] }
  const out = runGateRows(r, gates)
  assert.deepEqual(out.map((g) => [g.id, g.status, g.turn, g.legTurns, g.timeS, g.costUsd]), [
    ['left_bedroom', 'done', 3, 3, 30, 0.01], ['left_house', 'done', 9, 6, 95, 0.035],
    ['oaks_lab_entered', 'failed', null, 30, null, null],          // 40 − 9 = 31 turns on the leg, capped at 30
    ['starter_chosen', 'pending', null, null, null, null],
  ])
  assert.ok(Math.abs(out[0].efficiency - 0.75) < 1e-9 && out[1].efficiency === 1 && Math.abs(out[2].efficiency - 4 / 31) < 1e-9 && out[3].efficiency === null)
  assert.deepEqual(out.map((g) => g.stepsSource), ['video', 'trace', 'bound', null])
  // A terminated run without a named gate fails the first uncleared one; a completed run fails nothing.
  assert.equal(runGateRows({ ...r, terminationReason: null }, gates)[2].status, 'failed')
  assert.equal(runGateRows({ ...r, status: 'completed', terminationReason: null }, gates)[2].status, 'pending')
  // A stamp after a gap (a later gate stamped, an earlier one not) does not count: the chain stops at the gap.
  assert.deepEqual(runGateRows({ turns: 20, status: 'terminated', gateTurns: { left_bedroom: 3, oaks_lab_entered: 9 } }, gates).map((g) => g.status), ['done', 'failed', 'pending', 'pending'])
  // Missing time/cost/legs on an older row: nulls, never NaN.
  const bare = runGateRows({ turns: 5, status: 'completed', gateTurns: { left_bedroom: 2 } }, gates.slice(0, 1))
  assert.deepEqual([bare[0].timeS, bare[0].costUsd, bare[0].efficiency], [null, null, null])
})
