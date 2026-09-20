// The coordinate lattice: what a map looks like before anyone has rendered its
// artwork.
//
// Six of the seven games have a route and no pictures. Until 2026-09-20 that
// rendered as NOTHING — `RouteMap.svelte` guarded on `worldLayout(...)` being
// truthy, and with no atlas entry for any of the run's maps it was null, so the
// "Where it walked" section opened onto an empty frame with no error and no
// message. The data was all there and the page said nothing.
//
// So the fallback is structural rather than per-game: build a synthetic atlas
// out of the route itself, merge the real one over the top PER MAP, and draw a
// grid wherever a map has no `file`. Every game works the day the seam lands,
// each artwork drop is an upgrade to something already correct, and a NEW run
// that walks into an unrendered map degrades to a grid rather than vanishing.
//
// What the lattice must never do is pretend. A grid is drawn at the extent of
// the ground the run actually covered, not at the map's real size, because we
// do not know the map's real size — that is the one thing only the artwork
// source can tell us.

/** Tiles of air around a lattice map, so its route does not sit on the border. */
const PAD = 1

/**
 * A synthetic schema-2 atlas derived from a route alone — no network, no
 * decomp, no walk graph.
 *
 * Each map gets a bounding box over every visit on it, plus the same frame
 * derivation the Python side uses: two maps whose coordinates are within one
 * tile of each other across a map change share a coordinate plane. That is what
 * places gen 4/5 outdoor zones correctly (their coordinates are global and
 * contiguous) while leaving gen 1-3 maps, whose seams are coordinate
 * DIScontinuities, unplaced and therefore in the inset column.
 */
export function latticeAtlas(route) {
  if (!route?.visits?.length) return null
  const box = new Map()
  for (const v of route.visits) {
    const key = `${v[2]}:${v[3]}`
    const b = box.get(key)
    if (!b) box.set(key, [v[4], v[5], v[4], v[5]])
    else {
      if (v[4] < b[0]) b[0] = v[4]
      if (v[5] < b[1]) b[1] = v[5]
      if (v[4] > b[2]) b[2] = v[4]
      if (v[5] > b[3]) b[3] = v[5]
    }
  }

  const frames = sharedFrames(route)
  const maps = {}
  for (const [key, b] of box) {
    const shared = frames.get(key)
    maps[key] = {
      name: null,
      // Extent of ground walked, not the map's real size. `origin` is the
      // tile the drawn rectangle starts at, so tile coordinates still land.
      origin: shared ? [0, 0] : [b[0] - PAD, b[1] - PAD],
      width: shared ? b[2] + PAD + 1 : b[2] - b[0] + 1 + 2 * PAD,
      height: shared ? b[3] + PAD + 1 : b[3] - b[1] + 1 + 2 * PAD,
      indoor: false,
      synthetic: true,
      // A map sharing a coordinate plane IS placed — at the origin of that
      // plane, because its tile coordinates are already world coordinates.
      ...(shared ? { frame: shared, world: [0, 0] } : {}),
    }
  }
  return { schema: 2, game: route.game ?? null, synthetic: true, tile_px: route.tile_px ?? 16, maps }
}

/**
 * Map keys that provably share one coordinate plane, as `key -> frame name`.
 *
 * The proof is the same one `src/app/route.py` and the world-frame derivation
 * use: if two consecutive visits cross a map change and the coordinates moved
 * by one tile or less, the two maps are measured in the same numbers. A gen 1-3
 * seam cannot produce that — Pallet Town's y=0 meets Route 1's y=39 — so this
 * yields nothing on a GBA cartridge and the whole region on a DS one.
 */
export function sharedFrames(route) {
  const parent = new Map()
  const find = (k) => {
    while (parent.get(k) !== k) { parent.set(k, parent.get(parent.get(k))); k = parent.get(k) }
    return k
  }
  const union = (a, b) => {
    if (!parent.has(a)) parent.set(a, a)
    if (!parent.has(b)) parent.set(b, b)
    const ra = find(a), rb = find(b)
    if (ra !== rb) parent.set(ra, rb)
  }
  const visits = route?.visits ?? []
  for (let i = 1; i < visits.length; i++) {
    const a = visits[i - 1], b = visits[i]
    if (a[2] === b[2] && a[3] === b[3]) continue
    if (Math.abs(a[4] - b[4]) + Math.abs(a[5] - b[5]) <= 1) {
      union(`${a[2]}:${a[3]}`, `${b[2]}:${b[3]}`)
    }
  }
  const out = new Map()
  for (const k of parent.keys()) out.set(k, `frame-${find(k)}`)
  return out
}

/**
 * The real atlas laid over the synthetic one, per MAP rather than per game.
 *
 * That is what lets a partly-rendered game draw four PNGs and three grids in
 * one frame, instead of the whole game flipping from lattice to artwork at
 * once. A real entry always wins; a map the real atlas has never heard of keeps
 * its synthetic entry rather than disappearing.
 */
export function mergeAtlas(real, synthetic) {
  if (!real?.maps) return synthetic
  if (!synthetic?.maps) return real
  return { ...real, maps: { ...synthetic.maps, ...real.maps } }
}

/** True when this atlas entry has no artwork and must be drawn as a grid. */
export const isLattice = (m) => !m?.file

/**
 * Draw one map as a coordinate grid.
 *
 * `place(x, y)` converts a tile on this map to a canvas pixel, exactly as the
 * artwork path uses it, so the route drawn on top needs no special case.
 */
export function drawLattice(c, entry, x0, y0, tilePx) {
  const w = entry.width * tilePx
  const h = entry.height * tilePx
  c.save()
  c.fillStyle = 'rgba(28, 32, 40, .92)'
  c.fillRect(x0, y0, w, h)
  c.strokeStyle = 'rgba(120, 132, 155, .22)'
  c.lineWidth = 1
  c.beginPath()
  for (let t = 0; t <= entry.width; t++) {
    c.moveTo(x0 + t * tilePx + 0.5, y0)
    c.lineTo(x0 + t * tilePx + 0.5, y0 + h)
  }
  for (let t = 0; t <= entry.height; t++) {
    c.moveTo(x0, y0 + t * tilePx + 0.5)
    c.lineTo(x0 + w, y0 + t * tilePx + 0.5)
  }
  c.stroke()
  c.strokeStyle = 'rgba(150, 162, 185, .5)'
  c.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, h - 1)
  c.restore()
}
