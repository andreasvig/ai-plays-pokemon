// The map atlas: the artwork a route is drawn on, one namespace per game.
//
// `scripts/render_gamemaps.py` renders one PNG per map from pret's own
// tilesets at the game's 16 px per tile and writes
// `public/maps/<game>/index.json` beside them (artifacts/game-map-render/plan.md
// M1-M3). Vite copies `public/`
// to the bundle root, so the files sit at BASE + 'maps/...' on the published
// site exactly as they do locally — the same path the vendor logos use.
//
// Everything here is pure geometry and fetching. The two components that draw
// (RouteMap for the world, InteriorPopup for a building) share `drawRoute` by
// passing their own tile → pixel function, so a route inside Oak's lab is
// drawn by the same code as one across Route 1.
import { BASE } from './static.js'

// Everything below is keyed on a `game` — the `configs/roms.yaml` key, which
// `route.json` has carried since ROUTE_VERSION 3. It has to be: FireRed's map
// `3:0` is Pallet Town and Emerald's `3:0` is somewhere else, and route.py and
// observed.py both refuse that collision server-side. The browser used to have
// no equivalent refusal — one global atlas, one global trainer index, and an
// image cache keyed on the BARE FILENAME, so two games' `3-0.png` were one
// entry and whichever loaded first won.

/** Fallback pixels per tile. Prefer the atlas's own `tile_px`: a DS render is
 *  not obliged to be 16, and both the atlas and route.json have always carried
 *  the number while nothing read it. */
export const TILE = 16
/**
 * Pixels between neighbouring cables, at the game's own 16 px scale; it is
 * scaled with the drawing, so the cables stay 3.5 px apart at 1× and spread as
 * you zoom in. Picked off the corner sheet on 2026-09-15.
 */
export const CABLE_GAP = 3.5

/**
 * Most cables one step will ever carry. Andreas, 2026-09-15: *"per tile i think
 * we should aim for max 4."* It costs almost nothing — of the 1,381 distinct
 * steps in the longest run to date, 764 were walked once, 405 twice, 152 three
 * times, 48 four times and only 12 more than that. The passes past the cap
 * stack onto the outermost cable, which is why an over-cap step is marked.
 */
export const MAX_LANES = 4

/** A join further than this many gaps from the corner is jogged, not mitred. */
const MITRE_SPIKE = 3

/**
 * How far back from the node a lane change is braided, as a fraction of a tile
 * (Andreas 2026-09-17: *"braids would be preferable"*, picked off the cable
 * bench after seeing both drawn on the real artwork).
 *
 * A cable that changes lane used to do it in zero length: the jog was a
 * perpendicular step at the tile centre, which reads as a BREAK in the cable
 * rather than as a cable moving over, and it lands exactly on the vertex of
 * every cable it passes — so the crossing was both invisible and stacked on
 * top of every other swap at that node. Pulling both ends back turns it into a
 * short diagonal: the same crossing, drawn where you can see it, and spread out
 * rather than piled in one tile. Andreas, same day: *"more small knots are
 * better than big ones."*
 *
 * A true reversal is NOT braided — the jog across the end tile is the U-turn
 * cap and is exactly the right shape for it (2026-09-15).
 */
const BRAID_BACK = 0.42
/** Below this dot product the two segments are a U-turn, not a lane change. */
const BRAID_MIN_DOT = -0.9

/**
 * The marching arrows (Andreas, 2026-09-15: "for arrows maybe make them
 * animated where they follow along the path instead of being static … also
 * whether there should be noise in the start and times of the arrows, to not
 * create faulty illusions and overlaps which keep repeating").
 *
 * One chevron every 12 tiles of cable, sliding forward at 2.5 tiles a second.
 * `ARROW_START_NOISE` scatters each cable's first chevron over a whole gap:
 * without it four passes down a corridor put their chevrons at the same place
 * across all four lanes and the group reads as ONE wide arrow sliding along,
 * which also beats against the 16 px tile grid. There is deliberately no speed
 * noise — every cable runs at the same rate, so the scatter is a fixed offset
 * rather than a pattern that drifts and re-forms.
 *
 * Sparse spacing means a cable shorter than a gap often carries no chevron at
 * a given instant. That is fine and it is why they move: the phase advances,
 * so a chevron passes through every cable in turn.
 */
export const ARROW_EVERY_TILES = 12
export const ARROW_SPEED_TILES = 2.5     // tiles a second
export const ARROW_SIZE = 0.26           // of a tile
export const ARROW_START_NOISE = 1       // of a gap
export const tilePxOf = (atlas, route) => atlas?.tile_px ?? route?.tile_px ?? TILE

export const mapImageUrl = (game, file) => `${BASE}maps/${game}/${file}`

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
  // A synthetic (lattice) entry carries an `origin`: the tile its rectangle
  // starts at. Gen 4/5 coordinates are GLOBAL — Platinum's y reaches 888 — so
  // without it every tile lands hundreds of tiles off its own rectangle. It is
  // the same job `trim` does for artwork, so it is the same field: everything
  // downstream subtracts `win`, and neither case needs a special path.
  const [ox, oy] = m?.origin ?? [0, 0]
  return {
    x: ox + (t.left ?? 0),
    y: oy + (t.top ?? 0),
    w: Math.max(1, (m?.width ?? 1) - (t.left ?? 0) - (t.right ?? 0)),
    h: Math.max(1, (m?.height ?? 1) - (t.top ?? 0) - (t.bottom ?? 0)),
  }
}

/**
 * The tile PNG pixel (0, 0) holds, `[0, 0]` unless the atlas says otherwise.
 *
 * Every gen 1-3 atlas ships artwork that starts at the route's own origin, so
 * `drawWindow(m).x * tile_px` IS the source rect and this is zero. A gen-4
 * outdoor map's route coordinates are GLOBAL — Platinum's y reaches 888 — so an
 * atlas that kept the same rule would have to pad each PNG out to route tile
 * (0, 0): Sandgem Town at 16 px/tile becomes 3072 x 13824, which is 42
 * megapixels and past what iOS Safari will decode. The DS atlases ship the map
 * alone and declare where it starts; the source rect subtracts this.
 */
export const pngOrigin = (m) => m?.png_origin ?? [0, 0]

export const drawSize = (m) => [
  Math.max(1, (m?.width ?? 1) - (m?.trim?.right ?? 0)),
  Math.max(1, (m?.height ?? 1) - (m?.trim?.bottom ?? 0)),
]

