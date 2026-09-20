<script>
  // One thinking level's run, expanded on its model page (decision 3A,
  // 2026-09-14): the gate ladder with the stamp turn, wall time and cost at
  // each gate, turns on the leg / cap and the leg's movement efficiency — all
  // from the board row (lib/board.js runGateRows), no summary fetch — and the
  // recording. The public site's only run view; locally the full report is a
  // click away.
  import { runGateRows } from '../lib/board.js'
  import { GATES } from '../lib/gates.js'
  import { dateShort, dur, usd } from '../lib/format.js'
  import { STATIC } from '../lib/static.js'
  import GateTable from './GateTable.svelte'
  import RunVideo from './RunVideo.svelte'
  import InputCensus from './InputCensus.svelte'
  import RouteMap from './RouteMap.svelte'
  import Icon from './Icon.svelte'
  // `onreport(row, turn)` is local-only: the published site passes null, so the
  // report button and the map's turn links are absent there rather than broken.
  let { row, benchmarks = [], onreport = null } = $props()
  // The map is the heaviest thing on the page, so it is collapsed by default —
  // which also means its PNGs are not fetched until someone asks for it (M13).
  let mapOpen = $state(false)

  // The ladder this run played, with today's per-leg caps from the shared
  // benchmark list; the flattened GATES ladder when the registry has no entry.
  const ladder = $derived((() => {
    const b = benchmarks.find((x) => x.id === row.benchmark) ?? benchmarks.find((x) => x.default) ?? null
    const gs = Array.isArray(b?.gates) && b.gates.length ? b.gates : GATES.slice(0, row.totalGates || 12)
    return gs.map((g) => ({ id: g.id, name: g.name, cap: g.leg_cap_turns ?? null }))
  })())
  const gates = $derived(runGateRows(row, ladder))
  const cleared = $derived(gates.filter((g) => g.status === 'done').length)
  const failed = $derived(gates.find((g) => g.status === 'failed') ?? null)
  const verdict = $derived(cleared >= gates.length && gates.length ? 'All gates cleared' : failed ? `Ended on the leg to ${failed.name}` : '')
  const hasTimes = $derived(gates.some((g) => g.timeS != null))
  const hasCosts = $derived(gates.some((g) => g.costUsd != null))
  const hasEff = $derived(gates.some((g) => g.efficiency != null))

</script>

<div class="detail">
  <div class="cols">
    <section class="score">
      <div class="score-head">
        <h4>Gates</h4>
        <span class="cleared">{cleared}/{gates.length} cleared</span>
        <span class="verdict" class:win={cleared >= gates.length && gates.length} class:fail={!!failed}>{verdict}</span>
      </div>
      <GateTable rows={gates} time={hasTimes} cost={hasCosts} efficiency={hasEff} />
      <p class="foot faint">
        {row.turns} turns · {dur(row.durationS)} · {usd(row.totalCostUsd)} · started {dateShort(row.startedAt)} on <span class="mono">{row.config}</span>{#if row.continuedFrom} · continued from an earlier run{/if}
        {#if hasTimes}· time and cost are cumulative at each gate{/if}{#if hasEff}· walk = shortest path ÷ steps charged, a press into a wall counting as one step{/if}
      </p>
      {#if !STATIC && onreport}
        <button class="btn ghost report" onclick={() => onreport(row)}><Icon name="report" size={13} /> Open the full report</button>
      {/if}
    </section>
    {#if row.videoUrl || row.hasRecording}
      <section class="video">
        <RunVideo run={row} />
      </section>
    {/if}
    {#if row.inputBreakdown?.inputs}
      <section class="score census"><InputCensus inputs={row.inputBreakdown} /></section>
    {/if}
    {#if row.routePoints}
      <section class="score map">
        <button class="maphead" onclick={() => (mapOpen = !mapOpen)} aria-expanded={mapOpen}>
          <span class="arr">{mapOpen ? '▾' : '▸'}</span>
          <h4>Where it walked</h4>
          <span class="cleared">every tile, on the game's own map</span>
        </button>
        {#if mapOpen}
          <RouteMap runId={row.runId}
            onturn={onreport ? (turn) => onreport(row, turn) : null} />
        {/if}
      </section>
    {/if}
  </div>
</div>

<style>
  .detail { padding: 4px 0 14px; }
  .cols { display: grid; grid-template-columns: minmax(0, 7fr) minmax(0, 5fr); gap: 18px; align-items: start; }
  @media (max-width: 960px) {
    .cols { grid-template-columns: minmax(0, 1fr); }
    .video { grid-column: auto; grid-row: auto; }
  }
  .score { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; box-shadow: var(--shadow); min-width: 0; }
  .score-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  h4 { font-size: 14px; font-weight: 750; margin: 0; }
  .cleared { font-size: 12px; font-weight: 650; color: var(--muted); }
  .verdict { margin-left: auto; font-size: 12px; font-weight: 700; color: var(--muted); }
  .verdict.win { color: var(--green); } .verdict.fail { color: var(--red); }
  .foot { font-size: 11px; margin: 10px 0 0; line-height: 1.5; }
  .maphead { display: flex; align-items: center; gap: 10px; width: 100%; background: none; border: 0; padding: 0; cursor: pointer; color: inherit; text-align: left; }
  .maphead .arr { color: var(--faint); font-size: 11px; width: 10px; }
  .map:has(.maphead[aria-expanded="true"]) .maphead { margin-bottom: 10px; }
  .report { margin-top: 8px; font-size: 12px; display: inline-flex; align-items: center; gap: 6px; }
  /* M13, 2026-09-15: gates and the recording side by side, then the census,
     then the map — so the map, the heaviest thing here, is last and closed. */
  .video { grid-column: 2; grid-row: 1; border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); border: 1px solid var(--border); background: var(--dark); min-width: 0; }
  .census, .map { grid-column: 1 / -1; }
</style>
