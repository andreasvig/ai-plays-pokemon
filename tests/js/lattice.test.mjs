// Unit tests for src/dashboard/web/src/lib/lattice.js — the fallback that keeps
// a run's map from rendering as nothing.
//
// The defect: RouteMap guarded on `worldLayout(...)` being truthy, and with no
// atlas entry for any map the run entered it was null, so six of the seven
// games opened "Where it walked" onto an empty frame with no message.
//
// The frame derivation here is the JS twin of the one measured against pret's
// FireRed world frame in Python (7 of 7 correct, 0 of 10 candidates a door).
// It is duplicated on purpose: the client has to work on a run whose atlas has
// not been generated yet, which is every run of six games today.
import test from 'node:test'
import assert from 'node:assert/strict'
import { latticeAtlas, sharedFrames, mergeAtlas, isLattice, drawLattice }
  from '../../src/dashboard/web/src/lib/lattice.js'

// visits: [turn, i, a, b, x, y, in_battle]
const v = (a, b, x, y, turn = 1, i = 0) => [turn, i, a, b, x, y, 0]
const routeOf = (visits, extra = {}) => ({ visits, maps: {}, fills: {}, transitions: {}, ...extra })

test('a route with no visits yields no atlas', () => {
  assert.equal(latticeAtlas(routeOf([])), null)
  assert.equal(latticeAtlas(null), null)
})

test('each map gets a box around the ground actually walked', () => {
  const a = latticeAtlas(routeOf([v(3, 0, 10, 4), v(3, 0, 12, 9), v(3, 0, 11, 6)]))
  const m = a.maps['3:0']
  // 3 wide, 6 tall, plus one tile of air on every side.
  assert.equal(m.width, 5)
  assert.equal(m.height, 8)
  assert.deepEqual(m.origin, [9, 3])
  assert.equal(m.synthetic, true)
  assert.equal(isLattice(m), true, 'a synthetic entry has no artwork')
})

test('the atlas carries the game so it cannot be drawn on another cartridge', () => {
  assert.equal(latticeAtlas(routeOf([v(3, 0, 1, 1)], { game: 'crystal-us' })).game, 'crystal-us')
  assert.equal(latticeAtlas(routeOf([v(3, 0, 1, 1)])).game, null)
})

// --- the frame derivation ----------------------------------------------------

test('DS maps whose coordinates are continuous share one frame', () => {
  // Platinum's real numbers: leaving map 342 at (159,850) and arriving on 418
  // at (160,850) is one step east, so the two are measured in the same space.
  const r = routeOf([v(342, 0, 113, 843), v(342, 0, 159, 850),
                     v(418, 0, 160, 850), v(418, 0, 187, 863)])
  const frames = sharedFrames(r)
  assert.equal(frames.get('342:0'), frames.get('418:0'))

  const a = latticeAtlas(r)
  const m342 = a.maps['342:0'], m418 = a.maps['418:0']
  // Both are placed, and their placement is RELATIVE — the offset between the
  // two rectangles must equal the offset between the coordinates themselves,
  // or a route crossing the seam would jump. Asserting `world: [0, 0]` on both
  // (the first version of this test) held while the rectangles were anchored at
  // tile zero, which made Platinum a 203x852 canvas holding a 47x21 walk.
  assert.deepEqual(m342.world, m342.origin)
  assert.deepEqual(m418.world, m418.origin)
  assert.equal(m418.world[0] - m342.world[0], 160 - 113)
  assert.equal(m418.world[1] - m342.world[1], 850 - 843)
})

test('a global coordinate space does not stretch the canvas back to tile zero', () => {
  // The regression this guards: Platinum's y reaches 888, so a rectangle
  // anchored at the origin is nine hundred tiles tall for a twenty-tile walk.
  const a = latticeAtlas(routeOf([v(342, 0, 113, 843), v(418, 0, 114, 843),
                                  v(418, 0, 140, 863)]))
  for (const m of Object.values(a.maps)) {
    assert.ok(m.height < 40, `a map ${m.height} tiles tall for a walk this size is the bug`)
    assert.ok(m.width < 40)
  }
})

