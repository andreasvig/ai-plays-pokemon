// DEV-ONLY harness for src/components/BattleCard.svelte, one card per cartridge.
//
//   npm run dev  →  http://localhost:5173/battlecards.html
//
// Every battle below is REAL — the `foe` block a recorded run's own
// `src/app/route.py` output carries, pasted unchanged, with the screenshot that
// shows what was on the other side named beside it. That is the whole point:
// a species id means a different mon depending on the cartridge, so the only
// way to see the card is right is to draw the seven cartridges side by side and
// compare each against the frame the run captured.
//
// Not referenced by src/main.js, and battlecards.html is not the Vite build
// input, so none of it reaches `npm run build`.
import { mount } from 'svelte'
import BattleCard from '../components/BattleCard.svelte'
import { loadTrainers } from '../lib/mapatlas.js'

const seg = (extra) => ({
  kind: 'wild', opened_turn: 40, closed_turn: 44, turns: 4, trainer_id: null,
  trainer: null, won: null, uncounted: false, tile: [1, 0, 3, 4], ...extra,
})

export const CASES = [
  { game: 'crystal-us', say: 'SENTRET Lv3 — run 2026-09-20_19-14-38 turn 46',
    battle: seg({ foe: { species: 161, level: 2 }, outcome: 'ran' }) },
  // The exact card the orchestrator shot on 2026-09-21, which read "#19 · Lv 2"
  // beside a picture of a Rattata: right sprite, no name.
  { game: 'crystal-us', say: 'RATTATA Lv2 — run 2026-09-20_21-18-34 turn 74',
    battle: seg({ opened_turn: 73, closed_turn: 73, turns: 1,
                  foe: { species: 19, level: 2 }, outcome: 'ran' }) },
  { game: 'firered-us', say: 'CHARMANDER Lv5, the rival — run 2026-09-20_17-42-57 turn 22',
    battle: seg({ kind: 'trainer', trainer_id: 328, trainer: 'Rival (Route 22)',
                  foe: { species: 4, level: 5 }, outcome: 'won' }) },
  { game: 'emerald-us', say: 'POOCHYENA Lv2 — run 2026-09-20_17-59-44 turn 91 (dex 286 is Breloom)',
    battle: seg({ foe: { species: 286, level: 2 }, outcome: 'ran' }) },
  { game: 'platinum-us', say: 'CHIMCHAR Lv5, the rival — run 2026-09-20_21-42-46 turn 83 (gen-3 390 is Anorith)',
    battle: seg({ kind: 'trainer', opened_turn: 81, closed_turn: 89, turns: 9,
                  trainer_id: 851, foe: { species: 390, level: 5 } }) },
  { game: 'platinum-us', say: 'SHINX — run 2026-09-20_09-53-29 turn 25 (gen-3 403 is Registeel)',
    battle: seg({ kind: null, foe: { species: 403 } }) },
  { game: 'soulsilver-us', say: 'SENTRET Lv3 — run 2026-09-21_09-03-49 turn 154',
    battle: seg({ kind: null, foe: { species: 161, level: 3 } }) },
  { game: 'black-us', say: 'Snivy Lv.5 — run 2026-09-20_23-09-22 turn 11',
    battle: seg({ kind: 'trainer', foe: { species: 495, level: 5 } }) },
  { game: 'black-us', say: 'Patrat Lv.2 — run 2026-09-20_23-09-22 turn 97',
    battle: seg({ foe: { species: 504, level: 2 } }) },
  { game: 'black2-us', say: 'Oshawott Lv.5, Hugh — run 2026-09-20_00-51-00 turn 168',
    battle: seg({ kind: 'trainer', foe: { species: 501, level: 5 } }) },
  // The two failure modes the card must survive rather than hide: a species
  // past the end of its own keyspace, and a run that cannot name its cartridge.
  { game: 'black-us', say: 'PLACEHOLDER — 999 is past the end of gen 5',
    battle: seg({ foe: { species: 999, level: 9 } }) },
  { game: null, say: 'PLACEHOLDER — no game, so no keyspace, so no guess',
    battle: seg({ foe: { species: 390, level: 5 } }) },
]

const root = document.getElementById('app')
for (const c of CASES) {
  const row = document.createElement('div')
  row.className = 'row'
  const label = document.createElement('div')
  label.className = 'label'
  label.textContent = `${c.game ?? '(no game)'} — species ${c.battle.foe.species} — screen says: ${c.say}`
  const host = document.createElement('div')
  host.className = 'host'
  row.append(label, host)
  root.append(row)
  // The roster index is per game and only the two gen-3 cartridges have one;
  // the others resolve to null, which is what the card has to cope with.
  loadTrainers(c.game).then((trainers) => {
    mount(BattleCard, { target: host, props: { battle: c.battle, trainers, game: c.game } })
  })
}
