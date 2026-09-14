<script>
  // The gate ladder of one run, one line per gate: status mark, name, the
  // stamp turn, turns on the leg / the leg cap, and — when the caller has
  // them — wall time and cost at the stamp and the leg's movement efficiency
  // (a model page's expanded level, 2026-09-14). Rows come built: the Report
  // derives them from the referee scorecard, the model page from the board
  // row (lib/board.js runGateRows). Each: {id, name, status, turn, legTurns,
  // cap, capThen?, capChanged?, group?, timeS?, costUsd?, efficiency?,
  // stepsSource?}.
  import { dur, usd } from '../lib/format.js'
  let { rows = [], time = false, cost = false, efficiency = false } = $props()
  const stIcon = { done: '✓', auto: '✓', missed: '✗', failed: '✗', pending: '·', unmet: '·' }
  const STEPS = { trace: 'steps traced per input', video: 'steps counted from the recording', bound: 'steps bounded between polls (an upper bound)', mixed: 'steps partly traced, partly bounded' }
  const extra = $derived((time ? 1 : 0) + (cost ? 1 : 0) + (efficiency ? 1 : 0))
</script>

<div class="gtable" style={`--extra:${extra}`}>
  {#if extra}
    <div class="grow head">
      <span></span><span>gate</span><span class="r">turn</span>
      {#if time}<span class="r">time</span>{/if}
      {#if cost}<span class="r">cost</span>{/if}
      <span class="r">leg / cap</span>
      {#if efficiency}<span class="r">walk</span>{/if}
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
      {#if efficiency}<span class="gx tnum" title={g.efficiency != null ? `movement efficiency on this leg: shortest walk ÷ steps taken · ${STEPS[g.stepsSource] || ''}` : ''}>{g.efficiency != null ? Math.round(g.efficiency * 100) + '%' : ''}</span>{/if}
    </div>
  {/each}
</div>

<style>
  .gtable { display: flex; flex-direction: column; }
  .grow { display: grid; grid-template-columns: 22px minmax(0, 1fr) 48px repeat(var(--extra), 58px) 72px; gap: 10px; align-items: center; padding: 6px 8px; border-radius: var(--radius-sm); font-size: 12.5px; }
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
  .gleg.recap { text-decoration: underline dotted var(--faint); text-underline-offset: 3px; cursor: help; }
  .r { text-align: right; }
</style>
