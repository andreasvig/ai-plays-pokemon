// Unit tests for the map atlas helpers:
//   src/dashboard/web/src/lib/mapatlas.js  (layout, markers, route drawing)
//
// Run directly (`node tests/js/mapatlas.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory. No svelte, no DOM —
// `drawRoute` is handed a recording stub in place of a canvas context.
import test from 'node:test'
import assert from 'node:assert/strict'
import { TILE, tilePxOf, pngOrigin, mapImageUrl, worldLayout, markersFor, buildingLabel, drawRoute, drawArrows, ARROW_EVERY_TILES, drawSize, drawWindow, laneSteps, wheelColour, cableColour, COLOUR_LOOP_TILES, visitTimes, visitAt } from '../../src/dashboard/web/src/lib/mapatlas.js'

// Pallet Town at the world origin, Route 1 above it, Viridian Forest with no
// place in the frame, and the player's two floors, which are never laid out.
const ATLAS = {
  maps: {
    '3:0': { name: 'PalletTown', width: 24, height: 20, world: [0, 0], type: 'MAP_TYPE_TOWN', indoor: false, file: '3-0.png',
             doors: [{ x: 6, y: 7, to: '4:0', building: 'PalletTown_PlayersHouse' },
                     { x: 15, y: 7, to: '4:2', building: 'PalletTown_RivalsHouse' }] },
    '3:19': { name: 'Route1', width: 24, height: 40, world: [0, -40], type: 'MAP_TYPE_ROUTE', indoor: false, file: '3-19.png' },
    '1:0': { name: 'ViridianForest', width: 54, height: 69, type: 'MAP_TYPE_ROUTE', indoor: false, file: '1-0.png' },
    '4:0': { name: 'PalletTown_PlayersHouse_1F', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', indoor: true, file: '4-0.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'] },
    '4:1': { name: 'PalletTown_PlayersHouse_2F', width: 12, height: 9, type: 'MAP_TYPE_INDOOR', indoor: true, file: '4-1.png',
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'] },
    '4:2': { name: 'PalletTown_RivalsHouse', width: 13, height: 10, type: 'MAP_TYPE_INDOOR', indoor: true, file: '4-2.png',
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

test('a DS building falls back to the name the cartridge prints', () => {
  // A gen 4/5 cartridge has no decomp, so its building key is the bare map
  // id and the popup header read "61:0". Those atlases carry the cartridge's
  // own place name since 2026-09-21, so it reads that instead.
  assert.equal(buildingLabel('61:0', 'New Bark Town'), 'New Bark Town')
  assert.equal(buildingLabel('428:0', 'Aspertia City'), 'Aspertia City')

  // The decomp label WINS where there is one. Platinum's name is the symbol
  // in full, so preferring the name there would be a regression: the header
  // would read TwinleafTown_RivalHouse_1F instead of a room.
  assert.equal(buildingLabel('TwinleafTown_RivalHouse_1F',
                             'TwinleafTown_RivalHouse_1F'), 'Rival House 1F')

  // And with no name at all nothing changes — the old single-argument
  // behaviour is intact, including the id it has no better answer than.
  assert.equal(buildingLabel('PewterCity_PokemonCenter'), 'Pokémon Center')
  assert.equal(buildingLabel('61:0'), '61:0')
  assert.equal(buildingLabel('61:0', '61:0'), '61:0')
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

// -- the chevrons -------------------------------------------------------------
// Arrows moved out of `drawRoute` and into `drawArrows` on 2026-09-20, with the
// port of the published site's engine: the route is baked once and only the
// chevrons are redrawn, so they can slide along the cable. Re-pointed rather
// than deleted — and note the middle one of these USED TO PASS VACUOUSLY after
// the port, because its loop ran over an empty list of arrowheads.
//
// A chevron is a three-point STROKE, not a filled triangle, so it needs its own
// stub; the `tris` stub above only records fills.
function stubWithChevrons() {
  const c = stub()
  c.calls.chev = []
  let pending = []
  c.beginPath = () => { pending = [] }
  c.moveTo = (x, y) => { pending = [[x, y]] }
  c.lineTo = (x, y) => { pending.push([x, y]) }
  c.closePath = () => {}
  c.fill = () => {}
  c.stroke = () => { if (pending.length === 3) c.calls.chev.push(pending.slice()) }
  return c
}
// Two strokes per chevron (a dark outline then the colour), so halve the count.
const chevrons = (c) => {
  const seen = []
  for (let i = 0; i < c.calls.chev.length; i += 2) seen.push(c.calls.chev[i])
  return seen
}

function arrowsOn(route, opts = {}) {
  const bake = stub()
  const lines = drawRoute(bake, route, placeAll, { scale: TILE })
  const c = stubWithChevrons()
  drawArrows(c, lines, { scale: TILE, tiles: route.visits.length, now: 0, ...opts })
  return chevrons(c)
}

test('a long walk carries chevrons, spaced along the path', () => {
  const got = arrowsOn(walkEast(40), { every: 5, startNoise: 0 })
  // 39 tiles of travel, one chevron every 5 tiles, the first half a gap in.
  assert.ok(got.length >= 6 && got.length <= 9, `got ${got.length}`)
})

test('a chevron points the way the run went', () => {
  const got = arrowsOn(walkEast(40), { every: 5, startNoise: 0 })
  assert.ok(got.length > 0, 'a vacuous loop is how this test passed while drawing nothing')
  for (const [a, tip, b] of got) {
    // walking east: the tip leads, and the two arms span across the line
    assert.ok(tip[0] > a[0] && tip[0] > b[0], 'the tip leads')
    assert.ok(Math.abs(a[1] - b[1]) > 0, 'the arms span across the line')
  }
})

test('the chevrons slide along the cable as time advances', () => {
  const bake = stub()
  const route = walkEast(40)
  const lines = drawRoute(bake, route, placeAll, { scale: TILE })
  const at = (now) => {
    const c = stubWithChevrons()
    drawArrows(c, lines, { scale: TILE, tiles: route.visits.length, now, every: 5, startNoise: 0 })
    return chevrons(c).map((t) => t[1][0])
  }
  const t0 = at(0), t1 = at(0.4)
  assert.ok(t0.length && t1.length)
  assert.notDeepEqual(t0, t1, 'a static chevron is the thing this replaced')
})

test('a short hop is not decorated', () => {
  assert.equal(arrowsOn(walkEast(3), { every: 5, startNoise: 0 }).length, 0)
})

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


// -- the cable colour ---------------------------------------------------------
// Andreas, 2026-09-15: "can we render the colours as fully gradual changes?"
//
// The five-stop blue-to-red ramp (`turnColour`) was replaced 2026-09-20 by the
// published site's colour WHEEL: hue cycles once every COLOUR_LOOP_TILES tiles
// walked, so a long run keeps separating instead of saturating at red halfway.
// The gradualness claim below is the original one and still has to hold; the
// second test replaces "blue to red, clamped" with the wheel's real contract —
// it WRAPS, which is the opposite of clamping, and a test that silently dropped
// that would be claiming less than the one it replaced.

const hue = (s) => Number(s.match(/hsl\((-?[\d.]+)/)[1])

test('the wheel never jumps: neighbouring values are neighbouring hues', () => {
  let prev = hue(wheelColour(0))
  for (let k = 0.005; k <= 1.0001; k += 0.005) {
    const h = hue(wheelColour(k))
    // Compare the short way round, since the wheel legitimately crosses 360.
    const d = Math.min(Math.abs(h - prev), 360 - Math.abs(h - prev))
    assert.ok(d <= 4, `a jump of ${d} degrees at ${k.toFixed(3)}`)
    prev = h
  }
})

test('the wheel wraps rather than clamping, and a full turn returns to the start', () => {
  assert.equal(wheelColour(0), wheelColour(1), 'one turn is a full circle')
  assert.equal(wheelColour(0.25), wheelColour(1.25), 'past the end it keeps going')
  assert.equal(wheelColour(-0.75), wheelColour(0.25), 'and it goes backwards too')
  // The old ramp clamped: turnColour(9) === turnColour(1). The wheel must not.
  assert.notEqual(wheelColour(0.5), wheelColour(0.9),
    'clamping would make every long run one flat colour past the midpoint')
})

test('cable colour cycles once every COLOUR_LOOP_TILES tiles walked', () => {
  const tiles = COLOUR_LOOP_TILES * 3          // three full turns over the run
  // t is 0..1 across the run, so one loop is 1/3 of it.
  assert.equal(cableColour(0, tiles), cableColour(1 / 3, tiles))
  assert.notEqual(cableColour(0, tiles), cableColour(1 / 6, tiles))
  // A run shorter than one loop never repeats a colour.
  const short = COLOUR_LOOP_TILES / 2
  assert.notEqual(cableColour(0, short), cableColour(1, short))
})

test('cable colour survives the degenerate inputs a real route hands it', () => {
  assert.equal(cableColour(undefined, 100), cableColour(0, 100))
  assert.equal(cableColour(NaN, 100), cableColour(0, 100))
  assert.ok(cableColour(0.5, 0), 'a zero-tile run must still get a colour')
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


// --- schema 2: the atlas is per game, and indoor-ness is a normalised flag ---
//
// Added 2026-09-20. `worldLayout` used to branch on the string 'MAP_TYPE_INDOOR',
// a pret gen-3 constant that pokecrystal, pokeplatinum and the DS rips do not
// emit at all — so every map of every other game fell through as outdoor.
// A rename-only refactor passes the tests above; these are the ones it fails.

// A DS atlas: single-id keys padded to the wire encoding, NO `type` anywhere,
// and a shared global frame, which is how gen 4/5 outdoor coordinates really
// arrive (Platinum maps 342/343/418 tile into one plane).
const DS_ATLAS = {
  schema: 2, game: 'platinum-us', key_shape: 'id', tile_px: 16,
  maps: {
    '342:0': { name: 'Route201', width: 48, height: 24, indoor: false, frame: 'global', world: [112, 840], file: '342-0.png' },
    '418:0': { name: 'SandgemTown', width: 32, height: 32, indoor: false, frame: 'global', world: [160, 832], file: '418-0.png' },
    '422:0': { name: 'SandgemLab', width: 12, height: 16, indoor: true, file: '422-0.png' },
  },
}

test('a DS atlas with no map_type at all still separates indoor from outdoor', () => {
  const L = worldLayout(route(['342:0', '418:0', '422:0']), DS_ATLAS)
  assert.equal(L.outdoor, 2, 'both outdoor zones belong in the world frame')
  assert.equal(L.at['422:0'], undefined, 'the lab is an interior and is not laid out')
  assert.deepEqual(L.interiors, ['422:0'])
})

test('an entry marked indoor is excluded even when it carries no type', () => {
  const atlas = { maps: { '9:0': { width: 4, height: 4, indoor: true } } }
  assert.equal(worldLayout(route(['9:0']), atlas), null)
})

test('an entry with a type but no indoor flag is not treated as indoor', () => {
  // The inverse control. A source that emits `type` and no `indoor` must not
  // have its interiors silently guessed from the old string.
  const atlas = { maps: { '9:0': { width: 4, height: 4, type: 'MAP_TYPE_INDOOR', world: [0, 0] } } }
  const L = worldLayout(route(['9:0']), atlas)
  assert.notEqual(L, null, 'indoor-ness must come from the normalised flag, not the raw string')
  assert.equal(L.outdoor, 1)
})

test('two games with the same map key get different image URLs', () => {
  // The collision route.py and observed.py both refuse server-side, and which
  // the browser had no defence against: one flat `maps/` namespace.
  assert.notEqual(mapImageUrl('firered-us', '3-0.png'), mapImageUrl('emerald-us', '3-0.png'))
  assert.match(mapImageUrl('platinum-us', '418-0.png'), /maps\/platinum-us\/418-0\.png$/)
})

test('the tile size comes from the atlas, then the route, then 16', () => {
  assert.equal(tilePxOf({ tile_px: 24 }, { tile_px: 16 }), 24)
  assert.equal(tilePxOf(null, { tile_px: 32 }), 32)
  assert.equal(tilePxOf(null, null), TILE)
})

test('the PNG frame defaults to the route origin and a DS map says otherwise', () => {
  // The default IS the assertion: every gen 1-3 atlas ships artwork that starts
  // at the route's own corner, and adding this field must not move any of it.
  assert.deepEqual(pngOrigin({ width: 13, height: 10 }), [0, 0])
  assert.deepEqual(pngOrigin(null), [0, 0])
  // A gen-4 outdoor map ships the map alone, so the source rect starts at its
  // own corner and NOT at (origin * tile_px) — which at 16 px/tile would be
  // 13,824 px down a 42-megapixel image.
  assert.deepEqual(pngOrigin({ origin: [160, 832], png_origin: [160, 832] }), [160, 832])
})
