// Unit tests for the map atlas helpers:
//   src/dashboard/web/src/lib/mapatlas.js  (layout, markers, route drawing)
//
// Run directly (`node tests/js/mapatlas.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory. No svelte, no DOM —
// `drawRoute` is handed a recording stub in place of a canvas context.
import test from 'node:test'
import assert from 'node:assert/strict'
import { TILE, worldLayout, markersFor, buildingLabel, drawRoute, drawSize, drawWindow, laneSteps, turnColour, visitTimes, visitAt } from '../../src/dashboard/web/src/lib/mapatlas.js'

// Pallet Town at the world origin, Route 1 above it, Viridian Forest with no
// place in the frame, and the player's two floors, which are never laid out.
const ATLAS = {
  maps: {
    '3:0': { name: 'PalletTown', width: 24, height: 20, world: [0, 0], type: 'MAP_TYPE_TOWN', file: '3-0.png',
             doors: [{ x: 6, y: 7, to: '4:0', building: 'PalletTown_PlayersHouse' },
                     { x: 15, y: 7, to: '4:2', building: 'PalletTown_RivalsHouse' }] },
    '3:19': { name: 'Route1', width: 24, height: 40, world: [0, -40], type: 'MAP_TYPE_ROUTE', file: '3-19.png' },
    '1:0': { name: 'ViridianForest', width: 54, height: 69, type: 'MAP_TYPE_ROUTE', file: '1-0.png' },
    '4:0': { name: 'PalletTown_PlayersHouse_1F', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', file: '4-0.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'] },
    '4:1': { name: 'PalletTown_PlayersHouse_2F', width: 12, height: 9, type: 'MAP_TYPE_INDOOR', file: '4-1.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'] },
    '4:2': { name: 'PalletTown_RivalsHouse', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', file: '4-2.png',
             building: 'PalletTown_RivalsHouse', floors: ['4:2'] },
  },
}
const route = (maps, visits = [], extra = {}) => ({
  maps: Object.fromEntries(maps.map((k) => [k, {}])),
  visits, fills: {}, transitions: {}, ...extra,
})

test('the world frame places outdoor maps by their walk-graph position', () => {
  const L = worldLayout(route(['3:0', '3:19']), ATLAS)
  // Route 1 sits 40 tiles above Pallet Town, so it is 40 tiles higher here too.
  assert.equal(L.at['3:0'].y - L.at['3:19'].y, 40)
  assert.equal(L.at['3:0'].x, L.at['3:19'].x)
  assert.equal(L.outdoor, 2)
  assert.equal(L.insets, 0)
})

test('an interior is not laid out — it belongs to its building, not the world', () => {
  const L = worldLayout(route(['3:0', '4:0', '4:1']), ATLAS)
  assert.equal(L.at['4:0'], undefined)
  assert.deepEqual(L.interiors.sort(), ['4:0', '4:1'])
})

test('a visited map with no world position is drawn beside the world, not on it', () => {
  const L = worldLayout(route(['3:0', '1:0']), ATLAS)
  assert.equal(L.insets, 1)
  assert.ok(L.at['1:0'].inset)
  // beyond the right edge of Pallet Town, never overlapping it
  assert.ok(L.at['1:0'].x >= L.at['3:0'].x + 24)
})

test('a run that entered no mapped map lays out nothing', () => {
  assert.equal(worldLayout(route([]), ATLAS), null)
  assert.equal(worldLayout(null, ATLAS), null)
  assert.equal(worldLayout(route(['3:0']), null), null)
})

test('every door on a drawn map gets a marker, entered or not', () => {
  const L = worldLayout(route(['3:0', '4:0']), ATLAS)
  const ms = markersFor(L, route(['3:0', '4:0']), ATLAS)
  assert.equal(ms.length, 2)
  const players = ms.find((m) => m.building === 'PalletTown_PlayersHouse')
  const rivals = ms.find((m) => m.building === 'PalletTown_RivalsHouse')
  assert.equal(players.entered, true)
  assert.equal(rivals.entered, false, 'a house the run never opened still shows, faintly')
  assert.deepEqual(players.floors, ['4:0', '4:1'])
  // the marker sits on the door tile of the map it belongs to
  assert.deepEqual(players.tile, { x: L.at['3:0'].x + 6, y: L.at['3:0'].y + 7 })
})