const trainersPromises = new Map()
/** `{version, pret_sha, trainers: {"<id>": {label, name, class, pic, party}}}`.
 *  Extracted from pret by scripts/extract_trainers.py: a trainer's roster is a
 *  constant of the ROM, so every run already published gets a full hover card
 *  from the trainer id the referee has stored since 2026-09-14 (M16).
 *  Per game, for the same reason the atlas is: trainer 4 is a different person
 *  on every cartridge. */
export function loadTrainers(game) {
  if (!game) return Promise.resolve(null)
  if (!trainersPromises.has(game)) {
    trainersPromises.set(game, fetch(`${BASE}trainers/${game}/index.json`, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null))
  }
  return trainersPromises.get(game)
}

export const trainerSpriteUrl = (game, pic) => `${BASE}trainers/${game}/${pic}`

/** A species' own front pic, named by the id the ROM uses (scripts/extract_trainers.py).
 *  NOT per game, unlike the trainer sprites above it: a species id is the
 *  National Dex number on every cartridge we run, so one set serves all seven. */
export const pokemonSpriteUrl = (id) => `${BASE}pokemon/${id}.png`

const atlasPromises = new Map()
/** `{schema, game, tile_px, source, maps: {"a:b": {...}}}` for one game.
 *  A null `game` resolves to null rather than guessing a cartridge — that is
 *  what makes a route with no game render on the lattice instead of on somebody
 *  else's artwork. */
export function loadAtlas(game) {
  if (!game) return Promise.resolve(null)
  if (!atlasPromises.has(game)) {
    atlasPromises.set(game, fetch(`${BASE}maps/${game}/index.json`, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : null))
      // The atlas must SAY it is this game's. Two reasons, and the second is
      // the one that matters: the dev server answers an unknown path with the
      // SPA's index.html at status 200, so a game with no atlas yet returns
      // HTML rather than a 404 — today that only fails safe because the catch
      // below swallows the JSON parse error. And an atlas that announces a
      // different game is the cross-cartridge collision route.py and
      // observed.py both refuse server-side; the browser had no equivalent.
      .then((d) => (d && d.maps && (!d.game || d.game === game) ? d : null))
      .catch(() => null))
  }
  return atlasPromises.get(game)
}

/** Tests and the dev harness swap the fetch and drop the memos. */
export function _resetAtlas() { atlasPromises.clear(); trainersPromises.clear(); imgCache.clear() }

const imgCache = new Map()
/** One decoded map image, cached across components and popups.
 *  Keyed `game/file`, not `file`: the bare filename made FireRed's `3-0.png`
 *  and Emerald's the same cache entry, so fixing only the URL would still have
 *  handed Emerald a picture of Pallet Town. */
export function loadMapImage(game, file) {
  const key = `${game}/${file}`
  if (!imgCache.has(key)) {
    imgCache.set(key, new Promise((resolve) => {
      const img = new Image()
      img.onload = () => resolve(img)
      img.onerror = () => resolve(null)
      img.src = mapImageUrl(game, file)
    }))
  }
  return imgCache.get(key)
}

/**
 * The ramp: 0 is the first turn, 1 the last, and every value between is a real
 * mix of its neighbours — five stops, linearly interpolated, no steps
 * (Andreas, 2026-09-15: "can we render the colours as fully gradual changes").
 */
export const COLOUR_LOOP_TILES = 300
const HUE_START = 220              // blue, where the run's start box also is

/** A point on the wheel, 0 → 1 being one full turn. */
export const wheelColour = (f) =>
  `hsl(${(((HUE_START + f * 360) % 360) + 360) % 360}, 74%, 56%)`

/**
 * `t` is 0→1 across the run (see `visitTimes`); `tiles` is how many tiles it
 * walked, which is what turns that fraction back into a distance.
 */
export function cableColour(t, tiles) {
  const n = Math.max(1, tiles || 1)
  const k = Number.isFinite(t) ? t : 0
  return wheelColour((((k * n) / COLOUR_LOOP_TILES) % 1 + 1) % 1)
}

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
 *
 * Indoor-ness comes from the normalised `indoor` boolean the atlas writer sets,
 * NOT from the raw `type` string. `type` is `MAP_TYPE_INDOOR`, a pret gen-3
 * constant that no other source emits — pokecrystal, pokeplatinum and the DS
 * rips have no such field — so branching on it silently classified every map of
 * every other game as outdoor.
 */
export function worldLayout(route, atlas) {
  if (!route?.maps || !atlas?.maps) return null
  const entries = Object.keys(route.maps)
    .map((k) => [k, atlas.maps[k]])
    // `popup` where the atlas says so (a gate-house cluster can hold a ROUTE),
    // otherwise the normalised `indoor` flag. Never the raw MAP_TYPE string.
    .filter(([, m]) => m && !(m.popup ?? m.indoor))
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
  for (const [k, m] of outdoor) at[k] = { x: m.world[0] - x0, y: m.world[1] - y0, m, win: drawWindow(m), inset: false }
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
    at[k] = { x: colX, y: colY, m, win: drawWindow(m), inset: true }
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
    interiors: Object.keys(route.maps).filter((k) => !!(atlas.maps[k]?.popup ?? atlas.maps[k]?.indoor)),
  }
}

/** Every door marker on the maps this layout draws, with whether the run went in. */
/**
 * One building or cluster, laid out in a single coordinate space.
 *
 * Returns the same shape as `worldLayout`, which is the point: the map does not
 * open a modal over itself any more, it WALKS IN (Andreas, 2026-09-16: "the
 * whole map should change to that sub-map with an arrow to go back, or the
 * ability to just press on the door to get back out"). Everything that draws
 * the world — `place`, `drawRoute`, `drawArrows`, `battlesFor`, `visitAt` — then
 * draws a cluster with no idea it is doing anything different, and the panel
 * keeps its pan and zoom.
 *
 * A CLUSTER stacks the way you walk it: Viridian Forest between its two gate
 * houses, north at the top. A BUILDING keeps its floors side by side, 1F first,
 * because stacking 1F above 2F would draw the building upside down.
 */
