// Where a map gets a fringe of the game's own border block drawn outside it.
//
// BY HAND, one map at a time. Andreas, 2026-09-20, after two automatic
// attempts: "nope it just didn't work with adding trees as the standard. can't
// we just manually augment the places where 1 row of trees would help
// enormously?"
//
// Why automatic failed, twice, because both failures are still worth avoiding
// when adding an entry here:
//
//  1. It covered the roads. A map's border block is what the game repeats
//     beyond its bounds ONLY where there is nothing else; on an edge with a
//     connection the game draws the neighbour. Tiling it everywhere put a wall
//     of trees across Oldale Town's three exits. That is fixed in the data and
//     is not something this file can undo: `open` on each atlas entry carries
//     the exact spans a neighbour covers, from pret's own `connections`, and
//     the viewer punches every one of them out of whatever is listed here.
//
//  2. It was a second map, not a fringe. Twenty-eight tiles in every direction
//     of one repeated block, meeting another map's field along a straight line.
//     The unit here is a ROW — one border block, which for a gen-3 tree block
//     is two tiles — and the default is one of them.
//
// An entry is a list of sides, in pret's own four words. A side that is
// partly open still belongs in the list: Viridian City's top edge is 48 tiles
// and Route 2 covers 24 of them, so listing 'up' draws the other 24 and the
// road stays clear.
//
//   '3:1': ['up', 'down', 'left', 'right'],
//
// For more than one row, give an object instead. Two rows of trees around
// Viridian Forest, say:
//
//   '1:0': { sides: ['left', 'right'], rows: 2 },
//
// Delete an entry and that map ends at its own artwork again.
export const BORDER_EDGES = {
  // Named by Andreas, 2026-09-20, looking at a row of trees running down Route
  // 104's sea edge: "i dont want them popping up randomly at water edges or
  // cave edges. the only places they are needed is at the bottom of the second
  // route, and to the left, right, bottom of the first town and first route,
  // and to the left of the second town (first non-start town)."
  //
  // Read in story order and applied to both cartridges. Four of the named sides
  // are ENTIRELY a connection, so there is nothing there to draw — a row of
  // trees across one would be a wall over the road out. They are named in the
  // comments rather than listed, because an entry that draws nothing looks like
  // a setting and is really a misread of the geography:
  //
  //   Route 1 and Route 101       bottom — all of it is the town below
  //   Route 2 (FireRed)           bottom — all of it is Viridian City
  //   Oldale Town                 left   — all of it is Route 102
  //
  // Emerald's second route DOES have a closed bottom, and Viridian City's left
  // is only half road (Route 22 covers 24 of its 40), so both are listed.
  'firered-us': {
    '3:0': ['left', 'right'],      // Pallet Town, the first town
    '3:19': ['left', 'right'],     // Route 1, the first route
    '3:1': ['left'],               // Viridian City, the first non-start town
  },
  'emerald-us': {
    '0:9': ['left', 'right', 'down'],   // Littleroot Town, the first town
    '0:16': ['left', 'right'],          // Route 101, the first route
    // "left of Oldale town": Oldale's OWN left edge is 20 of 20 Route 102, so
    // what is actually west of Oldale is Route 102 itself. Both of its long
    // edges, then — its short ones are the town at one end and Petalburg at
    // the other.
    '0:17': ['up', 'down'],             // Route 102, the second route
    '0:0': ['down'],                    // south of Petalburg City
    // Its EAST side, not the west one the sea is on — 50 of its 80 tiles, the
    // rest being the connection down to Petalburg.
    '0:19': ['right'],                  // Route 104
  },
}

const SIDES = ['up', 'down', 'left', 'right']
const entry = (game, key) => BORDER_EDGES?.[game]?.[key] ?? null
const listed = (e) => (Array.isArray(e) ? e : Array.isArray(e?.sides) ? e.sides : [])

/** The sides to draw for one map, as a Set. Empty unless this map is listed. */
export function borderSides(game, key, m) {
  if (!m?.border) return new Set()          // no block to tile: a lattice map
  return new Set(listed(entry(game, key)).filter((s) => SIDES.includes(s)))
}

/** How many rows of the border block to draw. One — a single row of trees. */
export function borderRows(game, key) {
  const r = entry(game, key)
  const n = Array.isArray(r) ? 1 : Number(r?.rows ?? 1)
  return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 1
}

/** True when this map has any border to draw at all — the viewer's fast path. */
export const hasBorder = (game, key, m) => borderSides(game, key, m).size > 0
