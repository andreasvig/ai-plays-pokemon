// Unit tests for the shared model selection's pure half:
//   src/dashboard/web/src/lib/selection.js
// Run directly (`node tests/js/selection.test.mjs`) or via tests/test_live_feed_js.py.
import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultPicked, applySelection, parsePicked, serializePicked, presets, paretoFront, bestPerLab } from '../../src/dashboard/web/src/lib/selection.js'
import { GATES } from '../../src/dashboard/web/src/lib/gates.js'

const GATE_IDS = GATES.slice(0, 12).map((g) => g.id)
// A full clear: every task stamped, two turns apiece. Every row here clears, so
// every leg has a typical value and every row is projectable — the fixture is
// about WHICH rows the default keeps, not about eligibility.
const stamps = () => Object.fromEntries(GATE_IDS.map((g, i) => [g, (i + 1) * 2]))

/**
 * `perf` is the rank score, `usd` the run's total cost and `mins` its wall
 * clock — the two the per-task figures divide by the 12 tasks, so they are the
 * frontier axes directly.
 */
const row = (model, resolved, perf, usd, mins, openSource = false) => ({
  runId: `${model}-run`, model, modelResolved: resolved, openSource,
  completion: 100, perfScore: perf, turns: 24, totalGates: 12, gateTurns: stamps(),
  totalCostUsd: usd, durationS: mins * 60, avgCostPerTurn: usd / 24, avgSPerTurn: (mins * 60) / 24,
})

// Six rows, three labs, two thinking levels each — in board rank order (perf desc).
//
//            lab        perf   $/task   min/task
//   B astra(low)     openai  150    0.20     3.33   ← openai's best; cheapest-per-perf AND fastest-per-perf
//   C flash(medium)  google  140    0.50     4.17   ← google's best
//   D flash(low)     google  130    0.80     1.67   ← FASTEST of all; dominated on price
//   A astra(minimal) openai  120    0.10     5.00   ← CHEAPEST of all; dominated on speed
//   E fable(medium)  anthro  110    1.00     8.33   ← anthropic's best; dominated on both
//   F fable(low)     anthro  100    0.40     6.67   ← dominated on both, and not its lab's best
const B = row('gpt-6-astra(low)', 'openai/gpt-6-astra', 150, 2.4, 40)
const C = row('gemini-3.8-flash(medium)', 'google/gemini-3.8-flash', 140, 6.0, 50)
const D = row('gemini-3.8-flash(low)', 'google/gemini-3.8-flash', 130, 9.6, 20)
const A = row('gpt-6-astra(minimal)', 'openai/gpt-6-astra', 120, 1.2, 60)
const E = row('claude-fable-5.1(medium)', 'anthropic/claude-fable-5.1', 110, 12.0, 100)
const F = row('claude-fable-5.1(low)', 'anthropic/claude-fable-5.1', 100, 4.8, 80, true)
const ROWS = [B, C, D, A, E, F]

test('paretoFront keeps the upper-left envelope and always keeps the cheapest point', () => {
  const pts = [{ x: 3, y: 5 }, { x: 1, y: 2 }, { x: 2, y: 1 }, { x: 4, y: 5 }]
  assert.deepEqual(paretoFront(pts), [{ x: 1, y: 2 }, { x: 3, y: 5 }])
  // A point equal on y to something cheaper is dominated — the cheaper one wins.
  assert.deepEqual(paretoFront([{ x: 1, y: 9 }, { x: 2, y: 9 }]), [{ x: 1, y: 9 }])
  assert.deepEqual(paretoFront([{ x: null, y: 1 }, { x: 1, y: null }]), [])
  assert.deepEqual(paretoFront([]), [])
})

test('bestPerLab takes one row per lab, not per model family', () => {
  const got = bestPerLab(ROWS).map((r) => r.model)
  assert.deepEqual(got, ['gpt-6-astra(low)', 'gemini-3.8-flash(medium)', 'claude-fable-5.1(medium)'])
  // Two rows of the same lab from DIFFERENT families still collapse to one.
  const twoFamilies = [row('gpt-6-astra(low)', 'openai/gpt-6-astra', 150, 1, 10),
                       row('gpt-5.6-sol(high)', 'openai/gpt-5.6-sol', 140, 1, 10)]
  assert.deepEqual(bestPerLab(twoFamilies).map((r) => r.model), ['gpt-6-astra(low)'])
})

test('the default is the best row per lab PLUS every row on either frontier', () => {
  const picked = defaultPicked(ROWS)
  // Board rank order is preserved.
  assert.deepEqual(picked, ['gpt-6-astra(low)', 'gemini-3.8-flash(medium)', 'gemini-3.8-flash(low)',
                            'gpt-6-astra(minimal)', 'claude-fable-5.1(medium)'])
  // The two rows that earn their place ONLY on a frontier are the point of the rule:
  // a second level of a lab already represented, kept because nothing dominates it.
  assert.ok(picked.includes('gpt-6-astra(minimal)'), 'the cheapest row is in, though astra(low) outranks it')
  assert.ok(picked.includes('gemini-3.8-flash(low)'), 'the fastest row is in, though flash(medium) outranks it')
  // And the control: dominated on price, dominated on speed, not its lab's best.
  assert.ok(!picked.includes('claude-fable-5.1(low)'), 'a row on neither frontier and not a lab best is left out')
  assert.deepEqual(applySelection(ROWS, null).map((r) => r.model), picked)
})

