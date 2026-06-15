<script>
  import ScatterChart from './ScatterChart.svelte'
  import { usd } from '../lib/format.js'
  let { rows = [], onpick } = $props()

  let costMode = $state('all')
  let speedMode = $state('all')

  const pts = (xKey) => rows.map((r) => ({
    label: r.model, x: r[xKey], y: r.perfScore, openSource: r.openSource, completed: r.completion >= 100,
    slug: r.slug, completion: r.completion, furthestGateName: r.furthestGateName,
    turns: r.turns, avgCostPerTurn: r.avgCostPerTurn, avgSPerTurn: r.avgSPerTurn,
  }))
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
        <h3>Cost</h3>
        <p class="faint">Performance vs price per turn (log). Up = further / fewer turns; left = cheaper. Dashed = cost-performance frontier. Hover a dot for values; click to open the run.</p>
      </div>
      <div class="segs">{#each MODES as [v, label]}<button class:on={costMode === v} onclick={() => costMode = v}>{label}</button>{/each}</div>
    </header>
    <ScatterChart points={filt(pts('avgCostPerTurn'), costMode)} xLabel="Cost / turn (USD)" xFormat={usd} xLog={true} {onpick} />
  </div>

  <div class="chart-card">
    <header>
      <div>
        <h3>Speed</h3>
        <p class="faint">Performance vs seconds per turn. Up = further / fewer turns; left = faster. Dashed = speed-performance frontier. Hover a dot for values; click to open the run.</p>
      </div>
      <div class="segs">{#each MODES as [v, label]}<button class:on={speedMode === v} onclick={() => speedMode = v}>{label}</button>{/each}</div>
    </header>
    <ScatterChart points={filt(pts('avgSPerTurn'), speedMode)} xLabel="Seconds / turn" xFormat={(v) => `${v < 1 ? v.toFixed(1) : Math.round(v)}s`} {onpick} />
  </div>

  <p class="legend faint">
    <span class="key prop">●</span> proprietary
    <span class="key oss">●</span> open-source
    <span class="sep">·</span>
    y-axis: 0–100% = gate completion; above 100% = turn efficiency among full clears (top = fewest turns to complete).
  </p>
</section>

<style>
  .charts { max-width: var(--maxw); margin: 0 auto 70px; padding: 0 24px; display: grid; gap: 18px; }
  .chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 8px; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; }
  header p { font-size: 12px; margin: 2px 0 0; max-width: 460px; }
  .segs { display: flex; background: #eef1f5; border-radius: 8px; padding: 3px; gap: 2px; flex: none; }
  .segs button { border: none; background: none; padding: 5px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 600; color: var(--muted); white-space: nowrap; }
  .segs button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); }
  .legend { font-size: 11.5px; text-align: center; display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; }
  .key { font-size: 13px; }
  .key.prop { color: var(--accent); }
  .key.oss { color: #0d9488; }
  .sep { opacity: .5; }
</style>