export function clusterLayout(building, atlas) {
  if (!building || !atlas?.maps) return null
  const seed = Object.values(atlas.maps).find((m) => m.building === building)
  const keys = (seed?.floors ?? []).filter((k) => atlas.maps[k])
  if (!keys.length) return null
  const stacked = keys.some((k) => atlas.maps[k].complex)
  const wins = keys.map((k) => drawWindow(atlas.maps[k]))
  const w = stacked ? Math.max(...wins.map((v) => v.w))
                    : wins.reduce((a, v) => a + v.w, 0) + INSET_GAP * (keys.length - 1)
  const h = stacked ? wins.reduce((a, v) => a + v.h, 0) + INSET_GAP * (keys.length - 1)
                    : Math.max(...wins.map((v) => v.h))

  const at = {}
  // A little more air than the world gets: a floor's caption sits above its top
  // edge and a door can sit ON that edge, and `fit` fits the layout exactly.
  let run = stacked ? MARGIN + 1 : MARGIN
  keys.forEach((k, i) => {
    const win = wins[i]
    // the cross axis is centred, so a narrow gate house sits under the middle
    // of the forest rather than jammed against its left edge
    at[k] = stacked
      ? { x: MARGIN + Math.round((w - win.w) / 2), y: run, m: atlas.maps[k], win, floor: true }
      : { x: run, y: MARGIN + 1 + Math.round((h - win.h) / 2), m: atlas.maps[k], win, floor: true }
    run += (stacked ? win.h : win.w) + INSET_GAP
  })
  return { at, w: w + 2 * MARGIN, h: h + 2 * MARGIN + 2, outdoor: 0, insets: 0,
           interiors: keys, building, stacked }
}

/** The tiles that leave this cluster — the doors you walk back out through. */
export function exitsFor(layout, atlas) {
  if (!layout?.building) return []
  const out = []
  for (const [key, p] of Object.entries(layout.at)) {
    for (const e of atlas?.maps?.[key]?.exits ?? []) {
      out.push({
        key: `${key}:${e.x}:${e.y}`,
        to: e.to,
        name: atlas.maps[e.to]?.name ?? e.to,
        tile: { x: p.x + e.x - p.win.x, y: p.y + e.y - p.win.y },
      })
    }
  }
  return out
}

/** A caption for each floor, at its top-left corner in layout tiles. */
export function floorsFor(layout, atlas) {
  if (!layout?.building) return []
  return Object.entries(layout.at).map(([key, p]) => ({
    key,
    label: floorLabel(p.m?.name, layout.building) || buildingLabel(layout.building),
    tile: { x: p.x, y: p.y },
  }))
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
        tile: { x: p.x + d.x - p.win.x, y: p.y + d.y - p.win.y },
        entered: floors.some((f) => route.maps[f]),
      })
    }
  }
  return out
}

/** 'Route2_ViridianForest_NorthEntrance' → 'North Entrance'; '' when the map IS the building. */
export function floorLabel(mapName, building) {
  const name = String(mapName || '')
  const floor = /_(B?\d+F)$/.exec(name)
  if (floor) return floor[1]
  // A complex: the member's name minus the complex it belongs to, which is
  // already the popup's title. The forest itself then labels as nothing, and
  // gets the building's own name below.
  const tail = name.replace(new RegExp(`^${String(building)}_?`), '')
    .replace(/^Route\d+_/, '')
    .replace(new RegExp(`^${String(building)}_?`), '')
  if (!tail || tail === name) return ''
  return tail.replace(/([a-z])([A-Z0-9])/g, '$1 $2')
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

// ---------------------------------------------------------------------------
// The route-drawing engine below is the one running on the published site
// (github pages), ported here 2026-09-20 on Andreas's instruction: "use the
// same dr[a]wing a[l]gorithm which is on the github pages, which is a bit more
// advanced and beautiful".
//
// It is a straight lift of the working tree in the sibling `ai-plays-pokemon`
// checkout, which is what that site was built from. Every tuned number in it
// was picked BY EYE off a comparison sheet and must not be re-derived:
// CABLE_GAP 3.5 and mitred joins (2026-09-15, "please use mitred: B, mitred
// with a cable gap of 3.5"), ARROW_EVERY_TILES 12 ("i think 12 is the number
// for spacing"), MAX_LANES 4 ("per tile i think we should aim for max 4"),
// braided lane changes and many-small-knots crossing minimisation (2026-09-17,
// "braids would be preferable" / "more small knots are better than big ones" —
// the second REVERSES the published block-crossing result, on the evidence of
// the real map at 1.6 px cables on a 16 px tile).
//
// What was NOT taken: that tree's worldLayout/clusterLayout interior model and
// its per-map `win` window. This branch keeps its own, because it carries the
// per-game atlas and the lattice fallback, which that tree has no notion of.
// ---------------------------------------------------------------------------
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
 * Returns each step with `lane` (0-based), `lanes` (how many that step has) and
 * `over` (the run walked it more times than the cap allows for).
 */
export function laneSteps(route, { maxLanes = MAX_LANES, times = null } = {}) {
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
    const all = total.get(k)
    s.lanes = Math.min(all, maxLanes)
    s.lane = Math.min(n, s.lanes - 1)
    s.over = all > maxLanes
    // canonical: lane 0 is always on the same side of the line
    s.flip = s.u.join(',') > s.v.join(',')
  }
  return steps
}

/* ── the lane solver ────────────────────────────────────────────────────────
 *
 * `laneSteps` numbers the passes over a step by WHEN they happened, which says
 * nothing about where each one is going: a cable that was lane 0 coming into a
 * junction is lane 2 leaving it, and has to cross the others to get there. The
 * fix is the one every metro-map paper reaches for — choose the order of the
 * lines on each edge so the drawing has fewer crossings (Fink & Pupyrev, GD
 * 2013; Nöllenburg, GD 2009). The problem is NP-hard, so this is a local
 * search: start from continuity, then swap neighbouring cables on an edge and
 * keep the swap when the drawing improves.
 *
 * Measured over 4,113 tiles on 35 maps of 5 published runs (2026-09-17):
 * 676 crossings today → 508 braided → 384 with this, and the worst pile-up
 * anywhere on the board goes from 31 crossings in one tile to 15.
 *
 * Two things make it cheap enough to run in the page:
 *
 *  - The drawing is SCALE-INVARIANT. Every length — the tile, the cable gap,
 *    the braid, the mitre limit — scales together, so the crossings at 2× are
 *    the crossings at 1×. The solver works at one scale, in tile-local
 *    coordinates, and the answer holds for every canvas that draws the route.
 *  - A swap moves FIVE drawn segments (the two cable bodies and the joins on
 *    either side of each), so the cost is re-counted for those against a tile
 *    bucket rather than re-counting the whole map. Re-drawing everything per
 *    candidate took 102s over the corpus; this is milliseconds.
 *
 * Andreas 2026-09-17: *"more small knots are better than big ones"* — so the
 * cost is not the block-crossing objective the literature optimises (fewer,
 * bigger crossing blocks). It is the opposite: a crossing that lands on top of
 * another one is charged extra, which spreads a tangle out into pieces a
 * reader can follow one at a time.
 */