test('entering only an upper floor still counts the building as entered', () => {
  const r = route(['3:0', '4:1'])
  const ms = markersFor(worldLayout(r, ATLAS), r, ATLAS)
  assert.equal(ms.find((m) => m.building === 'PalletTown_PlayersHouse').entered, true)
})

test('building labels read as English, not as pret identifiers', () => {
  assert.equal(buildingLabel('PewterCity_PokemonCenter'), 'Pokémon Center')
  assert.equal(buildingLabel('PalletTown_ProfessorOaksLab'), "Professor Oak's Lab")
  assert.equal(buildingLabel('PewterCity_House1'), 'House 1')
})

// -- drawing -----------------------------------------------------------------

function stub() {
  const calls = { lines: [], arcs: [], rects: [] }
  let from = null
  return {
    calls,
    set lineCap(_v) {}, set lineJoin(_v) {}, set strokeStyle(_v) {}, set lineWidth(_v) {},
    beginPath() {},
    moveTo(x, y) { from = [x, y] },
    lineTo(x, y) { calls.lines.push([from, [x, y]]) },
    arc(x, y) { calls.arcs.push([x, y]) },
    stroke() {}, fill() {},
    strokeRect(x, y, w, h) { calls.rects.push([x, y, w, h]) },
  }
}
const placeAll = (g, m, x, y) => [(x + 0.5) * TILE, (y + 0.5) * TILE]
const visit = (turn, g, m, x, y, battle = 0) => [turn, 0, g, m, x, y, battle]

test('adjacent tiles are drawn as a line, each with a dark halo under it', () => {
  const c = stub()
  drawRoute(c, route(['3:0'], [visit(1, 3, 0, 5, 5), visit(2, 3, 0, 6, 5)]), placeAll)
  // one segment, drawn twice: the halo and the coloured line over it
  assert.equal(c.calls.lines.length, 2)
  assert.deepEqual(c.calls.lines[0], [[5.5 * TILE, 5.5 * TILE], [6.5 * TILE, 5.5 * TILE]])
  assert.equal(c.calls.arcs.length, 0)
})

test('a warp is drawn as two rings, never as a line across the map', () => {
  const c = stub()
  drawRoute(c, route(['3:0', '4:0'], [visit(1, 3, 0, 6, 7), visit(2, 4, 0, 4, 8)]), placeAll)
  assert.equal(c.calls.lines.length, 0, 'no chord between two distant tiles')
  assert.equal(c.calls.arcs.length, 4, 'both ends ringed, halo and colour')
})

test('a segment on a map this canvas does not draw is skipped, not misplaced', () => {
  const c = stub()
  const only = (g, m, x, y) => (g === 3 ? [(x + 0.5) * TILE, (y + 0.5) * TILE] : null)
  drawRoute(c, route(['3:0', '4:0'], [visit(1, 3, 0, 5, 5), visit(2, 4, 0, 4, 8), visit(3, 4, 0, 4, 7)]), only)
  assert.equal(c.calls.lines.length, 0)
  assert.equal(c.calls.arcs.length, 0)
  // start and end markers: only the start is on a map we draw
  assert.equal(c.calls.rects.length, 2, 'the start box, halo and colour; the end is off-canvas')
})

test('the hover readout finds the visit under the cursor and nothing beyond a tile', () => {
  const r = route(['3:0'], [visit(7, 3, 0, 5, 5), visit(9, 3, 0, 9, 9)])
  assert.equal(visitAt(r, placeAll, 5.5 * TILE, 5.5 * TILE, TILE)[0], 7)
  assert.equal(visitAt(r, placeAll, 9.5 * TILE, 9.5 * TILE, TILE)[0], 9)
  assert.equal(visitAt(r, placeAll, 20 * TILE, 20 * TILE, TILE), null)
})

// -- direction arrows ---------------------------------------------------------
// Andreas, 2026-09-15: "would love for the routes to have arrows such that it
// would be easier to see at overlaps and which way it is going."

/** A straight walk of `n` tiles east from (0,0), one visit per tile. */
const walkEast = (n) => route(['3:0'], Array.from({ length: n }, (_, i) => visit(i + 1, 3, 0, i, 0)))

function arrows(c) {
  // an arrowhead is the only thing drawRoute fills: three points, closed
  return c.calls.tris
}

