// Which map edges get the game's border block drawn outside them.
//
// A map's border block is the 2x2 metatile the game repeats beyond its own
// bounds. Drawing it is what stops a town ending at a hard black line — but it
// took three passes to get right, and the two failures are worth keeping,
// because each one made the SAME picture look wrong for a different reason.
//
//  1. Roads. The game repeats the block only where there is nothing else; on
//     an edge with a connection it draws the NEIGHBOUR. Tiling it everywhere
//     put a wall of trees across Oldale Town's three exits — Andreas,
//     2026-09-20: "the woods you create cover possible roads, so it looks like
//     there is no road there ... where you can both go up and to the right."
//     Fixed in the data, not here: `open` on each atlas entry carries the exact
//     spans a neighbour covers, from pret's own `connections`, and the viewer
//     punches every one of them out of the border. Nothing can paint a road.
//
//  2. Scale. It was 28 tiles in every direction, which is not a fringe — it is
//     a second map made of one repeated block, and where two of those fields
//     met they butted along a straight line, tan against trees. Now 8, roughly
//     what the GBA itself shows past an edge, faded out over the outer 6.
//
// With both of those fixed the picture reads as one region again. What is left
// is taste, one edge at a time, which is what this file is for: Andreas,
// looking at a border running into the sea, "maybe the answer is just to add
// them manually to the places which feel like they are missing them."
//
// So the default is ON for every closed edge, and this list turns one OFF:
//
//   export const BORDER_OFF = {
//     'emerald-us': { '0:19': ['left'] },   // Route 104's sea edge
//     'firered-us': { '1:0': true },        // the whole map, never
//   }
//
// A side is named with the same four words pret's connections use.
export const BORDER_OFF = {
  // nothing suppressed yet — name a map's side when its border reads wrong
}

const SIDES = ['up', 'down', 'left', 'right']

/**
 * The sides to draw for one map, as a Set.
 *
 * Every closed side, less whatever `BORDER_OFF` suppresses. The `open` spans
 * are NOT subtracted here: a connection usually covers part of an edge rather
 * than all of it — Route 2 takes 24 tiles of Viridian City's 48 — so the side
 * is still drawn and the viewer punches the span out of it.
 */
export function borderSides(game, key, m) {
  if (!m?.border) return new Set()
  const off = BORDER_OFF?.[game]?.[key]
  if (off === true) return new Set()
  const hidden = Array.isArray(off) ? off : []
  return new Set(SIDES.filter((s) => !hidden.includes(s)))
}

/** True when this map has any border to draw at all — the viewer's fast path. */
export const hasBorder = (game, key, m) => borderSides(game, key, m).size > 0
