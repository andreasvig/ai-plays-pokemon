// Unit tests for the scatter plots' label placement:
//   src/dashboard/web/src/lib/labels.js
//
// Run directly (`node tests/js/labels.test.mjs`) or through tests/test_live_feed_js.py,
// which globs every suite in this directory. Pure geometry, no DOM.
//
// The claim under test is what Andreas asked for on 2026-09-16: a name must not
// be covered by another name, by any dot, or by the frontier line, and when it
// has to move it must say so (`moved`) so the chart draws a leader.
import test from 'node:test'
import assert from 'node:assert/strict'
import { placeLabels, edgePoint, overlap1, nearestOnSegment } from '../../src/dashboard/web/src/lib/labels.js'

const BOUNDS = { x0: 0, y0: 0, x1: 600, y1: 400 }
const box = (b) => ({ x0: b.x, y0: b.y, x1: b.x + b.w, y1: b.y + b.h })
const boxesOverlap = (a, b) =>
  overlap1(a.x, a.x + a.w, b.x, b.x + b.w) > 0 && overlap1(a.y, a.y + a.h, b.y, b.y + b.h) > 0
const withSize = (placed, l) => ({ ...placed, w: l.w, h: l.h })

/** Distance from a box to a segment, 0 when they touch or cross. */
function boxSegDistance(b, seg) {
  // Sample the segment densely: enough for a test, and it cannot be fooled by
  // an endpoint sitting just outside the box.
  let best = Infinity
  for (let t = 0; t <= 1; t += 1 / 400) {
    const px = seg[0] + (seg[2] - seg[0]) * t
    const py = seg[1] + (seg[3] - seg[1]) * t
    const dx = Math.max(b.x - px, 0, px - (b.x + b.w))
    const dy = Math.max(b.y - py, 0, py - (b.y + b.h))
    best = Math.min(best, Math.hypot(dx, dy))
  }
  return best
}


// The published cost plot's geometry (760x410 viewBox, ML 60 / MR 118 / MT 26):
// a tight cluster low-left, four points on the frontier, and the long
// gemini-3.5-flash-lite(high) name pinned at the left edge.
const REAL_BOARD = [
  ['gpt-6-astra(low)', 150, 120], ['gemini-3.8-flash(medium)', 196, 150],
  ['claude-fable-5.1(medium)', 470, 128], ['gemini-3.8-flash(low)', 205, 158],
  ['claude-opus-5(high)', 600, 210], ['glm-5.3-flash(max)', 250, 236],
  ['deepseek-v4.1-flash(high)', 300, 240], ['grok-4.6(high)', 520, 232],
  ['gpt-5.6-sol(high)', 430, 244], ['gpt-5.6-luna(high)', 440, 250],
  ['muse-spark-1.3(high)', 330, 258], ['qwen3.8-flash(thinking)', 360, 262],
  ['gemini-3.5-flash-lite(high)', 118, 300],
]
const REAL_FRONTIER = [[118, 300, 150, 120], [150, 120, 470, 128]]
const CHAR = 9 * 0.6                                   // monospace at 9px
const boardLabels = (raw) => raw.map(([label, x, y]) => ({
  key: label, ax: x, ay: y, w: label.length * CHAR, h: 12,
  side: x > 60 + (760 - 60 - 118) * 0.6 ? 'left' : 'right',
}))

test('overlap1 is positive only when the spans actually overlap', () => {
  assert.equal(overlap1(0, 10, 5, 20), 5)
  assert.equal(overlap1(0, 10, 10, 20), 0)
  assert.ok(overlap1(0, 10, 12, 20) < 0)
})

test('nearestOnSegment clamps to the endpoints', () => {
  assert.deepEqual(nearestOnSegment([0, 0, 10, 0], 5, 5), [5, 0])
  assert.deepEqual(nearestOnSegment([0, 0, 10, 0], -8, 3), [0, 0])
  assert.deepEqual(nearestOnSegment([0, 0, 10, 0], 40, 3), [10, 0])
  assert.deepEqual(nearestOnSegment([4, 4, 4, 4], 9, 9), [4, 4])   // degenerate
})

