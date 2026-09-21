// Which mon a battle card is looking at — the picture and the name.
//
// A species id means DIFFERENT THINGS on different cartridges, and that is the
// whole of the bug this module exists to refuse. Andreas, 2026-09-21, on a
// Platinum card: *"it gets thwe wrong pokemion /sprite"* — the rival's Chimchar
// drew a picture of Anorith.
//
// Two numberings are in play across the seven games, and the referee reports
// each cartridge's OWN one (src/referee/contracts.py):
//
//   gen-3 internal  firered-us, emerald-us. 1..411, with 252..276 an unused
//                   band the ROM fills with one "?" pic, and the Hoenn species
//                   from 277. Emerald's Route 101 Poochyena reads 286, which is
//                   BRELOOM's National Dex number — that pair is what tells the
//                   two numberings apart, and it is measured: run
//                   2026-09-20_17-59-44 turn 91 reads 286 and the screen says
//                   POOCHYENA.
//   National Dex    crystal-us, platinum-us, soulsilver-us, black-us, black2-us.
//                   Gen 2 numbers by the National Dex to 251 and gen 4/5 do so
//                   outright (pokeplatinum's `NATIONAL_DEX_COUNT` is literally
//                   `MAX_SPECIES - 2`). Platinum turn 83 reads 390 with
//                   CHIMCHAR on the plate; Black turn 11 reads 495 with Snivy;
//                   Black 2 turn 168 reads 501 with Oshawott.
//
// `public/pokemon/<id>.png` is the gen-3 internal set (scripts/extract_trainers.py,
// out of pokefirered) and `public/pokemon/dex/<n>.png` is the National Dex one
// (scripts/extract_dex_sprites.py, out of Platinum's own pokegra archive plus
// the 156 Unova species). Feeding one keyspace's number to the other set is the
// defect; the point of this module is that the number and the set are chosen
// together, from the game, in one place.
//
// The names work the same way and are NOT the same table: gen 3's per-game
// `trainers/<game>/index.json` carries `species` keyed by internal index, and
// the dex set carries its own keyed by dex number. A card with neither shows
// `#<id>`, which is honest and is what a Crystal card showed for every wild
// battle until 2026-09-21.
import { BASE } from './static.js'

/** How each cartridge numbers a species — the `configs/roms.yaml` game key.
 *
 *  Crystal sits with the dex games because that is what gen 2 is, even though
 *  its whole range (1..251) is a range where the two numberings agree, so it
 *  would render the same species out of either set. Filing it by what is TRUE
 *  rather than by what happens not to matter is the difference between a table
 *  that stays right when a Johto run meets its 252nd species and one that does
 *  not.
 *
 *  A game absent here is a game whose numbering nobody has established. */
export const SPECIES_KEYSPACE = {
  'crystal-us': 'national-dex',
  'firered-us': 'gen3-internal',
  'emerald-us': 'gen3-internal',
  'platinum-us': 'national-dex',
  'soulsilver-us': 'national-dex',
  'black-us': 'national-dex',
  'black2-us': 'national-dex',
}

/** The "?" pic FireRed itself draws for a species that is not one — gen-3
 *  internal 252, the first of the unused band, copied to its own name by
 *  scripts/extract_dex_sprites.py. A card with no sprite shows THIS rather than
 *  hiding the image: a blank slot reads as "wild battle, no mon", which is a
 *  claim, and a visibly-unknown one does not. */
export const UNKNOWN_SPRITE = `${BASE}pokemon/unknown.png`

/**
 * The sprite for `id` as `game` numbers it, or the placeholder.
 *
 * A null/unknown game returns the placeholder rather than guessing a keyspace.
 * Same rule as route.py's `_game_and_contract` ("a route that cannot name its
 * cartridge must not be drawn on some other cartridge's artwork") and as
 * `loadAtlas`, which refuses an atlas that does not say it is this game's:
 * drawing the wrong mon confidently is worse than drawing a question mark.
 */
export function pokemonSpriteUrl(game, id) {
  const n = Number(id)
  if (!Number.isInteger(n) || n < 1) return UNKNOWN_SPRITE
  const keyspace = SPECIES_KEYSPACE[game]
  if (keyspace === 'gen3-internal') return n <= 411 ? `${BASE}pokemon/${n}.png` : UNKNOWN_SPRITE
  if (keyspace === 'national-dex') return n <= 649 ? `${BASE}pokemon/dex/${n}.png` : UNKNOWN_SPRITE
  return UNKNOWN_SPRITE
}

/** A trainer's party member, whose id comes from the pret roster in
 *  `trainers/<game>/index.json` and is therefore ALWAYS gen-3 internal — the
 *  only two cartridges with a roster are the two gen-3 ones. Separate from the
 *  function above because the number has a different provenance, not because
 *  it happens to resolve the same way today. */
export function partySpriteUrl(id) {
  const n = Number(id)
  return Number.isInteger(n) && n >= 1 && n <= 411 ? `${BASE}pokemon/${n}.png` : UNKNOWN_SPRITE
}

const dexPromise = { p: null }
/** `{version, keyspace, species: {"390": "Chimchar", …}}`, fetched once.
 *
 *  National Dex names do not change between generations — Rattata is Rattata on
 *  every cartridge — so ONE table serves all five dex-numbered games, unlike
 *  the sprites' per-game trainer index. scripts/extract_dex_sprites.py refuses
 *  to write it unless all 386 gen-3 species agree with the names pret already
 *  gives this repo, which is what makes that claim checked rather than assumed. */
export function loadDexNames() {
  if (!dexPromise.p) {
    dexPromise.p = fetch(`${BASE}pokemon/dex/index.json`, { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : null))
      // The dev server answers an unknown path with the SPA's index.html at
      // status 200, so a missing file arrives as HTML rather than a 404 and
      // `json()` is what rejects. Same trap loadAtlas documents.
      .then((d) => (d && d.keyspace === 'national-dex' && d.species ? d : null))
      .catch(() => null)
  }
  return dexPromise.p
}

/** Tests and the dev harness drop the memo. */
export function _resetSpecies() { dexPromise.p = null }

/**
 * What to call species `id` on `game`, or `#<id>` when nothing names it.
 *
 * `trainers` is the per-game roster index (gen-3 internal names) and `dex` is
 * the National Dex table; each game reads the one its numbering belongs to and
 * never the other, which is the same rule as the sprite above. `#390` stays the
 * fallback: a number with no name is a number, and printing a guessed name
 * would be the sprite bug wearing words.
 */
export function speciesName(game, id, { trainers = null, dex = null } = {}) {
  if (id == null) return null
  const key = String(id)
  const keyspace = SPECIES_KEYSPACE[game]
  const table = keyspace === 'national-dex' ? dex?.species : (keyspace === 'gen3-internal' ? trainers?.species : null)
  return table?.[key] ?? `#${key}`
}
