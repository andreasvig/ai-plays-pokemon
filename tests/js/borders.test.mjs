// Which map edges get a fringe of the game's border block, and the things the
// list must never be able to do.
//
// It was automatic twice and wrong twice: the tiled block covered the roads out
// of a town (Oldale read as ringed by forest with no way north, south or west),
// and at 28 tiles it was a second map rather than a fringe. Then it was a hand
// list, and Andreas got tired of naming edges: "don't you have a way to analyse
// and guestimate where we need a row to indicate a wall such that I don't have
// to tell you each time?"
//
// So it is MEASURED now — scripts/analyse_border_edges.py writes MEASURED, and
// OVERRIDES is the hand half that wins over it. The three load-bearing tests are
// at the bottom and all three read the REAL atlases, because the failures that
// actually happened were about real geography, not about this module's logic:
//
//   * every side named has ground to draw on (the road-walling failure)
//   * nothing is named on a map that has no place in the world (an interior)
//   * the edges Andreas rejected by name are still not named (the water failure)
//
// `open` is punched out in the VIEWER, not subtracted here, because a connection
// usually covers part of an edge rather than all of it — so listing a side is
// not a claim that the whole side is drawable, and a future simplification that
// made it one would put the road-walling straight back.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { BORDER_EDGES, MEASURED, OVERRIDES, mergeEdges, borderSides, borderRows, hasBorder }
  from '../../src/dashboard/web/src/lib/borders.js'

const withBorder = { width: 20, height: 20, file: '0-10.png',
                     border: { file: '0-10-border.png', w: 2, h: 2 },
                     open: [{ side: 'up', from: 0, to: 20 }] }
const lattice = { width: 8, height: 8 }

/** Swap a game's entries for the duration of one test and put back what was
 *  there. A bare `delete` used to do this, which was safe while the list was
 *  hand-written and every fixture game was absent from it — MEASURED now names
 *  all three real cartridges, so a delete would quietly empty the atlas tests
 *  that run after it and they would pass by having nothing left to check. */
function withEdges(game, entries, body) {
  const had = Object.prototype.hasOwnProperty.call(BORDER_EDGES, game)
  const was = BORDER_EDGES[game]
  BORDER_EDGES[game] = entries
  try { body() } finally {
    if (had) BORDER_EDGES[game] = was
    else delete BORDER_EDGES[game]
  }
}

const atlasOf = (game) => JSON.parse(readFileSync(
  new URL(`../../src/dashboard/web/public/maps/${game}/index.json`, import.meta.url), 'utf8'))

const sidesOf = (e) => (Array.isArray(e) ? e : e?.sides ?? [])

test('a map nobody listed has no fringe', () => {
  // The default IS the assertion: automatic trees everywhere are what looked wrong.
  assert.equal(hasBorder('platinum-us', '418:0', withBorder), false)
  assert.equal(borderSides('firered-us', '999:0', withBorder).size, 0)
})

test('a listed map gets exactly the sides named, one row of block', () => {
  withEdges('soulsilver-us', { '3:0': ['left', 'right'] }, () => {
    assert.deepEqual([...borderSides('soulsilver-us', '3:0', withBorder)].sort(), ['left', 'right'])
    assert.equal(borderRows('soulsilver-us', '3:0'), 1)
    assert.equal(hasBorder('soulsilver-us', '3:0', withBorder), true)
    // and only that map, on that cartridge
    assert.equal(borderSides('soulsilver-us', '3:1', withBorder).size, 0)
    assert.equal(borderSides('platinum-us', '3:0', withBorder).size, 0)
  })
})

test('rows are opt-in and never below one', () => {
  withEdges('soulsilver-us', {
    a: { sides: ['up'], rows: 3 }, b: { sides: ['up'], rows: 0 },
    c: { sides: ['up'], rows: 2.7 }, d: { sides: ['up'] }, e: { sides: ['up'], rows: 'lots' },
  }, () => {
    assert.equal(borderRows('soulsilver-us', 'a'), 3)
    assert.equal(borderRows('soulsilver-us', 'b'), 1, 'zero rows is spelled by deleting the entry')
    assert.equal(borderRows('soulsilver-us', 'c'), 2)
    assert.equal(borderRows('soulsilver-us', 'd'), 1)
    assert.equal(borderRows('soulsilver-us', 'e'), 1)
    assert.equal(borderRows('soulsilver-us', 'nobody'), 1)
  })
})