/** A crossing stacked on another inside a tile costs this much on top of it. */
const STACK_COST = 0.6
/** Give up after this many sweeps, or this many candidate swaps, whichever first. */
const SOLVE_SWEEPS = 6
const SOLVE_BUDGET = 60000

const solveCache = new WeakMap()

/**
 * The lane for every step of `route`, crossing-minimised — one number per step
 * of `laneSteps`, in the same order. Memoised per (route, maxLanes): the answer
 * does not depend on scale, on the canvas, or on `times`, so it is computed
 * once however many times the map is redrawn.
 */
export function solveLanes(route, { maxLanes = MAX_LANES } = {}) {
  let byLanes = solveCache.get(route)
  if (!byLanes) { byLanes = new Map(); solveCache.set(route, byLanes) }
  const hit = byLanes.get(maxLanes)
  if (hit) return hit
  const out = runSolver(route, maxLanes)
  byLanes.set(maxLanes, out)
  return out
}

const SOLVE_GAP = CABLE_GAP
const SOLVE_LIMIT = CABLE_GAP * MITRE_SPIKE + TILE * 0.9
const SOLVE_BRAID = TILE * BRAID_BACK

/* Tile coordinates are MAP-LOCAL, so Pallet Town's (0,0) and Route 1's (0,0)
   are the same point — laid out naively the solver would score phantom
   crossings between cables on different maps and optimise against them (found
   2026-09-17: Viridian Forest came out of the search with exactly the crossings
   it went in with). Every map is slid into its own lane of the plane instead.
   A cable never spans two maps — `chainsOf` breaks there — so the gap only has
   to be wider than any crossing test can reach. */
const MAP_STRIDE = 4096
const mapSlot = (slots, n) => {
  const k = n[0] + ':' + n[1]
  let i = slots.get(k)
  if (i === undefined) { i = slots.size; slots.set(k, i) }
  return i
}
const tilePx = (slots, n) => [(mapSlot(slots, n) * MAP_STRIDE + n[2]) * TILE + TILE / 2, n[3] * TILE + TILE / 2]
const sameNode = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3]

/** One step's offset body, at the solver's fixed scale. */
function bodyOf(slots, s) {
  const pu = tilePx(slots, s.u), pv = tilePx(slots, s.v)
  const dx = pv[0] - pu[0], dy = pv[1] - pu[1]
  const len = Math.hypot(dx, dy)
  const d = [dx / len, dy / len]
  const o = (s.lane - (s.lanes - 1) / 2) * SOLVE_GAP * (s.flip ? -1 : 1)
  const n = [-d[1] * o, d[0] * o]
  return { a: [pu[0] + n[0], pu[1] + n[1]], b: [pv[0] + n[0], pv[1] + n[1]], d, len }
}

/* The join between two cable bodies, as a PAIR of points — the one place the
   three cases live, called by BOTH the renderer and the solver. They used to be
   two copies of the same arithmetic, and a mutation test proved the copies
   could drift apart without anything failing: the solver would then be
   optimising a drawing nobody renders.

   A mitre and a straight continuation return the same point twice. The renderer
   collapses that to one point; the solver keeps both so a segment keeps its
   index in the point array when a lane moves. `limit` is the mitre spike cut-off
   and `braid` the pull-back; a true reversal is never braided, because that jog
   is the U-turn cap. */
function joinOf(A, B, limit, braid) {
  const cross = A.d[0] * B.d[1] - A.d[1] * B.d[0]
  if (Math.abs(cross) > 1e-6) {
    const wx = B.a[0] - A.a[0], wy = B.a[1] - A.a[1]
    const k = (wx * B.d[1] - wy * B.d[0]) / cross
    const J = [A.a[0] + A.d[0] * k, A.a[1] + A.d[1] * k]
    if (Math.hypot(J[0] - A.b[0], J[1] - A.b[1]) <= limit) return [J, J]
  }
  if (Math.hypot(A.b[0] - B.a[0], A.b[1] - B.a[1]) < 0.01) return [A.b, A.b]
  const dot = A.d[0] * B.d[0] + A.d[1] * B.d[1]
  const r = dot > BRAID_MIN_DOT ? Math.min(braid, segLen(A) * 0.45, segLen(B) * 0.45) : 0
  return [[A.b[0] - A.d[0] * r, A.b[1] - A.d[1] * r], [B.a[0] + B.d[0] * r, B.a[1] + B.d[1] * r]]
}

/** The contiguous cables, the way `routePolylines` breaks them. */
function chainsOf(steps) {
  const chains = []
  let cur = null, last = null
  for (const s of steps) {
    const sameMap = s.u[0] === s.v[0] && s.u[1] === s.v[1]
    const adjacent = sameMap && Math.abs(s.u[2] - s.v[2]) + Math.abs(s.u[3] - s.v[3]) <= 2
    if (!adjacent) { cur = null; last = null; continue }
    // A turn that advanced without moving draws nothing and must not break the cable.
    if (s.u[2] === s.v[2] && s.u[3] === s.v[3]) { last = s.v; continue }
    if (!cur || !last || !sameNode(last, s.u)) { cur = { steps: [], P: [], bodies: [] }; chains.push(cur) }
    cur.steps.push(s)
    last = s.v
  }
  return chains.filter((c) => c.steps.length)
}

/** Recompute the points a lane change at step `i` moves — and only those. */
function refresh(slots, chain, i) {
  const { steps, bodies, P } = chain
  const n = steps.length
  for (let j = Math.max(0, i - 1); j <= Math.min(n - 1, i + 1); j++) bodies[j] = bodyOf(slots, steps[j])
  for (let j = Math.max(0, i - 1); j <= Math.min(n - 2, i); j++) {
    const [p, q] = joinOf(bodies[j], bodies[j + 1], SOLVE_LIMIT, SOLVE_BRAID)
    P[2 * j + 1] = p
    P[2 * j + 2] = q
  }
  P[0] = bodies[0].a
  P[2 * n - 1] = bodies[n - 1].b
}

function buildChain(slots, chain) {
  const n = chain.steps.length
  chain.bodies = chain.steps.map((s) => bodyOf(slots, s))
  chain.P = new Array(2 * n)
  chain.P[0] = chain.bodies[0].a
  for (let j = 0; j < n - 1; j++) {
    const [p, q] = joinOf(chain.bodies[j], chain.bodies[j + 1], SOLVE_LIMIT, SOLVE_BRAID)
    chain.P[2 * j + 1] = p
    chain.P[2 * j + 2] = q
  }
  chain.P[2 * n - 1] = chain.bodies[n - 1].b
}

