<script>
  // The two scatter plots under the board. Since 2026-09-14 (Andreas: "the 2
  // dimensional graph please use the per task for time / per task cost") the
  // x-axes are USD per task and minutes per task — the same projected figures
  // as the headline cards (lib/board.js perTaskSeries): a partial run's cost
  // and time to finish the ladder at its own pace and rates, ÷ the gate count.
  // Projected points are drawn hollow; runs below the projection gate have no
  // value and are listed beside the plot instead of drawn (no ghost markers).
  import ScatterChart from './ScatterChart.svelte'
  import ModelPicker from './ModelPicker.svelte'
  import { selection } from '../lib/selection.svelte.js'
  import { perTaskSeries, fmtUsd, fmtMinutes, PROJECT_FROM_GATE } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  // `pool` is every board row; the shared picker (lib/selection.svelte.js) chooses which are plotted.
  let { pool = [], onpick } = $props()
  const rows = $derived(selection.apply(pool))

  let costMode = $state('all')
  let speedMode = $state('all')

  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(perTaskSeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))

  const pts = (pick) => series.filter((s) => s.eligible).map((s) => {
    const r = s.row
    return {
      label: r.model, x: pick(s), y: r.perfScore, openSource: r.openSource, completed: r.completion >= 100,
      projected: !s.complete, slug: r.slug, completion: r.completion, furthestGateName: r.furthestGateName,
      legGateName: r.legGateName, legFraction: r.legFraction,
      tip: [
        ['cost / task', fmtUsd(s.costPerTask)],
        ['time / task', fmtMinutes(s.minutesPerTask)],
        ['turns / task', s.turnsPerTask.toFixed(1)],
        s.complete ? ['turns', String(r.turns)]
                   : ['turns', `${r.turns} played → ${Math.round(s.projected)} projected`],
      ],
    }
  })
  // Left off both plots: no projection until the run reaches the projection gate.
  const left = $derived(series.filter((s) => !s.eligible).map((s) => ({ label: s.row.model, slug: s.row.slug, openSource: s.row.openSource })))
  const filt = (points, mode) =>
    mode === 'partial' ? points.filter((p) => !p.completed)
    : mode === 'complete' ? points.filter((p) => p.completed)
    : points
  const MODES = [['all', 'All'], ['partial', 'Not completed'], ['complete', 'Completed only']]
</script>

<section class="charts">
  <div class="chart-card">
    <header>
      <div>
        <h3>Cost per task</h3>
        <p class="faint">Performance vs USD per task (log): cost to finish all {nGates} gates ÷ {nGates}, projected for runs that did not finish. Up = further / fewer turns; left = cheaper. Dashed = cost-performance frontier. Hollow = projected. Hover for values; click to open the run.</p>
      </div>
      <div class="tools">
        <ModelPicker rows={pool} />
        <div class="segs">{#each MODES as [v, label]}<button class:on={costMode === v} onclick={() => costMode = v}>{label}</button>{/each}</div>
      </div>
    </header>
    <ScatterChart points={filt(pts((s) => s.costPerTask), costMode)} {left} leftTitle={`No projection yet · below ${fromGate}`}
      xLabel="Cost / task (USD)" xFormat={fmtUsd} xLog={true} {onpick} />
  </div>

  <div class="chart-card">
    <header>
      <div>
        <h3>Time per task</h3>
        <p class="faint">Performance vs minutes per task: wall-clock time to finish all {nGates} gates ÷ {nGates}, projected for runs that did not finish. Up = further / fewer turns; left = faster. Dashed = speed-performance frontier. Hollow = projected. Hover for values; click to open the run.</p>
      </div>
      <div class="tools">
        <ModelPicker rows={pool} />
        <div class="segs">{#each MODES as [v, label]}<button class:on={speedMode === v} onclick={() => speedMode = v}>{label}</button>{/each}</div>
      </div>
    </header>
    <ScatterChart points={filt(pts((s) => s.minutesPerTask), speedMode)} {left} leftTitle={`No projection yet · below ${fromGate}`}
      xLabel="Minutes / task" xFormat={fmtMinutes} {onpick} />
  </div>

  <p class="legend faint">
    <span class="key prop"></span> proprietary
    <span class="key oss"></span> open-source
    <span class="key hollow"></span> projected (run not completed)
    <span class="sep">·</span>
    y-axis: 0–100% = gate completion; above 100% = turn efficiency among full clears (top = fewest turns to complete).
    {#if left.length}<span class="sep">·</span> {left.length} model{left.length === 1 ? '' : 's'} left off: no projection before {fromGate}.{/if}
  </p>
</section>

<style>
  .charts { max-width: var(--maxw); margin: 0 auto 70px; padding: 0 24px; display: grid; gap: 18px; }
  .chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; }
  header p { font-size: 12px; margin: 2px 0 0; max-width: 460px; }
  .tools { display: flex; align-items: center; gap: 10px; flex: none; }
  .segs { display: flex; background: var(--wash); border-radius: var(--radius); padding: 3px; gap: 2px; flex: none; }
  .segs button { border: none; background: none; padding: 5px 10px; border-radius: var(--radius-sm); font-size: 11.5px; font-weight: 600; color: var(--muted); white-space: nowrap; }
  .segs button.on { background: var(--surface); color: var(--text); box-shadow: inset 0 0 0 1px var(--border); }
  .legend { font-size: 11.5px; text-align: center; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; }
  /* The legend key IS the marker: a square, drawn the same way the plot
     draws it, rather than a ● that no longer matches anything. */
  .key { display: inline-block; width: 9px; height: 9px; background: currentColor;
    vertical-align: -1px; }
  .key.prop { color: var(--accent); }
  .key.oss { color: var(--oss); }
  .key.hollow { background: var(--surface); box-shadow: inset 0 0 0 1.5px var(--accent); }
  .sep { opacity: .5; }
</style>
