// The curated border list.
//
// Borders were automatic — every map, every side — and it was wrong twice.
// First the tiled block covered the roads out of a town, so Oldale Town read as
// ringed by forest with no way north or east. Then, with the roads clear, the
// one repeated block still looked wrong beside the map's own trees and ran
// straight into the sea, and Andreas landed on: "maybe the answer is just to
// add them manually to the places which feel like they are missing them."
//
// So: nothing by default, and a side that IS named can still never be painted
// across a connection. These tests pin both halves — the second one matters
// most, because it is the guarantee that curating cannot reintroduce the first
// bug by hand.
import test from 'node:test'
import assert from 'node:assert/strict'
import { BORDER_EDGES, borderSides, hasBorder } from '../../src/dashboard/web/src/lib/borders.js'

const MAP = { width: 20, height: 20, file: '0-10.png', border: { file: '0-10-border.png', w: 2, h: 2 },
              open: [{ side: 'up', from: 0, to: 20 }, { side: 'left', from: 0, to: 20 }] }

test('no map has a border until one is named', () => {
  // The default IS the assertion: an automatic border is what looked wrong.
  assert.equal(borderSides('emerald-us', '0:10').size, 0)
  assert.equal(hasBorder('emerald-us', '0:10', MAP), false)
  assert.equal(hasBorder('firered-us', '3:0', MAP), false)
})

test('naming a side turns exactly that side on', () => {
  BORDER_EDGES['emerald-us'] = { '0:10': ['right', 'down'] }
  try {
    const s = borderSides('emerald-us', '0:10')
    assert.deepEqual([...s].sort(), ['down', 'right'])
    assert.equal(hasBorder('emerald-us', '0:10', MAP), true)
    // and only that map, on that cartridge
    assert.equal(borderSides('emerald-us', '0:9').size, 0)
    assert.equal(borderSides('firered-us', '0:10').size, 0)
  } finally { delete BORDER_EDGES['emerald-us'] }
})

test('a map with no border artwork cannot be turned on', () => {
  BORDER_EDGES['platinum-us'] = { '418:0': ['up'] }
  try {
    assert.equal(hasBorder('platinum-us', '418:0', { width: 8, height: 8 }), false,
      'a lattice map has no block to tile')
    assert.equal(hasBorder('platinum-us', '418:0', null), false)
  } finally { delete BORDER_EDGES['platinum-us'] }
})

test('a nonsense side is ignored rather than drawn somewhere arbitrary', () => {
  BORDER_EDGES['firered-us'] = { '3:20': ['north', 'left', ''] }
  try {
    assert.deepEqual([...borderSides('firered-us', '3:20')], ['left'])
  } finally { delete BORDER_EDGES['firered-us'] }
})

test('a malformed entry is empty, not a throw', () => {
  BORDER_EDGES['firered-us'] = { '3:20': 'left' }
  try { assert.equal(borderSides('firered-us', '3:20').size, 0) }
  finally { delete BORDER_EDGES['firered-us'] }
  assert.equal(borderSides(null, null).size, 0)
  assert.equal(borderSides('firered-us', undefined).size, 0)
})
