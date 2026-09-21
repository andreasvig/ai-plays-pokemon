// The camera the atlas ships, and what the viewer does with it:
//   src/dashboard/web/src/lib/mapatlas.js  (cameraOf, projectTile, heights, pad)
//
// Run directly (`node tests/js/camera.test.mjs`) or through
// tests/test_live_feed_js.py, which globs this directory.
//
// The fixtures are HAND-BUILT rather than lifted from a rendered atlas, on
// purpose: these are the schema's own rules, and a fixture copied out of the
// writer would pass whatever the writer did. The numbers in PITCHED are the
// ones `scripts/ds3d/camera.py` actually emits at the field camera's pitch —
// tile_px_y = 16 * sin(59.051513671875 deg) and height_px = cos / 16 — so a
// drift between the two sides is a failure here.
//
// Each test names the mutation that makes it fail; they were all applied.
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  cameraOf, topdownCamera, isTopdown, tilePxX, tilePxY, projectTile, unprojectTile,
  layoutSize, pngPad, heightGrid, heightAt, padTiles, worldLayout, markersFor,
  battlesFor, drawWindow,
} from '../../src/dashboard/web/src/lib/mapatlas.js'

const PITCH = 59.051513671875
const PY = 16 * Math.sin((PITCH * Math.PI) / 180)          // 13.7220...
const HP = Math.cos((PITCH * Math.PI) / 180) / 1           // px per world unit at 16 px/tile
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} !~ ${b}`)

// ---------------------------------------------------------------------------
// The two fixture atlases. LEGACY is what every atlas on disk looks like today
// — `camera` is the bare string "topdown" and no map carries a pad or a height
// grid. PITCHED is a two-map DS atlas: a 4x3 town whose ground is 16 world
// units up (which is every gen-4 outdoor map), and a 4x2 route east of it with
// a terrace: its right-hand column stands 8 units higher.
// ---------------------------------------------------------------------------
const LEGACY = {
  schema: 2, game: 'firered-us', tile_px: 16, camera: 'topdown',
  maps: {
    '3:0': { name: 'PalletTown', width: 4, height: 3, world: [0, 0], indoor: false,
             file: '3-0.png',
             doors: [{ x: 1, y: 1, to: '4:0', building: 'PalletTown_PlayersHouse' }] },
    '4:0': { name: 'PlayersHouse_1F', width: 3, height: 3, indoor: true, popup: true,
             file: '4-0.png', building: 'PalletTown_PlayersHouse', floors: ['4:0'] },
  },
}

const PITCHED = {
  schema: 2, game: 'platinum-us', tile_px: 16, render: '3d-pitched',
  camera: { kind: 'pitched', pitch_deg: PITCH, matrix: [16, 0, 0, PY, 0, 0],
            height_px: HP, height_unit: 'world' },
  maps: {
    // origin 10,20 — gen-4 coordinates are global, so map-local tile (0,0) is
    // route tile (10,20) and the height grid is indexed from there.
    '418:0': { name: 'SandgemTown', width: 4, height: 3, world: [10, 20],
               origin: [10, 20], png_origin: [10, 20], indoor: false,
               file: '418-0.png', png_pad: [4, 31, 4, 0],
               heights: [12, 16],
               doors: [{ x: 11, y: 21, to: '420:0', building: 'Sandgem_Pokecenter' }] },
    // 8 tiles: rows [0,0,0,8] twice. The last column is a terrace half a tile up.
    '342:0': { name: 'Route201', width: 4, height: 2, world: [14, 20],
               origin: [14, 20], png_origin: [14, 20], indoor: false,
               file: '342-0.png', png_pad: [0, 8, 0, 0],
               heights: [3, 0, 1, 8, 3, 0, 1, 8] },
  },
}

const LEGACY_ROUTE = { game: 'firered-us', maps: { '3:0': {} }, visits: [] }

const ROUTE = {
  game: 'platinum-us',
  maps: { '418:0': {}, '342:0': {} },
  visits: [],
  battles: [{ tile: [342, 0, 17, 20], kind: 'wild' }],
}

// ---------------------------------------------------------------------------
// cameraOf — both shapes of the field
// ---------------------------------------------------------------------------

test('the legacy string "topdown" reads as the identity at the atlas tile_px', () => {
  const cam = cameraOf(LEGACY, null)
  assert.equal(cam.kind, 'topdown')
  assert.ok(isTopdown(cam))
  assert.deepEqual(cam.matrix, [16, 0, 0, 16, 0, 0])
  assert.equal(cam.heightPx, 0)
  // MUTATION: return `{ kind: 'topdown' }` with no matrix from the string
  // branch of cameraOf — every projectTile below becomes NaN and every gen 1-3
  // atlas draws nothing. That is the whole reason the string has to be read.
  assert.deepEqual(projectTile(cam, 2, 3), [32, 48])
})

test('an atlas with no camera field at all is still the identity', () => {
  const cam = cameraOf({ tile_px: 16, maps: {} }, null)
  assert.ok(isTopdown(cam))
  assert.deepEqual(projectTile(cam, 1, 1), [16, 16])
})

test('the camera OBJECT carries the matrix and the height scale', () => {
  const cam = cameraOf(PITCHED, null)
  assert.equal(cam.kind, 'pitched')
  assert.equal(isTopdown(cam), false)
  near(tilePxX(cam), 16)
  near(tilePxY(cam), PY)
  near(cam.heightPx, HP)
})

test('tile_px is never read twice — the matrix is the authority for both axes', () => {
  // A deliberately inconsistent atlas: the scalar says 16, the matrix says 8.
  // The matrix wins, because it is the number the renderer actually drew with.
  const cam = cameraOf({ tile_px: 16, camera: { kind: 'pitched', matrix: [8, 0, 0, 7, 0, 0], height_px: 0.5 } }, null)
  assert.equal(tilePxX(cam), 8)
  assert.equal(tilePxY(cam), 7)
  // MUTATION: `tilePxX = () => atlas.tile_px` — the artwork and the route then
  // disagree by a factor of two and nothing raises.
})

// ---------------------------------------------------------------------------
// projectTile / unprojectTile
// ---------------------------------------------------------------------------

test('a pitched camera foreshortens the y axis and leaves x alone', () => {
  const cam = cameraOf(PITCHED, null)
  const [x, y] = projectTile(cam, 3, 4, 0)
  near(x, 48)
  near(y, 4 * PY)
  // MUTATION: `b * tx + d * ty` -> `b * tx + a * ty` (square tiles again) and
  // the route sits 9 px below the road by row 4 alone.
  assert.ok(y < 4 * 16, 'a pitched ground tile must be HIGHER up than a square one')
})

test('altitude lifts a tile UP the picture, by height_px per world unit', () => {
  const cam = cameraOf(PITCHED, null)
  const ground = projectTile(cam, 1, 1, 0)
  const raised = projectTile(cam, 1, 1, 16)              // one tile of altitude
  near(raised[0], ground[0])                              // x is unaffected
  near(ground[1] - raised[1], 16 * HP)
  // MUTATION: drop `- h * cam.heightPx`. Every gen-4 outdoor route then draws
  // 8.2 px — half a tile — below the path it walked, on every map, which is
  // the single biggest placement error this schema exists to remove.
  assert.ok(raised[1] < ground[1])
})

test('unprojectTile inverts projectTile exactly, at altitude too', () => {
  const cam = cameraOf(PITCHED, null)
  for (const [tx, ty, h] of [[0, 0, 0], [3.5, 7.25, 0], [12, 4, 16], [1, 1, -2]]) {
    const [px, py] = projectTile(cam, tx, ty, h)
    const [bx, by] = unprojectTile(cam, px, py, h)
    near(bx, tx, 1e-9)
    near(by, ty, 1e-9)
  }
  // MUTATION: drop `+ h * cam.heightPx` from the inverse — the round trip is
  // still exact at h = 0, so only the altitude cases catch it.
})

test('the inverse survives a degenerate matrix instead of returning Infinity', () => {
  const cam = { kind: 'pitched', matrix: [0, 0, 0, 0, 0, 0], heightPx: 0 }
  assert.deepEqual(unprojectTile(cam, 10, 10), [0, 0])
})

// ---------------------------------------------------------------------------
// layoutSize — what `fit` fits
// ---------------------------------------------------------------------------

test('a layout extent is the PROJECTED one, not the tile count times tile_px', () => {
  const cam = cameraOf(PITCHED, null)
  const [w, h] = layoutSize(cam, { w: 32, h: 32 })
  near(w, 512)
  near(h, 32 * PY)
  assert.ok(h < 512 - 40, 'a 32-tile map is 439 px tall under this pitch, not 512')
  // MUTATION: `layoutSize = (cam, l) => [l.w * tilePxX(cam), l.h * tilePxX(cam)]`
  // — `fit` then under-zooms by 14% and leaves the map small and off centre.
})

test('the identity camera gives back exactly the old extent', () => {
  assert.deepEqual(layoutSize(topdownCamera(16), { w: 10, h: 7 }), [160, 112])
})

// ---------------------------------------------------------------------------
// heights — the run-length grid
// ---------------------------------------------------------------------------

test('a flat map is two integers and decodes to its whole grid', () => {
  const g = heightGrid(PITCHED.maps['418:0'])
  assert.equal(g.w, 4)
  assert.equal(g.h, 3)
  assert.equal(g.data.length, 12)
  assert.ok([...g.data].every((v) => v === 16))
})

test('the grid is indexed from the map ORIGIN, not from route tile zero', () => {
  const m = PITCHED.maps['342:0']
  // map-local (3, 0) is route tile (17, 20) and is the terrace
  assert.equal(heightAt(m, 17, 20), 8)
  assert.equal(heightAt(m, 16, 20), 0)
  assert.equal(heightAt(m, 17, 21), 8)
  // MUTATION: index `g.data[y * g.w + x]` without subtracting `origin`. Gen 4
  // coordinates are GLOBAL — Platinum's y reaches 888 — so every lookup falls
  // outside the grid, returns 0, and the whole feature silently does nothing.
})

test('a tile outside the grid is zero rather than undefined', () => {
  const m = PITCHED.maps['342:0']
  assert.equal(heightAt(m, 0, 0), 0)
  assert.equal(heightAt(m, 99, 99), 0)
})

test('an atlas that ships no heights reads as flat, which is what it is', () => {
  assert.equal(heightGrid(LEGACY.maps['3:0']), null)
  assert.equal(heightAt(LEGACY.maps['3:0'], 1, 1), 0)
  // and flat times height_px 0 is no displacement at all
  const cam = cameraOf(LEGACY, null)
  assert.deepEqual(projectTile(cam, 1, 1, heightAt(LEGACY.maps['3:0'], 1, 1)), [16, 16])
})

test('the decoded grid is cached per entry rather than rebuilt per lookup', () => {
  const m = PITCHED.maps['418:0']
  assert.equal(heightGrid(m), heightGrid(m))
})

test('a truncated run list fills what it can and leaves the rest at zero', () => {
  // Defensive: a hand-edited atlas is a real thing and it must not throw.
  const g = heightGrid({ width: 4, height: 2, heights: [3, 5] })
  assert.equal(g.data.length, 8)
  assert.deepEqual([...g.data], [5, 5, 5, 0, 0, 0, 0, 0])
})

// ---------------------------------------------------------------------------
// png_pad and the layout that has to leave room for it
// ---------------------------------------------------------------------------

test('png_pad defaults to nothing, and a pitched entry declares it', () => {
  assert.deepEqual(pngPad(LEGACY.maps['3:0']), [0, 0, 0, 0])
  assert.deepEqual(pngPad(PITCHED.maps['418:0']), [4, 31, 4, 0])
})

test('padTiles is the widest pad in the atlas, in TILES, rounded up', () => {
  const cam = cameraOf(PITCHED, null)
  // left/right: 4 px over 16 px a tile -> 1. top: 31 px over 13.72 -> 3.
  assert.deepEqual(padTiles(PITCHED, cam), [1, 3, 1, 0])
  // MUTATION: return the pad in PIXELS. The world then reserves 31 TILES of
  // air above itself, `fit` zooms out to hold it, and the map is a stamp in
  // the middle of the panel.
})

test('a topdown atlas reserves nothing, however its entries are shaped', () => {
  const cam = cameraOf(LEGACY, null)
  assert.deepEqual(padTiles(LEGACY, cam), [0, 0, 0, 0])
  // even if a pad leaked into a topdown atlas, it cannot move a topdown layout
  assert.deepEqual(padTiles({ ...LEGACY, maps: { a: { png_pad: [9, 9, 9, 9] } } }, cam),
                   [0, 0, 0, 0])
})

test('the world layout leaves room for the pad above and left of the maps', () => {
  const flat = worldLayout(ROUTE, { ...PITCHED, camera: 'topdown' })
  const tall = worldLayout(ROUTE, PITCHED)
  const [padL, padT] = padTiles(PITCHED, cameraOf(PITCHED, null))
  assert.equal(tall.at['418:0'].x - flat.at['418:0'].x, padL)
  assert.equal(tall.at['418:0'].y - flat.at['418:0'].y, padT)
  assert.equal(tall.w - flat.w, padL + 1)               // + padR
  assert.equal(tall.h - flat.h, padT + 0)               // + padB
  // MUTATION: drop padL/padT from `x0`/`y0` in worldLayout. `fit` then frames
  // the ground rectangle exactly and slices the roofs off the top row of
  // buildings — the control claims to fit the map and does not.
})

test('the layout hands its own camera back, so the drawer cannot pick another', () => {
  assert.equal(worldLayout(ROUTE, PITCHED).cam.kind, 'pitched')
  assert.equal(worldLayout(LEGACY_ROUTE, LEGACY).cam.kind, 'topdown')
})

// ---------------------------------------------------------------------------
// the overlays carry the ground they stand on
// ---------------------------------------------------------------------------

test('a door marker carries the altitude of the tile it sits on', () => {
  const layout = worldLayout(ROUTE, PITCHED)
  const [marker] = markersFor(layout, { maps: { '420:0': {} } }, PITCHED)
  assert.equal(marker.h, 16)
  // MUTATION: `h: 0`. The marker then floats 8.2 px below the door it points
  // at on every gen-4 map, and the route drawn through `place` does not.
})

test('a battle icon carries it too, and reads the terrace it was fought on', () => {
  const layout = worldLayout(ROUTE, PITCHED)
  const [b] = battlesFor(layout, ROUTE)
  assert.equal(b.h, 8)                    // route tile 17,20 is the terrace
})

test('a legacy atlas gives every overlay zero, which is the old behaviour', () => {
  const layout = worldLayout(LEGACY_ROUTE, LEGACY)
  const [marker] = markersFor(layout, { maps: {} }, LEGACY)
  assert.equal(marker.h, 0)
  assert.deepEqual(marker.tile, { x: 2 + 1 - 0, y: 2 + 1 - 0 })   // MARGIN 2, no pad
})

// ---------------------------------------------------------------------------
// the whole placement, the way RouteMap does it
// ---------------------------------------------------------------------------

test('a tile on a terrace is drawn higher than its neighbour on the plain', () => {
  const atlas = PITCHED
  const layout = worldLayout(ROUTE, atlas)
  const cam = layout.cam
  // RouteMap.place, inlined: layout tile + the map-local ground height
  const place = (g, m, x, y) => {
    const p = layout.at[`${g}:${m}`]
    if (!p) return null
    const win = drawWindow(p.m)
    return projectTile(cam, p.x + x - win.x + 0.5, p.y + y - win.y + 0.5,
                       heightAt(p.m, x, y))
  }
  const plain = place(342, 0, 16, 20)
  const terrace = place(342, 0, 17, 20)
  near(terrace[0] - plain[0], 16)                    // one tile east
  near(plain[1] - terrace[1], 8 * HP)                // and 8 units up
  assert.ok(terrace[1] < plain[1])
})

test('the same placement on a legacy atlas is the arithmetic it always was', () => {
  const atlas = LEGACY
  const layout = worldLayout(LEGACY_ROUTE, atlas)
  const cam = layout.cam
  const p = layout.at['3:0']
  const win = drawWindow(p.m)
  const got = projectTile(cam, p.x + 2 - win.x + 0.5, p.y + 1 - win.y + 0.5,
                          heightAt(p.m, 2, 1))
  assert.deepEqual(got, [(2 + 2 + 0.5) * 16, (2 + 1 + 0.5) * 16])
})