function stubWithTris() {
  const c = stub()
  c.calls.tris = []
  let pending = []
  const orig = { moveTo: c.moveTo, lineTo: c.lineTo }
  c.beginPath = () => { pending = [] }
  c.moveTo = (x, y) => { pending = [[x, y]] }
  c.lineTo = (x, y) => { pending.push([x, y]) }
  c.closePath = () => {}
  c.fill = () => { if (pending.length === 3) c.calls.tris.push(pending.slice()) }
  c.stroke = () => {
    // a two-point path that was drawn, not filled, is a route segment
    if (pending.length === 2) c.calls.lines.push([pending[0], pending[1]])
  }
  return c
}

test('a long walk carries arrowheads, spaced along the path', () => {
  const c = stubWithTris()
  drawRoute(c, walkEast(40), placeAll, { arrowEvery: TILE * 5 })
  // 39 tiles of travel, one arrow every 5 tiles, the first half a gap in
  assert.ok(arrows(c).length >= 6 && arrows(c).length <= 9, `got ${arrows(c).length}`)
})

test('an arrowhead points the way the run went', () => {
  const c = stubWithTris()
  drawRoute(c, walkEast(40), placeAll, { arrowEvery: TILE * 5 })
  for (const [tip, a, b] of arrows(c)) {
    // walking east: the tip is the rightmost of the three points
    assert.ok(tip[0] > a[0] && tip[0] > b[0], 'the tip leads')
    assert.ok(Math.abs(a[1] - b[1]) > 0, 'the base spans across the line')
  }
})

test('a short hop is not decorated', () => {
  const c = stubWithTris()
  drawRoute(c, walkEast(3), placeAll, { arrowEvery: TILE * 5 })
  assert.equal(arrows(c).length, 0)
})

test('spacing is measured along the path, so a route that doubles back marks both ways', () => {
  // out 10 east, then back 10 west over the same tiles
  const out = Array.from({ length: 11 }, (_, i) => visit(i + 1, 3, 0, i, 0))
  const back = Array.from({ length: 10 }, (_, i) => visit(12 + i, 3, 0, 9 - i, 0))
  const c = stubWithTris()
  drawRoute(c, route(['3:0'], [...out, ...back]), placeAll, { arrowEvery: TILE * 5 })
  const dirs = new Set(arrows(c).map(([tip, a]) => Math.sign(tip[0] - a[0])))
  assert.deepEqual([...dirs].sort(), [-1, 1], 'arrows point both ways over the same ground')
})


// -- trimmed edges ------------------------------------------------------------
// Every FireRed interior ends in a flat strip nothing can stand on — drawn, it
// reads as an empty progress bar under the floor.

test('the drawn window leaves out the trimmed edges', () => {
  const m = { width: 13, height: 10, trim: { left: 1, bottom: 1 } }
  assert.deepEqual(drawWindow(m), { x: 1, y: 0, w: 12, h: 9 })
  assert.deepEqual(drawWindow({ width: 13, height: 10 }), { x: 0, y: 0, w: 13, h: 10 })
})

test('drawSize only ever cuts TRAILING edges, so tile coordinates cannot move', () => {
  // The world frame lines outdoor maps up with their neighbours; a caller that
  // cannot shift tiles gets the safe subset.
  assert.deepEqual(drawSize({ width: 13, height: 10, trim: { left: 1, top: 2, bottom: 1 } }), [13, 9])
})

test('a map is never trimmed away entirely', () => {
  assert.deepEqual(drawWindow({ width: 2, height: 2, trim: { left: 5, bottom: 5 } }), { x: 5, y: 0, w: 1, h: 1 })
})


// -- the colour ramp ----------------------------------------------------------
// Andreas, 2026-09-15: "can we render the colours as fully gradual changes?"

const rgb = (s) => s.match(/\d+/g).map(Number)

test('the ramp never jumps: neighbouring values are neighbouring colours', () => {
  let prev = rgb(turnColour(0))
  for (let k = 0.005; k <= 1; k += 0.005) {
    const c = rgb(turnColour(k))
    const step = Math.max(...c.map((v, i) => Math.abs(v - prev[i])))
    assert.ok(step <= 6, `a jump of ${step} at ${k.toFixed(3)}`)
    prev = c
  }
})

test('the ramp runs blue to red and is clamped outside 0..1', () => {
  const [r0, , b0] = rgb(turnColour(0))
  const [r1, , b1] = rgb(turnColour(1))
  assert.ok(b0 > r0, 'it starts blue')
  assert.ok(r1 > b1, 'it ends red')
  assert.equal(turnColour(-3), turnColour(0))
  assert.equal(turnColour(9), turnColour(1))
  assert.equal(turnColour(undefined), turnColour(0))
})

