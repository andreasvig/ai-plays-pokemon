<script>
  // Three headline bar cards at the top of the board (Andreas 2026-09-12,
  // modelled on Artificial Analysis's Intelligence / Speed / Cost strip):
  // performance with a 100% line and clears rising above it, then — since
  // 2026-09-13 — time per task and cost per task, where a TASK is one rung of
  // the ladder (called a gate in the code) and a partial run is PROJECTED to a
  // full clear from its pace
  // (lib/board.js projectRun). One bar per MODEL, its best-ranked thinking
  // level; the caller passes the collapsed rows, and `pool` (every row) is what
  // the field's typical turns per leg are computed over. These three do NOT
  // follow the shared model picker (Andreas 2026-09-14): they always show the
  // whole board.
  import { headlineSeries, PERF_LINE, projectionCutoff, fmtMinutes, fmtUsd } from '../lib/board.js'
  import { GATES } from '../lib/gates.js'
  import BarCard from './BarCard.svelte'
  // `highlight` (a model page, 2026-09-14): (row) => boolean; the rows that
  // fail it are drawn faded, and the caller passes a field that holds every
  // level of the page's model (lib/board.js modelField).
  let { rows = [], pool = rows, oninspect = () => {}, highlight = null } = $props()

  // The ladder the board plays: the first `totalGates` ids of the flattened ladder.
  const gateIds = $derived(GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(headlineSeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const cut = $derived(projectionCutoff(gateIds))
  // Models below the projection gate are left OFF the two per-task cards rather
  // than drawn as a placeholder (Andreas 2026-09-13: "remove those ghosts").
  const noProjection = $derived(series.cost.filter((s) => !s.eligible).length)
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
  // A free model has no cost per task, so it is left off this card rather than
  // shown at '—' or, worse, at $0.00 in first place (Andreas 2026-09-16).
  const cost = $derived(series.cost.filter((s) => s.eligible && s.value != null).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtUsd) })))
</script>

{#if rows.length}
<section class="cards" aria-label="Headline comparison">
  <BarCard title="Performance" subtitle={`How far up the ${nGates} tasks · full clears rise above the line · Higher is better`}
    entries={performance} line={{ frac: PERF_LINE, label: '100%' }} href="#performance" {oninspect} {highlight} />
  <BarCard title="Time per task" subtitle="Average minutes one task takes · Lower is better"
    entries={time} href="#speed" {oninspect} {highlight} />
  <BarCard title="Cost per task" subtitle="Average USD one task costs · Lower is better"
    entries={cost} href="#price" {oninspect} {highlight} />

  <p class="legend faint">
    <span class="key hatch"></span>Hatched = estimated, not measured: the run did not finish the ladder.{cut
      ? ` A run that cleared fewer than ${cut.no} of the ${nGates} tasks — half the ladder — is not estimated at all${noProjection ? `, so ${noProjection} ${noProjection === 1 ? 'is' : 'are'} left off the last two cards` : ''}.`
      : ''}
  </p>
</section>
{/if}

<style>
  .cards { max-width: var(--maxw); margin: 26px auto 0; padding: 0 24px; display: grid; gap: 18px;
    grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 960px) { .cards { grid-template-columns: minmax(0, 1fr); } }
  /* A block, not a flex row: the swatch and the sentence are one run of text,
     and as flex items they wrapped onto separate lines (Andreas 2026-09-17). */
  .legend { grid-column: 1 / -1; font-size: 11.5px; text-align: center; line-height: 1.55; margin: -4px 0 0; }
  .key { display: inline-block; width: 9px; height: 9px; background: var(--c); border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
  @media (max-width: 720px) {
    .cards { margin-top: 16px; padding: 0 10px; gap: 14px; }
    .legend { text-align: left; }
  }
  .key.hatch { --c: var(--muted); background: repeating-linear-gradient(135deg, var(--muted) 0 2px, transparent 2px 4px); border: 1px solid var(--border); }
</style>