test('the speed frontier is load-bearing on its own', () => {
  // Make the fastest row slow instead: nothing else changes, and it drops out —
  // so the row really was kept by the TIME axis and not by the price one.
  const slowed = ROWS.map((r) => (r === D ? { ...D, durationS: 999 * 60, avgSPerTurn: (999 * 60) / 24 } : r))
  assert.ok(!defaultPicked(slowed).includes('gemini-3.8-flash(low)'))
})

test('the price frontier is load-bearing on its own', () => {
  const dear = ROWS.map((r) => (r === A ? { ...A, totalCostUsd: 99, avgCostPerTurn: 99 / 24 } : r))
  assert.ok(!defaultPicked(dear).includes('gpt-6-astra(minimal)'))
})

test('a row the board cannot project cannot reach a frontier, but its lab still stands', () => {
  // Stopped at task 3: no pace, no per-task figure. It is the only row of its
  // lab, so it is in — on the lab rule alone, never on a frontier.
  const early = { ...row('grok-4.6(high)', 'x-ai/grok-4.6', 5, 0.01, 1), completion: 25, perfScore: 25,
    gateTurns: Object.fromEntries(GATE_IDS.slice(0, 3).map((g, i) => [g, (i + 1) * 2])) }
  const picked = defaultPicked([...ROWS, early])
  assert.ok(picked.includes('grok-4.6(high)'), 'the lab is represented')
  assert.ok(!picked.includes('claude-fable-5.1(low)'), 'and nothing else sneaks in with it')
})

test('an explicit selection keeps board order and can include a second level of one model', () => {
  const picked = ['claude-fable-5.1(low)', 'claude-fable-5.1(medium)']
  assert.deepEqual(applySelection(ROWS, picked).map((r) => r.model), ['claude-fable-5.1(medium)', 'claude-fable-5.1(low)'])
  assert.deepEqual(applySelection(ROWS, []), [])
  assert.deepEqual(applySelection(ROWS, ['not-a-model']), [])
})

test('the URL round-trips: absent = default, empty = none, parentheses survive encoding', () => {
  assert.equal(parsePicked(''), null)
  assert.equal(parsePicked('?other=1'), null)
  assert.deepEqual(parsePicked('?models='), [])
  const picked = ['gpt-6-astra(low)', 'gemini-3.8-flash(medium)']
  const qs = serializePicked(picked)
  assert.ok(qs.startsWith('models='))
  assert.deepEqual(parsePicked('?' + qs), picked)
  assert.equal(serializePicked(null), '')
})

test('presets: the default, best per model, all levels, completed only, open-weights, none', () => {
  const by = Object.fromEntries(presets(ROWS).map((p) => [p.key, p.picked]))
  assert.equal(by.default, null, 'the default preset is null, so it follows defaultPicked')
  // "Best per model" is now its own preset, and it is NOT the default: it keeps
  // one row per family, so it drops the frontier siblings the default keeps.
  assert.deepEqual(by.best, ['gpt-6-astra(low)', 'gemini-3.8-flash(medium)', 'claude-fable-5.1(medium)'])
  assert.notDeepEqual(by.best, defaultPicked(ROWS))
  assert.equal(by.all.length, 6)
  assert.equal(by.complete.length, 6)
  assert.deepEqual(by.oss, ['claude-fable-5.1(low)'])
  assert.deepEqual(by.none, [])
})

test('a free model cannot buy its way onto the default via the price frontier', () => {
  // Same lab as B (so bestPerLab cannot be what keeps it) and strictly worse than
  // B on speed (so the time frontier cannot either). Its ONLY claim is price, and
  // at $0 that claim would be unbeatable — which is exactly what must not count.
  const FREE = { ...row('gpt-6-astra(free)', 'openai/gpt-6-astra', 130, 0, 90),
                 pricePerM: { prompt: 0, completion: 0 } }
  const picked = defaultPicked([...ROWS, FREE])
  assert.ok(!picked.includes('gpt-6-astra(free)'), 'a $0 row is not on the price frontier')
  // The control that proves the fixture could have won: the SAME row with a real
  // (tiny) price does take the frontier, so the exclusion is about being unpriced
  // and not about the row being uncompetitive.
  const CHEAP = { ...FREE, model: 'gpt-6-astra(cheap)', pricePerM: { prompt: 0.01, completion: 0.01 },
                  totalCostUsd: 0.01, avgCostPerTurn: 0.01 / 24 }
  assert.ok(defaultPicked([...ROWS, CHEAP]).includes('gpt-6-astra(cheap)'))
})
