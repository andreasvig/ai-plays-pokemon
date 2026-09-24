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

  const buckets = $derived(
    !inputs?.inputs
      ? []
      : [
          { key: 'moved', label: 'Moved the player', n: inputs.moved_inputs,
            note: `${(inputs.overworld_steps ?? 0).toLocaleString()} tiles — one scripted press can move several` },
          { key: 'battle', label: 'Pressed in a battle', n: inputs.battle_inputs, note: 'menus and attacks' },
          { key: 'wall', label: 'Walked into a wall', n: inputs.walls_hit,
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
  // Which columns fit, from the table's OWN width (Andreas 2026-09-17) — the
  // census sits full-width under a run on a phone and in a card on a desktop,
  // and a window query cannot tell those apart. The label's old 170px floor
  // outranked the note's `1.1fr` share, so at a ~665px table the note was left
  // 55px and wrapped one word per line; the bar goes first now, because the
  // share column already says what it says.
  let bw = $state(0)
  const showBar = $derived(!compact && bw >= 700)
  const showNote = $derived(!compact && bw >= 560)
  const tight = $derived(bw > 0 && bw < 420)
  const bcols = $derived([
    showNote ? 'minmax(150px, 1fr)' : 'minmax(0, 1fr)',
    ...(showBar ? ['minmax(110px, 260px)'] : []),
    tight ? '56px' : '72px', tight ? '46px' : '58px',
    ...(showNote ? ['minmax(0, 1.2fr)'] : []),
  ].join(' '))
  // The buckets partition the run's presses, so each share is out of the same
  // total and the column adds to 100%.
  const share = (n) => (total ? `${(n * 100 / total).toFixed(n * 100 / total < 10 ? 1 : 0)}%` : '—')
  // The track IS the whole run's presses, so a 55% bucket fills 55% of it
  // (Andreas 2026-09-16). Scaling to the largest bucket instead would have made
  // the biggest one look like all of them.
</script>

{#if buckets.length}
  <div class="census" class:compact>
    <div class="chead">
      <h4>Where the inputs went</h4>
      <span class="sub">{total.toLocaleString()} presses over {inputs.traced_turns} turns</span>
    </div>
    <div class="btable" class:tight class:nobar={!showBar} class:nonote={!showNote}
         bind:clientWidth={bw} style={`--bcols:${bcols}`}>
      <div class="brow head">
        <span>where</span><span class="colbar"></span><span class="r">presses</span><span class="r">share</span><span class="colnote">what it was</span>
      </div>
      {#each buckets as b, i (b.key)}
        <div class="brow" class:alt={i % 2 === 1}>
          <span class="blabel">{b.label}</span>
          <span class="bbar colbar"><i style={`width:${total ? (b.n / total) * 100 : 0}%`}></i></span>
          <span class="bn tnum">{b.n.toLocaleString()}</span>
          <span class="bs tnum">{share(b.n)}</span>
          <span class="bnote colnote faint">{b.note}</span>
        </div>
      {/each}
      <div class="brow total">
        <span class="blabel">Total</span>
        <span class="colbar"></span>
        <span class="bn tnum">{total.toLocaleString()}</span>
        <span class="bs tnum">100%</span>
        <span class="colnote"></span>
      </div>
    </div>
  </div>
{/if}

<style>
  .chead { display: flex; align-items: baseline; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  h4 { font-size: 13px; font-weight: 750; margin: 0; }
  .sub { font-size: 12px; color: var(--muted); }
  /* Same table idiom as the task ladder (GateTable.svelte): a quiet header row,
     a wash on every second line, right-aligned tabular numbers, one total at the
     bottom (Andreas 2026-09-16). The bars are gone with it — the share column
     already says what they said. */
  .btable { display: flex; flex-direction: column; --zebra: rgba(24, 26, 32, .045); }
  .brow { display: grid; grid-template-columns: var(--bcols);
    gap: 10px; align-items: center; font-size: 12.5px; padding: 6px 8px; border-radius: var(--radius-sm); }
  .bbar { background: var(--border); border-radius: 3px; height: 8px; overflow: hidden; }
  .bbar i { display: block; height: 100%; background: var(--muted); border-radius: 3px; }

  .brow.head { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; padding-bottom: 2px; }
  .brow.alt { background-image: linear-gradient(var(--zebra), var(--zebra)); }
  .brow.total { border-top: 1px solid var(--border); margin-top: 3px; padding-top: 8px; font-weight: 750; }
  .blabel { font-weight: 550; }
  .brow.total .blabel { font-weight: 800; }
  .bn, .bs { text-align: right; white-space: nowrap; font-size: 11.5px; }
  .bn { font-weight: 650; }
  .bs { color: var(--muted); }
  .brow.total .bs { color: var(--text); font-weight: 700; }
  .bnote { font-size: 11px; }
  .r { text-align: right; }
  /* Dropping a column drops it from EVERY row (Andreas 2026-09-17). The old
     rules hid `.bbar` and `.bnote`, which only the data rows carry — so the
     header and the total row still handed five cells to a three-track grid and
     wrapped onto a second line, putting "share / what it was" under "where"
     and the total's 100% under its own label. `.colbar` / `.colnote` mark the
     column, not the content, so all three row kinds lose the same cells. */
  .nobar .colbar { display: none; }
  .nonote .colnote { display: none; }
  .tight .brow { gap: 8px; padding: 6px 6px; }
  .tight .blabel { font-size: 12px; }
  .tight .bn, .tight .bs { font-size: 11px; }
</style>
