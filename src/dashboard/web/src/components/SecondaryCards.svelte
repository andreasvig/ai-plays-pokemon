<script>
  // The strip under the headline cards (Andreas 2026-09-13): the per-turn
  // measurements the headline cards used to lead with — turns per minute and
  // cost per ten turns — plus average turns per task, projected for partial
  // runs the same way the headline cards are (lib/board.js secondarySeries).
  // Since 2026-09-14 these three follow the shared model picker: `pool` is
  // every board row (all levels) and the picker chooses which are drawn.
  import { secondarySeries, PROJECT_FROM_GATE } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  import { selection } from '../lib/selection.svelte.js'
  import BarCard from './BarCard.svelte'
  let { pool = [], oninspect = () => {} } = $props()

  const rows = $derived(selection.apply(pool))
  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(secondarySeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))
  const leftOff = $derived(series.turnsPerTask.filter((s) => !s.eligible).length)

  const speed = $derived(series.speed.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    tip: `${s.row.model}: ${s.label} turns/min (${s.row.avgSPerTurn.toFixed(1)}s per turn)` })))
  const cost10 = $derived(series.cost10.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    tip: `${s.row.model}: ${s.label} per 10 turns` })))
  // Models below the projection gate are left off, as on the headline cards (Andreas 2026-09-13).
  const turnsPerTask = $derived(series.turnsPerTask.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete,
    tip: `${s.row.model}: ${s.label} turns per task${s.complete ? '' : ` · projected ${Math.round(s.projected)} turns to beat Brock`}` })))
</script>

{#if pool.length}
<section class="cards" aria-label="Per-turn measurements">
  <h2>Per-turn measurements</h2>
  <p class="faint intro">The rates behind the cards above. Pick the models to compare in any card; the choice carries to every card and plot below.</p>
  <div class="grid">
    <BarCard title="Turns per minute" subtitle="Wall clock, all turns of the run · Higher is better" entries={speed} picker pickerRows={pool} bars={120} {oninspect} />
    <BarCard title="Cost per 10 turns" subtitle="Average USD for ten turns, all calls included · Lower is better" entries={cost10} picker pickerRows={pool} bars={120} {oninspect} />
    <BarCard title="Turns per task" subtitle={`Turns to beat Brock ÷ ${nGates} gates · partial runs projected (hatched) · Lower is better`}
      entries={turnsPerTask} picker pickerRows={pool} bars={120} {oninspect}
      note={leftOff ? `${leftOff} selected model${leftOff === 1 ? '' : 's'} not shown: never reached ${fromGate}, so nothing to project.` : ''} />
  </div>
</section>
{/if}

<style>
  .cards { max-width: var(--maxw); margin: 26px auto 0; padding: 0 24px; }
  h2 { font-size: 17px; font-weight: 750; margin: 0; }
  .intro { font-size: 12px; margin: 2px 0 12px; }
  .grid { display: grid; gap: 18px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 960px) { .grid { grid-template-columns: minmax(0, 1fr); } }
</style>
