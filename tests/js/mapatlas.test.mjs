// Unit tests for the map atlas helpers:
//   src/dashboard/web/src/lib/mapatlas.js  (layout, markers, route drawing)
//
// Run directly (`node tests/js/mapatlas.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory. No svelte, no DOM —
// `drawRoute` is handed a recording stub in place of a canvas context.
import test from 'node:test'
import assert from 'node:assert/strict'
import { TILE, CABLE_GAP, MAX_LANES, COLOUR_LOOP_TILES, ARROW_EVERY_TILES, ARROW_SPEED_TILES, worldLayout, clusterLayout, exitsFor, floorsFor, markersFor, battlesFor, buildingLabel, drawRoute, drawArrows, routePolylines, solveLanes, drawSize, drawWindow, laneSteps, cableColour, wheelColour, visitTimes, visitAt } from '../../src/dashboard/web/src/lib/mapatlas.js'

// Pallet Town at the world origin, Route 1 above it, the Viridian Forest
// COMPLEX (its two gates and the forest itself, all `popup`), the player's two
// floors, and one made-up cave.
//
// The fixture speaks the producer's contract: `scripts/render_gamemaps.py`
// flags every map drawn in a popup rather than on the world, and since
// 2026-09-16 that includes Viridian Forest — a ROUTE — so the flag, not the map
// type, is what the world frame reads. `9:9` is invented: after the forest
// moved indoors NO map in the first-badge region is an inset any more, and the
// fallback for a map with no world position still has to work or a run that
// walks into one loses that ground off the map entirely.
const ATLAS = {
  maps: {
    '3:0': { name: 'PalletTown', width: 24, height: 20, world: [0, 0], type: 'MAP_TYPE_TOWN', file: '3-0.png',
             doors: [{ x: 6, y: 7, to: '4:0', building: 'PalletTown_PlayersHouse' },
                     { x: 15, y: 7, to: '4:2', building: 'PalletTown_RivalsHouse' }] },
    '3:19': { name: 'Route1', width: 24, height: 40, world: [0, -40], type: 'MAP_TYPE_ROUTE', file: '3-19.png' },
    '3:20': { name: 'Route2', width: 24, height: 80, world: [0, -160], type: 'MAP_TYPE_ROUTE', file: '3-20.png',
              doors: [{ x: 5, y: 13, to: '15:3', building: 'ViridianForest' },
                      { x: 5, y: 51, to: '15:0', building: 'ViridianForest' }] },
    '1:0': { name: 'ViridianForest', width: 54, height: 69, type: 'MAP_TYPE_ROUTE', file: '1-0.png',
             building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'], popup: true, complex: true },
    '15:3': { name: 'Route2_ViridianForest_NorthEntrance', width: 15, height: 12, type: 'MAP_TYPE_INDOOR', file: '15-3.png',
              building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'], popup: true, complex: true,
              trim: { left: 1, right: 1, bottom: 1 }, exits: [{ x: 7, y: 1, to: '3:20' }] },
    '15:0': { name: 'Route2_ViridianForest_SouthEntrance', width: 15, height: 12, type: 'MAP_TYPE_INDOOR', file: '15-0.png',
              building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'], popup: true, complex: true,
              trim: { left: 1, right: 1, bottom: 1 }, exits: [{ x: 7, y: 10, to: '3:20' }] },
    '4:0': { name: 'PalletTown_PlayersHouse_1F', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', file: '4-0.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'], popup: true,
             trim: { left: 1 }, exits: [{ x: 4, y: 8, to: '3:0' }] },
    '4:1': { name: 'PalletTown_PlayersHouse_2F', width: 12, height: 9, type: 'MAP_TYPE_INDOOR', file: '4-1.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'], popup: true, trim: { left: 1 } },
    '4:2': { name: 'PalletTown_RivalsHouse', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', file: '4-2.png',
             building: 'PalletTown_RivalsHouse', floors: ['4:2'], popup: true },
    '9:9': { name: 'MadeUpCave', width: 20, height: 18, type: 'MAP_TYPE_UNDERGROUND', file: '9-9.png' },
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

test('a map the atlas flags `popup` is not laid out — it belongs to its building', () => {
  const L = worldLayout(route(['3:0', '4:0', '4:1']), ATLAS)
  assert.equal(L.at['4:0'], undefined)
  assert.deepEqual(L.interiors.sort(), ['4:0', '4:1'])
})

test('Viridian Forest is a ROUTE and still stays off the world frame', () => {
  // Andreas, 2026-09-16: "i would actually like viridian forest to be a
  // separate room". The world frame reads `popup`, not the map type — keyed on
  // the type, a 54x69 route would be laid out beside the world exactly as it
  // was before, and the popup would open on a map already drawn behind it.
  const L = worldLayout(route(['3:20', '1:0', '15:0', '15:3']), ATLAS)
  assert.equal(L.at['1:0'], undefined, 'the forest itself')
  assert.equal(L.at['15:0'], undefined)
  assert.equal(L.at['15:3'], undefined)
  assert.equal(L.insets, 0, 'and not as an inset either')
  assert.deepEqual(L.interiors.sort(), ['15:0', '15:3', '1:0'])
  assert.equal(ATLAS.maps['1:0'].type, 'MAP_TYPE_ROUTE')
})

test('both gate openings carry a marker, and both open the same three floors', () => {
  const L = worldLayout(route(['3:20', '1:0']), ATLAS)
  const ms = markersFor(L, route(['3:20', '1:0']), ATLAS).filter((m) => m.building === 'ViridianForest')
  assert.equal(ms.length, 2, 'one per gate, not one per building')
  for (const m of ms) {
    assert.deepEqual(m.floors, ['15:3', '1:0', '15:0'], 'north, forest, south')
    assert.equal(m.entered, true, 'the run was in the forest')
  }
  assert.notDeepEqual(ms[0].tile, ms[1].tile, 'two gates a long way apart')
})

test('a visited map with no world position is drawn beside the world, not on it', () => {
  // No real map needs this today — every map in the first-badge region is
  // either placed by the walk graph or drawn in a popup. It is the fallback
  // for a map that is neither, and without it that ground would vanish from
  // the map rather than be drawn somewhere honest.
  const L = worldLayout(route(['3:0', '9:9']), ATLAS)
  assert.equal(L.insets, 1)
  assert.ok(L.at['9:9'].inset)
  // beyond the right edge of Pallet Town, never overlapping it
  assert.ok(L.at['9:9'].x >= L.at['3:0'].x + 24)
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
// would be easier to see at overlaps and which way it is going" — and, once
// they existed, "maybe make them animated where they follow along the path
// instead of being static … [with] noise in the start and times of the arrows,
// to not create faulty illusions and overlaps which keep repeating".
//
// The heads moved out of `drawRoute` into `drawArrows`, which is called once a
// frame over a baked still layer. The claims below are the ones the static
// version made, re-pointed at the new call — plus what marching added.

/** A straight walk of `n` tiles east from (0,0), one visit per tile. */
const walkEast = (n) => route(['3:0'], Array.from({ length: n }, (_, i) => visit(i + 1, 3, 0, i, 0)))

/** Every chevron drawn: a three-point open path, stroked twice. */
function chevrons(c) {
  return c.calls.chevrons
}

function stubWithTris() {
  const c = stub()
  c.calls.chevrons = []
  c.calls.tris = c.calls.chevrons
  let pending = []
  c.beginPath = () => { pending = [] }
  c.moveTo = (x, y) => { pending = [[x, y]] }
  c.lineTo = (x, y) => { pending.push([x, y]) }
  c.closePath = () => {}
  c.fill = () => {}
  c.stroke = () => {
    if (pending.length === 2) c.calls.lines.push([pending[0], pending[1]])
    // a chevron is stroked twice, dark then coloured — count it once
    if (pending.length === 3) {
      const key = JSON.stringify(pending)
      if (c.calls.chevrons.at(-1)?.key !== key) c.calls.chevrons.push(Object.assign(pending.slice(), { key }))
      else c.calls.chevrons.at(-1).key = null
    }
  }
  return c
}

/** The cables of a route, as `drawArrows` wants them. */
const cablesOf = (r, opts = {}) => routePolylines(r, placeAll, opts).lines

test('a long walk carries chevrons, spaced along the path', () => {
  const c = stubWithTris()
  drawArrows(c, cablesOf(walkEast(40)), { every: 5, startNoise: 0 })
  // 39 tiles of cable, one chevron every 5 tiles
  assert.ok(chevrons(c).length >= 7 && chevrons(c).length <= 9, `got ${chevrons(c).length}`)
})

test('a chevron points the way the run went', () => {
  const c = stubWithTris()
  drawArrows(c, cablesOf(walkEast(40)), { every: 5, startNoise: 0 })
  assert.ok(chevrons(c).length > 0)
  for (const [a, tip, b] of chevrons(c)) {
    // walking east: the tip leads and the two tails trail behind it
    assert.ok(tip[0] > a[0] && tip[0] > b[0], 'the tip leads')
    assert.ok(Math.abs(a[1] - b[1]) > 0, 'the tails span across the cable')
  }
})

test('a hop shorter than the spacing carries none at rest', () => {
  const c = stubWithTris()
  drawArrows(c, cablesOf(walkEast(3)), { every: 5, startNoise: 0 })
  assert.equal(chevrons(c).length, 0)
})

test('but the march carries one THROUGH a short hop, which is why they move', () => {
  // The same two-tile cable, sampled across one full period. A static arrow
  // every 12 tiles would leave every short cable in a town permanently
  // undirected; a moving one passes through each in turn.
  const cables = cablesOf(walkEast(3))
  const period = (5 * TILE) / (ARROW_SPEED_TILES * TILE)      // seconds per gap
  let seen = 0
  for (let k = 0; k < 20; k++) {
    const c = stubWithTris()
    drawArrows(c, cables, { every: 5, startNoise: 0, now: (period * k) / 20 })
    seen += chevrons(c).length
  }
  assert.ok(seen > 0, 'a chevron passes through the short cable at some point')
})

test('spacing is measured along the path, so a route that doubles back marks both ways', () => {
  // out 10 east, then back 10 west over the same tiles
  const out = Array.from({ length: 11 }, (_, i) => visit(i + 1, 3, 0, i, 0))
  const back = Array.from({ length: 10 }, (_, i) => visit(12 + i, 3, 0, 9 - i, 0))
  const c = stubWithTris()
  drawArrows(c, cablesOf(route(['3:0'], [...out, ...back])), { every: 5, startNoise: 0 })
  const dirs = new Set(chevrons(c).map(([a, tip]) => Math.sign(tip[0] - a[0])))
  assert.deepEqual([...dirs].sort(), [-1, 1], 'chevrons point both ways over the same ground')
})

test('start noise puts one cable out of phase with the next', () => {
  // TWO cables over the same ground: a walk, a warp (which breaks the cable),
  // then the same walk again. `placeAll` ignores the map, so the two land on
  // top of each other — the worst case the noise exists for.
  const one = Array.from({ length: 10 }, (_, i) => visit(i + 1, 3, 0, i, 0))
  const two = Array.from({ length: 10 }, (_, i) => visit(20 + i, 4, 0, i, 0))
  const cables = cablesOf(route(['3:0', '4:0'], [...one, ...two]))
  assert.equal(cables.length, 2, 'the warp really did split them')
  const xs = (startNoise) => {
    const c = stubWithTris()
    drawArrows(c, cables, { every: 3, startNoise })
    return new Set(chevrons(c).map(([, tip]) => Math.round(tip[0])))
  }
  const lockstep = xs(0), noised = xs(1)
  assert.equal(lockstep.size, chevrons_count(cables, 3) / 2,
    'in lockstep the second cable lands exactly on the first')
  assert.ok(noised.size > lockstep.size,
    `noise separated them (${lockstep.size} distinct x in lockstep, ${noised.size} noised)`)
})

/** How many chevrons two identical cables carry in total, for the check above. */
function chevrons_count(cables, every) {
  const c = stubWithTris()
  drawArrows(c, cables, { every, startNoise: 0 })
  return chevrons(c).length
}

test('the same frame is drawn the same way twice — the noise is stable, not jitter', () => {
  const cables = cablesOf(walkEast(40))
  const snap = () => {
    const c = stubWithTris()
    drawArrows(c, cables, { every: 4, now: 1.25 })
    return JSON.stringify(chevrons(c).map((p) => p.map((q) => q.map(Math.round))))
  }
  assert.equal(snap(), snap())
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


// -- the colour wheel --------------------------------------------------------
// Andreas, 2026-09-15: "can we render the colours as fully gradual changes?",
// then "make the colour loop shorter, also as a configurable". The one-shot
// blue→red ramp became a hue wheel that turns once every COLOUR_LOOP_TILES
// tiles walked — so the claims are continuity and CLOSURE, not endpoints.

const rgb = (s) => s.match(/[\d.]+/g).map(Number)
/** hsl() hue distance, the short way round. */
const hueGap = (a, b) => {
  const d = Math.abs(rgb(a)[0] - rgb(b)[0]) % 360
  return Math.min(d, 360 - d)
}

test('the wheel never jumps: neighbouring values are neighbouring colours', () => {
  for (let f = 0; f < 1; f += 0.005) {
    const step = hueGap(wheelColour(f), wheelColour(f + 0.005))
    assert.ok(step <= 3, `a jump of ${step}° at ${f.toFixed(3)}`)
  }
  // and across the seam, which is the whole reason it is a wheel
  assert.ok(hueGap(wheelColour(0.999), wheelColour(1.001)) <= 3, 'it closes on itself')
})

test('the wheel closes, and one turn of it is exactly COLOUR_LOOP_TILES tiles', () => {
  const tiles = 1200
  assert.equal(wheelColour(0), wheelColour(1), 'a full turn comes home')
  // t is 0..1 across the run, so one loop is COLOUR_LOOP_TILES/tiles of it
  const one = COLOUR_LOOP_TILES / tiles
  assert.equal(cableColour(0, tiles), cableColour(one, tiles))
  assert.equal(cableColour(0.25, tiles), cableColour(0.25 + one, tiles))
  // half a loop apart is the opposite side of the wheel
  assert.ok(Math.abs(hueGap(cableColour(0, tiles), cableColour(one / 2, tiles)) - 180) < 1)
  // it starts blue, where the run's start box is
  const [h] = rgb(cableColour(0, tiles))
  assert.ok(h > 190 && h < 250, `starts blue (hue ${h})`)
  assert.equal(cableColour(undefined, tiles), cableColour(0, tiles))
})

test('a short run does not race through the wheel — the loop is a DISTANCE', () => {
  // 100 tiles is a third of a loop, whatever else the run did. Two runs of
  // different length are directly comparable by colour because of this.
  const near = (a, b) => Math.abs(a - b) < 1
  assert.ok(near(hueGap(cableColour(0, 100), cableColour(1, 100)), 120), 'a 100-tile run covers a third')
  assert.ok(near(hueGap(cableColour(0, 150), cableColour(1, 150)), 180), 'a 150-tile run covers a half')
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


// -- mitred corners -----------------------------------------------------------
// Andreas, 2026-09-15: "i still think we could make this more smooth, rendering
// real corners, u-turns and so on, such that it doesn't look so rugged" — and,
// off the five-option sheet, "please use mitred: B [with] a cable gap of 3.5".

const seg = (from, to) => [from, to].map((t, i) => visit(i + 1, 3, 0, t[0], t[1]))

test('a straight walk is ONE cable, with one point a tile — no stub per step', () => {
  const { lines } = routePolylines(walkEast(6), placeAll)
  assert.equal(lines.length, 1)
  assert.equal(lines[0].pts.length, 6, 'six tiles, six points, nothing doubled at the joins')
})

test('a corner is ONE mitred point, at the crossing of the two offset lines', () => {
  // Walk an L twice — east then south, then straight back — so the corner
  // carries two cables and the offsets are not zero. Each cable turns at the
  // point where its own two lines actually cross, which for the outer one sits
  // OUTSIDE the tile centre by half a gap on both axes.
  //
  // This is the test the old renderer fails: it offset every step
  // perpendicular to ITSELF and stopped, so a corner was two stubs meeting in
  // a notch — six points here become eight, and none of them is the crossing.
  const G = 6
  const r = route(['3:0'], [
    visit(1, 3, 0, 0, 0), visit(2, 3, 0, 1, 0), visit(3, 3, 0, 1, 1),
    visit(4, 3, 0, 1, 0), visit(5, 3, 0, 0, 0),
  ])
  const { lines } = routePolylines(r, placeAll, { gap: G })
  assert.equal(lines.length, 1, 'out and back are one cable')
  const pts = lines[0].pts
  assert.equal(pts.length, 6, `two mitres and one U-turn cap: ${JSON.stringify(pts)}`)
  // Each corner sits half a gap off the tile centre on BOTH axes — the crossing
  // of its own two offset lines — and the two passes take opposite sides of it.
  // Which pass gets which side is the lane solver's business, not this test's.
  const corner = (p) => [Math.round((p[0] - 1.5 * TILE) * 100) / 100, Math.round((p[1] - 0.5 * TILE) * 100) / 100]
  const [out, back] = [corner(pts[1]), corner(pts[4])]
  assert.deepEqual([Math.abs(out[0]), Math.abs(out[1])], [G / 2, G / 2])
  assert.deepEqual(out, [-back[0], -back[1]], 'the two passes turn on opposite sides of the centre')
})

test('a corner is never left on the bare tile centre when the step has lanes', () => {
  const out = [[0, 0], [1, 0], [1, 1]].map((t, i) => visit(i + 1, 3, 0, t[0], t[1]))
  const back = [[1, 0], [0, 0]].map((t, i) => visit(4 + i, 3, 0, t[0], t[1]))
  const { lines } = routePolylines(route(['3:0'], [...out, ...back]), placeAll)
  for (const p of lines.flatMap((l) => l.pts.slice(1, -1))) {
    const dx = Math.abs(p[0] - 1.5 * TILE), dy = Math.abs(p[1] - 0.5 * TILE)
    assert.ok(dx > 0.5 || dy > 0.5, `a join sits on the bare tile centre: ${p}`)
  }
})

test('a U-turn is capped across the end tile, one cable gap wide', () => {
  // out three east, straight back: the offset lines are parallel at the turn,
  // so the join is a short jog — which is exactly the cap a cable needs.
  const r = route(['3:0'], [...there(3), ...back(3, 4)])
  const { lines } = routePolylines(r, placeAll, { gap: 4 })
  assert.equal(lines.length, 1, 'out and back are one cable, not two')
  const jog = lines[0].pts
  let capped = 0
  for (let i = 1; i < jog.length; i++) {
    const d = Math.hypot(jog[i][0] - jog[i - 1][0], jog[i][1] - jog[i - 1][1])
    if (Math.abs(d - 4) < 0.01) capped += 1
  }
  assert.equal(capped, 1, 'exactly one gap-wide hop, at the turn')
})

/* A cable that changes lane mid-corridor used to do it in zero length — a
   perpendicular step at the tile centre, which reads as a BREAK in the cable
   and lands on the vertex of every cable it passes. Since 2026-09-17 it is
   braided into a short diagonal (Andreas: "braids would be preferable", off the
   cable bench). The U-turn cap is NOT braided, and this walk doubles back
   twice, so the exemption is tested by the same fixture that tests the braid:
   two lane changes lean over, two caps stay square, in both arms. */
const zigzag = () => route(['3:0'], [0, 1, 2, 3, 4, 3, 4, 5].map((x, i) => visit(i + 1, 3, 0, x, 0)))
const hops = (lines, gap) => {
  let square = 0, diagonal = 0
  for (const l of lines) {
    for (let i = 1; i < l.pts.length; i++) {
      const dx = Math.abs(l.pts[i][0] - l.pts[i - 1][0]), dy = Math.abs(l.pts[i][1] - l.pts[i - 1][1])
      if (Math.abs(dy - gap) > 0.01) continue
      if (dx < 0.01) square += 1
      else diagonal += 1
    }
  }
  return { square, diagonal }
}

test('a lane change is braided into a diagonal; a U-turn keeps its square cap', () => {
  // `solve: false` on purpose: this is a claim about how the RENDERER draws a
  // lane change, so it is measured against the chronological lanes rather than
  // against whatever the solver picks. See the test below for what the solver
  // then does to this same walk.
  const { lines } = routePolylines(zigzag(), placeAll, { gap: 4, solve: false })
  const h = hops(lines, 4)
  assert.equal(h.diagonal, 2, 'both lane changes lean over')
  assert.equal(h.square, 2, 'and the two square hops left are the two U-turn caps')
})

test('the control: without the braid every one of those is a square step', () => {
  // `braid: false` is the pre-2026-09-17 drawing. Nothing in the app passes it;
  // it is here so the test above is measured against something.
  const { lines } = routePolylines(zigzag(), placeAll, { gap: 4, braid: false, solve: false })
  const h = hops(lines, 4)
  assert.equal(h.diagonal, 0)
  assert.equal(h.square, 4, 'two lane changes and two caps, all square')
})

/* The lane solver is measured on a REAL walk, not a made-up one: every shape I
   invented by hand drew zero crossings, so it could not have witnessed the fix
   either way. This is Professor Oak's lab as gemini-3.8-flash(high) walked it
   on 2026-09-15 — down the west aisle, up the east one, then down the west
   again — expanded through the run's own fills, which is why it is tile by tile
   and carries a zero-length step where the run turned on the spot. */
const LAB = [[6, 12], [6, 11], [6, 10], [6, 9], [6, 8], [6, 7], [6, 6], [6, 5], [6, 4], [7, 4],
             [7, 3], [8, 3], [7, 3], [7, 4], [7, 5], [7, 6], [7, 7], [7, 8], [7, 9], [7, 10],
             [7, 11], [7, 12], [6, 12], [6, 12], [6, 11], [6, 10], [6, 9], [6, 8], [6, 7], [6, 6],
             [6, 5], [6, 4], [6, 5], [6, 6], [6, 7], [6, 8], [6, 9], [6, 10], [6, 11], [6, 12]]
const labRoute = () => route(['4:3'], LAB.map((p, i) => visit(i + 1, 4, 3, p[0], p[1])))

/** Every crossing in a drawing, counted on the drawn geometry. Two cables that
 *  merely touch at a shared endpoint are a crossing to a reader, so endpoints
 *  count; the same meeting found from both sides is deduped by position. */
function crossingCount(lines) {
  const segs = []
  lines.forEach((l, li) => {
    for (let i = 0; i < l.pts.length - 1; i++) {
      const [a, b] = [l.pts[i], l.pts[i + 1]]
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) >= 0.2) segs.push({ a, b, li, si: i })
    }
  })
  const seen = new Set()
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const P = segs[i], Q = segs[j], same = P.li === Q.li
      if (same && Math.abs(P.si - Q.si) <= 1) continue
      const d1 = [P.b[0] - P.a[0], P.b[1] - P.a[1]], d2 = [Q.b[0] - Q.a[0], Q.b[1] - Q.a[1]]
      const den = d1[0] * d2[1] - d1[1] * d2[0]
      if (Math.abs(den) < 1e-9) continue
      const wx = Q.a[0] - P.a[0], wy = Q.a[1] - P.a[1]
      const t = (wx * d2[1] - wy * d2[0]) / den, u = (wx * d1[1] - wy * d1[0]) / den
      const e = same ? 1e-6 : -1e-6
      if (t <= e || t >= 1 - e || u <= e || u >= 1 - e) continue
      seen.add(`${Math.round((P.a[0] + d1[0] * t) * 4)}:${Math.round((P.a[1] + d1[1] * t) * 4)}:${Math.min(P.li, Q.li)}:${Math.max(P.li, Q.li)}`)
    }
  }
  return seen.size
}

test('the solver draws fewer crossings than numbering the passes by arrival', () => {
  const r = labRoute()
  const before = crossingCount(routePolylines(r, placeAll, { solve: false }).lines)
  const after = crossingCount(routePolylines(r, placeAll, { solve: true }).lines)
  assert.ok(before > 0, 'the control has something to fix')
  assert.ok(after < before, `solved ${after} against ${before} by arrival`)
})

test('no two passes of a step are drawn in the same lane', () => {
  const r = labRoute()
  const solved = solveLanes(r)
  const steps = laneSteps(r)
  const byEdge = new Map()
  steps.forEach((s, i) => {
    ;[s.lane, s.lanes] = solved[i]
    assert.ok(s.lane >= 0 && s.lane < s.lanes, `lane ${s.lane} of ${s.lanes}`)
    const k = [s.u.join(','), s.v.join(',')].sort().join('|')
    if (!byEdge.has(k)) byEdge.set(k, [])
    byEdge.get(k).push(s.lane)
  })
  for (const [k, ls] of byEdge) {
    // past the cap the extra passes share the outermost lane, by design
    const want = Math.min(ls.length, MAX_LANES)
    assert.equal(new Set(ls).size, want, `${k} put ${ls.length} passes on ${new Set(ls).size} lanes`)
  }
})

test('the solver takes the jogs out of a walk that doubles back on itself', () => {
  // The same zigzag. By arrival it changes lane twice; the solver puts the two
  // directions on opposite sides of the corridor, where neither has to move
  // over — so the only square hops left are the two U-turn caps, and nothing
  // is braided because nothing changes lane.
  const h = hops(routePolylines(zigzag(), placeAll, { gap: 4 }).lines, 4)
  assert.equal(h.diagonal, 0, 'no lane change survives')
  assert.equal(h.square, 2, 'just the two caps')
})

test('the answer is memoised per route and per lane cap, not recomputed per draw', () => {
  const r = labRoute()
  assert.equal(solveLanes(r), solveLanes(r), 'the same array comes back')
  assert.notEqual(solveLanes(r), solveLanes(r, { maxLanes: 2 }), 'a different cap is its own answer')
  assert.deepEqual(solveLanes(labRoute()), solveLanes(r), 'and it is deterministic across routes')
})

/* Pallet Town as gpt-6-astra(low) walked it on 2026-09-11, expanded through the
   run's own fills — 99 tiles, the map Andreas pointed at. The lab walk above is
   too small to separate the rules from each other: it comes out untangled
   whatever they do. This one does not. */
const PALLET = [
  [6,7], [6,8], [7,8], [8,8], [9,8], [10,8], [11,8], [11,7], [11,6], [11,5],
  [11,4], [11,3], [11,2], [12,2], [12,1], [12,2], [11,2], [11,3], [11,4], [11,5],
  [11,6], [11,7], [11,8], [11,9], [11,10], [11,11], [11,12], [11,13], [12,13], [12,14],
  [13,14], [14,14], [15,14], [16,14], [16,13], [16,13], [16,14], [15,14], [14,14], [13,14],
  [12,14], [11,14], [11,13], [11,12], [11,11], [11,10], [11,9], [11,8], [11,7], [11,6],
  [11,5], [11,4], [11,3], [11,2], [12,2], [12,1], [12,0], [12,0], [12,1], [12,2],
  [12,3], [12,4], [12,5], [12,6], [12,7], [12,8], [12,9], [12,10], [12,11], [12,12],
  [12,13], [12,12], [12,13], [12,14], [13,14], [14,14], [15,14], [16,14], [16,13], [16,13],
  [16,14], [15,14], [14,14], [13,14], [12,14], [12,13], [12,12], [12,11], [12,10], [12,9],
  [12,8], [12,7], [12,6], [12,5], [12,4], [12,3], [12,2], [12,1], [12,0]]
const palletRoute = () => route(['3:0'], PALLET.map((p, i) => visit(i + 1, 3, 0, p[0], p[1])))

test('on a real town the solver draws under a third of the crossings', () => {
  const r = palletRoute()
  const before = crossingCount(routePolylines(r, placeAll, { solve: false }).lines)
  const after = crossingCount(routePolylines(r, placeAll, { solve: true }).lines)
  assert.ok(before >= 20, `the control has a tangle to fix: ${before}`)
  // The bound is what the direction split buys. Order the passes without it —
  // by arrival, or by continuity alone — and this walk lands in the teens, so
  // a rule that quietly stopped splitting by direction would fail here rather
  // than pass quietly with a worse drawing.
  assert.ok(after * 3 < before, `solved ${after} against ${before}`)
})

test('a warp breaks the cable and rings both ends', () => {
  const r = route(['3:0', '4:0'], [visit(1, 3, 0, 0, 0), visit(2, 3, 0, 1, 0),
                                   visit(3, 4, 0, 9, 9), visit(4, 4, 0, 9, 8)])
  const { lines, marks } = routePolylines(r, placeAll)
  assert.equal(lines.length, 2, 'two cables, never bridged')
  assert.equal(marks.length, 2, 'both ends of the warp marked')
})

test('the cap and the gap are the ones that were picked, not local literals', () => {
  assert.equal(CABLE_GAP, 3.5)
  assert.equal(MAX_LANES, 4)
  // the default really is the constant: a fifth pass does not get a fifth lane
  const visits = []
  for (let p = 0; p < 6; p++)
    visits.push(...(p % 2 ? back(3, 1 + p * 3) : there(3).map((v, i) => visit(1 + p * 3 + i, 3, 0, i, 0))))
  const steps = laneSteps(route(['3:0'], visits))
  assert.ok(steps.every((s) => s.lanes <= 4))
  assert.ok(steps.some((s) => s.over), 'a step past the cap is marked as over')
})


// -- walking into a cluster ---------------------------------------------------
// Andreas, 2026-09-16: "i am starting to feel like we should get rid of the
// pop-up … the whole map should change to that sub-map with an arrow to go
// back, or the ability to just press on the door to get back out" — and "please
// keep the building clusters we established".

test('a cluster is laid out in ONE coordinate space, the same shape as the world', () => {
  const L = clusterLayout('ViridianForest', ATLAS)
  // the same keys the world layout hands its callers, so `place`, `drawRoute`,
  // `battlesFor` and the hover readout work on a cluster with no idea it is one
  for (const k of ['at', 'w', 'h', 'outdoor', 'insets', 'interiors']) assert.ok(k in L, k)
  assert.deepEqual(Object.keys(L.at).sort(), ['15:0', '15:3', '1:0'])
  for (const p of Object.values(L.at)) assert.ok(p.win, 'every entry carries its drawn window')
})

test('a cluster stacks the way you walk it, and nothing overlaps', () => {
  const L = clusterLayout('ViridianForest', ATLAS)
  const n = L.at['15:3'], f = L.at['1:0'], s = L.at['15:0']
  assert.ok(n.y + n.win.h <= f.y, 'the north gate sits above the forest')
  assert.ok(f.y + f.win.h <= s.y, 'and the south gate below it')
  // cross-axis centred: a 13-wide gate under the middle of a 54-wide forest,
  // not jammed against its left edge
  const mid = (p) => p.x + p.win.w / 2
  assert.ok(Math.abs(mid(n) - mid(f)) <= 1, `${mid(n)} vs ${mid(f)}`)
  assert.ok(Math.abs(mid(s) - mid(f)) <= 1)
})

test("a building keeps its floors side by side — stacked, it would be upside down", () => {
  const L = clusterLayout('PalletTown_PlayersHouse', ATLAS)
  const a = L.at['4:0'], b = L.at['4:1']
  assert.ok(a.x + a.win.w <= b.x, '1F left of 2F')
  // one row, both centred across it — not "same y", which a shorter floor
  // would fail purely for being shorter
  const mid = (p) => p.y + p.win.h / 2
  assert.ok(Math.abs(mid(a) - mid(b)) <= 1, `${mid(a)} vs ${mid(b)}`)
})

test('a cluster is sized to what it holds, so `fit` shows all of it', () => {
  const L = clusterLayout('ViridianForest', ATLAS)
  for (const p of Object.values(L.at)) {
    assert.ok(p.x >= 0 && p.y >= 0, 'inside the frame')
    assert.ok(p.x + p.win.w <= L.w, 'and not off its right edge')
    assert.ok(p.y + p.win.h <= L.h, 'or its bottom')
  }
})

test('the way out is on the door, and the forest itself has none', () => {
  const L = clusterLayout('ViridianForest', ATLAS)
  const out = exitsFor(L, ATLAS)
  assert.equal(out.length, 2, 'one per gate')
  assert.deepEqual(out.map((e) => e.to), ['3:20', '3:20'], 'both come out on Route 2')
  // the forest's own warps go to its gate houses — inside the cluster — so it
  // carries no exit of its own, which is also where you would actually walk
  assert.ok(!out.some((e) => e.key.startsWith('1:0')))
  // placed through the drawn window: the gate's first column is a flat strip,
  // so its tile 7 is six tiles in from the drawn left edge
  const north = out.find((e) => e.key.startsWith('15:3'))
  assert.equal(north.tile.x, L.at['15:3'].x + 7 - 1)
  assert.equal(north.tile.y, L.at['15:3'].y + 1)
})

test('every floor gets a caption, and the cluster member keeps its own name', () => {
  const labs = floorsFor(clusterLayout('ViridianForest', ATLAS), ATLAS).map((f) => f.label)
  assert.deepEqual(labs.sort(), ['North Entrance', 'South Entrance', 'Viridian Forest'])
  const house = floorsFor(clusterLayout('PalletTown_PlayersHouse', ATLAS), ATLAS).map((f) => f.label)
  assert.deepEqual(house.sort(), ['1F', '2F'])
})

test('a door marker and a battle are both placed through the drawn window', () => {
  // The world has no trim, so this only bites indoors — where it is the
  // difference between a dot on the door and a dot one tile off it.
  const L = clusterLayout('PalletTown_PlayersHouse', ATLAS)
  const r = route(['4:0'], [], { battles: [{ kind: 'wild', tile: [4, 0, 6, 4], opened_turn: 3 }] })
  const [b] = battlesFor(L, r)
  assert.equal(b.tile.x, L.at['4:0'].x + 6 - 1, 'minus the trimmed leading column')
  assert.equal(b.tile.y, L.at['4:0'].y + 4)
})

test('a cluster that is not in the atlas lays out nothing rather than half of one', () => {
  assert.equal(clusterLayout('NoSuchBuilding', ATLAS), null)
  assert.equal(clusterLayout(null, ATLAS), null)
})
