// Which edges get a border, and the one thing curating must never be able to do.
//
// Borders went on, off, and on again in one afternoon, for two separate
// reasons: the tiled block covered the roads out of a town (Oldale read as
// ringed by forest with no way north, south or west), and at 28 tiles it was a
// second map rather than a fringe. With both fixed the picture reads, so the
// default is every closed edge and this list is the by-hand exception —
// Andreas: "maybe the answer is just to add them manually to the places which
// feel like they are missing them."
//
// The load-bearing test is the last one. `open` is punched out in the viewer,
// NOT subtracted here, because a connection usually covers part of an edge
// rather than all of it — so `borderSides` returning a side is not a claim that
// the whole side is drawable, and a future simplification that made it one
// would put the road-walling straight back.
import test from 'node:test'
import assert from 'node:assert/strict'
import { BORDER_OFF, borderSides, hasBorder } from '../../src/dashboard/web/src/lib/borders.js'

const withBorder = { width: 20, height: 20, file: '0-10.png', border: { file: '0-10-border.png', w: 2, h: 2 },
                     open: [{ side: 'up', from: 0, to: 20 }, { side: 'left', from: 0, to: 20 }] }
const lattice = { width: 8, height: 8 }

test('by default every side of a map with artwork is drawn', () => {
  assert.deepEqual([...borderSides('emerald-us', '0:10', withBorder)].sort(),
    ['down', 'left', 'right', 'up'])
  assert.equal(hasBorder('emerald-us', '0:10', withBorder), true)
})

test('a map with no border block has nothing to draw', () => {
  assert.equal(hasBorder('platinum-us', '418:0', lattice), false, 'a lattice map has no block to tile')
  assert.equal(hasBorder('platinum-us', '418:0', null), false)
  assert.equal(borderSides('firered-us', '3:0', {}).size, 0)
})

test('naming a side turns exactly that side off', () => {
  BORDER_OFF['emerald-us'] = { '0:19': ['left'] }
  try {
    assert.deepEqual([...borderSides('emerald-us', '0:19', withBorder)].sort(), ['down', 'right', 'up'])
    // and only that map, on that cartridge
    assert.equal(borderSides('emerald-us', '0:10', withBorder).size, 4)
    assert.equal(borderSides('firered-us', '0:19', withBorder).size, 4)
  } finally { delete BORDER_OFF['emerald-us'] }
})

test('`true` suppresses the whole map', () => {
  BORDER_OFF['firered-us'] = { '1:0': true }
  try {
    assert.equal(hasBorder('firered-us', '1:0', withBorder), false)
    assert.equal(borderSides('firered-us', '1:0', withBorder).size, 0)
  } finally { delete BORDER_OFF['firered-us'] }
})

test('a nonsense side suppresses nothing, and a malformed entry is not a throw', () => {
  BORDER_OFF['firered-us'] = { '3:20': ['north'] }
  try { assert.equal(borderSides('firered-us', '3:20', withBorder).size, 4) }
  finally { delete BORDER_OFF['firered-us'] }
  BORDER_OFF['firered-us'] = { '3:20': 'left' }   // a string, not a list
  try { assert.equal(borderSides('firered-us', '3:20', withBorder).size, 4) }
  finally { delete BORDER_OFF['firered-us'] }
  assert.equal(borderSides(null, null, withBorder).size, 4)
})

test('an open edge is still a drawn side — the span is punched out downstream', () => {
  // Viridian City is 48 wide and Route 2 covers 24 of its top edge, so the top
  // side IS drawn and only tiles 12..36 of it come out. If this ever returned
  // "up is off" the other 24 tiles of real border would vanish; if the viewer
  // stopped punching, the road would be walled. The two halves have to stay
  // split, and this is the test that says so.
  const viridian = { width: 48, height: 40, file: '3-1.png', border: { file: '3-1-border.png', w: 2, h: 2 },
                     open: [{ side: 'up', from: 12, to: 36 }] }
  assert.ok(borderSides('firered-us', '3:1', viridian).has('up'))
})
