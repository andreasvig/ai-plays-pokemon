// Unit tests for src/dashboard/web/src/lib/battle.js — what a battle card is
// allowed to claim.
//
// The defect these were written against, found 2026-09-20: src/app/route.py
// reports `kind: null` for every battle on the six games BattleTracker does not
// cover, deliberately, and tests/test_route_multigame.py:474 pins that. The
// Svelte side had no null branch, so a Platinum gym leader rendered as "Wild
// battle", the verdict element disappeared, and a screen reader was read
// "null battle on turn 12".
//
// Every assertion below is about the NULL case specifically. None of them can
// be satisfied by weakening a wild-case or trainer-case assertion, which is the
// point: re-pointing a test at rewritten prose must not lower what it claims.
import test from 'node:test'
import assert from 'node:assert/strict'
import { battleTitle, battleSubtitle, battleVerdict, battleNote, battleAria, kindIsKnown }
  from '../../src/dashboard/web/src/lib/battle.js'

// The same table BattleCard.svelte renders from.
const OUTCOME = {
  won: ['won', 'win'], lost: ['lost', 'loss'], ran: ['ran', ''], caught: ['caught', 'win'],
  drew: ['drew', ''], teleported: ['teleported away', ''], mon_fled: ['it fled', ''],
  forfeited: ['forfeited', 'loss'], mon_teleported: ['it teleported away', ''],
}

// Exactly what _battles_from_trace emits: a tile, turns, and nothing else.
const traced = (extra = {}) => ({
  kind: null, opened_turn: 12, closed_turn: 13, trainer_id: null, trainer: null,
  won: null, uncounted: false, tile: [418, 0, 20, 14], turns: 2, ...extra,
})

test('an unknown kind is not reported as wild', () => {
  const b = traced()
  assert.equal(kindIsKnown(b), false)
  assert.notEqual(battleTitle(b), 'Wild battle')
  assert.equal(battleTitle(b), 'Battle')
})

test('an unknown kind does not borrow the wild explanation', () => {
  // "this run did not record which Pokémon" presumes the fight WAS wild.
  const sub = battleSubtitle(traced())
  assert.notEqual(sub, 'this run did not record which Pokémon')
  assert.match(sub, /wild or trainer/)
})

test('an unknown kind still renders a verdict, saying it was not measured', () => {
  // The bug: the old markup reached "outcome unknown" only through
  // kind === 'trainer', so this case rendered no verdict element at all.
  const v = battleVerdict(traced(), OUTCOME)
  assert.notEqual(v, null, 'an unknown-kind battle must not render a blank verdict slot')
  assert.equal(v.tone, '')
  assert.match(v.text, /not measured/)
})

test('an unknown kind does not get the wild-only footnote', () => {
  const note = battleNote(traced())
  assert.doesNotMatch(note, /won, ran or caught/)
  assert.match(note, /in-battle flag alone/)
})

test('screen-reader text never interpolates a null kind', () => {
  const aria = battleAria(traced())
  assert.doesNotMatch(aria, /null/)
  assert.equal(aria, 'Battle of unrecorded kind on turn 12')
})

// --- the two known kinds must be unchanged by the fix -----------------------
// Controls. If the null case were made to pass by loosening these, they fail.

test('a wild battle still says wild, and keeps its own footnote', () => {
  const b = traced({ kind: 'wild' })
  assert.equal(battleTitle(b), 'Wild battle')
  assert.equal(battleSubtitle(b), 'this run did not record which Pokémon')
  assert.match(battleNote(b), /won, ran or caught/)
  assert.equal(battleAria(b), 'Wild battle on turn 12')
})

test('a wild battle with a foe read names it', () => {
  const b = traced({ kind: 'wild' })
  assert.equal(battleSubtitle(b, { foeName: 'Starly', foeLevel: 3 }), 'Starly · Lv 3')
  assert.equal(battleNote(b), 'won, ran or caught all look the same here: this run predates the read that tells them apart')
})