function runSolver(route, maxLanes) {
  const steps = laneSteps(route, { maxLanes })
  const corridors = widenCorridors(steps, maxLanes)
  carryLanes(steps)
  const slots = new Map()
  const chains = chainsOf(steps)
  if (!chains.length) return steps.map((s) => [s.lane, s.lanes])

  // segment id → (chain, k) where the segment runs P[k] .. P[k+1]
  const base = []
  let total = 0
  for (const c of chains) { base.push(total); buildChain(slots, c); total += 2 * c.steps.length - 1 }
  const at = (id) => {
    let lo = 0, hi = base.length - 1
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (base[m] <= id) lo = m; else hi = m - 1 }
    return { c: chains[lo], k: id - base[lo] }
  }
  const ends = (id) => { const { c, k } = at(id); return [c.P[k], c.P[k + 1]] }

  // tile buckets, so a segment is only tested against its neighbourhood
  const cells = new Map()
  const held = new Map()
  const keysFor = (id) => {
    const [a, b] = ends(id)
    const out = []
    const x0 = Math.floor(Math.min(a[0], b[0]) / TILE), x1 = Math.floor(Math.max(a[0], b[0]) / TILE)
    const y0 = Math.floor(Math.min(a[1], b[1]) / TILE), y1 = Math.floor(Math.max(a[1], b[1]) / TILE)
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) out.push(x + ':' + y)
    return out
  }
  const put = (id) => {
    const ks = keysFor(id)
    held.set(id, ks)
    for (const k of ks) { let set = cells.get(k); if (!set) { set = new Set(); cells.set(k, set) } set.add(id) }
  }
  const drop = (id) => {
    for (const k of held.get(id) || []) cells.get(k)?.delete(id)
    held.delete(id)
  }
  for (let id = 0; id < total; id++) put(id)

  /* The cost of the TILES a swap touches — not of the moved segments.
     Scoring only the segments that moved counted their crossings correctly but
     could never see a knot: a pile-up is mostly crossings between cables that
     did not move, so the stacking term measured nothing and the whole weight
     range 0.6 → 6 produced an identical drawing (2026-09-17). Counting every
     crossing inside the touched tiles fixes that — the pairs that did not move
     contribute equally before and after, so the crossing delta is still exact,
     while the stacking term now sees the tangle the swap is landing in. */
  const costOf = (keys) => {
    const ids = new Set()
    for (const k of keys) for (const j of cells.get(k) || []) ids.add(j)
    const list = [...ids]
    const pts = []
    for (let x = 0; x < list.length; x++) {
      const i = list[x]
      const ai = at(i)
      const [a1, a2] = ends(i)
      for (let y = x + 1; y < list.length; y++) {
        const j = list[y]
        const aj = at(j)
        if (ai.c === aj.c && Math.abs(ai.k - aj.k) <= 1) continue
        const [b1, b2] = ends(j)
        const p = crossPoint(a1, a2, b1, b2, ai.c === aj.c)
        if (p) pts.push(p)
      }
    }
    let stacked = 0
    for (let i = 0; i < pts.length; i++)
      for (let j = i + 1; j < pts.length; j++)
        if (Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]) <= TILE) stacked++
    return pts.length + STACK_COST * stacked
  }

  // where each step sits, so a swap knows which segments move
  const where = new Map()
  chains.forEach((c, ci) => c.steps.forEach((s, i) => where.set(s, { c, ci, i })))
  const windowOf = (s) => {
    const w = where.get(s)
    if (!w) return []
    const span = 2 * w.c.steps.length - 1
    const out = []
    for (let k = Math.max(0, 2 * w.i - 2); k <= Math.min(span - 1, 2 * w.i + 2); k++) out.push(base[w.ci] + k)
    return out
  }

  const byEdge = new Map()
  for (const s of steps) {
    if (!where.has(s)) continue
    const k = edgeKey(s.u, s.v)
    if (!byEdge.has(k)) byEdge.set(k, [])
    byEdge.get(k).push(s)
  }
  const edges = [...byEdge.values()].filter((o) => o.length > 1)

  /* The move is a whole PERMUTATION of one step's lanes, not an adjacent swap.
     A swap could not find the arrangement Pallet Town needed: putting the two
     passes that carry on through a junction into the middle lanes costs a
     crossing on the step itself and saves two at the join, so every single
     swap on the way there is uphill. A step carries at most `MAX_LANES` cables,
     so "try them all" is 24 arrangements at the very worst and usually two. */
  const perms = (n) => {
    if (n <= 1) return [[0]]
    const out = []
    for (const rest of perms(n - 1)) {
      for (let i = 0; i <= rest.length; i++) out.push([...rest.slice(0, i), n - 1, ...rest.slice(i)])
    }
    return out
  }
  const PERMS = [0, 1, 2, 3, 4, 5, 6].map((n) => (n <= 1 ? [[0]] : perms(n)))

  let budget = SOLVE_BUDGET
  for (let sweep = 0; sweep < SOLVE_SWEEPS && budget > 0; sweep++) {
    let moved = false
    for (const occ of edges) {
      const L = Math.min(occ.length, occ[0].lanes)
      const slot = []
      let clean = true
      for (let l = 0; l < L; l++) {
        const here = occ.filter((s) => s.lane === l)
        if (here.length !== 1) { clean = false; break }
        slot.push(here[0])
      }
      // past the lane cap two passes share the outermost lane; there is no
      // order to choose between them, so leave that step alone.
      if (!clean || L < 2 || budget <= 0) continue
      const ids = [...new Set(slot.flatMap(windowOf))]
      const keys = new Set()
      const apply = () => {
        for (const s of slot) { const w = where.get(s); refresh(slots, w.c, w.i) }
        for (const id of ids) { drop(id); put(id) }
      }
      const addKeys = () => { for (const id of ids) for (const k of held.get(id) || []) keys.add(k) }
      const set = (p) => { p.forEach((from, l) => { slot[from].lane = l }); apply(); addKeys() }
      const table = PERMS[L]
      // one pass to learn every tile any arrangement touches, so they are all
      // scored over the same ground
      for (const p of table) set(p)
      set(table[0])
      let best = 0, bestCost = Infinity
      for (let i = 0; i < table.length && budget > 0; i++) {
        budget--
        set(table[i])
        const c = costOf(keys)
        if (c < bestCost - 1e-9) { bestCost = c; best = i }
      }
      set(table[best])
      if (best !== 0) moved = true
    }
    if (!moved) break
  }

  /* A street, moved as one piece.
   *
   * Permuting a single step cannot reorder a corridor: the good arrangement
   * costs a sideways step at every edge on the way to it, so each move on its
   * own is uphill and the search stops at the tangle it started with. Here the
   * cables that run the length of a corridor — its STRANDS, in metro-map terms
   * its lines — are permuted together, so the whole street reorders at once and
   * the only cost is at its two ends. */
  for (let sweep = 0; sweep < 3 && budget > 0; sweep++) {
    let moved = false
    for (const [, keySet] of corridors) {
      const mine = steps.filter((s) => keySet.has(s.k) && where.has(s))
      if (mine.length < 2) continue
      // strands: contiguous runs of this walk that stay inside the corridor
      const strands = []
      let cur = null, prev = null
      for (const s of mine) {
        if (!cur || !prev || !sameNode(prev.v, s.u) || prev.lane !== s.lane) { cur = []; strands.push(cur) }
        cur.push(s)
        prev = s
      }
      const L = mine[0].lanes
      if (strands.length < 2 || strands.length > L) continue
      // one lane per strand, and no two strands may want the same step
      const lanesOf = strands.map((st) => st[0].lane)
      if (new Set(lanesOf).size !== strands.length) continue
      const seen = new Set()
      let ok = true
      for (const st of strands) for (const s of st) { if (seen.has(s)) { ok = false } seen.add(s) }
      if (!ok) continue
      const ids = [...new Set(mine.flatMap(windowOf))]
      const keys = new Set()
      const set = (p) => {
        p.forEach((from, i) => { for (const s of strands[from]) s.lane = lanesOf[i] })
        for (const s of mine) { const w = where.get(s); refresh(slots, w.c, w.i) }
        for (const id of ids) { drop(id); put(id) }
        for (const id of ids) for (const k of held.get(id) || []) keys.add(k)
      }
      const table = PERMS[strands.length]
      for (const p of table) set(p)
      let best = 0, bestCost = Infinity
      for (let i = 0; i < table.length && budget > 0; i++) {
        budget--
        set(table[i])
        const c = costOf(keys)
        if (c < bestCost - 1e-9) { bestCost = c; best = i }
      }
      set(table[best])
      if (best !== 0) moved = true
    }
    if (!moved) break
  }
  return steps.map((s) => [s.lane, s.lanes])
}

