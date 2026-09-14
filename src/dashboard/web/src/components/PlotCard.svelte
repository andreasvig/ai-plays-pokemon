<script>
  // One scatter-plot card: performance against USD per task (`kind` "cost") or
  // minutes per task ("time") — the same projected per-task figures as the bar
  // cards (lib/board.js perTaskSeries). Projected points are drawn hollow;
  // runs below the projection gate have no value and are listed beside the
  // plot instead of drawn (no ghost markers). Follows the shared model picker.
  import ScatterChart from './ScatterChart.svelte'
  import ModelPicker from './ModelPicker.svelte'
  import { selection } from '../lib/selection.svelte.js'
  import { perTaskSeries, fmtUsd, fmtMinutes, PROJECT_FROM_GATE } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  // `picker` false (a model page) shows `pool` as is; `highlight` fades the
  // points whose row fails it (2026-09-14).
  let { kind = 'cost', pool = [], onpick, picker = true, highlight = null, pinned = null } = $props()

  let mode = $state('all')
  // Pinned rows (a model page's own levels) stay in whatever the picker says.
  const rows = $derived.by(() => {
    if (!picker) return pool
    const chosen = new Set(selection.apply(pool).map((r) => r.model))
    return pool.filter((r) => chosen.has(r.model) || (pinned && pinned(r)))
  })
  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(perTaskSeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))

  const COPY = {
    cost: { title: 'Performance vs cost per task', x: 'Cost / task (USD)', log: true, fmt: fmtUsd, pick: (s) => s.costPerTask,
      blurb: (n) => `USD per task (log): cost to finish all ${n} gates ÷ ${n}, projected for runs that did not finish. Up = further / fewer turns; left = cheaper. Dashed = cost-performance frontier. Hollow = projected.` },
    time: { title: 'Performance vs time per task', x: 'Minutes / task', log: false, fmt: fmtMinutes, pick: (s) => s.minutesPerTask,
      blurb: (n) => `Minutes per task: wall-clock time to finish all ${n} gates ÷ ${n}, projected for runs that did not finish. Up = further / fewer turns; left = faster. Dashed = speed-performance frontier. Hollow = projected.` },
  }
  const c = $derived(COPY[kind])

  const points = $derived(series.filter((s) => s.eligible).map((s) => {
    const r = s.row
    return {
      label: r.model, x: c.pick(s), y: r.perfScore, openSource: r.openSource, completed: r.completion >= 100,
      projected: !s.complete, faded: !!highlight && !highlight(r), slug: r.slug, completion: r.completion, furthestGateName: r.furthestGateName,
      legGateName: r.legGateName, legFraction: r.legFraction,
      tip: [
        ['cost / task', fmtUsd(s.costPerTask)],
        ['time / task', fmtMinutes(s.minutesPerTask)],
        ['turns / task', s.turnsPerTask.toFixed(1)],
        s.complete ? ['turns', String(r.turns)] : ['turns', `${r.turns} played → ${Math.round(s.projected)} projected`],
      ],
    }
  }))
  const left = $derived(series.filter((s) => !s.eligible).map((s) => ({ label: s.row.model, slug: s.row.slug, openSource: s.row.openSource })))
  const shown = $derived(mode === 'partial' ? points.filter((p) => !p.completed) : mode === 'complete' ? points.filter((p) => p.completed) : points)
  const MODES = [['all', 'All'], ['partial', 'Not completed'], ['complete', 'Completed only']]
</script>

<div class="card">
  <header>
    <div class="head">
      <h3>{c.title}</h3>
      <p class="faint">{c.blurb(nGates)} Hover for values; click to open the run.</p>
    </div>
    <div class="tools">
      {#if picker}<ModelPicker rows={pool} {pinned} />{/if}
      <div class="segs">{#each MODES as [v, label]}<button class:on={mode === v} onclick={() => mode = v}>{label}</button>{/each}</div>
    </div>
  </header>
  <ScatterChart points={shown} {left} leftTitle={`No projection yet · below ${fromGate}`} xLabel={c.x} xFormat={c.fmt} xLog={c.log} {onpick} />
  <p class="legend faint">
    <span class="key prop"></span> proprietary
    <span class="key oss"></span> open-source
    <span class="key hollow"></span> projected (run not completed)
    <span class="sep">·</span>
    y-axis: 0–100% = gate completion; above 100% = turn efficiency among full clears.
    {#if left.length}<span class="sep">·</span> {left.length} selected model{left.length === 1 ? '' : 's'} left off: no projection before {fromGate}.{/if}
  </p>
</div>

<style>
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); min-width: 0; }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
  .head { min-width: 0; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; letter-spacing: -.01em; }
  header p { font-size: 12px; margin: 2px 0 0; max-width: 520px; line-height: 1.45; }
  .tools { display: flex; align-items: center; gap: 10px; flex: none; flex-wrap: wrap; justify-content: flex-end; }
  .segs { display: flex; background: var(--wash); border-radius: var(--radius); padding: 3px; gap: 2px; flex: none; }
  .segs button { border: none; background: none; padding: 5px 10px; border-radius: var(--radius-sm); font-size: 11.5px; font-weight: 600; color: var(--muted); white-space: nowrap; }
  .segs button.on { background: var(--surface); color: var(--text); box-shadow: inset 0 0 0 1px var(--border); }
  .legend { font-size: 11.5px; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; margin: 10px 0 0; }
  .key { display: inline-block; width: 9px; height: 9px; background: currentColor; vertical-align: -1px; }
  .key.prop { color: var(--accent); }
  .key.oss { color: var(--oss); }
  .key.hollow { background: var(--surface); box-shadow: inset 0 0 0 1.5px var(--accent); }
  .sep { opacity: .5; }
</style>
