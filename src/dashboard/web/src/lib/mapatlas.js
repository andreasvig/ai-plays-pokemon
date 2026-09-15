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
export function _resetAtlas() { atlasPromise = null }

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

/** 0 → blue (first turn), .5 → green, 1 → red (last). The ramp the PNG uses. */
export function turnColour(t) {
  const k = Math.max(0, Math.min(1, t))
  return k < 0.5
    ? `rgb(40,${Math.round(80 + 280 * k)},${Math.round(220 - 280 * k)})`
    : `rgb(${Math.round(360 * (k - 0.5))},${Math.round(220 - 340 * (k - 0.5))},${Math.round(80 - 120 * (k - 0.5))})`
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
 */
export function drawRoute(c, route, place, { scale = TILE, width = null } = {}) {
  const visits = route?.visits ?? []
  if (visits.length < 2) return
  const lw = width ?? Math.max(2, scale * 0.22)
  const t0 = visits[0][0]
  const t1 = Math.max(visits[visits.length - 1][0], t0 + 1)
  c.lineCap = 'round'
  c.lineJoin = 'round'
  for (let i = 0; i < visits.length - 1; i++) {
    const a = visits[i], b = visits[i + 1]
    const colour = turnColour((a[0] - t0) / (t1 - t0))
    // A scripted walk moved several tiles on one press; `fills` carries the
    // tiles between, so the line follows the ground rather than cutting a chord.
    const fill = route.fills?.[String(i)] || null
    const chain = [a.slice(2, 6), ...(fill || []), b.slice(2, 6)]
    for (let j = 0; j < chain.length - 1; j++) {
      const pu = place(...chain[j]), pv = place(...chain[j + 1])
      if (!pu || !pv) continue
      const u = chain[j], v = chain[j + 1]
      const sameMap = u[0] === v[0] && u[1] === v[1]
      const adjacent = sameMap && Math.abs(u[2] - v[2]) + Math.abs(u[3] - v[3]) <= 2
      const seam = !sameMap && Math.abs(pu[0] - pv[0]) + Math.abs(pu[1] - pv[1]) <= 2 * scale
      if (adjacent || seam) {
        // A dark halo first: the artwork underneath is busy, and a bare line
        // on Viridian Forest's canopy is invisible.
        c.strokeStyle = 'rgba(15,18,22,.55)'
        c.lineWidth = lw + Math.max(2, scale * 0.12)
        c.beginPath(); c.moveTo(pu[0], pu[1]); c.lineTo(pv[0], pv[1]); c.stroke()
        c.strokeStyle = fill ? 'rgba(190,190,255,.85)' : colour
        c.lineWidth = fill ? lw * 0.7 : lw
        c.beginPath(); c.moveTo(pu[0], pu[1]); c.lineTo(pv[0], pv[1]); c.stroke()
      } else {
        // A warp or a blackout: mark both ends, never a chord across the map.
        c.lineWidth = Math.max(1.5, lw * 0.7)
        for (const p of [pu, pv]) {
          c.strokeStyle = 'rgba(15,18,22,.55)'
          c.beginPath(); c.arc(p[0], p[1], scale * 0.42, 0, 6.284); c.stroke()
          c.strokeStyle = colour
          c.beginPath(); c.arc(p[0], p[1], scale * 0.36, 0, 6.284); c.stroke()
        }
      }
    }
  }
  for (const [v, colour] of [[visits[0], '#2850dc'], [visits[visits.length - 1], '#dc3214']]) {
    const p = place(...v.slice(2, 6))
    if (!p) continue
    c.lineWidth = Math.max(2, scale * 0.16)
    c.strokeStyle = 'rgba(15,18,22,.6)'
    c.strokeRect(p[0] - scale * 0.6, p[1] - scale * 0.6, scale * 1.2, scale * 1.2)
    c.strokeStyle = colour
    c.strokeRect(p[0] - scale * 0.5, p[1] - scale * 0.5, scale, scale)
  }
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