/* One lane count for a whole corridor, not one per step.
 *
 * `laneSteps` sizes each step on its own, so a corridor carrying four passes
 * that narrows to two re-centres: lane 0 of 4 sits 1.5 gaps off the tile line,
 * lane 0 of 2 only 0.5, and a cable that never changed lane still steps
 * sideways at the seam. On Pallet Town 14 of 33 sideways steps came from the
 * count alone (2026-09-17). Between two junctions the count is now the widest
 * the corridor ever gets, so a cable that holds its lane holds its line, and a
 * pass that ends simply stops — the way a metro map drops a line from the side
 * of a bundle rather than re-spacing the whole thing.
 */
function widenCorridors(steps, maxLanes) {
  const drawn = steps.filter((s) => s.u[0] === s.v[0] && s.u[1] === s.v[1]
    && Math.abs(s.u[2] - s.v[2]) + Math.abs(s.u[3] - s.v[3]) === 1)
  const byKey = new Map()
  for (const s of drawn) {
    if (!byKey.has(s.k ?? (s.k = edgeKey(s.u, s.v)))) byKey.set(s.k, [])
    byKey.get(s.k).push(s)
  }
  const keys = [...byKey.keys()]
  const idx = new Map(keys.map((k, i) => [k, i]))
  const parent = keys.map((_, i) => i)
  const find = (i) => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i] } return i }
  const join = (a, b) => { const x = find(a), y = find(b); if (x !== y) parent[x] = y }
  // a node with exactly two distinct steps through it is inside a corridor
  const atNode = new Map()
  for (const k of keys) {
    const s = byKey.get(k)[0]
    for (const n of [s.u, s.v]) {
      const nk = n.join(',')
      if (!atNode.has(nk)) atNode.set(nk, new Set())
      atNode.get(nk).add(k)
    }
  }
  for (const [, ks] of atNode) {
    if (ks.size !== 2) continue
    const [a, b] = [...ks]
    join(idx.get(a), idx.get(b))
  }
  const widest = new Map()
  for (const k of keys) {
    const r = find(idx.get(k))
    const n = Math.min(byKey.get(k).length, maxLanes)
    widest.set(r, Math.max(widest.get(r) ?? 0, n))
  }
  const groups = new Map()
  for (const k of keys) {
    const r = find(idx.get(k))
    for (const s of byKey.get(k)) s.lanes = widest.get(r)
    if (!groups.has(r)) groups.set(r, new Set())
    groups.get(r).add(k)
  }
  return groups
}

/* Which side of the corridor, then which cable on that side.
 *
 * Two passes that never swap order can never cross, and this is the assignment
 * that makes most pairs unable to swap. Passes walking the step the same way go
 * on one side, ordered by when they walked it; passes walking it the other way
 * go on the other side. Then:
 *
 *  - two passes going the same way keep their time order for as long as they
 *    share the corridor, so they cannot cross;
 *  - two passes going opposite ways sit on opposite sides, so they cannot
 *    cross either — which is how a road works, and how the cable already
 *    handled a simple there-and-back.
 *
 * What is left over is the junctions, where the split between the two sides
 * moves because the next step carries a different mix. That is what the search
 * after this is for.
 */
function carryLanes(steps) {
  const byEdge = new Map()
  for (const s of steps) {
    const k = s.k ?? (s.k = edgeKey(s.u, s.v))
    if (!byEdge.has(k)) byEdge.set(k, [])
    byEdge.get(k).push(s)
  }
  for (const [, occ] of byEdge) {
    const L = Math.min(occ.length, occ[0].lanes)
    // `flip` is already "walked against the canonical direction", so it is the
    // side marker; occurrences arrive in time order.
    const fwd = occ.filter((s) => !s.flip), back = occ.filter((s) => s.flip)
    const order = [...fwd, ...back.reverse()]
    order.forEach((s, i) => { s.lane = Math.min(i, L - 1) })
  }
}

/** Where two segments cross, or null. Endpoints count: a cable changing lane
 *  meets the one it passes exactly at that cable's vertex. */
function crossPoint(p1, p2, p3, p4, sameCable) {
  const d1 = [p2[0] - p1[0], p2[1] - p1[1]], d2 = [p4[0] - p3[0], p4[1] - p3[1]]
  const den = d1[0] * d2[1] - d1[1] * d2[0]
  if (Math.abs(den) < 1e-9) return null
  const wx = p3[0] - p1[0], wy = p3[1] - p1[1]
  const s = (wx * d2[1] - wy * d2[0]) / den
  const u = (wx * d1[1] - wy * d1[0]) / den
  const e = sameCable ? 1e-6 : -1e-6
  if (s <= e || s >= 1 - e || u <= e || u >= 1 - e) return null
  return [p1[0] + d1[0] * s, p1[1] + d1[1] * s]
}

