// Label placement for the board's scatter plots (Andreas 2026-09-16: "way too
// much of the names are overlapped either by each other, other dots or by the
// pareto line — all of these need a push away effect").
//
// The old placement was a per-side vertical column: sort by y, push anything
// closer than one line-height downward. It only ever knew about OTHER LABELS ON
// ITS OWN SIDE, so a label could still be laid straight across a dot or along
// the frontier, and a wide name could run off the plot.
//
// This is a small force relaxation instead. Every label is an axis-aligned box
// that starts at its home position (one gap to the left or right of its dot) and
// is pushed out of anything it overlaps — another label, ANY dot, the frontier
// polyline — while a weak spring pulls it back home. Cooling over a fixed number
// of passes so it settles rather than oscillates. 10–30 labels, so the O(n²)
// pass is free and the result is deterministic (no randomness, no time input):
// the same input always places the same way, which is what makes it testable.
//
// Pure geometry, no DOM: tests/js/labels.test.mjs runs it under plain node.

const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v)

/** Overlap of two 1-D spans; positive when they overlap, negative when apart. */
export function overlap1(a0, a1, b0, b1) {
  return Math.min(a1, b1) - Math.max(a0, b0)
}

/** The point on segment [x1,y1,x2,y2] closest to (px,py). */
export function nearestOnSegment([x1, y1, x2, y2], px, py) {
  const dx = x2 - x1, dy = y2 - y1
  const len2 = dx * dx + dy * dy
  if (len2 === 0) return [x1, y1]
  const t = clamp(((px - x1) * dx + (py - y1) * dy) / len2, 0, 1)
  return [x1 + t * dx, y1 + t * dy]
}

/**
 * Where a leader line from (fx,fy) meets the border of box b — so the line stops
 * at the text instead of running under it.
 */
export function edgePoint(b, fx, fy) {
  const cx = b.x + b.w / 2, cy = b.y + b.h / 2
  const dx = fx - cx, dy = fy - cy
  if (dx === 0 && dy === 0) return [cx, cy]
  const tx = dx === 0 ? Infinity : (b.w / 2) / Math.abs(dx)
  const ty = dy === 0 ? Infinity : (b.h / 2) / Math.abs(dy)
  const t = Math.min(tx, ty, 1)
  return [cx + dx * t, cy + dy * t]
}

const DEFAULTS = {
  gap: 7,          // space between a dot and its label at rest
  pad: 2.5,        // extra clearance demanded between two labels
  dotPad: 3.5,     // extra clearance demanded around a dot
  segPad: 3,       // extra clearance demanded from a segment (the frontier)
  iterations: 260,
  step: 0.5,
  spring: 0.06,    // pull back home; low enough that clearance always wins
  settle: 400,     // hard-separation passes after the relaxation
}

/**
 * Place labels so they clear each other, every dot and every segment.
 *
 * labels:  [{key, ax, ay, w, h, side}] — `ax,ay` is the dot the label belongs
 *          to, `w,h` the text box, `side` ('left'|'right') which side of the dot
 *          it would sit on if nothing were in the way.
 * opts:    {dots: [{x,y,r}], segments: [[x1,y1,x2,y2]], bounds: {x0,y0,x1,y1}, …}
 *
 * Returns Map key → {x, y, w, h, moved} — `x,y` is the box's top-left corner,
 * `moved` true when it had to leave home (the caller draws a leader for those).
 */
