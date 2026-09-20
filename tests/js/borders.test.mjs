// Which map edges get a fringe of the game's border block, and the one thing
// the curated list must never be able to do.
//
// It was automatic twice and wrong twice: the tiled block covered the roads out
// of a town (Oldale read as ringed by forest with no way north, south or west),
// and at 28 tiles it was a second map rather than a fringe. Andreas, after the
// second: "nope it just didn't work with adding trees as the standard. can't we
// just manually augment the places where 1 row of trees would help enormously?"
//
// So it is a by-hand list, measured in ROWS of the block, defaulting to one.
//
// The load-bearing test is the last one. `open` is punched out in the VIEWER,
// not subtracted here, because a connection usually covers part of an edge
// rather than all of it — so listing a side is not a claim that the whole side
// is drawable, and a future simplification that made it one would put the
// road-walling straight back.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { BORDER_EDGES, borderSides, borderRows, hasBorder }
  from '../../src/dashboard/web/src/lib/borders.js'

const withBorder = { width: 20, height: 20, file: '0-10.png',
                     border: { file: '0-10-border.png', w: 2, h: 2 },
                     open: [{ side: 'up', from: 0, to: 20 }] }
const lattice = { width: 8, height: 8 }

test('a map nobody listed has no fringe', () => {
  // The default IS the assertion: automatic trees are what looked wrong.
  assert.equal(hasBorder('platinum-us', '418:0', withBorder), false)
  assert.equal(borderSides('crystal-us', '3:0', withBorder).size, 0)
})

test('a listed map gets exactly the sides named, one row of block', () => {
  BORDER_EDGES['crystal-us'] = { '3:0': ['left', 'right'] }
  try {
    assert.deepEqual([...borderSides('crystal-us', '3:0', withBorder)].sort(), ['left', 'right'])
    assert.equal(borderRows('crystal-us', '3:0'), 1)
    assert.equal(hasBorder('crystal-us', '3:0', withBorder), true)
    // and only that map, on that cartridge
    assert.equal(borderSides('crystal-us', '3:1', withBorder).size, 0)
    assert.equal(borderSides('platinum-us', '3:0', withBorder).size, 0)
  } finally { delete BORDER_EDGES['crystal-us'] }
})

test('rows are opt-in and never below one', () => {
  BORDER_EDGES['crystal-us'] = {
    a: { sides: ['up'], rows: 3 }, b: { sides: ['up'], rows: 0 },
    c: { sides: ['up'], rows: 2.7 }, d: { sides: ['up'] }, e: { sides: ['up'], rows: 'lots' },
  }
  try {
    assert.equal(borderRows('crystal-us', 'a'), 3)
    assert.equal(borderRows('crystal-us', 'b'), 1, 'zero rows is spelled by deleting the entry')
    assert.equal(borderRows('crystal-us', 'c'), 2)
    assert.equal(borderRows('crystal-us', 'd'), 1)
    assert.equal(borderRows('crystal-us', 'e'), 1)
    assert.equal(borderRows('crystal-us', 'nobody'), 1)
  } finally { delete BORDER_EDGES['crystal-us'] }
})

test('a map with no border block cannot be listed into having one', () => {
  BORDER_EDGES['platinum-us'] = { '418:0': ['up'] }
  try {
    assert.equal(hasBorder('platinum-us', '418:0', lattice), false, 'a lattice map has no block to tile')
    assert.equal(hasBorder('platinum-us', '418:0', null), false)
  } finally { delete BORDER_EDGES['platinum-us'] }
})

test('a nonsense side is ignored and a malformed entry is not a throw', () => {
  BORDER_EDGES['crystal-us'] = { a: ['north', 'left'], b: 'left', c: { rows: 2 } }
  try {
    assert.deepEqual([...borderSides('crystal-us', 'a', withBorder)], ['left'])
    assert.equal(borderSides('crystal-us', 'b', withBorder).size, 0)
    assert.equal(borderSides('crystal-us', 'c', withBorder).size, 0)
  } finally { delete BORDER_EDGES['crystal-us'] }
  assert.equal(borderSides(null, null, withBorder).size, 0)
})

test('a partly open side is still listed — the span is punched out downstream', () => {
  // Viridian City's left edge is 40 tiles and Route 22 covers 24 of them, so
  // 'left' IS listed and only those 24 come out. If this module started
  // dropping partly-open sides, 16 tiles of real fringe would vanish; if the
  // viewer stopped punching, the road would be walled again. The two halves
  // have to stay split, and this is what says so.
  assert.ok(borderSides('firered-us', '3:1',
    { border: { file: '3-1-border.png', w: 2, h: 2 }, open: [{ side: 'left', from: 10, to: 34 }] }).has('left'))
})

test('every side the shipped list names has real ground to draw on', () => {
  // Read against the ACTUAL atlases, not a fixture — the list is hand-written
  // about real geography and the thing that goes wrong is naming a side that is
  // entirely a road. Four such sides were named and correctly left out (Route 1
  // and Route 101's bottoms, Route 2's bottom, Oldale's left); this is what
  // stops the next one going in unnoticed.
  for (const [game, maps] of Object.entries(BORDER_EDGES)) {
    const atlas = JSON.parse(
      readFileSync(new URL(`../../src/dashboard/web/public/maps/${game}/index.json`, import.meta.url), 'utf8'))
    for (const [key, sides] of Object.entries(maps)) {
      const m = atlas.maps[key]
      assert.ok(m, `${game}:${key} is listed and not in the atlas`)
      assert.ok(m.border, `${game}:${key} is listed and has no border block`)
      for (const side of Array.isArray(sides) ? sides : sides.sides) {
        const along = side === 'up' || side === 'down' ? m.width : m.height
        const road = (m.open ?? []).filter((o) => o.side === side)
          .reduce((a, o) => a + (o.to - o.from), 0)
        assert.ok(along - road > 0,
          `${game}:${key} ${side} is ${road}/${along} road — nothing would be drawn`)
      }
    }
  }
})