/**
 * The route as mitred polylines — the geometry behind `drawRoute`.
 *
 * Andreas, 2026-09-15: *"i still think we could make this more smooth,
 * rendering real corners, u-turns and so on, such that it doesn't look so
 * rugged"* — picked option B off artifacts/game-map-render/corners.md after
 * seeing all five drawn on the real artwork.
 *
 * Until now every step was stroked on its own, offset perpendicular to ITSELF.
 * Two steps meeting at a corner were therefore two rectangles that happened to
 * touch, with a notch on the outside and an overlap on the inside, and a U-turn
 * was two cables that never met at all. Here consecutive steps are collected
 * into one polyline and each join is placed where the two OFFSET LINES actually
 * cross, so a cable turns a corner the way a cable does.
 *
 * Two joins are not a crossing and fall back to a short jog (`a.b` then `b.a`):
 *  - a reversal, where the offset lines are parallel — this is the U-turn cap,
 *    and the jog across the end tile is exactly the right shape for it;
 *  - a lane change mid-corridor, same reason.
 * A near-reversal would put the true crossing point a long way out (the mitre
 * spike), so anything past `MITRE_SPIKE` gaps is jogged too.
 *
 * `place(g, m, x, y)` → `[px, py]` of that tile's centre on this canvas, or
 * null when the caller does not draw that map — which is how the world canvas
 * skips interiors and a popup skips everything but its own floors. A step with
 * an end the caller cannot place breaks the polyline rather than bridging it.
 *
 * Returns `{ lines, marks }`: `lines` are `{pts, ts, fill, over}` with one `ts`
 * per point, and `marks` are the ends of a warp or a blackout, which are drawn
 * as rings because a chord across the map would be a lie.
 */
export function routePolylines(route, place, { scale = TILE, gap = null, maxLanes = MAX_LANES, times = null, braid = true, solve = true } = {}) {
  const ts = times ?? visitTimes(route?.visits ?? [])
  const tiles = route?.visits?.length ?? 1
  const off = gap ?? (scale / TILE) * CABLE_GAP
  const limit = off * MITRE_SPIKE + scale * 0.9
  const lines = []
  const marks = []
  let segs = []
  let last = null
  // `braid: false` is the control the tests measure against — the pre-2026-09-17
  // hard jog. Nothing in the app passes it.
  const flush = () => { if (segs.length) lines.push(mitreJoin(segs, limit, braid ? scale * BRAID_BACK : 0)); segs = []; last = null }

  /* The lanes come from `solveLanes`, which is memoised per route: the answer
     is scale-invariant, so panning and zooming redraw with the order already
     chosen. `solve: false` is the control — the chronological numbering
     `laneSteps` hands back on its own, which is what the board drew before
     2026-09-17. Nothing in the app passes it. */
  const steps = laneSteps(route, { times: ts, maxLanes })
  if (solve) {
    const solved = solveLanes(route, { maxLanes })
    for (let i = 0; i < steps.length; i++) { steps[i].lane = solved[i][0]; steps[i].lanes = solved[i][1] }
  }
  for (const s of steps) {
    const pu = place(...s.u), pv = place(...s.v)
    if (!pu || !pv) { flush(); continue }
    const sameMap = s.u[0] === s.v[0] && s.u[1] === s.v[1]
    const adjacent = sameMap && Math.abs(s.u[2] - s.v[2]) + Math.abs(s.u[3] - s.v[3]) <= 2
    const seam = !sameMap && Math.abs(pu[0] - pv[0]) + Math.abs(pu[1] - pv[1]) <= 2 * scale
    if (!(adjacent || seam)) {
      flush()
      marks.push({ p: pu, colour: cableColour(s.t0, tiles) }, { p: pv, colour: cableColour(s.t1, tiles) })
      continue
    }
    const dx = pv[0] - pu[0], dy = pv[1] - pu[1]
    const len = Math.hypot(dx, dy)
    // A turn that advanced without moving is a real visit and a zero-length
    // step. It draws nothing — but it must not BREAK the cable either, or a
    // corridor the run paused in comes apart at the pause.
    if (len < 0.01) { last = s.v; continue }
    const d = [dx / len, dy / len]
    // The offset is taken from the edge's CANONICAL direction, not the
    // direction of travel, so a there-and-back pair keeps each cable on the
    // same physical side of the corridor instead of swapping halfway.
    const o = (s.lane - (s.lanes - 1) / 2) * off * (s.flip ? -1 : 1)
    const n = [-d[1] * o, d[0] * o]
    const seg = {
      a: [pu[0] + n[0], pu[1] + n[1]],
      b: [pv[0] + n[0], pv[1] + n[1]],
      d, t0: s.t0, t1: s.t1, fill: s.fill, over: s.over,
    }
    if (!(last && last[0] === s.u[0] && last[1] === s.u[1] && last[2] === s.u[2] && last[3] === s.u[3])) flush()
    segs.push(seg)
    last = s.v
  }
  flush()
  return { lines, marks }
}

/** One run of offset segments → one polyline, joined where their lines cross. */
function mitreJoin(segs, limit, braid) {
  const pts = [segs[0].a]
  const ts = [segs[0].t0]
  for (let i = 0; i < segs.length - 1; i++) {
    const A = segs[i], B = segs[i + 1]
    const [p, q] = joinOf(A, B, limit, braid)
    pts.push(p)
    ts.push(A.t1)
    // a mitre and a straight continuation come back as the same point twice
    if (p !== q) { pts.push(q); ts.push(B.t0) }
  }
  pts.push(segs[segs.length - 1].b)
  ts.push(segs[segs.length - 1].t1)
  // Arc length along the finished cable. `drawArrows` walks this rather than
  // the segment list, which is what lets a chevron sit mid-segment and slide.
  const cum = [0]
  for (let i = 1; i < pts.length; i++)
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
  return { pts, ts, cum, L: cum[cum.length - 1], fill: segs[0].fill, over: segs.some((s) => s.over) }
}

const segLen = (s) => Math.hypot(s.b[0] - s.a[0], s.b[1] - s.a[1])