export function placeLabels(labels, opts = {}) {
  const o = { ...DEFAULTS, ...opts }
  const dots = o.dots || []
  const segments = o.segments || []
  const bounds = o.bounds || { x0: -1e5, y0: -1e5, x1: 1e5, y1: 1e5 }

  const boxes = labels.map((l) => {
    const hx = l.side === 'left' ? l.ax - o.gap - l.w : l.ax + o.gap
    const hy = l.ay - l.h / 2
    return { key: l.key, w: l.w, h: l.h, ax: l.ax, ay: l.ay, hx, hy, x: hx, y: hy }
  })
  const fit = (b) => {
    b.x = clamp(b.x, bounds.x0, Math.max(bounds.x0, bounds.x1 - b.w))
    b.y = clamp(b.y, bounds.y0, Math.max(bounds.y0, bounds.y1 - b.h))
  }
  boxes.forEach(fit)

  for (let n = 0; n < o.iterations; n++) {
    // Cool from 1 to 0.3: early passes move far enough to escape a pile-up,
    // late ones only trim, so the layout settles instead of ringing.
    const cool = 1 - 0.7 * (n / o.iterations)
    for (const a of boxes) {
      const acx = a.x + a.w / 2, acy = a.y + a.h / 2
      let fx = 0, fy = 0

      // Label ↔ label: separate along whichever axis needs the smaller move,
      // halved because the other label is pushing back on the same pass.
      for (const b of boxes) {
        if (b === a) continue
        const ox = overlap1(a.x, a.x + a.w, b.x, b.x + b.w) + o.pad
        const oy = overlap1(a.y, a.y + a.h, b.y, b.y + b.h) + o.pad
        if (ox <= 0 || oy <= 0) continue
        if (oy <= ox) fy += (acy <= b.y + b.h / 2 ? -oy : oy) * 0.5
        else fx += (acx <= b.x + b.w / 2 ? -ox : ox) * 0.5
      }

      // Label ↔ every dot, its own included — a name must never sit on a marker.
      for (const d of dots) {
        const r = (d.r ?? 3) + o.dotPad
        const ox = overlap1(a.x, a.x + a.w, d.x - r, d.x + r)
        const oy = overlap1(a.y, a.y + a.h, d.y - r, d.y + r)
        if (ox <= 0 || oy <= 0) continue
        if (oy <= ox) fy += acy <= d.y ? -oy : oy
        else fx += acx <= d.x ? -ox : ox
      }

      // Label ↔ the frontier: push along the normal away from the nearest point
      // on the line. `sup` is how far the box reaches in that direction, so the
      // push is exactly the penetration depth.
      for (const s of segments) {
        const [px, py] = nearestOnSegment(s, acx, acy)
        let dx = acx - px, dy = acy - py
        let len = Math.hypot(dx, dy)
        if (len < 1e-6) { dx = 0; dy = -1; len = 1 }   // dead on the line: lift it
        const nx = dx / len, ny = dy / len
        const sup = Math.abs(nx) * a.w / 2 + Math.abs(ny) * a.h / 2 + o.segPad
        if (len < sup) { fx += nx * (sup - len); fy += ny * (sup - len) }
      }

      fx += (a.hx - a.x) * o.spring
      fy += (a.hy - a.y) * o.spring
      a.x += fx * o.step * cool
      a.y += fy * o.step * cool
      fit(a)
    }
  }

  // Phase 2 — hard separation. The relaxation above gets the layout close and
  // decides WHERE each name wants to live; this makes "nothing overlaps" true
  // rather than nearly true. No spring here: only separation, run until it
  // converges. A box already against a bound cannot absorb its half of the
  // move, so whatever it could not take is handed to its partner — otherwise a
  // pile-up at the edge never resolves.
  const move = (b, dx, dy) => {
    const x0 = b.x, y0 = b.y
    b.x += dx; b.y += dy
    fit(b)
    return [dx - (b.x - x0), dy - (b.y - y0)]   // what it could NOT take
  }
  // Dot clearance is re-checked to convergence, not once: pushing a box off one
  // dot can drop it onto the next, and a single sweep would leave it there.
  const offDots = (a) => {
    let worst = 0
    for (const d of dots) {
      const r = (d.r ?? 3) + o.dotPad
      const ox = overlap1(a.x, a.x + a.w, d.x - r, d.x + r)
      const oy = overlap1(a.y, a.y + a.h, d.y - r, d.y + r)
      if (ox <= 0 || oy <= 0) continue
      worst = Math.max(worst, Math.min(ox, oy))
      if (oy <= ox) move(a, 0, a.y + a.h / 2 <= d.y ? -oy : oy)
      else move(a, a.x + a.w / 2 <= d.x ? -ox : ox, 0)
    }
    return worst
  }
  for (let n = 0; n < o.settle; n++) {
    let worst = 0
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j]
        const ox = overlap1(a.x, a.x + a.w, b.x, b.x + b.w) + o.pad
        const oy = overlap1(a.y, a.y + a.h, b.y, b.y + b.h) + o.pad
        if (ox <= 0 || oy <= 0) continue
        worst = Math.max(worst, Math.min(ox, oy))
        const vertical = oy <= ox
        const amt = (vertical ? oy : ox) / 2
        const dir = vertical
          ? (a.y + a.h / 2 <= b.y + b.h / 2 ? -1 : 1)
          : (a.x + a.w / 2 <= b.x + b.w / 2 ? -1 : 1)
        const [rx, ry] = vertical ? move(a, 0, amt * dir) : move(a, amt * dir, 0)
        const rest = amt * -dir - (vertical ? ry : rx)   // partner absorbs what a bound refused
        if (vertical) move(b, 0, rest); else move(b, rest, 0)
      }
    }
    for (const a of boxes) worst = Math.max(worst, offDots(a))
    if (worst <= 0.01) break
  }

  // Phase 3 — repair. Relaxation and settling can still strand a box: two dots
  // closer together than the label is tall hand it back and forth forever, and
  // no small push escapes. Such a box is RELOCATED outright — try a ring of
  // candidate positions around its own dot and take the cheapest that clears
  // everything. Deterministic: fixed directions, fixed distances, boxes handled
  // worst-first.
  const penetration = (b, skip) => {
    let c = 0
    for (const other of boxes) {
      if (other === b || other === skip) continue
      const ox = overlap1(b.x, b.x + b.w, other.x, other.x + other.w) + o.pad
      const oy = overlap1(b.y, b.y + b.h, other.y, other.y + other.h) + o.pad
      if (ox > 0 && oy > 0) c += Math.min(ox, oy)
    }
    for (const d of dots) {
      const r = (d.r ?? 3) + o.dotPad
      const ox = overlap1(b.x, b.x + b.w, d.x - r, d.x + r)
      const oy = overlap1(b.y, b.y + b.h, d.y - r, d.y + r)
      if (ox > 0 && oy > 0) c += Math.min(ox, oy)
    }
    for (const seg of segments) {
      const cx = b.x + b.w / 2, cy = b.y + b.h / 2
      const [px, py] = nearestOnSegment(seg, cx, cy)
      const dx = cx - px, dy = cy - py
      const len = Math.hypot(dx, dy) || 1e-6
      const sup = Math.abs(dx / len) * b.w / 2 + Math.abs(dy / len) * b.h / 2 + o.segPad
      if (len < sup) c += sup - len
    }
    return c
  }
  const RING = Array.from({ length: 16 }, (_, i) => {
    const a = (i / 16) * Math.PI * 2
    return [Math.cos(a), Math.sin(a)]
  })
  const REACH = [o.gap, o.gap + 9, o.gap + 20, o.gap + 34, o.gap + 52, o.gap + 74]
  for (let round = 0; round < 3; round++) {
    const stuck = boxes.map((b) => [b, penetration(b)]).filter(([, c]) => c > 0.01).sort((a, b) => b[1] - a[1])
    if (!stuck.length) break
    for (const [b, cost0] of stuck) {
      let best = { x: b.x, y: b.y, score: cost0 * 4 }
      for (const [ux, uy] of RING) {
        for (const reach of REACH) {
          const sup = Math.abs(ux) * b.w / 2 + Math.abs(uy) * b.h / 2
          const cx = b.ax + ux * (reach + sup), cy = b.ay + uy * (reach + sup)
          const keep = { x: b.x, y: b.y }
          b.x = cx - b.w / 2; b.y = cy - b.h / 2
          fit(b)
          // Cost is penetration first, travel a distant second — a clear spot
          // far away always beats a colliding one nearby.
          const score = penetration(b) * 4 + Math.hypot(b.x - b.hx, b.y - b.hy) * 0.02
          if (score < best.score) best = { x: b.x, y: b.y, score }
          b.x = keep.x; b.y = keep.y
        }
      }
      b.x = best.x; b.y = best.y
    }
  }

  // The box comes back with its size: the caller needs w/h to aim the leader at
  // the right edge (edgePoint), and a Map entry without them silently produced
  // NaN line coordinates.
  const out = new Map()
  for (const b of boxes)
    out.set(b.key, { x: b.x, y: b.y, w: b.w, h: b.h, moved: Math.hypot(b.x - b.hx, b.y - b.hy) > 2 })
  return out
}
