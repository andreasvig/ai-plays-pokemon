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
  import { LIST_NOTE } from '../lib/board.js'
  let { rows = [], time = false, cost = false, efficiency = false } = $props()
  const stIcon = { done: '✓', auto: '✓', missed: '✗', failed: '✗', pending: '·', unmet: '·' }
  // Every row of one run shares a cost basis (board.js runGateRows reads it off
  // the run, not the gate), so the provenance goes on the column once, from the
  // first row. `list` means the run was billed nothing and these are its own
  // calls at the model's list price — which prints as an ordinary figure.
  const STEPS = { trace: 'steps traced per input', video: 'steps counted from the recording', bound: 'steps bounded between polls (an upper bound)', mixed: 'steps partly traced, partly bounded' }
  const extra = $derived((time ? 1 : 0) + (cost ? 1 : 0))
  const costBasis = $derived(rows[0]?.costBasis ?? 'billed')
  const money = (v) => (v == null ? '' : usd(v))
  const costTip = $derived(costBasis === 'list' ? LIST_NOTE : 'USD spent on every call up to this task')
  // How much room the ladder actually has, measured — not read off the window
  // (Andreas 2026-09-17). The same ladder sits in a full-width card on a phone,
  // in a 7fr column beside the recording on a desktop and in the run report,
  // and only the card's own width says whether nine columns fit: at a 1100px
  // window the desktop card is 487px wide and the task name was handed 0px,
  // the identical failure a phone showed.
  let tw = $state(0)
  const narrow = $derived(tw > 0 && tw < 620)   // drop the two numbers behind the walk %
  const stack = $derived(tw > 0 && tw < 520)    // and below that, two lines per gate
  // The track list is BUILT here rather than written as repeat(var(--extra), …)
  // in the CSS. `repeat(0, 58px)` is invalid, which threw the whole
  // grid-template-columns declaration away — so on the run report, which passes
  // none of the optional columns, every cell stacked on its own line instead of
  // forming a table (found 2026-09-16 while restyling it).
  const cols = $derived([
    '22px', 'minmax(0, 1fr)', '48px',
    ...(time ? ['58px'] : []), ...(cost ? ['58px'] : []),
    '72px', ...(efficiency && !narrow ? ['46px', '46px', '46px'] : efficiency ? ['46px'] : []),
  ].join(' '))
  // Steps the leg is scored against: tiles walked plus one per press into a
  // wall. Null when the leg has no step measurement at all.
  const charged = (g) => (g.legSteps == null ? null : g.legSteps + (g.legWalls || 0))
  const effTip = (g) => {
    if (g.efficiency == null) return ''
    const c = charged(g)
    const made = g.legWalls ? `${g.legSteps} tiles walked + ${g.legWalls} press${g.legWalls === 1 ? '' : 'es'} into a wall` : `${g.legSteps} tiles walked`
    return `${g.legPath} steps was the shortest walk to this task; it was charged ${c} (${made}) · ${STEPS[g.stepsSource] || ''}`
  }
  // The bottom line (Andreas 2026-09-16). Turns, path and charged steps SUM
  // across the legs; time and cost are already cumulative, so the total is the
  // last row that has one — never a sum, which would double-count. The walk is
  // re-derived from the two totals rather than averaged over the rows, so it is
  // the run's real shortest-path ÷ steps and not a mean of percentages.
  const sum = (pick) => rows.reduce((a, g) => { const v = pick(g); return v == null ? a : a + v }, 0)
  const anyOf = (pick) => rows.some((g) => pick(g) != null)
  const last = (pick) => { for (let i = rows.length - 1; i >= 0; i--) { const v = pick(rows[i]); if (v != null) return v } return null }
  const totals = $derived.by(() => {
    if (rows.length < 2) return null
    const path = sum((g) => g.legPath), steps = sum((g) => charged(g))
    return {
      turn: last((g) => g.turn),
      timeS: last((g) => g.timeS),
      costUsd: last((g) => g.costUsd),
      legTurns: anyOf((g) => g.legTurns) ? sum((g) => g.legTurns) : null,
      cap: anyOf((g) => g.cap) ? sum((g) => g.cap) : null,
      path: anyOf((g) => g.legPath) ? path : null,
      steps: anyOf((g) => charged(g)) ? steps : null,
      walk: path && steps ? path / steps : null,
    }
  })