test('a trainer battle prefers its roster label and reports an unknown outcome', () => {
  const b = traced({ kind: 'trainer', trainer_id: 4, trainer: 'Rick' })
  assert.equal(battleTitle(b, 'Bug Catcher Rick'), 'Bug Catcher Rick')
  assert.equal(battleTitle(b, null), 'Rick')
  assert.deepEqual(battleVerdict(b, OUTCOME), { text: 'outcome unknown', tone: '' })
  assert.equal(battleNote(b), '')
})

test('a recorded outcome wins over won/lost, for every kind', () => {
  for (const kind of ['wild', 'trainer', null]) {
    assert.deepEqual(battleVerdict(traced({ kind, outcome: 'caught' }), OUTCOME),
      { text: 'caught', tone: 'win' })
  }
  assert.deepEqual(battleVerdict(traced({ kind: null, outcome: 'ran' }), OUTCOME), { text: 'ran', tone: '' })
})

test('an outcome the table does not know is shown verbatim, not dropped', () => {
  assert.deepEqual(battleVerdict(traced({ outcome: 'sublimated' }), OUTCOME),
    { text: 'sublimated', tone: '' })
})

test('won/lost are honoured even when the kind is unknown', () => {
  assert.deepEqual(battleVerdict(traced({ won: true }), OUTCOME), { text: 'won', tone: 'win' })
  assert.deepEqual(battleVerdict(traced({ won: false }), OUTCOME), { text: 'lost', tone: 'loss' })
})

test('an unfinished fight is not an unmeasured one', () => {
  // The two reasons a card has no outcome are different sentences. Before
  // 2026-09-20 there was one: the run predates the read. The first Emerald run
  // to carry a real card hit the other — it reached its 60-turn cap mid-fight,
  // so `closed_turn` is null and there was no close for the game to write a
  // result at. Saying "this run predates the read" there is simply false.
  const cut = { kind: 'wild', opened_turn: 57, closed_turn: null, turns: 2,
                foe: { species: 288, level: 2 } }
  assert.match(battleNote(cut), /ended before this fight did/)
  assert.doesNotMatch(battleNote(cut), /predates/)

  // and the old case still reads the old way
  const old = { kind: 'wild', opened_turn: 30, closed_turn: 33, turns: 3 }
  assert.match(battleNote(old), /predates the read/)

  // a fight that DID close and was measured says nothing at all
  assert.equal(battleNote({ kind: 'wild', opened_turn: 30, closed_turn: 33, outcome: 'ran' }), '')
})

test('a live read that missed is not a run that predates the read', () => {
  // The third reason, found on 2026-09-21 when Platinum's outcome went in:
  // the read IS live and the fight's result still was not caught, because the
  // game writes the flag in the last handful of frames and the trace samples
  // once per button. That continuation has five fights, one with a result and
  // four without — and the card told all four they predated a read the run
  // itself was carrying.
  const missed = { kind: 'wild', opened_turn: 191, closed_turn: 196, turns: 5,
                   outcome_read: true, foe: { species: 396, level: 2 } }
  assert.equal(battleNote(missed), 'the fight ended between two samples, so the game never showed us how')
  assert.doesNotMatch(battleNote(missed), /predates/)

  // Explicitly false is the run that really did predate it, and keeps the
  // old sentence — this is the pair that makes the field mean anything.
  const predates = { ...missed, outcome_read: false }
  assert.match(battleNote(predates), /predates the read/)

  // A payload written before the field has no key at all, and must fall to
  // the OLD sentence: absent is not "the read was live". Every such run
  // predates the read on four of the seven cartridges.
  const older = { kind: 'wild', opened_turn: 30, closed_turn: 33, turns: 3 }
  assert.equal(older.outcome_read, undefined)
  assert.match(battleNote(older), /predates the read/)

  // and the field changes NOTHING about a fight that has its outcome, or one
  // the run ended in the middle of — those branches come first and stay first.
  assert.equal(battleNote({ ...missed, outcome: 'won' }), '')
  assert.match(battleNote({ ...missed, closed_turn: null }), /ended before this fight did/)
})
