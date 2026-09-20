// Which map edges get the game's border block drawn outside them.
//
// This started automatic — every map, every side — and it was wrong twice over.
//
// First it painted over the roads. A map's border block is what the game
// repeats outside its bounds ONLY where there is nothing else; on an edge with
// a connection the game draws the neighbour. Tiling it everywhere put a wall of
// trees across Oldale Town's two exits (Andreas, 2026-09-20: "the problem is
// that the woods you create cover possible roads, so it looks like there is no
// road there ... where you can both go up and to the right"). That half is
// fixed in the data: `open` in the atlas carries the exact spans a neighbour
// covers, from pret's own `connections`, and nothing is ever drawn across one.
//
// Then, with the roads clear, the trees themselves still looked wrong: one
// block repeated on a perfect grid beside the map's own denser, differently
// shaded trees, and running straight into the sea — "it just looks very weird
// with the random adding of trees with the water and in combination with the
// other trees. maybe the answer is just to add them manually to the places
// which feel like they are missing them."
//
// So that is what this is. A border is drawn where it is NAMED here and
// nowhere else. Empty means every map ends at its own edge and fades to the
// background, which is how the published site looks and what it looked like
// before any of this.
//
// To add one: find the map's key in `public/maps/<game>/index.json` and list
// the sides you want. 'up' | 'down' | 'left' | 'right', the same four words
// pret's connections use.
//
//   'firered-us': { '3:20': ['left'] }   // Route 2's west cliff
//
// A side listed here is still never drawn across an `open` span — the curated
// list can only ADD border where the game itself would have drawn one, it
// cannot put a wall over a road.
export const BORDER_EDGES = {
  // nothing yet — add a map key and its sides when an edge reads as missing
}

const SIDES = ['up', 'down', 'left', 'right']

/** The sides to draw for one map, as a Set. Empty unless curated. */
export function borderSides(game, key) {
  const want = BORDER_EDGES?.[game]?.[key]
  if (!Array.isArray(want)) return new Set()
  return new Set(want.filter((s) => SIDES.includes(s)))
}

/** True when this map has any border to draw at all — the viewer's fast path. */
export const hasBorder = (game, key, m) => !!m?.border && borderSides(game, key).size > 0
