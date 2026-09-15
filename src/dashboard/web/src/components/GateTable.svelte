<script>
  // The gate ladder of one run, one line per gate: status mark, name, the
  // stamp turn, turns on the leg / the leg cap, and — when the caller has
  // them — wall time and cost at the stamp and the leg's movement efficiency
  // (a model page's expanded level, 2026-09-14). The efficiency columns show
  // the DERIVATION, not just the percentage (Andreas 2026-09-15): the leg's
  // shortest path, the steps it is charged, and path ÷ steps. A press into a
  // wall is one of those steps, so the three read straight across. Rows come
  // built: the Report derives them from the referee scorecard, the model page
  // from the board row (lib/board.js runGateRows). Each: {id, name, status,
  // turn, legTurns, cap, capThen?, capChanged?, group?, timeS?, costUsd?,
  // efficiency?, stepsSource?, legPath?, legSteps?, legWalls?}.
  import { dur, usd } from '../lib/format.js'
  let { rows = [], time = false, cost = false, efficiency = false } = $props()
  const stIcon = { done: '✓', auto: '✓', missed: '✗', failed: '✗', pending: '·', unmet: '·' }
  const STEPS = { trace: 'steps traced per input', video: 'steps counted from the recording', bound: 'steps bounded between polls (an upper bound)', mixed: 'steps partly traced, partly bounded' }
  const extra = $derived((time ? 1 : 0) + (cost ? 1 : 0))
  // Steps the leg is scored against: tiles walked plus one per press into a
  // wall. Null when the leg has no step measurement at all.
  const charged = (g) => (g.legSteps == null ? null : g.legSteps + (g.legWalls || 0))
  const effTip = (g) => {
    if (g.efficiency == null) return ''
    const c = charged(g)
    const made = g.legWalls ? `${g.legSteps} tiles walked + ${g.legWalls} press${g.legWalls === 1 ? '' : 'es'} into a wall` : `${g.legSteps} tiles walked`
    return `${g.legPath} steps was the shortest walk to this gate; it was charged ${c} (${made}) · ${STEPS[g.stepsSource] || ''}`
  }
</script>

<div class="gtable" style={`--extra:${extra};--eff:${efficiency ? 3 : 0}`}>
  {#if extra}
    <div class="grow head">
      <span></span><span>gate</span><span class="r">turn</span>
      {#if time}<span class="r">time</span>{/if}
      {#if cost}<span class="r">cost</span>{/if}
      <span class="r">leg / cap</span>
      {#if efficiency}<span class="r">path</span><span class="r">steps</span><span class="r">walk</span>{/if}
    </div>
  {/if}
  {#each rows as g (g.id)}
    <div class="grow {g.status}" class:grp={g.group}>
      <span class="gst {g.status}">{stIcon[g.status] ?? '·'}</span>
      <span class="gname">{g.name}</span>
      <span class="gturn tnum">{g.turn != null ? 'T' + g.turn : '—'}</span>
      {#if time}<span class="gx tnum" title="wall-clock time from the run's first turn to this gate (a continued run's pause is not counted)">{g.timeS != null ? dur(g.timeS) : ''}</span>{/if}
      {#if cost}<span class="gx tnum" title="USD spent on every call up to this gate">{g.costUsd != null ? usd(g.costUsd) : ''}</span>{/if}
      <!-- leg: turns spent walking into this gate / its per-leg cap. The cap
           shown is TODAY's, from the shared list (retroactive, 2026-09-10);
           when the run was judged under a different cap the tooltip says which. -->
      <span class="gleg tnum" class:faint={g.status !== 'failed'} class:recap={g.capChanged}
            title={g.capChanged ? `turns on this leg / today's leg cap — the cap was ${g.capThen} when this run was judged` : 'turns on this leg / leg cap'}>{g.cap != null ? `${g.legTurns != null ? g.legTurns : '·'} / ${g.cap}` : (g.legTurns != null ? String(g.legTurns) : '')}</span>
      {#if efficiency}
        <!-- path ÷ steps = walk. Wall bounces are inside `steps`, so the row
             reads across without a fourth column to reconcile. -->
        <span class="gx tnum" title={effTip(g)}>{g.legPath ?? ''}</span>
        <span class="gx tnum" class:bumped={g.legWalls > 0} title={effTip(g)}>{charged(g) ?? ''}</span>
        <span class="gx gwalk tnum" title={effTip(g)}>{g.efficiency != null ? Math.round(g.efficiency * 100) + '%' : ''}</span>
      {/if}
    </div>
  {/each}
</div>

<style>
  .gtable { display: flex; flex-direction: column; }
  .grow { display: grid; grid-template-columns: 22px minmax(0, 1fr) 48px repeat(var(--extra), 58px) 72px repeat(var(--eff), 46px); gap: 10px; align-items: center; padding: 6px 8px; border-radius: var(--radius-sm); font-size: 12.5px; }
  .grow.head { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; padding-bottom: 2px; }
  .grow.grp { padding-left: 18px; }
  .grow.done { background: var(--green-soft); }
  .grow.missed, .grow.failed { background: var(--red-soft); }
  .gst { text-align: center; font-weight: 800; color: var(--faint); }
  .gst.done { color: var(--green); } .gst.missed, .gst.failed { color: var(--red); }
  .gname { font-weight: 550; }
  .gturn { text-align: right; font-weight: 650; }
  .gleg, .gx { text-align: right; font-size: 11.5px; }
  .gx { color: var(--muted); }
  .gwalk { color: var(--text); font-weight: 650; }
  /* A leg whose step count carries wall bounces — the gap from the raw walk. */
  .bumped { text-decoration: underline dotted var(--faint); text-underline-offset: 3px; cursor: help; }
  /* Narrow: keep the percentage, drop the two numbers behind it. */
  @media (max-width: 620px) {
    .grow { grid-template-columns: 22px minmax(0, 1fr) 48px repeat(var(--extra), 58px) 72px repeat(var(--eff), 0); }
    .grow > .gx:nth-last-child(3), .grow > .gx:nth-last-child(2),
    .grow.head > .r:nth-last-child(3), .grow.head > .r:nth-last-child(2) { display: none; }
  }
  .gleg.recap { text-decoration: underline dotted var(--faint); text-underline-offset: 3px; cursor: help; }
  .r { text-align: right; }
</style>