test('a map with no border block cannot be listed into having one', () => {
  withEdges('platinum-us', { '418:0': ['up'] }, () => {
    assert.equal(hasBorder('platinum-us', '418:0', lattice), false, 'a lattice map has no block')
    assert.equal(hasBorder('platinum-us', '418:0', null), false)
  })
})

test('a nonsense side is ignored and a malformed entry is not a throw', () => {
  withEdges('soulsilver-us', { a: ['north', 'left'], b: 'left', c: { rows: 2 } }, () => {
    assert.deepEqual([...borderSides('soulsilver-us', 'a', withBorder)], ['left'])
    assert.equal(borderSides('soulsilver-us', 'b', withBorder).size, 0)
    assert.equal(borderSides('soulsilver-us', 'c', withBorder).size, 0)
  })
  assert.equal(borderSides(null, null, withBorder).size, 0)
})

test('a partly open side is still listed — the span is punched out downstream', () => {
  // Viridian City's left edge is 40 tiles and Route 22 covers 24 of them, so a
  // listed 'left' draws the other 16 and the road stays clear. If this module
  // started dropping partly-open sides, real fringe would vanish; if the viewer
  // stopped punching, the road would be walled again. The two halves have to
  // stay split, and this is what says so.
  withEdges('soulsilver-us', { '3:1': ['left'] }, () => {
    assert.ok(borderSides('soulsilver-us', '3:1',
      { border: { file: 'b.png', w: 2, h: 2 }, open: [{ side: 'left', from: 10, to: 34 }] })
      .has('left'))
  })
})

// ---------------------------------------------------------- the measured half

test('an override replaces the measured entry for its map, and [] switches it off', () => {
  // The whole point of keeping a hand half: Andreas has reversed automatic
  // behaviour here twice, so a measured side has to be switchable without
  // touching the generated block the script rewrites. This drives the module's
  // own mergeEdges — a copy of it here would agree with itself forever.
  const measured = { g: { a: ['up'], b: ['left', 'right'] }, h: { c: ['down'] } }
  assert.deepEqual(mergeEdges(measured, { g: { a: [] } }).g, { b: ['left', 'right'] },
    '[] removes the measured map')
  assert.deepEqual(mergeEdges(measured, { g: { b: ['down'] } }).g, { a: ['up'], b: ['down'] },
    'a list replaces the measured sides, it does not merge with them')
  assert.deepEqual(mergeEdges(measured, { g: { d: ['up'] } }).g.d, ['up'],
    'an override may name a map the measurement never listed')
  assert.deepEqual(mergeEdges(measured, {}).h, { c: ['down'] }, 'untouched games survive')
  assert.deepEqual(mergeEdges({}, { z: { a: ['up'] } }), { z: { a: ['up'] } })
  // and the shipped object really is those two halves, through that function
  assert.deepEqual(BORDER_EDGES, mergeEdges(MEASURED, OVERRIDES))
})

test('the generated half is not empty and names only atlases we ship', () => {
  // A --write that silently produced {} would turn every fringe off and every
  // other test here would still pass, because "nobody listed" is the default.
  assert.ok(Object.keys(MEASURED).length >= 3, 'MEASURED covers every rendered game')
  for (const [game, maps] of Object.entries(MEASURED)) {
    assert.ok(Object.keys(maps).length > 0, `${game} is in MEASURED with no maps`)
    assert.doesNotThrow(() => atlasOf(game), `${game} has no atlas`)
  }
})