test('edgePoint lands on the border of the box, on the side the dot is', () => {
  const b = { x: 100, y: 100, w: 40, h: 10 }
  const [x, y] = edgePoint(b, 0, 105)               // dot far to the left
  assert.equal(x, 100)                              // left edge
  assert.equal(y, 105)
  const [x2] = edgePoint(b, 400, 105)               // dot far to the right
  assert.equal(x2, 140)                             // right edge
})

test('a label that has nothing in its way stays home and draws no leader', () => {
  const l = { key: 'a', ax: 100, ay: 100, w: 60, h: 12, side: 'right' }
  const got = placeLabels([l], { bounds: BOUNDS, dots: [{ x: 100, y: 100, r: 3 }] })
  const p = got.get('a')
  assert.equal(p.moved, false)
  assert.ok(Math.abs(p.x - 107) < 0.001, `home x, got ${p.x}`)     // ax + gap
  assert.ok(Math.abs(p.y - 94) < 0.001, `home y, got ${p.y}`)      // ay - h/2
})

test('labels on dots stacked at the same point end up clear of each other', () => {
  // Five dots within a couple of pixels — the case the old column-push was
  // written for, and the one that has to keep working.
  const labels = Array.from({ length: 5 }, (_, i) => ({
    key: `m${i}`, ax: 300, ay: 200 + i * 1.5, w: 90, h: 12, side: 'right',
  }))
  const got = placeLabels(labels, { bounds: BOUNDS, dots: labels.map((l) => ({ x: l.ax, y: l.ay, r: 3.2 })) })
  const placed = labels.map((l) => withSize(got.get(l.key), l))
  for (let i = 0; i < placed.length; i++)
    for (let j = i + 1; j < placed.length; j++)
      assert.ok(!boxesOverlap(placed[i], placed[j]), `${labels[i].key} overlaps ${labels[j].key}`)
  // Five 12-high boxes cannot all sit on the same baseline, so at most one can
  // still be at home — the rest travelled and get a leader.
  assert.ok(placed.filter((p) => !p.moved).length <= 1, 'at most one stacked label stays home')
})

test('a label is pushed off a foreign dot, not just off other labels', () => {
  // One label, and a dot parked exactly where its home position is. With no
  // other label in play, only the dot can move it.
  const l = { key: 'a', ax: 100, ay: 100, w: 60, h: 12, side: 'right' }
  const blocker = { x: 130, y: 100, r: 4 }
  const got = placeLabels([l], { bounds: BOUNDS, dots: [{ x: 100, y: 100, r: 3.2 }, blocker] })
  const p = withSize(got.get('a'), l)
  assert.ok(p.moved, 'it moved')
  assert.ok(!boxesOverlap(p, { x: blocker.x - 4, y: blocker.y - 4, w: 8, h: 8 }), 'clear of the blocking dot')
})

test('a label is pushed off the frontier line', () => {
  // The frontier runs diagonally straight through the label's home box.
  const l = { key: 'a', ax: 100, ay: 100, w: 80, h: 12, side: 'right' }
  const seg = [90, 90, 220, 120]
  const got = placeLabels([l], { bounds: BOUNDS, dots: [{ x: 100, y: 100, r: 3.2 }], segments: [seg] })
  const p = withSize(got.get('a'), l)
  assert.ok(p.moved, 'it moved')
  assert.ok(boxSegDistance(p, seg) > 1, `clear of the frontier, got ${boxSegDistance(p, seg).toFixed(2)}`)
})

test('every label stays inside the bounds it was given', () => {
  // Twelve wide names crowded into the right-hand third: the placement must
  // spread them without pushing any of them off the plot.
  const labels = Array.from({ length: 12 }, (_, i) => ({
    key: `m${i}`, ax: 520 + (i % 3) * 8, ay: 60 + i * 4, w: 130, h: 12, side: 'left',
  }))
  const got = placeLabels(labels, { bounds: BOUNDS, dots: labels.map((l) => ({ x: l.ax, y: l.ay, r: 3.2 })) })
  for (const l of labels) {
    const p = got.get(l.key)
    assert.ok(p.x >= BOUNDS.x0 - 0.001 && p.x + l.w <= BOUNDS.x1 + 0.001, `${l.key} x in bounds (${p.x})`)
    assert.ok(p.y >= BOUNDS.y0 - 0.001 && p.y + l.h <= BOUNDS.y1 + 0.001, `${l.key} y in bounds (${p.y})`)
  }
})

