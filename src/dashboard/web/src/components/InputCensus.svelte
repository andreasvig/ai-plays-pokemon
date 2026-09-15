<script>
  // What every button press bought (artifacts/wasted-inputs/plan.md).
  //
  // One component, two callers: the local Report reads the census off the
  // replayed `referee.inputs`, the public model page off the row's
  // `input_breakdown`. Both are the same object, produced once by
  // src/app/replay.py — so the two surfaces cannot drift.
  //
  // The buckets PARTITION the run's inputs (trace.INPUT_BUCKETS, guarded by
  // tests/test_trace.py::test_the_buckets_partition_every_input), which is why
  // the header can state a total and mean it.
  let { inputs = null, compact = false } = $props()

  const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)}%`)

  const buckets = $derived(
    !inputs?.inputs
      ? []
      : [
          { key: 'moved', label: 'Moved the player', n: inputs.moved_inputs,
            note: `${(inputs.overworld_steps ?? 0).toLocaleString()} tiles — one scripted press can move several` },
          { key: 'battle', label: 'Pressed in a battle', n: inputs.battle_inputs, note: 'menus and attacks' },
          { key: 'wall', label: 'Walked into a wall', n: inputs.walls_hit, charged: true,
            note: 'already facing it, and the map has no way through' },
          { key: 'face', label: 'Turned to face', n: inputs.turns_to_face,
            note: 'first press in a new direction — how you face a sign or an NPC' },
          { key: 'eaten', label: 'Eaten by a textbox or an NPC', n: inputs.blocked_by_actor,
            note: 'the tile ahead is open, but nothing moved' },
          { key: 'unknown', label: 'Could not attribute', n: inputs.blocked_unknown,
            note: 'a door tile, or a tile off the walk graph' },
          { key: 'ab', label: 'A or B, nothing moved', n: inputs.idle_ab,
            note: 'advancing dialogue and pressing at nothing look identical here' },
          { key: 'edge', label: 'On a battle boundary', n: inputs.battle_edge,
            note: 'the press a battle ended on — neither an overworld press nor a battle one' },
          { key: 'none', label: 'Nothing to compare', n: inputs.unclassified,
            note: 'the bridge read no tile for this press' },
        ].filter((b) => typeof b.n === 'number' && b.n > 0)
  )
  const total = $derived(buckets.reduce((a, b) => a + b.n, 0))
  const max = $derived(Math.max(1, ...buckets.map((b) => b.n)))
</script>

{#if buckets.length}
  <div class="census" class:compact>
    <div class="chead">
      <h4>Where the inputs went</h4>
      <span class="sub">{total.toLocaleString()} presses over {inputs.traced_turns} turns</span>
      <span class="verdict" class:bad={inputs.wall_rate > 0.15}>{pct(inputs.wall_rate)} into walls</span>
    </div>
    <div class="btable">
      {#each buckets as b (b.key)}
        <div class="brow" class:charged={b.charged}>
          <span class="blabel">{b.label}{#if b.charged}<em>charged</em>{/if}</span>
          <span class="bbar"><i style={`width:${(b.n / max) * 100}%`}></i></span>
          <span class="bn tnum">{b.n.toLocaleString()}</span>
          <span class="bnote faint">{b.note}</span>
        </div>
      {/each}
    </div>
    <p class="bfoot faint">
      A press into a wall burns the same frames as a step and gains no ground, so each one is charged
      against movement efficiency like a walked step. The rate is measured against the
      {(inputs.overworld_inputs ?? 0).toLocaleString()} presses made outside a battle. Turning to face,
      presses an actor ate, and A/B are measured but never charged — the trace records the player's
      tile, not what was on screen, so they cannot be attributed.
    </p>
  </div>
{/if}

<style>
  .chead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  h4 { font-size: 13px; font-weight: 750; margin: 0; }
  .sub { font-size: 12px; color: var(--muted); }
  .verdict { font-size: 12px; font-weight: 650; margin-left: auto; }
  .verdict.bad { color: var(--red); }
  .btable { display: flex; flex-direction: column; gap: 4px; }
  .brow { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(70px, 180px) 56px minmax(0, 1.5fr); gap: 10px; align-items: center; font-size: 12px; }
  .blabel { font-weight: 600; }
  .blabel em { font-style: normal; font-size: 9.5px; text-transform: uppercase; letter-spacing: .05em; font-weight: 700; color: var(--red); margin-left: 6px; }
  .bbar { background: var(--border); border-radius: 3px; height: 8px; overflow: hidden; }
  .bbar i { display: block; height: 100%; background: var(--muted); border-radius: 3px; }
  .brow.charged .bbar i { background: var(--red); }
  .bn { text-align: right; font-weight: 650; }
  .bnote { font-size: 11px; }
  .bfoot { font-size: 11px; margin: 10px 0 0; line-height: 1.5; max-width: 72ch; }
  /* On the model page the census sits in a column, so the notes come off. */
  .compact .brow { grid-template-columns: minmax(150px, 1fr) minmax(60px, 1fr) 52px; }
  .compact .bnote, .compact .bfoot { display: none; }
  @media (max-width: 720px) {
    .brow { grid-template-columns: 1fr 56px; }
    .brow .bbar, .brow .bnote { display: none; }
  }
</style>