</script>

<div class="gtable" class:narrow class:stack bind:clientWidth={tw} style={`--cols:${cols}`}>
  <div class="grow head">
    <span></span><span>task</span><span class="r">turn</span>
    <span class="brk" aria-hidden="true"></span>
    {#if time}<span class="r">time</span>{/if}
    {#if cost}<span class="r" title={costTip}>cost</span>{/if}
    <span class="r">leg / cap</span>
    {#if efficiency}<span class="r hpath">path</span><span class="r hsteps">steps</span><span class="r">walk</span>{/if}
  </div>
  {#each rows as g, i (g.id)}
    <div class="grow {g.status}" class:grp={g.group} class:alt={i % 2 === 1}>
      <span class="gst {g.status}">{stIcon[g.status] ?? '·'}</span>
      <span class="gname">{g.name}</span>
      <span class="gturn tnum">{g.turn != null ? 'T' + g.turn : '—'}</span>
      <span class="brk" aria-hidden="true"></span>
      {#if time}<span class="gx gtime tnum" title="wall-clock time from the run's first turn to this task (a continued run's pause is not counted)">{g.timeS != null ? dur(g.timeS) : ''}</span>{/if}
      {#if cost}<span class="gx gcost tnum" title={costTip}>{money(g.costUsd)}</span>{/if}
      <!-- leg: turns spent walking into this gate / its per-leg cap. The cap
           shown is TODAY's, from the shared list (retroactive, 2026-09-10);
           when the run was judged under a different cap the tooltip says which. -->
      <span class="gleg tnum" class:faint={g.status !== 'failed'} class:recap={g.capChanged}
            title={g.capChanged ? `turns on this leg / today's leg cap — the cap was ${g.capThen} when this run was judged` : 'turns on this leg / leg cap'}>{g.cap != null ? `${g.legTurns != null ? g.legTurns : '·'} / ${g.cap}` : (g.legTurns != null ? String(g.legTurns) : '')}</span>
      {#if efficiency}
        <!-- path ÷ steps = walk. Wall bounces are inside `steps`, so the row
             reads across without a fourth column to reconcile. -->
        <span class="gx gpath tnum" title={effTip(g)}>{g.legPath ?? ''}</span>
        <span class="gx gsteps tnum" class:bumped={g.legWalls > 0} title={effTip(g)}>{charged(g) ?? ''}</span>
        <span class="gx gwalk tnum" title={effTip(g)}>{g.efficiency != null ? Math.round(g.efficiency * 100) + '%' : ''}</span>
      {/if}
    </div>
  {/each}
  {#if totals}
    <div class="grow total">
      <span></span>
      <span class="gname">Total</span>
      <span class="gturn tnum">{totals.turn != null ? 'T' + totals.turn : ''}</span>
      <span class="brk" aria-hidden="true"></span>
      {#if time}<span class="gx gtime tnum">{totals.timeS != null ? dur(totals.timeS) : ''}</span>{/if}
      {#if cost}<span class="gx gcost tnum" title={costTip}>{money(totals.costUsd)}</span>{/if}
      <span class="gleg tnum">{totals.legTurns != null ? `${totals.legTurns}${totals.cap != null ? ` / ${totals.cap}` : ''}` : ''}</span>
      {#if efficiency}
        <span class="gx gpath tnum">{totals.path ?? ''}</span>
        <span class="gx gsteps tnum">{totals.steps ?? ''}</span>
        <span class="gx gwalk tnum">{totals.walk != null ? Math.round(totals.walk * 100) + '%' : ''}</span>
      {/if}
    </div>
  {/if}
</div>

<style>
  /* The board has one (light) palette, so one wash is enough. */
  .gtable { display: flex; flex-direction: column; --zebra: rgba(24, 26, 32, .045); }
  .grow { display: grid; grid-template-columns: var(--cols); gap: 10px; align-items: center; padding: 6px 8px; border-radius: var(--radius-sm); font-size: 12.5px; }
  .grow.head { font-size: 10px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; padding-bottom: 2px; }
  .grow.grp { padding-left: 18px; }
  .grow.done { background: var(--green-soft); }
  .grow.missed, .grow.failed { background: var(--red-soft); }
  /* Zebra (Andreas 2026-09-16): a translucent wash LAID OVER whatever the row's
     status colour is, so the stripe reads on a cleared row, a failed one and a
     pending one alike, in both themes. */
  .grow.alt { background-image: linear-gradient(var(--zebra), var(--zebra)); }
  .grow.total { border-top: 1px solid var(--border); margin-top: 3px; padding-top: 8px;
    font-weight: 750; background: none; }
  .grow.total .gname { font-weight: 800; }
  .grow.total .gx, .grow.total .gleg { color: var(--text); font-weight: 700; }
  .gst { text-align: center; font-weight: 800; color: var(--faint); }
  .gst.done { color: var(--green); } .gst.missed, .gst.failed { color: var(--red); }
  .gname { font-weight: 550; }
  .gturn { text-align: right; font-weight: 650; }
  .gleg, .gx { text-align: right; font-size: 11.5px; }
  .gx { color: var(--muted); }
  .gwalk { color: var(--text); font-weight: 650; }
  /* A leg whose step count carries wall bounces — the gap from the raw walk. */
  .bumped { text-decoration: underline dotted var(--faint); text-underline-offset: 3px; cursor: help; }
  /* A line break for the phone layout below; no cell of its own in the grid,
     and `display: none` takes a grid item out of the flow entirely, so the
     track list keeps matching the cells. */
  .brk { display: none; }
  /* Narrow: keep the percentage, drop the two numbers behind it. Named classes
     rather than `:nth-last-child(3)` (Andreas 2026-09-17): counting from the
     end only lands on `path` and `steps` when the efficiency columns are there
     at all, and a run with times but no efficiency had its TIME and COST cells
     hidden instead. */
  .narrow .gpath, .narrow .gsteps, .narrow .hpath, .narrow .hsteps { display: none; }
  /* Narrowest (Andreas 2026-09-17). Seven tracks want 364px before the task
     name gets a pixel, and the card is ~316px wide on a 390px screen — so the
     name was crushed to nothing and ran over the turn column. One gate is two
     lines instead: the name with its stamp turn, then the leg's numbers. */
  .stack .grow.head { display: none; }
  .stack .grow { display: flex; flex-wrap: wrap; align-items: baseline; gap: 1px 9px; padding: 7px 8px; }
  .stack .grow.grp { padding-left: 16px; }
  .stack .gst { flex: 0 0 12px; text-align: left; }
  .stack .gname { flex: 1 1 auto; min-width: 0; font-size: 13px; }
  .stack .gturn { flex: 0 0 auto; margin-left: auto; }
  .stack .brk { display: block; flex: 0 0 100%; height: 0; }
  .stack .brk ~ span { font-size: 11px; text-align: left; }
  /* The second line starts under the name, not under the status mark. */
  .stack .brk + span { margin-left: 21px; }
  .stack .gleg::before { content: 'leg '; color: var(--faint); font-weight: 600; }
  .stack .gwalk { margin-left: auto; }
  .stack .gwalk::after { content: ' walk'; color: var(--faint); font-weight: 600; }
  .stack .grow.total { padding-top: 7px; }
  .gleg.recap { text-decoration: underline dotted var(--faint); text-underline-offset: 3px; cursor: help; }
  .r { text-align: right; }
</style>
