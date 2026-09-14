// Unit tests for the shared model selection's pure half:
//   src/dashboard/web/src/lib/selection.js
// Run directly (`node tests/js/selection.test.mjs`) or via tests/test_live_feed_js.py.
import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultPicked, applySelection, parsePicked, serializePicked, presets } from '../../src/dashboard/web/src/lib/selection.js'

const row = (model, completion, openSource = false) => ({ runId: `${model}-run`, model, completion, openSource })
// Rank order, as the leaderboard delivers it: two levels of sol, the high one ranked first.
const ROWS = [row('gpt-6-astra(medium)', 100), row('gpt-5.6-sol(high)', 80), row('qwen3.8-flash(thinking)', 60, true), row('gpt-5.6-sol(max)', 40)]

test('default selection is the best level per model, in rank order', () => {
  assert.deepEqual(defaultPicked(ROWS), ['gpt-6-astra(medium)', 'gpt-5.6-sol(high)', 'qwen3.8-flash(thinking)'])
  assert.deepEqual(applySelection(ROWS, null).map((r) => r.model), defaultPicked(ROWS))
})

test('an explicit selection keeps board order and can include a second level of one model', () => {
  const picked = ['gpt-5.6-sol(max)', 'gpt-5.6-sol(high)']
  assert.deepEqual(applySelection(ROWS, picked).map((r) => r.model), ['gpt-5.6-sol(high)', 'gpt-5.6-sol(max)'])
  assert.deepEqual(applySelection(ROWS, []), [])
  assert.deepEqual(applySelection(ROWS, ['not-a-model']), [])
})

test('the URL round-trips: absent = default, empty = none, parentheses survive encoding', () => {
  assert.equal(parsePicked(''), null)
  assert.equal(parsePicked('?other=1'), null)
  assert.deepEqual(parsePicked('?models='), [])
  const picked = ['gpt-5.6-sol(high)', 'qwen3.8-flash(thinking)']
  const qs = serializePicked(picked)
  assert.ok(qs.startsWith('models='))
  assert.deepEqual(parsePicked('?' + qs), picked)
  assert.equal(serializePicked(null), '')
})

test('presets: best (default), all levels, completed only, open-weights, none', () => {
  const by = Object.fromEntries(presets(ROWS).map((p) => [p.key, p.picked]))
  assert.equal(by.best, null)
  assert.equal(by.all.length, 4)
  assert.deepEqual(by.complete, ['gpt-6-astra(medium)'])
  assert.deepEqual(by.oss, ['qwen3.8-flash(thinking)'])
  assert.deepEqual(by.none, [])
})
