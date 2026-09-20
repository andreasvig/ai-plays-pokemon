// Doors, buildings and complexes: what happens when you click a marker.
//
// None of this had a test, which is how it broke silently. The engine port on
// 2026-09-20 dropped `floorLabel` from mapatlas.js while leaving `floorsFor`
// calling it, so every marker click raised a ReferenceError and the popup
// simply never opened — Andreas, the same day: "i dont want the clicking on
// maps doors donst work". Nothing in 137 JS tests or 1676 Python tests noticed,
// because the atlas was still well-formed and the world map still drew.
//
// The second half is the COMPLEX: Viridian Forest and its two gate houses open
// as one place, stacked north to south. That is a curated fact in
// scripts/render_gamemaps.py and a layout rule here, and the port lost the
// layout half too — the forest fell back to being a loose rectangle beside the
// world ("it should work as on the online version where the viridian forest is
// also its own sub map/room").
import test from 'node:test'
import assert from 'node:assert/strict'
import { clusterLayout, floorsFor, exitsFor, markersFor, floorLabel, buildingLabel }
  from '../../src/dashboard/web/src/lib/mapatlas.js'

// A two-floor building and a three-member complex, shaped like the real atlas.
const ATLAS = {
  schema: 2, game: 'firered-us', tile_px: 16,
  maps: {
    '3:0': { name: 'PalletTown', width: 24, height: 20, file: '3-0.png', indoor: false,
             world: [0, 0], doors: [{ x: 5, y: 8, to: '4:0', building: 'PalletTown_PlayersHouse' }] },
    '3:20': { name: 'Route2', width: 24, height: 80, file: '3-20.png', indoor: false, world: [0, -160],
              doors: [{ x: 9, y: 60, to: '15:0', building: 'ViridianForest' },
                      { x: 9, y: 11, to: '15:3', building: 'ViridianForest' }] },
    '4:0': { name: 'PalletTown_PlayersHouse_1F', width: 13, height: 10, file: '4-0.png', indoor: true,
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'], popup: true,
             exits: [{ x: 6, y: 9, to: '3:0' }] },
    '4:1': { name: 'PalletTown_PlayersHouse_2F', width: 12, height: 9, file: '4-1.png', indoor: true,
             building: 'PalletTown_PlayersHouse', floors: ['4:0', '4:1'], popup: true },
    '15:3': { name: 'Route2_ViridianForest_NorthEntrance', width: 15, height: 12, file: '15-3.png',
              indoor: true, building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'],
              popup: true, complex: true, exits: [{ x: 7, y: 11, to: '3:20' }] },
    '1:0': { name: 'ViridianForest', width: 54, height: 69, file: '1-0.png', indoor: false,
             building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'], popup: true, complex: true },
    '15:0': { name: 'Route2_ViridianForest_SouthEntrance', width: 15, height: 12, file: '15-0.png',
              indoor: true, building: 'ViridianForest', floors: ['15:3', '1:0', '15:0'],
              popup: true, complex: true, exits: [{ x: 7, y: 11, to: '3:20' }] },
  },
}
const ROUTE = { game: 'firered-us', maps: { '3:0': 1, '3:20': 1, '1:0': 1, '15:0': 1 }, visits: [], battles: [] }

test('clicking a door yields a layout with a caption per floor', () => {
  // THE regression test. `floorsFor` called a `floorLabel` that had been
  // deleted, so this threw rather than returning anything.
  const L = clusterLayout('PalletTown_PlayersHouse', ATLAS)
  assert.ok(L, 'a building the atlas knows must lay out')
  const f = floorsFor(L, ATLAS)
  assert.deepEqual(f.map((x) => x.label), ['1F', '2F'])
  assert.equal(exitsFor(L, ATLAS).length, 1, 'and a way back out to the town')
})

test('a building lays its floors side by side, a complex stacks them', () => {
  const house = clusterLayout('PalletTown_PlayersHouse', ATLAS)
  assert.equal(house.stacked, false, '1F above 2F would put the building upside down')
  // Side by side: 1F left of 2F, and each centred on the cross axis — so the
  // shorter floor's y is NOT equal to the taller one's, it is inset.
  const [f1, f2] = ['4:0', '4:1'].map((k) => house.at[k])
  assert.ok(f2.x > f1.x + f1.win.w - 1, '2F must start to the right of 1F')
  assert.ok(Math.abs(f1.y - f2.y) <= 1, 'and both centred, not stacked')

  const forest = clusterLayout('ViridianForest', ATLAS)
  assert.equal(forest.stacked, true)
  // north at the top, the forest under it, the south gate at the bottom — the
  // order the atlas declared, which is the order you walk it.
  const order = Object.entries(forest.at).sort((a, b) => a[1].y - b[1].y).map(([k]) => k)
  assert.deepEqual(order, ['15:3', '1:0', '15:0'])
})

test('a complex is one place: its members carry the complex, not their own names', () => {
  const L = clusterLayout('ViridianForest', ATLAS)
  assert.deepEqual(floorsFor(L, ATLAS).map((x) => x.label),
    ['North Entrance', 'Viridian Forest', 'South Entrance'])
  // The forest's own neighbours are its gates, so it has no way OUT of its own;
  // the two gates carry the cluster's only exits, both onto Route 2.
  const outs = exitsFor(L, ATLAS)
  assert.equal(outs.length, 2)
  assert.deepEqual([...new Set(outs.map((e) => e.to))], ['3:20'])
})

test('a complex gets one marker per way in, a building exactly one', () => {
  const world = { at: { '3:0': { x: 0, y: 0, m: ATLAS.maps['3:0'], win: { x: 0, y: 0, w: 24, h: 20 } },
                        '3:20': { x: 0, y: 0, m: ATLAS.maps['3:20'], win: { x: 0, y: 0, w: 24, h: 80 } } } }
  const M = markersFor(world, ROUTE, ATLAS)
  const forest = M.filter((m) => m.building === 'ViridianForest')
  assert.equal(forest.length, 2, 'two gates a long way apart; one marker leaves the other as scenery')
  assert.equal(M.filter((m) => m.building === 'PalletTown_PlayersHouse').length, 1)
  // `entered` follows ANY floor of the place, which is what makes a marker
  // whose forest the run walked read as entered though its gate key differs.
  assert.equal(forest.every((m) => m.entered), true)
  assert.equal(M.find((m) => m.building === 'PalletTown_PlayersHouse').entered, false)
})

test('floorLabel names a floor, a complex member, and nothing at all', () => {
  assert.equal(floorLabel('PewterCity_Museum_2F', 'PewterCity_Museum'), '2F')
  assert.equal(floorLabel('Route2_ViridianForest_NorthEntrance', 'ViridianForest'), 'North Entrance')
  // The map that IS the place gets no floor caption — the title already says it.
  assert.equal(floorLabel('ViridianForest', 'ViridianForest'), '')
  assert.equal(floorLabel('ViridianCity_Gym', 'ViridianCity_Gym'), '')
  assert.equal(buildingLabel('PewterCity_PokemonCenter'), 'Pokémon Center')
})

test('a building the atlas has never heard of lays out as nothing, not as a throw', () => {
  assert.equal(clusterLayout('CeruleanCity_Gym', ATLAS), null)
  assert.equal(clusterLayout(null, ATLAS), null)
  assert.deepEqual(floorsFor(null, ATLAS), [])
  assert.deepEqual(exitsFor(null, ATLAS), [])
})
