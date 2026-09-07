// Unit tests for the QUEUE payload's pure logic:
//   src/dashboard/web/src/lib/queue.js  (last_error → strip shape + subject line)
//
// Run directly (`node tests/js/queue.test.mjs`) or through
// tests/test_live_feed_js.py, which globs every suite in this directory. No svelte, no DOM: queue.js
// is import-free precisely so these rules are checkable without a browser.
//
// The payload shape is taken from the PRODUCER, not invented:
//   src/app/executor.py::RunExecutor._record_failure →
//     {queue_id, kind, model, config, provider_profile, error, at}
//   served verbatim by src/dashboard/server.py::api_queue_get as `last_error`.

import assert from 'node:assert/strict'
import test from 'node:test'

import { toQueueError, queueErrorSubject } from '../../src/dashboard/web/src/lib/queue.js'

const raw = (over = {}) => ({
  queue_id: 'q_1f0c92aa',
  kind: 'casual',
  model: 'gemma-4-31b(thinking)',
  config: 'config-5.0',
  provider_profile: 'gemma-replay',
  error: "ValueError: google/gemma-4-31b-it: unsupported reasoning effort 'xhigh'",
  at: '2026-09-07T11:02:13',
  ...over,
})

// ── toQueueError ─────────────────────────────────────────────────────────────

test('maps every field the producer writes', () => {
  const err = toQueueError(raw())
  assert.deepEqual(err, {
    queueId: 'q_1f0c92aa',
    kind: 'casual',
    model: 'gemma-4-31b(thinking)',
    config: 'config-5.0',
    providerProfile: 'gemma-replay',
    error: "ValueError: google/gemma-4-31b-it: unsupported reasoning effort 'xhigh'",
    at: '2026-09-07T11:02:13',
  })
})

test('no failure maps to null, not an empty strip', () => {
  // The normal case, and the one that must render NOTHING: `last_error` is null
  // on a healthy queue and the server clears it the moment a run starts.
  assert.equal(toQueueError(null), null)
  assert.equal(toQueueError(undefined), null)
})

test('a pre-profile payload still maps (the two new keys are absent)', () => {
  // Back-compat rung: `config` and `provider_profile` were added to
  // _record_failure alongside the profile axis. An executor that predates them
  // — or a payload replayed from an older app — must still render a strip
  // rather than throwing or showing "undefined".
  const err = toQueueError({
    queue_id: 'q_old', kind: 'casual', model: 'm(high)',
    error: 'boom', at: '2026-09-01T00:00:00',
  })
  assert.equal(err.config, null)
  assert.equal(err.providerProfile, null)
  assert.equal(err.error, 'boom')
})

test('a missing error message becomes empty, never the string "undefined"', () => {
  assert.equal(toQueueError({ at: 'x' }).error, '')
})

test('`at` is carried through — it is the dismissal identity', () => {
  // The strip dismisses on `at`, not on a boolean: the server re-serves the
  // SAME failure on every poll until a run starts, so a boolean would
  // un-dismiss on the next ping while a new failure (new `at`) must re-show.
  assert.equal(toQueueError(raw({ at: 'A' })).at, 'A')
  assert.notEqual(toQueueError(raw({ at: 'A' })).at, toQueueError(raw({ at: 'B' })).at)
})

// ── queueErrorSubject ────────────────────────────────────────────────────────

test('names the model, config, variant and queue id', () => {
  const line = queueErrorSubject(toQueueError(raw()))
  for (const bit of ['gemma-4-31b(thinking)', 'config-5.0', 'gemma-replay', 'q_1f0c92aa']) {
    assert.ok(line.includes(bit), `${bit} missing from ${line}`)
  }
})

test('omits the fields that are absent rather than printing blanks', () => {
  // Exact shape, not a substring check: `[a, null, null, b].join(' · ')`
  // renders the nulls as EMPTY strings, so a naive join produces
  // "a ·  ·  · b" — no literal "null" anywhere, and every substring assertion
  // about the present fields still passes. Only the whole string catches it.
  const line = queueErrorSubject(toQueueError(raw({ provider_profile: null, config: null })))
  assert.equal(line, 'gemma-4-31b(thinking) · q_1f0c92aa')
})

test('an official failure has no config or variant and still reads', () => {
  // Official items carry config=None by construction (the frozen wiring is not
  // on the item), so the subject line must not depend on it.
  const line = queueErrorSubject(
    toQueueError(raw({ kind: 'official', config: null, provider_profile: null })),
  )
  assert.equal(line, 'gemma-4-31b(thinking) · q_1f0c92aa')
})

test('no error means no subject line', () => {
  assert.equal(queueErrorSubject(null), '')
})
