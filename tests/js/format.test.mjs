// Unit tests for the board's display formatters:
//   src/dashboard/web/src/lib/format.js
//
// Run directly (`node tests/js/format.test.mjs`) or through tests/test_live_feed_js.py,
// which globs every suite in this directory.
import test from 'node:test'
import assert from 'node:assert/strict'
import { dur } from '../../src/dashboard/web/src/lib/format.js'

test('dur never prints a 60th second', () => {
  // The gate ladder reads wall time straight off the run, and a leg's seconds
  // are fractional. 1919.6s is 31m 59.6s: rounding the remainder on its own
  // pushed it to 60 while the minute term had already been floored past, and
  // the task ladder printed "31m 60s" (Andreas 2026-09-17).
  assert.equal(dur(1919.6), '32m 0s')
  assert.equal(dur(59.6), '1m 0s')
  // The hour form rolls the same way: 3599.6s is an hour, not "0h 59m".
  assert.equal(dur(3599.6), '1h 0m')
  for (let i = 0; i < 4000; i++) {
    const out = dur(i + 0.6)
    assert.ok(!/\b60s\b/.test(out), `${i + 0.6} → ${out}`)
    assert.ok(!/\b60m\b/.test(out), `${i + 0.6} → ${out}`)
  }
})

test('dur keeps the three shapes it had', () => {
  assert.equal(dur(null), '—')
  assert.equal(dur(0), '0s')
  assert.equal(dur(45), '45s')
  assert.equal(dur(125), '2m 5s')
  assert.equal(dur(14880), '4h 8m')
})
