<script>
  // What one battle was, shown on hovering its icon on the map (M15-M16).
  //
  // Andreas, 2026-09-15: "every time a battle happens I would also expect a
  // small icon which you can hover over and get the stats from just the turns in
  // battle, and outcome, win, run, capture, whiteout. If it is a wild battle
  // also which Pokémon it is, and if it is trainer, have the trainer sprite plus
  // which Pokémon they had, with level on the Pokémon."
  //
  // The trainer half is complete for every run ever recorded: the referee has
  // stored the trainer id since 2026-09-14 and the roster is a ROM constant —
  // though it is the ROSTER, not what was sent out, which we do not know.
  //
  // The wild half needed two memory reads that went in on 2026-09-15
  // (gBattleMons, gBattleOutcome — src/referee/battles.py). A run from before
  // that carries neither, and the card says the run did not look rather than
  // calling the outcome unknown: absent is not "we could not tell".
  import { trainerSpriteUrl } from '../lib/mapatlas.js'

  let { battle, trainers = null } = $props()

  const t = $derived(battle?.trainer_id != null ? trainers?.trainers?.[String(battle.trainer_id)] ?? null : null)
  const span = $derived(battle.closed_turn != null && battle.opened_turn != null
    ? (battle.closed_turn === battle.opened_turn ? `turn ${battle.opened_turn}` : `turns ${battle.opened_turn}–${battle.closed_turn}`)
    : `turn ${battle.opened_turn}`)
  const cost = $derived(battle.turns === 0 ? 'over inside one turn' : `${battle.turns} turn${battle.turns === 1 ? '' : 's'} in the battle`)
  const foe = $derived(battle.foe ?? null)
  const foeName = $derived(foe ? (trainers?.species?.[String(foe.species)] ?? `#${foe.species}`) : null)
  // The words the game's own outcome byte maps to, said the way a reader would.
  const OUTCOME = {
    won: ['won', 'win'], lost: ['lost', 'loss'], ran: ['ran', ''], caught: ['caught', 'win'],
    drew: ['drew', ''], teleported: ['teleported away', ''], mon_fled: ['it fled', ''],
    forfeited: ['forfeited', 'loss'], mon_teleported: ['it teleported away', ''],
  }
  const verdict = $derived(battle.outcome ? OUTCOME[battle.outcome] ?? [battle.outcome, ''] : null)
  // The referee's label usually IS the class and the given name, and printing
  // "Bug Catcher Rick" twice reads like a bug. Show the ROM's own naming only
  // where it says something the label does not — the rival, whose three ids
  // share one label but differ in the starter he took.
  const romName = $derived(t ? `${t.class} ${t.name}`.trim() : '')
  const sub = $derived(t ? (romName === (t.label ?? '') ? '' : romName) : 'not identified')
</script>

<div class="card" class:trainer={battle.kind === 'trainer'}>
  <div class="head">
    {#if t?.pic}<img src={trainerSpriteUrl(t.pic)} alt="" width="48" height="48" />{/if}
    <div class="who">
      <b>{battle.kind === 'trainer' ? (t?.label ?? battle.trainer ?? 'Trainer battle') : 'Wild battle'}</b>
      {#if battle.kind === 'trainer'}
        {#if sub}<span class="sub">{sub}</span>{/if}
      {:else if foe}
        <span class="sub">{foeName} · Lv {foe.level}</span>
      {:else}
        <span class="sub">this run did not record which Pokémon</span>
      {/if}
    </div>
    {#if verdict}
      <span class="verdict" class:win={verdict[1] === 'win'} class:loss={verdict[1] === 'loss'}>{verdict[0]}</span>
    {:else if battle.kind === 'trainer'}
      <span class="verdict" class:win={battle.won === true} class:loss={battle.won === false}>
        {battle.won === true ? 'won' : battle.won === false ? 'lost' : 'outcome unknown'}
      </span>
    {/if}
  </div>
  {#if t?.party?.length}
    <ul class="party">
      {#each t.party as m, i (i)}
        <li><span class="sp">{m.species}</span><span class="lv">Lv {m.level}</span></li>
      {/each}
    </ul>
    <p class="roster">the roster the ROM gives this trainer — not necessarily what was sent out</p>
  {/if}
  <div class="foot">
    <span>{span}</span>
    <span class="dot">·</span>
    <span>{cost}</span>
    {#if battle.uncounted}<span class="dot">·</span><span class="warn">flag only — no battle counted</span>{/if}
  </div>
  {#if battle.kind === 'wild' && !battle.outcome}
    <p class="roster">won, ran or caught all look the same here: this run predates the read that tells them apart</p>
  {/if}
</div>

<style>
  .card { min-width: 190px; max-width: 280px; display: flex; flex-direction: column; gap: 6px; }
  .head { display: flex; gap: 8px; align-items: center; }
  .head img { image-rendering: pixelated; flex: none; }
  .who { display: flex; flex-direction: column; min-width: 0; }
  .who b { font-weight: 700; }
  .sub { color: #9aa2b2; font-size: 10.5px; }
  .verdict { margin-left: auto; font-size: 10px; text-transform: uppercase; letter-spacing: .05em; font-weight: 700; color: #9aa2b2; }
  .verdict.win { color: #6fd98a; }
  .verdict.loss { color: #ff8b7a; }
  .party { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
  .party li { display: flex; gap: 8px; justify-content: space-between; }
  .lv { color: #9aa2b2; font-variant-numeric: tabular-nums; }
  .roster { margin: 0; font-size: 10px; color: #7f8798; line-height: 1.45; }
  .foot { display: flex; gap: 5px; flex-wrap: wrap; color: #c4cad6; }
  .dot { color: #6a7182; }
  .warn { color: #ffbf6b; }
</style>