/** Where a cable is, which way it points and how far along it is, at `d` px. */
function pointAt(line, d) {
  let lo = 0, hi = line.cum.length - 1
  while (lo < hi - 1) { const m = (lo + hi) >> 1; if (line.cum[m] <= d) lo = m; else hi = m }
  const span = line.cum[hi] - line.cum[lo]
  if (!(span > 0)) return null
  const f = (d - line.cum[lo]) / span
  const a = line.pts[lo], b = line.pts[hi]
  return {
    p: [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f],
    d: [(b[0] - a[0]) / span, (b[1] - a[1]) / span],
    t: line.ts[lo] + (line.ts[hi] - line.ts[lo]) * f,
  }
}

/**
 * A stable pseudo-random per cable. Stable is the whole point: a cable that
 * re-rolled its offset every frame would jitter instead of march.
 */
const noiseFor = (i) => {
  const x = Math.sin(i * 12.9898 + 4.1414) * 43758.5453
  return x - Math.floor(x)
}

/**
 * Draw a run's route — the STILL layer: cables, warp rings, start and end.
 *
 * Returns the cables it drew, so a caller that animates can bake this once to
 * an offscreen canvas and then only redraw the chevrons (`drawArrows`) each
 * frame. Re-stroking 1,400 cables sixty times a second is not free; blitting
 * one bitmap is.
 *
 * Lines are thin and laid in lanes (`laneSteps`), joined into cables
 * (`routePolylines`). Three passes, in this order, because they paint over
 * each other:
 *  1. a dark halo under every cable — the artwork below is busy, and a bare
 *     thin line on Viridian Forest's canopy disappears;
 *  2. the faint outer glow on a step walked more times than the lane cap;
 *  3. the colour, segment by segment so the wheel turns along the cable.
 */
export function drawRoute(c, route, place, opts = {}) {
  const { scale = TILE, width = null } = opts
  const visits = route?.visits ?? []
  if (visits.length < 2) return []
  const tiles = visits.length
  const lw = width ?? Math.max(1.2, scale * 0.11)
  const { lines, marks } = routePolylines(route, place, opts)

  c.lineCap = 'butt'
  c.lineJoin = 'miter'
  if ('miterLimit' in c) c.miterLimit = MITRE_SPIKE

  const trace = (p) => {
    c.moveTo(p.pts[0][0], p.pts[0][1])
    for (let i = 1; i < p.pts.length; i++) c.lineTo(p.pts[i][0], p.pts[i][1])
  }

  for (const p of lines) {
    c.strokeStyle = 'rgba(12,15,20,.58)'
    c.lineWidth = lw + Math.max(1.4, scale * 0.09)
    c.beginPath(); trace(p); c.stroke()
  }
  // a step walked more than the cap wears a faint outer halo, so a corridor
  // crossed nine times still reads as busier than one crossed four
  for (const p of lines) {
    if (!p.over) continue
    c.strokeStyle = 'rgba(255,255,255,.16)'
    c.lineWidth = lw + Math.max(3.2, scale * 0.2)
    c.beginPath(); trace(p); c.stroke()
  }

  c.lineWidth = lw
  for (const p of lines) {
    for (let i = 1; i < p.pts.length; i++) {
      const u = p.pts[i - 1], v = p.pts[i]
      const ca = p.fill ? 'rgb(190,190,255)' : cableColour(p.ts[i - 1], tiles)
      const cb = p.fill ? 'rgb(190,190,255)' : cableColour(p.ts[i], tiles)
      let stroke = ca
      if (ca !== cb && c.createLinearGradient) {
        const g = c.createLinearGradient(u[0], u[1], v[0], v[1])
        g.addColorStop(0, ca)
        g.addColorStop(1, cb)
        stroke = g
      }
      c.strokeStyle = stroke
      c.beginPath(); c.moveTo(u[0], u[1]); c.lineTo(v[0], v[1]); c.stroke()
    }
  }

  // a warp or a blackout: both ends ringed, never a chord across the map
  c.lineWidth = Math.max(1.2, lw)
  for (const { p, colour } of marks) {
    c.strokeStyle = 'rgba(15,18,22,.5)'
    c.beginPath(); c.arc(p[0], p[1], scale * 0.34, 0, 6.284); c.stroke()
    c.strokeStyle = colour
    c.beginPath(); c.arc(p[0], p[1], scale * 0.28, 0, 6.284); c.stroke()
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
  return lines
}

/**
 * Draw the chevrons — the MOVING layer, one call a frame.
 *
 * `now` is seconds; at `now = 0` this is a still picture, which is what a
 * viewer who has asked for reduced motion gets. Spacing is measured along each
 * cable's own arc length, so a chevron rounds a corner with the cable instead
 * of being pinned to a segment end, and a route that doubles back carries two
 * chevrons pointing opposite ways over the same ground.
 *
 * Each chevron takes its colour from the point of the cable it is sitting on,
 * so it cannot drift away from the wheel underneath it.
 */
export function drawArrows(c, lines, {
  scale = TILE, tiles = 1, now = 0,
  every = ARROW_EVERY_TILES, speed = ARROW_SPEED_TILES,
  size = ARROW_SIZE, startNoise = ARROW_START_NOISE,
} = {}) {
  const gap = every * scale
  if (!(gap > 0)) return
  const h = Math.max(3, scale * size), w = Math.max(3, scale * size)
  c.lineCap = 'round'
  c.lineJoin = 'round'
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (!(line.L > 0)) continue
    // Half a gap in, so a cable does not carry a chevron sitting exactly on its
    // own first point — which at `startNoise: 0` would be every cable at once.
    const from = ((gap * 0.5 + now * speed * scale + noiseFor(i) * gap * startNoise) % gap + gap) % gap
    for (let d = from; d < line.L; d += gap) {
      const q = pointAt(line, d)
      if (!q) continue
      const back = [q.p[0] - q.d[0] * h, q.p[1] - q.d[1] * h]
      const ax = back[0] - q.d[1] * w * 0.55, ay = back[1] + q.d[0] * w * 0.55
      const bx = back[0] + q.d[1] * w * 0.55, by = back[1] - q.d[0] * w * 0.55
      c.beginPath()
      c.moveTo(ax, ay)
      c.lineTo(q.p[0], q.p[1])
      c.lineTo(bx, by)
      // Outlined, or a chevron only a little wider than its own cable is
      // invisible against the artwork.
      c.lineWidth = Math.max(1, scale * 0.075)
      c.strokeStyle = 'rgba(12,15,20,.85)'
      c.stroke()
      c.lineWidth = Math.max(0.7, scale * 0.045)
      c.strokeStyle = line.fill ? 'rgb(190,190,255)' : cableColour(q.t, tiles)
      c.stroke()
    }
  }
}
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
    out.push({ ...b, id: `b${i}`, tile: { x: p.x + b.tile[2] - p.win.x, y: p.y + b.tile[3] - p.win.y } })
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
