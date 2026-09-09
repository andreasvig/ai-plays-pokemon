// Unit tests for the published site's data adapter:
//   src/dashboard/web/src/lib/static.js  (/api/* GET → gh-pages JSON files)
//
// Run directly (`node tests/js/static.test.mjs`) or through tests/test_live_feed_js.py.
// Two things are pinned here: that `rankBoard` ranks exactly as
// src/app/derivations.py::leaderboard (official + terminal + config-5.x, best per
// model by gates then fewest turns), and that every path api.js requests maps to
// the file `pokemon publish` writes — the contract between the two halves.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rankBoard, eligible, staticGet, _setFetch, BASE, STATIC } from '../../src/dashboard/web/src/lib/static.js'

const row = (o) => ({ kind: 'official', status: 'completed', config_stem: 'config-5.1', benchmark: 'pokebench-first-badge',
  model: 'm', gates_reached: 0, turns: 10, ...o })

test('outside vite: static off, base is /', () => {
  assert.equal(STATIC, false)
  assert.equal(BASE, '/')
})

test('eligible = official + completed/terminated + config-5.x, like RunSummary.leaderboard_eligible', () => {
  assert.equal(eligible(row({})), true)
  assert.equal(eligible(row({ status: 'terminated' })), true)
  assert.equal(eligible(row({ kind: 'casual' })), false)
  assert.equal(eligible(row({ status: 'cancelled' })), false)
  assert.equal(eligible(row({ status: 'crashed' })), false)
  assert.equal(eligible(row({ config_stem: 'config-4.0' })), false)
  assert.equal(eligible(row({ config_stem: null })), false)
})

test('rankBoard: best per model (gates desc, turns asc), sorted the same way, scoped by benchmark', () => {
  const rows = [
    row({ model: 'a', run_id: 'a1', gates_reached: 2, turns: 50 }),
    row({ model: 'a', run_id: 'a2', gates_reached: 3, turns: 90 }),   // more gates wins over fewer turns
    row({ model: 'b', run_id: 'b1', gates_reached: 3, turns: 40 }),
    row({ model: 'b', run_id: 'b2', gates_reached: 3, turns: 60 }),   // same gates → fewer turns wins
    row({ model: 'c', run_id: 'c1', gates_reached: 5, turns: 10, kind: 'casual' }),  // never ranked
    row({ model: 'd', run_id: 'd1', gates_reached: 9, turns: 10, benchmark: 'pokebench-full' }),
  ]
  assert.deepEqual(rankBoard(rows, 'pokebench-first-badge').map((r) => r.run_id), ['b1', 'a2'])
  // between-gate progress: same gates + same turns (both died on the same deadline) → the leg fraction decides;
  // a row without `progress` ranks on its gate count
  const tied = [
    row({ model: 'p', run_id: 'p1', gates_reached: 3, turns: 100, progress: 3.2 }),
    row({ model: 'q', run_id: 'q1', gates_reached: 3, turns: 100, progress: 3.6 }),
    row({ model: 'r', run_id: 'r1', gates_reached: 3, turns: 100 }),
    row({ model: 'q', run_id: 'q0', gates_reached: 3, turns: 100, progress: 3.1 }),   // q's worse run
  ]
  assert.deepEqual(rankBoard(tied).map((r) => r.run_id), ['q1', 'p1', 'r1'])
  assert.deepEqual(rankBoard(rows).map((r) => r.run_id), ['d1', 'b1', 'a2'])
  assert.deepEqual(rankBoard([]), [])
})

test('staticGet maps every api.js read onto the published files', async () => {
  const board = [row({ run_id: 'r1', model: 'x', gates_reached: 1, trace_published: true }),
                 row({ run_id: 'r2', model: 'y', kind: 'casual', trace_published: false })]
  const files = {
    '/data/leaderboard.json': board,
    '/data/benchmarks.json': [{ id: 'pokebench-first-badge' }],
    '/data/runs/r1/summary.json': { run_id: 'r1', session: {} },
    '/data/runs/r1/trace.json': { run_id: 'r1', tasks: [] },
  }
  const asked = []
  _setFetch(async (url, opts) => {
    asked.push(url)
    assert.equal(opts.cache, 'no-cache')
    if (!(url in files)) return { ok: false, status: 404 }
    return { ok: true, json: async () => files[url] }
  })
  assert.deepEqual(await staticGet('/api/leaderboard?benchmark=pokebench-first-badge'), [board[0]])
  assert.deepEqual(await staticGet('/api/runs'), board)
  assert.deepEqual(await staticGet('/api/runs/r2'), board[1])
  assert.deepEqual(await staticGet('/api/runs/r1/summary'), files['/data/runs/r1/summary.json'])
  assert.deepEqual(await staticGet('/api/runs/r1/trace'), files['/data/runs/r1/trace.json'])
  // result-and-video-only row: the trace is null WITHOUT a request (no 404 in the network log)
  const before = asked.length
  assert.equal(await staticGet('/api/runs/r2/trace'), null)
  assert.equal(asked.length, before, 'no fetch for an unpublished trace')
  assert.deepEqual(await staticGet('/api/benchmarks'), [{ id: 'pokebench-first-badge' }])
  assert.equal(asked.filter((u) => u === '/data/leaderboard.json').length, 1, 'the board is fetched once')
  // the control-center reads answer empty, never throw
  assert.deepEqual(await staticGet('/api/models'), [])
  assert.deepEqual((await staticGet('/api/queue')).items, [])
  assert.equal((await staticGet('/api/emulator/status')).configured, false)
  assert.deepEqual((await staticGet('/api/profiles')).profiles, [])
  await assert.rejects(staticGet('/api/runs/nope'), /404/)
  await assert.rejects(staticGet('/api/runs/r1/screenshots/x.png'), /not available/)
})

test('a missing leaderboard file is an empty board, not a broken page', async () => {
  _setFetch(async () => ({ ok: false, status: 404 }))
  assert.deepEqual(await staticGet('/api/leaderboard'), [])
  assert.deepEqual(await staticGet('/api/benchmarks'), [])
})
