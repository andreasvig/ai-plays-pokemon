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
  // Viridian City is 48 wide and Route 2 covers 24 of its top edge, so 'up' IS
  // listed and only tiles 12..36 of it come out. If this module started
  // dropping the side, 24 tiles of real fringe would vanish; if the viewer
  // stopped punching, the road would be walled again. The two halves have to
  // stay split, and this is what says so.
  assert.ok(borderSides('firered-us', '3:1',
    { border: { file: '3-1-border.png', w: 2, h: 2 }, open: [{ side: 'up', from: 12, to: 36 }] }).has('up'))
})

test('the shipped list names only sides that are not fully a road', () => {
  // A cheap guard on the hand-written list itself: Route 2 runs the full width
  // of its own top and bottom edges into two cities, so listing either would be
  // 24 tiles of border with 24 tiles punched straight back out of it.
  const r2 = BORDER_EDGES['firered-us']['3:20']
  assert.deepEqual([...r2].sort(), ['left', 'right'])
  assert.deepEqual([...BORDER_EDGES['emerald-us']['0:10']], ['right'], 'Oldale leaves three ways')
})
