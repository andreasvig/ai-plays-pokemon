<script>
  // Three headline bar cards at the top of the board (Andreas 2026-09-12,
  // modelled on Artificial Analysis's Intelligence / Speed / Cost strip):
  // performance with a 100% line and clears rising above it, then — since
  // 2026-09-13 — time per task and cost per task, where a task is one gate of
  // the ladder and a partial run is PROJECTED to a full clear from its pace
  // (lib/board.js projectRun). One bar per MODEL, its best-ranked thinking
  // level; the caller passes the collapsed rows, and `pool` (every row) is what
  // the field's typical turns per leg are computed over. These three do NOT
  // follow the shared model picker (Andreas 2026-09-14): they always show the
  // whole board.
  import { headlineSeries, vendorOf, PERF_LINE, PROJECT_FROM_GATE, fmtMinutes, fmtUsd } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  import BarCard from './BarCard.svelte'
  let { rows = [], pool = rows, oninspect = () => {} } = $props()

  // The ladder the board plays: the first `totalGates` ids of the flattened ladder.
  const gateIds = $derived(GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(headlineSeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))
  const projectedCount = $derived(series.cost.filter((s) => s.eligible && !s.complete).length)
  // Models below the projection gate are left OFF the two per-task cards rather
  // than drawn as a placeholder (Andreas 2026-09-13: "remove those ghosts").
  const noProjection = $derived(series.cost.filter((s) => !s.eligible).length)
  const vendors = $derived((() => {
    const seen = new Map()
    for (const r of rows) { const v = vendorOf(r); if (!seen.has(v.key)) seen.set(v.key, v) }
    return [...seen.values()]
  })())
  const tip = (s, fmt) => {
    const base = `${s.row.model}: ${fmt(s.value)} per task`
    if (s.complete) return `${base} · measured over ${s.row.turns} turns`
    return `${base} · projected: ${Math.round(s.projected)} turns to beat Brock at ${s.pace.toFixed(2)}× the field's pace (${Math.round(s.estimated)} estimated${s.floored ? ', failed leg floored at turns spent' : ''})`
  }
  const performance = $derived(series.performance.map((s) => ({
    row: s.row, height: s.height, label: s.label, complete: true /* solid: completion is measured, never projected */,
    above: s.complete ? `${s.row.turns}T` : null,
    tip: `${s.row.model}: ${s.label}${s.complete ? `, cleared in ${s.row.turns} turns` : ''}`,
  })))
  const time = $derived(series.time.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtMinutes) })))
  const cost = $derived(series.cost.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtUsd) })))
</script>

{#if rows.length}
<section class="cards" aria-label="Headline comparison">
  <BarCard title="Performance" subtitle="Gate completion · clears ranked by fewest turns above the line · Higher is better"
    entries={performance} line={{ frac: PERF_LINE, label: '100%' }} {oninspect} />
  <BarCard title="Time per task" subtitle={`Minutes to beat Brock ÷ ${nGates} gates · partial runs projected · Lower is better`}
    entries={time} {oninspect} />
  <BarCard title="Cost per task" subtitle={`USD to beat Brock ÷ ${nGates} gates · partial runs projected · Lower is better`}
    entries={cost} {oninspect} />

  <p class="legend faint">
    {#each vendors as v (v.key)}<span class="key" style={`--c:${v.color}`}></span>{v.label}{/each}
    <span class="sep">·</span> one bar per model, its best thinking level
    {#if projectedCount}<span class="sep">·</span><span class="key hatch"></span>projected from a full-run estimate: the run's pace on the gates it cleared, applied to the legs it never reached, the failed leg floored at the turns it spent{/if}
    {#if noProjection}<span class="sep">·</span>{noProjection} model{noProjection === 1 ? '' : 's'} left off these two cards: never reached {fromGate}, so nothing to project{/if}
  </p>
</section>
{/if}

<style>
  .cards { max-width: var(--maxw); margin: 26px auto 0; padding: 0 24px; display: grid; gap: 18px;
    grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 960px) { .cards { grid-template-columns: minmax(0, 1fr); } }
  .legend { grid-column: 1 / -1; font-size: 11.5px; text-align: center; display: flex; align-items: center; justify-content: center; gap: 6px 10px; flex-wrap: wrap; margin: -4px 0 0; }
  .key { display: inline-block; width: 9px; height: 9px; background: var(--c); border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
  .key.hatch { --c: var(--muted); background: repeating-linear-gradient(135deg, var(--muted) 0 2px, transparent 2px 4px); border: 1px solid var(--border); }
  .sep { opacity: .5; }
</style>
