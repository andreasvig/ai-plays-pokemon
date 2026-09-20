// What a battle record is ALLOWED to claim, in one place.
//
// The backend deliberately reports `kind: null` for every battle it found from
// the trace alone — src/app/route.py:_battles_from_trace, whose docstring says
// it "CANNOT know, and must not pretend: the outcome, the foe, the trainer, and
// whether the fight was wild or a trainer's", and tests/test_route_multigame.py
// pins that honesty. That is six of the seven games today, because BattleTracker
// is FireRed-only.
//
// The rendering contradicted it. `kind === 'trainer' ? … : 'Wild battle'` turns
// a *missing* category into a positive claim, so a Platinum gym leader rendered
// as "Wild battle" with a hollow (= wild) dot, the verdict element vanished
// entirely, and a screen reader was read "null battle on turn 12". A card must
// not say something the Python refused to say.
//
// Three-valued throughout: 'trainer' | 'wild' | null. Never coerce null to
// either side — `kind === 'wild'` and `kind !== 'trainer'` are different
// questions and only the first one is honest.

/** True only when the record positively says which kind of fight this was. */
export const kindIsKnown = (battle) => battle?.kind === 'trainer' || battle?.kind === 'wild'

/**
 * The headline. `trainerLabel` is the roster label when one resolved.
 * An unknown kind says so rather than picking the more common answer.
 */
export function battleTitle(battle, trainerLabel = null) {
  if (battle?.kind === 'trainer') return trainerLabel ?? battle.trainer ?? 'Trainer battle'
  if (battle?.kind === 'wild') return 'Wild battle'
  return 'Battle'
}

/**
 * The line under the headline: who was fought, or why we cannot say.
 *
 * The distinction the old code lost: a WILD battle with no foe read means the
 * run did not look, while an UNKNOWN kind means we never learned what sort of
 * fight it was at all. Those are different sentences and they license different
 * conclusions.
 */
export function battleSubtitle(battle, { trainerSub = '', foeName = null, foeLevel = null } = {}) {
  if (battle?.kind === 'trainer') return trainerSub || ''
  if (foeName) return foeLevel != null ? `${foeName} · Lv ${foeLevel}` : foeName
  if (battle?.kind === 'wild') return 'this run did not record which Pokémon'
  return 'wild or trainer — this game does not say yet'
}

/**
 * `{text, tone}` for the verdict chip, or null to render none.
 *
 * `tone` is '' | 'win' | 'loss'. The bug this replaces: the old markup reached
 * its "outcome unknown" branch only via `kind === 'trainer'`, so an unknown-kind
 * battle rendered NO verdict element — silently dropping the one field that
 * would have told a reader the outcome was not measured.
 */
export function battleVerdict(battle, outcomeWords) {
  if (battle?.outcome) {
    const [text, tone] = outcomeWords[battle.outcome] ?? [battle.outcome, '']
    return { text, tone }
  }
  if (battle?.won === true) return { text: 'won', tone: 'win' }
  if (battle?.won === false) return { text: 'lost', tone: 'loss' }
  if (battle?.kind === 'trainer') return { text: 'outcome unknown', tone: '' }
  if (!kindIsKnown(battle)) return { text: 'outcome not measured', tone: '' }
  return null
}

/**
 * The footnote, or '' for none.
 *
 * The wild-only note explains why won/ran/caught look alike on an old FireRed
 * run. It must NOT appear on an unknown-kind battle, which has a different and
 * larger gap — that one gets its own line.
 */
export function battleNote(battle) {
  // A fight with no outcome has TWO possible reasons and they are not the same
  // sentence. Since 2026-09-20 the outcome is read off the trace on both gen-3
  // cartridges, so "this run predates the read" became a claim that is false
  // for a live run — and the first Emerald run to carry a real card hit exactly
  // the other case: it reached its turn cap mid-fight, so `closed_turn` is null
  // and there was no close for the game to write a result at.
  if (battle?.closed_turn == null && battle?.opened_turn != null) {
    return 'the run ended before this fight did, so the game never wrote a result'
  }
  if (battle?.kind === 'wild' && !battle?.outcome) {
    return 'won, ran or caught all look the same here: this run predates the read that tells them apart'
  }
  if (!kindIsKnown(battle)) {
    return 'found from the in-battle flag alone: the tile is measured, the kind and outcome are not'
  }
  return ''
}

/** Screen-reader text. Never interpolates a null kind into the sentence. */
export function battleAria(battle) {
  const turn = battle?.opened_turn
  const where = turn != null ? ` on turn ${turn}` : ''
  if (battle?.kind === 'trainer') return `Trainer battle${where}`
  if (battle?.kind === 'wild') return `Wild battle${where}`
  return `Battle of unrecorded kind${where}`
}