test('a gen 1-3 seam is a coordinate DIScontinuity and shares no frame', () => {
  // Pallet Town's north edge (y=0) meets Route 1's south edge (y=39).
  const r = routeOf([v(3, 0, 10, 0), v(3, 19, 10, 39)])
  assert.equal(sharedFrames(r).size, 0)
  const a = latticeAtlas(r)
  assert.equal(a.maps['3:0'].world, undefined, 'an unplaced map must not claim a position')
  assert.equal(a.maps['3:19'].world, undefined)
})

test('a door is not a shared frame even on a DS game', () => {
  // Entering Sandgem's lab: the global outdoor coordinate jumps to a local one.
  const r = routeOf([v(418, 0, 170, 848), v(422, 0, 7, 12)])
  assert.equal(sharedFrames(r).size, 0)
})

test('frames are transitive across a chain of maps', () => {
  const r = routeOf([v(342, 0, 159, 850), v(418, 0, 160, 850),
                     v(418, 0, 187, 840), v(343, 0, 188, 840)])
  const f = sharedFrames(r)
  assert.equal(f.get('342:0'), f.get('343:0'), 'A-B and B-C must put A and C together')
})

// --- merging -----------------------------------------------------------------

test('real artwork wins per map, and unrendered maps keep their grid', () => {
  const synthetic = latticeAtlas(routeOf([v(418, 0, 160, 840), v(422, 0, 7, 12)]))
  const real = { schema: 2, game: 'platinum-us', maps: { '418:0': { width: 32, height: 32, file: '418-0.png' } } }
  const merged = mergeAtlas(real, synthetic)
  assert.equal(merged.maps['418:0'].file, '418-0.png')
  assert.equal(merged.maps['418:0'].width, 32, 'the real size must survive the merge')
  assert.equal(isLattice(merged.maps['418:0']), false)
  assert.equal(isLattice(merged.maps['422:0']), true, 'an unrendered map must not vanish')
  assert.equal(merged.maps['422:0'].synthetic, true)
})

test('merging degrades gracefully at both ends', () => {
  const synthetic = latticeAtlas(routeOf([v(3, 0, 1, 1)]))
  assert.equal(mergeAtlas(null, synthetic), synthetic)
  assert.equal(mergeAtlas(undefined, synthetic), synthetic)
  const real = { maps: { '3:0': {} } }
  assert.equal(mergeAtlas(real, null), real)
})

// --- drawing -----------------------------------------------------------------

function recorder() {
  const calls = []
  const rec = (name) => (...args) => calls.push([name, ...args])
  return {
    calls,
    save: rec('save'), restore: rec('restore'), beginPath: rec('beginPath'),
    moveTo: rec('moveTo'), lineTo: rec('lineTo'), stroke: rec('stroke'),
    fillRect: rec('fillRect'), strokeRect: rec('strokeRect'),
    drawImage: rec('drawImage'),
    set fillStyle(_) {}, set strokeStyle(_) {}, set lineWidth(_) {},
  }
}

test('the lattice draws a grid and never an image', () => {
  const c = recorder()
  drawLattice(c, { width: 4, height: 3 }, 0, 0, 16)
  const names = c.calls.map((x) => x[0])
  assert.ok(names.includes('strokeRect'), 'the map outline is what makes its extent readable')
  assert.equal(names.includes('drawImage'), false, 'a lattice has no artwork to draw')
  // 5 vertical lines for 4 tiles, 4 horizontal for 3.
  assert.equal(c.calls.filter((x) => x[0] === 'moveTo').length, 5 + 4)
})

test('the lattice honours a tile size other than 16', () => {
  const c = recorder()
  drawLattice(c, { width: 2, height: 2 }, 0, 0, 24)
  const rect = c.calls.find((x) => x[0] === 'fillRect')
  assert.deepEqual(rect.slice(1), [0, 0, 48, 48], 'a DS render need not be 16 px a tile')
})
