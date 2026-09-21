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
  // A battle found from the trace alone carries `kind: null`, and route.py
  // refuses to guess. Until 2026-09-20 this card guessed for it — rendering
  // "Wild battle" for every Platinum and Crystal fight, gym leaders included.
  // The decision logic now lives in one tested module (tests/js/battle.test.mjs)
  // so the card cannot claim more than the backend measured.
  import { trainerSpriteUrl } from '../lib/mapatlas.js'
  import { pokemonSpriteUrl, partySpriteUrl, speciesName, loadDexNames, UNKNOWN_SPRITE,
           SPECIES_KEYSPACE } from '../lib/species.js'
  import { battleTitle, battleSubtitle, battleVerdict, battleNote } from '../lib/battle.js'

  let { battle, trainers = null, game = null } = $props()

  // The species id the referee read means what THIS cartridge means by it, and
  // that differs by generation — so both the picture and the name are looked up
  // through lib/species.js, which takes the game. Until 2026-09-21 the card
  // passed the bare id to one gen-3-internal file set, so Platinum's rival
  // Chimchar (National Dex 390) drew gen-3 internal 390, which is Anorith, and
  // every Black card drew nothing at all because gen 5's numbers run past the
  // end of that set.
  let dex = $state(null)
  $effect(() => {
    if (SPECIES_KEYSPACE[game] !== 'national-dex') return
    loadDexNames().then((d) => { dex = d })
  })

  const t = $derived(battle?.trainer_id != null ? trainers?.trainers?.[String(battle.trainer_id)] ?? null : null)
  // ONE line about turns, and it has to agree with itself (Andreas 2026-09-16).
  // `turns` counts the turns that STARTED inside the fight, so the turn the
  // player walked INTO it (opened_turn) is not one of them: printing "turns
  // 43–47" beside "4 turns" showed a 5-turn range for a 4-turn number. The range
  // printed is the charged turns, opened+1…closed, and only when the two agree —
  // a gap in the per-turn poll would break that, and then the count stands alone.
  const charged = $derived(
    battle.turns > 0 && battle.closed_turn != null && battle.opened_turn != null
      && battle.closed_turn - battle.opened_turn === battle.turns
      ? (battle.turns === 1 ? `T${battle.closed_turn}` : `T${battle.opened_turn + 1}–T${battle.closed_turn}`)
      : null)
  const cost = $derived(battle.turns === 0
    ? `over inside turn ${battle.opened_turn}`
    : `${battle.turns} turn${battle.turns === 1 ? '' : 's'} in the battle${charged ? ` · ${charged}` : ` from T${battle.opened_turn + 1}`}`)
  // Andreas, 2026-09-16: "for the battle I would like Pokemon sprites on the
  // hover, for both trainer Pokemon and wild Pokemon." The file is named by
  // species id, so a party member needs its id — added to the roster in
  // scripts/extract_trainers.py (index version 3). A roster from the older
  // index has no `id` and simply shows no sprite rather than a broken one.
  //
  // A TRAINER portrait that will not load is still hidden: there is no
  // stand-in picture of a person, and a "?" mon pic in a human's place would
  // say something false. A MON that will not load shows the ROM's own "?"
  // instead, because hiding it leaves a card that looks like it met nobody.
  const hide = (e) => { e.currentTarget.style.display = 'none' }
  const unknown = (e) => {
    const img = e.currentTarget
    if (img.getAttribute('src') !== UNKNOWN_SPRITE) img.setAttribute('src', UNKNOWN_SPRITE)
    else img.style.display = 'none'
  }
  const foe = $derived(battle.foe ?? null)
  const foeName = $derived(foe ? speciesName(game, foe.species, { trainers, dex }) : null)
  // The words the game's own outcome byte maps to, said the way a reader would.
  const OUTCOME = {
    won: ['won', 'win'], lost: ['lost', 'loss'], ran: ['ran', ''], caught: ['caught', 'win'],
    drew: ['drew', ''], teleported: ['teleported away', ''], mon_fled: ['it fled', ''],
    forfeited: ['forfeited', 'loss'], mon_teleported: ['it teleported away', ''],
  }
  const verdict = $derived(battleVerdict(battle, OUTCOME))
  // The referee's label usually IS the class and the given name, and printing
  // "Bug Catcher Rick" twice reads like a bug. Show the ROM's own naming only
  // where it says something the label does not — the rival, whose three ids
  // share one label but differ in the starter he took.
  const romName = $derived(t ? `${t.class} ${t.name}`.trim() : '')
  const sub = $derived(t ? (romName === (t.label ?? '') ? '' : romName) : 'not identified')
  const title = $derived(battleTitle(battle, t?.label ?? null))
  const subtitle = $derived(battleSubtitle(battle, {
    trainerSub: sub, foeName, foeLevel: foe?.level ?? null,
  }))
  const note = $derived(battleNote(battle))
</script>

<div class="card" class:trainer={battle.kind === 'trainer'}>
  <div class="head">
    {#if t?.pic && game}
      <img src={trainerSpriteUrl(game, t.pic)} alt="" width="48" height="48" onerror={hide} />
    {:else if foe?.species}
      <!-- The foe's own sprite, whenever the run read a species — not gated on
           `kind === 'wild'`, which would drop it for exactly the battles whose
           kind this game does not report. -->
      <img class="mon" src={pokemonSpriteUrl(game, foe.species)} alt="" width="48" height="48" onerror={unknown} />
    {/if}
    <div class="who">
      <b>{title}</b>
      {#if subtitle}<span class="sub">{subtitle}</span>{/if}
    </div>
    {#if verdict}
      <span class="verdict" class:win={verdict.tone === 'win'} class:loss={verdict.tone === 'loss'}>{verdict.text}</span>
    {/if}
  </div>
  {#if t?.party?.length}
    <ul class="party">
      {#each t.party as m, i (i)}
        <li>
          {#if m.id}<img class="mon" src={partySpriteUrl(m.id)} alt="" width="28" height="28" onerror={unknown} />{/if}
          <span class="sp">{m.species}</span><span class="lv">Lv {m.level}</span>
        </li>
      {/each}
    </ul>
  {/if}
  <div class="foot">
    <span>{cost}</span>
    {#if battle.uncounted}<span class="dot">·</span><span class="warn">flag only — no battle counted</span>{/if}
  </div>
  {#if note}
    <p class="roster">{note}</p>
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
  .party li { display: flex; gap: 7px; align-items: center; }
  .party .sp { flex: 1; }
  .mon { image-rendering: pixelated; flex: none; }
  /* The pic is 64 px of mostly air; crop the dead margin so a 28 px row reads. */
  .party .mon { margin: -4px -2px; }
  .lv { color: #9aa2b2; font-variant-numeric: tabular-nums; }
  .roster { margin: 0; font-size: 10px; color: #7f8798; line-height: 1.45; }
  .foot { display: flex; gap: 5px; flex-wrap: wrap; color: #c4cad6; }
  .dot { color: #6a7182; }
  .warn { color: #ffbf6b; }
</style>