test('time advances WITHIN a turn, so one turn is not one flat band', () => {
  // four tiles walked on turn 1, then one on turn 3: colouring by turn number
  // alone paints the first four identically and steps at the join.
  const visits = [visit(1, 3, 0, 0, 0), visit(1, 3, 0, 1, 0), visit(1, 3, 0, 2, 0),
                  visit(1, 3, 0, 3, 0), visit(3, 3, 0, 4, 0)]
  const t = visitTimes(visits)
  assert.equal(t.length, 5)
  assert.ok(t[0] < t[1] && t[1] < t[2] && t[2] < t[3], 'it moves inside turn 1')
  assert.ok(t[3] < t[4], 'and across the turn boundary')
  assert.equal(t[0], 0)
  assert.ok(t[4] <= 1)
})

test('a run that never left one turn still gets a defined time', () => {
  const t = visitTimes([visit(5, 3, 0, 0, 0), visit(5, 3, 0, 1, 0)])
  assert.ok(t.every((v) => Number.isFinite(v) && v >= 0 && v <= 1))
  assert.deepEqual(visitTimes([]), [])
})


// -- cables on the floor ------------------------------------------------------
// Andreas, 2026-09-15: "is there a way we could design a system such that lines
// [are] very small and thin so that they can be rendered as more than one on the
// tile? I imagine it would look like 1,2,3,4 small cables on the floor."

const there = (n) => Array.from({ length: n }, (_, i) => visit(i + 1, 3, 0, i, 0))
const back = (n, from) => Array.from({ length: n }, (_, i) => visit(from + i, 3, 0, n - 1 - i, 0))

test('a step walked once has a single lane', () => {
  const steps = laneSteps(route(['3:0'], there(4)))
  assert.equal(steps.length, 3)
  assert.ok(steps.every((s) => s.lanes === 1 && s.lane === 0))
})

test('three passes over one corridor become three lanes', () => {
  const r = route(['3:0'], [...there(5), ...back(5, 6), ...there(5).map((v, i) => visit(12 + i, 3, 0, i, 0))])
  const onFirst = laneSteps(r).filter((s) => s.u[2] + s.v[2] === 1)   // the 0↔1 step
  assert.equal(onFirst.length, 3)
  assert.deepEqual(onFirst.map((s) => s.lane), [0, 1, 2])
  assert.ok(onFirst.every((s) => s.lanes === 3))
})

test('a lane keeps the same side of the tile whichever way it was walked', () => {
  // there and back over the same step: the two traversals face opposite ways,
  // so the perpendicular has to be taken from the edge, not from the travel —
  // otherwise the pair swaps sides halfway and the cables cross.
  const r = route(['3:0'], [...there(3), ...back(3, 4)])
  const pair = laneSteps(r).filter((s) => s.u[2] + s.v[2] === 1)
  assert.equal(pair.length, 2)
  assert.notEqual(pair[0].flip, pair[1].flip, 'they were walked in opposite directions')
  // with flip applied, the offsets differ in lane index, not in the side the
  // maths starts from: lane 0 is one side, lane 1 the other, for both.
  assert.deepEqual(pair.map((s) => s.lane), [0, 1])
})

test('lanes stop multiplying past the cap', () => {
  const visits = []
  for (let p = 0; p < 9; p++) visits.push(...(p % 2 ? back(4, 1 + p * 4) : there(4).map((v, i) => visit(1 + p * 4 + i, 3, 0, i, 0))))
  const steps = laneSteps(route(['3:0'], visits), { maxLanes: 4 })
  const onFirst = steps.filter((s) => s.u[2] + s.v[2] === 1)
  assert.ok(onFirst.length > 4)
  assert.ok(onFirst.every((s) => s.lanes === 4 && s.lane <= 3))
})

test('two passes are actually drawn apart, not on top of each other', () => {
  const c = stubWithTris()
  const r = route(['3:0'], [...there(4), ...back(4, 5)])
  drawRoute(c, r, placeAll, { arrowEvery: 1e6 })
  // every drawn line for the 0↔1 step, ignoring the halo that shares its path
  const ys = new Set(c.calls.lines
    .filter(([a, b]) => Math.min(a[0], b[0]) < TILE && Math.max(a[0], b[0]) > TILE)
    .map(([a]) => Math.round(a[1])))
  assert.ok(ys.size >= 2, `both passes share one y: ${[...ys]}`)
})