test('every side the shipped list names has real ground to draw on', () => {
  // Read against the ACTUAL atlases, not a fixture — the list is about real
  // geography and the thing that goes wrong is naming a side that is entirely a
  // road. Route 1's and Route 101's bottoms, Route 2's bottom and Oldale's left
  // are each 100% connection; this is what stops one of them going in unnoticed.
  let checked = 0
  for (const [game, maps] of Object.entries(BORDER_EDGES)) {
    const atlas = atlasOf(game)
    for (const [key, sides] of Object.entries(maps)) {
      const m = atlas.maps[key]
      assert.ok(m, `${game}:${key} is listed and not in the atlas`)
      assert.ok(m.border, `${game}:${key} is listed and has no border block`)
      for (const side of sidesOf(sides)) {
        const along = side === 'up' || side === 'down' ? m.width : m.height
        const road = (m.open ?? []).filter((o) => o.side === side)
          .reduce((a, o) => a + (o.to - o.from), 0)
        assert.ok(along - road > 0,
          `${game}:${key} ${side} is ${road}/${along} road — nothing would be drawn`)
        checked += 1
      }
    }
  }
  assert.ok(checked >= 25, `only ${checked} sides checked — the list emptied out`)
})

test('nothing is listed on a map that has no place in the world frame', () => {
  // An interior, a gate-house cluster or a lattice entry is drawn somewhere
  // other than the world grid, so a fringe on one is drawn against nothing.
  // `worldLayout` filters on `popup ?? indoor` and on `world` being an array,
  // and this asserts the list agrees with it.
  for (const [game, maps] of Object.entries(BORDER_EDGES)) {
    const atlas = atlasOf(game)
    for (const key of Object.keys(maps)) {
      const m = atlas.maps[key]
      assert.equal(m.popup ?? m.indoor ?? false, false, `${game}:${key} is not outdoors`)
      assert.ok(Array.isArray(m.world), `${game}:${key} has no world position`)
      assert.ok(m.file, `${game}:${key} is a lattice entry with no artwork`)
    }
  }
})

test('the edges Andreas rejected by name are still not drawn', () => {
  // "i dont want them popping up randomly at water edges or cave edges." Every
  // row here is a side he looked at and said no to, or a block that is open sea
  // and so cannot read as a wall at all. They are the negative class the rule
  // has to keep rejecting, and a happy-path list of the sides he DID ask for
  // would never have caught the version that put trees on Route 104's beach.
  const banned = [
    ['emerald-us', '0:19', 'left', "Route 104's sea side — the row of trees he rejected"],
    ['emerald-us', '0:18', 'up', 'Route 103 — sea and cliff along the whole north edge'],
    ['firered-us', '3:39', 'left', 'Route 21 — the border block IS open water'],
    ['firered-us', '3:39', 'right', 'Route 21 — the border block IS open water'],
    ['crystal-us', '26:3', 'up', 'Cherrygrove — the border block IS open water'],
    ['crystal-us', '26:3', 'down', 'Cherrygrove — the border block IS open water'],
    ['crystal-us', '26:3', 'left', 'Cherrygrove — the border block IS open water'],
  ]
  for (const [game, key, side, why] of banned) {
    const m = atlasOf(game).maps[key]
    assert.ok(m, `${game}:${key} left the atlas — this guard is now vacuous`)
    assert.ok(!borderSides(game, key, m).has(side), `${game}:${key} ${side}: ${why}`)
  }
  assert.equal(banned.length, 7, 'the rejected list shrank — say why in the diff')

  // Platinum's atlas is a collision SILHOUETTE, not a tile render: its border
  // block is 4x4 pixels of pure black, so a fringe of it is a fringe of void.
  // Guarded separately because that atlas is still being built and may move;
  // the seven rows above are the part that must never go vacuous.
  let plat
  try { plat = atlasOf('platinum-us') } catch { plat = null }
  if (plat) {
    for (const [key, m] of Object.entries(plat.maps)) {
      assert.equal(borderSides('platinum-us', key, m).size, 0,
        `platinum-us:${key} — a black block is not a wall`)
    }
  }
})

test('the two long closed sides of Crystal Route 30 ARE drawn', () => {
  // The other direction, and the one he asked for last: 26:1 is open only up
  // and down, so both 54-tile flanks are closed wall and a fringe belongs on
  // them. A rule that got safe by drawing nothing would pass every guard above.
  const m = atlasOf('crystal-us').maps['26:1']
  assert.deepEqual([...borderSides('crystal-us', '26:1', m)].sort(), ['left', 'right'])
})
