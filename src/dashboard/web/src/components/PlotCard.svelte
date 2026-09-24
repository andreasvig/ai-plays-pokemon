<script>
  // One scatter-plot card: performance against USD per task (`kind` "cost") or
  // minutes per task ("time") — the same projected per-task figures as the bar
  // cards (lib/board.js perTaskSeries). Every marker is solid (Andreas
  // 2026-09-16: projection is a tooltip fact, not a second marker style); runs
  // below the projection gate have no value and are listed beside the plot
  // instead of drawn (no ghost markers). The shared model picker is the ONLY
  // filter on this card — the all / not-completed / completed segments were a
  // second way to do the same thing.
  import ScatterChart from './ScatterChart.svelte'
  import ModelPicker from './ModelPicker.svelte'
  import { selection } from '../lib/selection.svelte.js'
  import { perTaskSeries, vendorOf, projectionCutoff, fmtUsd, fmtMinutes, NO_PRICE } from '../lib/board.js'
  import { GATES } from '../lib/gates.js'
  // `picker` false (a model page) shows `pool` as is; `highlight` fades the
  // points whose row fails it (2026-09-14).
  let { kind = 'cost', pool = [], onpick, picker = true, highlight = null, pinned = null } = $props()

  // Pinned rows (a model page's own levels) stay in whatever the picker says.
  const rows = $derived.by(() => {
    if (!picker) return pool
    const chosen = new Set(selection.apply(pool).map((r) => r.model))
    return pool.filter((r) => chosen.has(r.model) || (pinned && pinned(r)))
  })
  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(perTaskSeries(rows, gateIds, pool))
  // Which rows of the whole pool this plot can place — for the picker's greying.
  const canShow = $derived(new Set(perTaskSeries(pool, gateIds, pool).filter((s) => s.eligible).map((s) => s.row.model)))
  const dimmed = (r) => !canShow.has(r.model)
  const nGates = $derived(gateIds.length)
  const cut = $derived(projectionCutoff(gateIds))

  const COPY = {
    cost: { title: 'Performance vs cost per task', x: 'Cost / task (USD)', log: true, fmt: fmtUsd, pick: (s) => s.costPerTask,
      blurb: () => 'Average USD one task costs, log scale · up = further, or the same distance in fewer turns; left = cheaper · dashed = the cost-performance frontier' },
    time: { title: 'Performance vs time per task', x: 'Minutes / task', log: true, fmt: fmtMinutes, pick: (s) => s.minutesPerTask,
      blurb: () => 'Average wall-clock minutes one task takes, log scale · up = further, or the same distance in fewer turns; left = faster · dashed = the speed-performance frontier' },
  }
  const c = $derived(COPY[kind])

  // A free model has no cost/task, so it cannot be a point on the cost plot —
  // and not merely as a label problem: the x-axis is log, and 0 has no place on
  // it. It stays on the TIME plot, where its minutes are an ordinary measurement.
  const points = $derived(series.filter((s) => s.eligible && c.pick(s) != null).map((s) => {
    const r = s.row
    return {
      label: r.model, x: c.pick(s), y: r.perfScore, color: vendorOf(r).color, completed: r.completion >= 100,
      projected: !s.complete, faded: !!highlight && !highlight(r), slug: r.slug, completion: r.completion, furthestGateName: r.furthestGateName,
      legGateName: r.legGateName, legFraction: r.legFraction,
      tip: [
        ['cost / task', s.costPerTask == null ? NO_PRICE : fmtUsd(s.costPerTask)],
        ['time / task', fmtMinutes(s.minutesPerTask)],
        ['turns / task', s.turnsPerTask.toFixed(1)],
        s.complete ? ['turns', String(r.turns)] : ['turns', `${r.turns} played → ${Math.round(s.projected)} projected`],
      ],
    }
  }))
  const left = $derived(series.filter((s) => !s.eligible))
  // Counted apart from `left`: these cleared the ladder far enough to be plotted
  // and were dropped for a different reason, so they need their own sentence.
  const priceless = $derived(series.filter((s) => s.eligible && c.pick(s) == null).length)
</script>

<div class="card">
  <!-- Title and picker on one line, the blurb UNDER both at full card width:
       inside the flex row the blurb was the only shrinkable item, so a phone
       wrapped it every twelve characters (Andreas 2026-09-17). -->
  <header>
    <div class="topline">
      <h3>{c.title}</h3>
      {#if picker}<div class="tools"><ModelPicker rows={pool} {pinned} {dimmed} /></div>{/if}
    </div>
    <!-- The blurb is a run of "·"-separated clauses and ends without a stop, so
         this used to read "…cost-performance frontier Hover for values". -->
    <p class="faint">{c.blurb(nGates)} · Hover for values; click to open the run.</p>
  </header>
  <ScatterChart {points} xLabel={c.x} xFormat={c.fmt} xLog={c.log} {onpick} />
  <!-- Running prose, so a BLOCK. As a flex container each sentence between two
       separator spans was an anonymous flex item, which meant every clause took
       a line of its own and the bare "·" sat centred on a line between them
       (Andreas 2026-09-17: the asterisk text "wraps weirdly and early"). -->
  <p class="legend faint">
    y-axis: 0–100% = tasks cleared; above 100% = turn efficiency among full clears.
    {#if left.length}<span class="sep">·</span>{left.length} {picker ? 'selected ' : ''}model{left.length === 1 ? '' : 's'} not shown: cleared fewer than {cut?.no ?? 6} of the {nGates} tasks.{/if}
    {#if priceless}<span class="sep">·</span>{priceless} {picker ? 'selected ' : ''}model{priceless === 1 ? '' : 's'} not shown: served free, so there is no price to plot.{/if}
  </p>
</div>

<style>
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); min-width: 0; }
  header { margin-bottom: 8px; }
  .topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; letter-spacing: -.01em; min-width: 0; }
  header p { font-size: 12px; margin: 5px 0 0; max-width: 76ch; line-height: 1.5; }
  .tools { display: flex; align-items: center; gap: 10px; flex: none; flex-wrap: wrap; justify-content: flex-end; }
  .legend { font-size: 11.5px; text-align: center; line-height: 1.55; margin: 10px 0 0; }
  .sep { opacity: .5; margin: 0 7px; }

  @media (max-width: 720px) {
    .card { padding: 12px 10px 10px; }
    h3 { font-size: 15px; }
    .topline { gap: 8px; }
    /* Centred running text is fine over one line and bad over five. */
    .legend { text-align: left; }
  }
</style>
