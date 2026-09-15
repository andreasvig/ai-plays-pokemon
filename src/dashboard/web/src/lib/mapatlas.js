// The map atlas: the FireRed artwork the route is drawn on.
//
// `scripts/render_gamemaps.py` renders one PNG per map from pret's own
// tilesets at the game's 16 px per tile and writes `public/maps/index.json`
// beside them (artifacts/game-map-render/plan.md M1-M3). Vite copies `public/`
// to the bundle root, so the files sit at BASE + 'maps/...' on the published
// site exactly as they do locally — the same path the vendor logos use.
//
// Everything here is pure geometry and fetching. The two components that draw
// (RouteMap for the world, InteriorPopup for a building) share `drawRoute` by
// passing their own tile → pixel function, so a route inside Oak's lab is
// drawn by the same code as one across Route 1.
import { BASE } from './static.js'

/** The game's pixels per tile. The atlas asserts it against the walk graph. */
export const TILE = 16

export const mapImageUrl = (file) => `${BASE}maps/${file}`

/**
 * The part of a map worth drawing, in tiles: `{x, y, w, h}`.
 *
 * `trim` marks edge rows and columns that are one flat colour and that nothing
 * can stand on — every FireRed interior ends in a black strip under the door
 * that the game hides behind its exit fade, and drawn honestly it reads as an
 * empty progress bar under the floor. No outdoor map has any, which matters:
 * their edges have to line up with their neighbours in the world frame.
 *
 * Cutting a LEADING row or column moves every tile, so a caller that uses
 * `x`/`y` must subtract them when placing one. `drawSize` is the safe subset
 * for a caller that cannot move anything — trailing only, coordinates intact.
 */
export const drawWindow = (m) => {
  const t = m?.trim ?? {}
  return {
    x: t.left ?? 0,
    y: t.top ?? 0,
    w: Math.max(1, (m?.width ?? 1) - (t.left ?? 0) - (t.right ?? 0)),
    h: Math.max(1, (m?.height ?? 1) - (t.top ?? 0) - (t.bottom ?? 0)),
  }
}

export const drawSize = (m) => [
  Math.max(1, (m?.width ?? 1) - (m?.trim?.right ?? 0)),
  Math.max(1, (m?.height ?? 1) - (m?.trim?.bottom ?? 0)),
]

let trainersPromise = null
/** `{version, pret_sha, trainers: {"<id>": {label, name, class, pic, party}}}`.
 *  Extracted from pret by scripts/extract_trainers.py: a trainer's roster is a
 *  constant of the ROM, so every run already published gets a full hover card
 *  from the trainer id the referee has stored since 2026-09-14 (M16). */