test('placement is deterministic — the same input places the same way twice', () => {
  const labels = Array.from({ length: 8 }, (_, i) => ({
    key: `m${i}`, ax: 200 + i * 3, ay: 150 + i * 2, w: 100, h: 12, side: i % 2 ? 'left' : 'right',
  }))
  const opts = { bounds: BOUNDS, dots: labels.map((l) => ({ x: l.ax, y: l.ay, r: 3.2 })), segments: [[180, 140, 300, 190]] }
  const a = placeLabels(labels, opts), b = placeLabels(labels, opts)
  for (const l of labels) assert.deepEqual(a.get(l.key), b.get(l.key))
})

// The relaxation (phases 1-2) is not needed for CORRECTNESS — the ring search
// alone lands every label somewhere legal, and every no-overlap assertion above
// still passes with `iterations: 0, settle: 0`. What it buys is proximity: a
// name that drifts a little stays readable as this dot's name, and one that gets
// teleported to a ring position does not. So the relaxation is pinned by the one
// thing it actually changes — how far the names have to travel.
test('the relaxation keeps names closer to their dots than the ring search alone', () => {
  const raw = REAL_BOARD
  const labels = boardLabels(raw)
  const dots = labels.map((l) => ({ x: l.ax, y: l.ay, r: 3.2 }))
  const opts = { dots, segments: REAL_FRONTIER, bounds: { x0: 62, y0: 28, x1: 757, y1: 356 } }
  const travel = (got) => labels.reduce((a, l) => {
    const p = got.get(l.key)
    const hx = l.side === 'left' ? l.ax - 7 - l.w : l.ax + 7
    return a + Math.hypot(p.x - hx, p.y - (l.ay - l.h / 2))
  }, 0) / labels.length
  const full = travel(placeLabels(labels, opts))
  const ringOnly = travel(placeLabels(labels, { ...opts, iterations: 0, settle: 0 }))
  assert.ok(full < ringOnly * 0.9, `relaxed ${full.toFixed(1)} should beat ring-only ${ringOnly.toFixed(1)} by 10%+`)
})

test('the real board shape: 13 names, a frontier, nothing left overlapping', () => {
  // Coordinates taken from the published cost plot's geometry (760×410 viewBox,
  // ML 60 / MR 118 / MT 26): a tight cluster low-left, four on the frontier, and
  // the long gemini-3.5-flash-lite(high) name at the right edge.
  const labels = boardLabels(REAL_BOARD)
  const dots = labels.map((l) => ({ x: l.ax, y: l.ay, r: 3.2 }))
  const segments = REAL_FRONTIER
  const got = placeLabels(labels, { dots, segments, bounds: { x0: 62, y0: 28, x1: 757, y1: 356 } })

  const placed = labels.map((l) => withSize(got.get(l.key), l))
  let pairs = 0
  for (let i = 0; i < placed.length; i++)
    for (let j = i + 1; j < placed.length; j++)
      if (boxesOverlap(placed[i], placed[j])) pairs++
  assert.equal(pairs, 0, `${pairs} label pairs still overlap`)

  for (const p of placed)
    for (const d of dots)
      assert.ok(!(d.x > p.x - 1 && d.x < p.x + p.w + 1 && d.y > p.y - 1 && d.y < p.y + p.h + 1),
        `a dot sits inside ${p.key ?? ''} at ${d.x},${d.y}`)

  for (const p of placed)
    for (const s of segments)
      assert.ok(boxSegDistance(p, s) > 0.5, 'a name still sits on the frontier')

  // And the ones that never had to move report it, so no leader is drawn for them.
  assert.ok(placed.some((p) => !p.moved), 'some labels stay home')
})
