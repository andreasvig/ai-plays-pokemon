// Unit tests for the map atlas helpers:
//   src/dashboard/web/src/lib/mapatlas.js  (layout, markers, route drawing)
//
// Run directly (`node tests/js/mapatlas.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory. No svelte, no DOM —
// `drawRoute` is handed a recording stub in place of a canvas context.
import test from 'node:test'
import assert from 'node:assert/strict'
import { TILE, worldLayout, markersFor, buildingLabel, drawRoute, visitAt } from '../../src/dashboard/web/src/lib/mapatlas.js'

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