export function loadTrainers() {
  if (!trainersPromise) {
    trainersPromise = fetch(`${BASE}trainers/index.json`, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
  }
  return trainersPromise
}

export const trainerSpriteUrl = (pic) => `${BASE}trainers/${pic}`

let atlasPromise = null
/** `{version, tile_px, pret_sha, maps: {"g:m": {...}}}`, fetched once per page. */
export function loadAtlas() {
  if (!atlasPromise) {
    atlasPromise = fetch(`${BASE}maps/index.json`, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
  }
  return atlasPromise
}

/** Tests and the dev harness swap the fetch and drop the memo. */
export function _resetAtlas() { atlasPromise = null; trainersPromise = null }

const imgCache = new Map()
/** One decoded map image, cached across components and popups. */
export function loadMapImage(file) {
  if (!imgCache.has(file)) {
    imgCache.set(file, new Promise((resolve) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => resolve(null)
      img.src = mapImageUrl(file)
    }))
  }
  return imgCache.get(file)
}

/**
 * The ramp: 0 is the first turn, 1 the last, and every value between is a real
 * mix of its neighbours — five stops, linearly interpolated, no steps
 * (Andreas, 2026-09-15: "can we render the colours as fully gradual changes").
 */
const RAMP = [
  [0.0, [40, 90, 220]],    // blue
  [0.25, [0, 170, 205]],   // cyan
  [0.5, [40, 200, 90]],    // green
  [0.75, [235, 190, 40]],  // amber
  [1.0, [220, 50, 20]],    // red
]

export function turnColour(t) {
  const k = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0))
  let i = 0
  while (i < RAMP.length - 2 && k > RAMP[i + 1][0]) i++
  const [k0, a] = RAMP[i]
  const [k1, b] = RAMP[i + 1]
  const f = k1 === k0 ? 0 : (k - k0) / (k1 - k0)
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)})`
}

/** The ramp as a CSS gradient, so the legend cannot drift from the drawing. */
export const rampCss = () =>
  `linear-gradient(90deg, ${RAMP.map(([k, c]) => `rgb(${c.join(',')}) ${Math.round(k * 100)}%`).join(', ')})`

/**
 * Where along the run each visit falls, 0 → 1.
 *
 * By POSITION IN THE SEQUENCE, not by turn number. Visits are already in
 * chronological order, so this is still "first to last" — but every tile
 * advances the colour by the same amount, which is what makes a cable shade
 * along its own length. Keyed on the turn instead, a turn that walked twenty
 * tiles paints all twenty identically and then steps at the boundary, and the
 * map reads as a handful of flat-coloured runs (Andreas, 2026-09-15: "it
 * doesn't look gradual at all").
 */
export function visitTimes(visits) {
  const n = visits.length
  if (!n) return []
  if (n === 1) return [0]
  return visits.map((_v, i) => i / (n - 1))
}

const MARGIN = 2          // tiles of air around the drawn world
const INSET_GAP = 3       // tiles between the world and a map with no place in it

/**
 * Where each map the run entered sits, in tiles.
 *
 * Outdoor maps take their position from the walk graph's shared world frame.
 * A visited map with no place in it and no interior of its own — in the
 * first-badge region that is Viridian Forest alone — is drawn beside the world
 * (M12: it is a route, too big and too central to hide behind a marker).
 * INTERIORS are not laid out at all: they live in their building's popup (M10).
 */
export function worldLayout(route, atlas) {
  if (!route?.maps || !atlas?.maps) return null
  const entries = Object.keys(route.maps)
    .map((k) => [k, atlas.maps[k]])
    .filter(([, m]) => m && m.type !== 'MAP_TYPE_INDOOR')
  const outdoor = entries.filter(([, m]) => Array.isArray(m.world))
  const insets = entries.filter(([, m]) => !Array.isArray(m.world))
  if (!outdoor.length && !insets.length) return null

  const xs = outdoor.flatMap(([, m]) => [m.world[0], m.world[0] + m.width])
  const ys = outdoor.flatMap(([, m]) => [m.world[1], m.world[1] + m.height])
  const x0 = (outdoor.length ? Math.min(...xs) : 0) - MARGIN
  const y0 = (outdoor.length ? Math.min(...ys) : 0) - MARGIN
  const worldW = outdoor.length ? Math.max(...xs) - Math.min(...xs) + 2 * MARGIN : 0
  const worldH = outdoor.length ? Math.max(...ys) - Math.min(...ys) + 2 * MARGIN : 0

  const at = {}
  for (const [k, m] of outdoor) at[k] = { x: m.world[0] - x0, y: m.world[1] - y0, m, inset: false }
  let colX = worldW ? worldW + INSET_GAP : MARGIN
  let colY = MARGIN
  let colW = 0
  let bottom = 0
  for (const [k, m] of insets) {
    if (colY > MARGIN && colY + m.height > Math.max(worldH, 1)) {
      colX += colW + INSET_GAP
      colY = MARGIN
      colW = 0
    }
    at[k] = { x: colX, y: colY, m, inset: true }
    colY += m.height + INSET_GAP
    colW = Math.max(colW, m.width)
    bottom = Math.max(bottom, colY)
  }
  return {
    at,
    w: Math.max(worldW, insets.length ? colX + colW + MARGIN : 0),
    h: Math.max(worldH, bottom, 1),
    outdoor: outdoor.length,
    insets: insets.length,
    interiors: Object.keys(route.maps).filter((k) => atlas.maps[k]?.type === 'MAP_TYPE_INDOOR'),
  }
}

/** Every door marker on the maps this layout draws, with whether the run went in. */
export function markersFor(layout, route, atlas) {
  if (!layout) return []
  const out = []
  for (const [key, p] of Object.entries(layout.at)) {
    for (const d of atlas.maps[key]?.doors ?? []) {
      const floors = atlas.maps[d.to]?.floors?.length ? atlas.maps[d.to].floors : [d.to]
      out.push({
        key: `${key}:${d.x}:${d.y}`,
        building: d.building,
        name: buildingLabel(d.building),
        floors,
        tile: { x: p.x + d.x, y: p.y + d.y },
        entered: floors.some((f) => route.maps[f]),
      })
    }
  }
  return out
}

/** 'PewterCity_PokemonCenter' → 'Pokémon Center'. The town is on the map already. */
export function buildingLabel(building) {
  const tail = String(building).split('_').slice(1).join(' ') || String(building)
  return tail
    .replace(/([a-z])([A-Z0-9])/g, '$1 $2')
    .replace(/\bPokemon\b/g, 'Pokémon')
    .replace(/\bProfessor Oaks Lab\b/, "Professor Oak's Lab")
    .replace(/\bPlayers House\b/, "Player's house")
    .replace(/\bRivals House\b/, "Rival's house")
    .replace(/\bHouse(\d)\b/, 'House $1')
}

/**
 * Draw a run's route.
 *
 * `place(g, m, x, y)` → `[px, py]` of that tile's centre on this canvas, or
 * null when the caller does not draw that map — which is how the world canvas
 * skips interiors and a popup skips everything but its own floors. Segments
 * with an end the caller cannot place are simply not drawn.
 *
 * An arrowhead is dropped every `arrowEvery` pixels of walking (Andreas,
 * 2026-09-15: "would love for the routes to have arrows such that it would be
 * easier to see at overlaps and which way it is going"). Spacing is measured
 * along the path, not per segment, so a run that crosses its own track leaves
 * two arrows pointing different ways instead of one ambiguous line.
 */
/**
 * Walk the route as a flat list of drawn segments.
 *
 * One entry per tile-to-tile step, in order: `{u, v, t0, t1, fill}` where
 * `u`/`v` are `[g, m, x, y]` and `t0`/`t1` are where that step starts and ends
 * on the run's 0→1 timeline, for its colour.
 *
 * A scripted walk that covered several tiles on one press is expanded through
 * `fills`, so the line follows the ground rather than cutting a chord — and the
 * timeline is split across those sub-steps too. Giving all of them the visit's
 * own pair repeats one short gradient over and over, which is the same flat
 * look as no gradient at all.
 */
function routeSteps(route, times) {
  const visits = route?.visits ?? []
  const out = []
  for (let i = 0; i < visits.length - 1; i++) {
    const fill = route.fills?.[String(i)] || null
    const chain = [visits[i].slice(2, 6), ...(fill || []), visits[i + 1].slice(2, 6)]
    const a = times[i], b = times[i + 1]
    const n = chain.length - 1
    for (let j = 0; j < n; j++) {
      out.push({
        u: chain[j], v: chain[j + 1], fill: !!fill,
        t0: a + ((b - a) * j) / n,
        t1: a + ((b - a) * (j + 1)) / n,
      })
    }
  }
  return out
}

/** A step's identity regardless of which way it was walked. */
const edgeKey = (u, v) => {
  const a = u.join(','), b = v.join(',')
  return a < b ? `${a}|${b}` : `${b}|${a}`
}

/**
 * Lay the route out as parallel lanes — cables on the floor.
 *
 * Andreas, 2026-09-15: "is there a way we could design a system such that lines
 * [are] very small and thin so that they can be rendered as more than one on
 * the tile? I imagine it would look like 1,2,3,4 small cables on the floor."
 *
 * A step that the run walked more than once gets one lane per walk, offset
 * perpendicular to the path and centred on it, so four crossings of a corridor
 * read as four thin cables side by side instead of one line painted over three
 * times. The offset is computed from the edge's CANONICAL direction, not the
 * direction of travel, so a lane sits on the same physical side of the tile
 * whichever way the run was going — otherwise a there-and-back pair would swap
 * sides halfway and cross.
 *
 * Returns each step with `lane` (0-based) and `lanes` (how many that step has).
 */
export function laneSteps(route, { maxLanes = 5, times = null } = {}) {
  const steps = routeSteps(route, times ?? visitTimes(route?.visits ?? []))
  const total = new Map()
  for (const s of steps) {
    const k = edgeKey(s.u, s.v)
    total.set(k, (total.get(k) ?? 0) + 1)
  }
  const seen = new Map()
  for (const s of steps) {
    const k = edgeKey(s.u, s.v)
    const n = seen.get(k) ?? 0
    seen.set(k, n + 1)
    s.lanes = Math.min(total.get(k), maxLanes)
    s.lane = Math.min(n, s.lanes - 1)
    // canonical: lane 0 is always on the same side of the line
    s.flip = s.u.join(',') > s.v.join(',')
  }
  return steps
}

/**
 * Draw a run's route.
 *
 * `place(g, m, x, y)` → `[px, py]` of that tile's centre on this canvas, or
 * null when the caller does not draw that map — which is how the world canvas
 * skips interiors and a popup skips everything but its own floors. Segments
 * with an end the caller cannot place are simply not drawn.
 *
 * Lines are thin and laid in lanes (see `laneSteps`); an arrowhead is dropped
 * every `arrowEvery` pixels of walking, measured along the path rather than per
 * segment, so a run that doubles back leaves two arrows pointing opposite ways.
 */
export function drawRoute(c, route, place, { scale = TILE, width = null, arrowEvery = TILE * 2.5 } = {}) {
  const visits = route?.visits ?? []
  if (visits.length < 2) return
  const lw = width ?? Math.max(1.2, scale * 0.11)
  const gap = Math.max(2, scale * 0.2)          // between neighbouring cables
  const times = visitTimes(visits)
  let since = arrowEvery * 0.5                  // the first arrow lands half a gap in

  const arrow = (from, to, colour) => {
    const dx = to[0] - from[0], dy = to[1] - from[1]
    const len = Math.hypot(dx, dy)
    if (!len) return
    since += len
    if (since < arrowEvery) return
    since = 0
    const ux = dx / len, uy = dy / len
    const h = Math.max(4, scale * 0.34)
    const w = Math.max(4, scale * 0.34)
    const back = [to[0] - ux * h, to[1] - uy * h]
    c.beginPath()
    c.moveTo(to[0], to[1])
    c.lineTo(back[0] - uy * w * 0.5, back[1] + ux * w * 0.5)
    c.lineTo(back[0] + uy * w * 0.5, back[1] - ux * w * 0.5)
    c.closePath()
    // Outlined, or a head only a little wider than its own cable is invisible.
    c.fillStyle = colour
    c.strokeStyle = 'rgba(12,15,20,.75)'
    c.lineWidth = Math.max(0.6, scale * 0.04)
    c.fill()
    c.stroke()
  }

  c.lineCap = 'butt'      // butt, so neighbouring cables do not smear together
  c.lineJoin = 'round'

  for (const s of laneSteps(route, { times })) {
    const pu = place(...s.u), pv = place(...s.v)
    if (!pu || !pv) continue
    const sameMap = s.u[0] === s.v[0] && s.u[1] === s.v[1]
    const adjacent = sameMap && Math.abs(s.u[2] - s.v[2]) + Math.abs(s.u[3] - s.v[3]) <= 2
    const seam = !sameMap && Math.abs(pu[0] - pv[0]) + Math.abs(pu[1] - pv[1]) <= 2 * scale
    const colour = turnColour(s.t0)
    const colourTo = turnColour(s.t1)

    if (!(adjacent || seam)) {
      // A warp or a blackout: mark both ends, never a chord across the map.
      c.lineWidth = Math.max(1.2, lw)
      for (const p of [pu, pv]) {
        c.strokeStyle = 'rgba(15,18,22,.5)'
        c.beginPath(); c.arc(p[0], p[1], scale * 0.34, 0, 6.284); c.stroke()
        c.strokeStyle = colour
        c.beginPath(); c.arc(p[0], p[1], scale * 0.28, 0, 6.284); c.stroke()
      }
      continue
    }

    // offset this lane perpendicular to the CANONICAL direction of the edge
    const dx = pv[0] - pu[0], dy = pv[1] - pu[1]
    const len = Math.hypot(dx, dy) || 1
    const sign = s.flip ? -1 : 1
    const nx = (-dy / len) * sign, ny = (dx / len) * sign
    const off = (s.lane - (s.lanes - 1) / 2) * gap
    const au = [pu[0] + nx * off, pu[1] + ny * off]
    const av = [pv[0] + nx * off, pv[1] + ny * off]

    // A dark hairline under each cable: the artwork below is busy, and a bare
    // thin line on Viridian Forest's canopy disappears.
    c.strokeStyle = 'rgba(12,15,20,.55)'
    c.lineWidth = lw + Math.max(1.4, scale * 0.09)
    c.beginPath(); c.moveTo(au[0], au[1]); c.lineTo(av[0], av[1]); c.stroke()

    let stroke = s.fill ? 'rgba(190,190,255,.9)' : colour
    if (!s.fill && colour !== colourTo && c.createLinearGradient) {
      const g = c.createLinearGradient(au[0], au[1], av[0], av[1])
      g.addColorStop(0, colour)
      g.addColorStop(1, colourTo)
      stroke = g
    }
    c.strokeStyle = stroke
    c.lineWidth = lw
    c.beginPath(); c.moveTo(au[0], au[1]); c.lineTo(av[0], av[1]); c.stroke()
    arrow(au, av, s.fill ? 'rgb(190,190,255)' : colourTo)
  }

  for (const [v, colour] of [[visits[0], '#2850dc'], [visits[visits.length - 1], '#dc3214']]) {
    const p = place(...v.slice(2, 6))
    if (!p) continue
    c.lineWidth = Math.max(1.5, scale * 0.12)
    c.strokeStyle = 'rgba(15,18,22,.6)'
    c.strokeRect(p[0] - scale * 0.6, p[1] - scale * 0.6, scale * 1.2, scale * 1.2)
    c.strokeStyle = colour
    c.strokeRect(p[0] - scale * 0.5, p[1] - scale * 0.5, scale, scale)
  }
}

/**
 * The run's battles, placed on this layout.
 *
 * `route.battles` (src/app/route.py) already carries the tile each fight opened
 * on, its turn span, its kind and — for a trainer — who it was and whether it
 * was won. A battle on a map this layout does not draw (an interior, which
 * lives in its building's popup) is dropped here and drawn there instead.
 */
export function battlesFor(layout, route, { onlyMap = null } = {}) {
  const out = []
  for (const [i, b] of (route?.battles ?? []).entries()) {
    if (!Array.isArray(b.tile)) continue
    const key = `${b.tile[0]}:${b.tile[1]}`
    if (onlyMap) {
      if (key !== onlyMap) continue
      out.push({ ...b, id: `b${i}`, tile: { x: b.tile[2], y: b.tile[3] } })
      continue
    }
    const p = layout?.at[key]
    if (!p) continue
    out.push({ ...b, id: `b${i}`, tile: { x: p.x + b.tile[2], y: p.y + b.tile[3] } })
  }
  return out
}

/** The visit nearest a canvas point, for the hover readout. `null` beyond `within` px. */
export function visitAt(route, place, px, py, within) {
  let best = null
  let bestD = within * within
  const visits = route?.visits ?? []
  for (let i = 0; i < visits.length; i++) {
    const p = place(...visits[i].slice(2, 6))
    if (!p) continue
    const d = (p[0] - px) ** 2 + (p[1] - py) ** 2
    if (d <= bestD) { bestD = d; best = visits[i] }
  }
  return best
}
