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
  'firered-us': {
    '3:0': ['left', 'right'],               // PalletTown
    '3:1': ['up', 'down', 'left', 'right'], // ViridianCity
    '3:2': ['up', 'down', 'left', 'right'], // PewterCity
    '3:19': ['left', 'right'],              // Route1
    '3:20': ['left', 'right'],              // Route2
    '3:21': ['up', 'down', 'right'],        // Route3
    '3:39': ['left', 'right'],              // Route21_North
    '3:41': ['up', 'down', 'left'],         // Route22
  },
  'emerald-us': {
    '0:0': ['up', 'down', 'right'],         // PetalburgCity
    '0:9': ['down', 'left', 'right'],       // LittlerootTown
    '0:10': ['right'],                      // OldaleTown
    '0:16': ['left', 'right'],              // Route101
    '0:17': ['up', 'down'],                 // Route102
    '0:18': ['up', 'down', 'left'],         // Route103
    '0:19': ['left', 'right'],              // Route104
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
