// Unit tests for the walk map's turn range:
//   src/dashboard/web/src/lib/turnrange.js  (the `?turns=` parameter)
//   src/dashboard/web/src/lib/query.js      (one query string, several owners)
//
// Run directly (`node tests/js/turnrange.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory.
import test from 'node:test'
import assert from 'node:assert/strict'
import { parseRanges, serializeRanges, normalizeRange, URL_KEY } from '../../src/dashboard/web/src/lib/turnrange.js'
import { readParam, withParam } from '../../src/dashboard/web/src/lib/query.js'
import { parsePicked, URL_KEY as MODELS_KEY } from '../../src/dashboard/web/src/lib/selection.js'

test('a range round-trips through the URL, keyed by the board alias', () => {
  const ranges = { 'gpt-6-sol(high)': [100, 150], pareto: [1, 40] }
  const qs = withParam('', URL_KEY, serializeRanges(ranges))
  assert.deepEqual(parseRanges('?' + qs), ranges)
  // The alias carries parentheses; they survive the trip.
  assert.deepEqual(parseRanges('?turns=gpt-6-sol%28high%29%3A100-150'), { 'gpt-6-sol(high)': [100, 150] })
})

test('a URL that says nothing, or says nonsense, is no range at all', () => {
  assert.deepEqual(parseRanges(''), {})
  assert.deepEqual(parseRanges('?models=pareto'), {})
  assert.deepEqual(parseRanges('?turns='), {})
  assert.deepEqual(parseRanges('?turns=high'), {}, 'no bounds')
  assert.deepEqual(parseRanges('?turns=high:abc-def'), {}, 'not numbers')
  assert.deepEqual(parseRanges('?turns=high:150-100'), {}, 'backwards')
  // One bad entry does not take the good one with it.
  assert.deepEqual(parseRanges('?turns=high:oops,low:1-40'), { low: [1, 40] })
})

test('the whole span is not a range: it serializes to nothing', () => {
  assert.equal(normalizeRange([0, 231], 0, 231), null)
  assert.equal(normalizeRange([-5, 999], 0, 231), null, 'clamped past both ends is still the whole run')
  assert.deepEqual(normalizeRange([100, 150], 0, 231), [100, 150])
  assert.deepEqual(normalizeRange([150, 100], 0, 231), [100, 150], 'ordered')
  assert.deepEqual(normalizeRange([-5, 150], 0, 231), [0, 150], 'clamped at one end only')
  assert.deepEqual(normalizeRange([100.4, 150.6], 0, 231), [100, 151], 'whole turns')
  assert.equal(normalizeRange(null, 0, 231), null)
  assert.equal(serializeRanges({}), '')
  assert.equal(serializeRanges({ high: null }), '')
})

test('the model picker and the turn range share one query string without eating each other', () => {
  // The regression this pair of helpers exists for: until 2026-09-28 the
  // selection writer rebuilt the URL as `pathname + '?models=…'`, so the first
  // touch of a picker deleted every other parameter.
  let qs = withParam('', MODELS_KEY, 'gpt-6-sol(high),pareto')
  qs = withParam(qs, URL_KEY, serializeRanges({ 'gpt-6-sol(high)': [100, 150] }))
  assert.deepEqual(parsePicked('?' + qs), ['gpt-6-sol(high)', 'pareto'])
  assert.deepEqual(parseRanges('?' + qs), { 'gpt-6-sol(high)': [100, 150] })

  // Move one: the other is untouched, both directions.
  const picked = withParam(qs, MODELS_KEY, 'pareto')
  assert.deepEqual(parseRanges('?' + picked), { 'gpt-6-sol(high)': [100, 150] })
  const ranged = withParam(qs, URL_KEY, serializeRanges({ pareto: [3, 9] }))
  assert.deepEqual(parsePicked('?' + ranged), ['gpt-6-sol(high)', 'pareto'])

  // Clear one: the other survives, and the dead key leaves no '?turns=' behind.
  const cleared = withParam(ranged, URL_KEY, '')
  assert.equal(readParam('?' + cleared, URL_KEY), null)
  assert.deepEqual(parsePicked('?' + cleared), ['gpt-6-sol(high)', 'pareto'])

  // And a parameter neither of them owns is carried through both writes.
  const foreign = withParam(withParam('?tab=gates', MODELS_KEY, 'pareto'), URL_KEY, 'pareto:1-2')
  assert.equal(readParam('?' + foreign, 'tab'), 'gates')
})
