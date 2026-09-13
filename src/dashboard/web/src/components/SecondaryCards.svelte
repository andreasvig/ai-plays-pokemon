<script>
  // The strip under the board (Andreas 2026-09-13): the per-turn measurements
  // the headline cards used to lead with — turns per minute and cost per ten
  // turns — plus average turns per task, projected for partial runs the same
  // way the headline cards are (lib/board.js secondarySeries).
  import { secondarySeries, vendorOf, PROJECT_FROM_GATE } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  let { rows = [], pool = rows, oninspect = () => {} } = $props()

  const gateIds = $derived(GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id))
  const series = $derived(secondarySeries(rows, gateIds, pool))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived(gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE)
  const INSIDE_MIN = 0.22
</script>

{#if rows.length}
<section class="cards" aria-label="Per-turn measurements">
  <h2>Per-turn measurements</h2>
  <p class="faint intro">The rates behind the cards above, one bar per model (its best thinking level).</p>
  <div class="grid">
    <div class="card">
      <header><h3>Turns per minute</h3><p class="faint">Wall clock, all turns of the run · Higher is better</p></header>
      <div class="plot">
        {#each series.speed as s (s.row.runId)}
          <button class="bar" style={`--h:${(s.height * 100).toFixed(1)}%; --c:${vendorOf(s.row).color}`}
            title={`${s.row.model}: ${s.label} turns/min (${s.row.avgSPerTurn.toFixed(1)}s per turn)`} onclick={() => oninspect(s.row)}>
            <span class="fill"><span class="val tnum" class:outside={s.height < INSIDE_MIN}>{s.label}</span></span>
            <span class="name mono">{s.row.model}</span>
          </button>
        {/each}
      </div>
    </div>
    <div class="card">
      <header><h3>Cost per 10 turns</h3><p class="faint">Average USD for ten turns, all calls included · Lower is better</p></header>
      <div class="plot">
        {#each series.cost10 as s (s.row.runId)}
          <button class="bar" style={`--h:${(s.height * 100).toFixed(1)}%; --c:${vendorOf(s.row).color}`}
            title={`${s.row.model}: ${s.label} per 10 turns`} onclick={() => oninspect(s.row)}>
            <span class="above tnum">{s.label}</span>
            <span class="fill"></span>
            <span class="name mono">{s.row.model}</span>
          </button>
        {/each}
      </div>
    </div>
    <div class="card">
      <header><h3>Average turns per task</h3><p class="faint">Turns to beat Brock ÷ {nGates} gates · partial runs projected (hatched) · Lower is better</p></header>
      <div class="plot">
        {#each series.turnsPerTask as s (s.row.runId)}
          <button class="bar" class:none={!s.eligible} style={`--h:${(s.height * 100).toFixed(1)}%; --c:${vendorOf(s.row).color}`}
            title={s.eligible ? `${s.row.model}: ${s.label} turns per task${s.complete ? '' : ` · projected ${Math.round(s.projected)} turns to beat Brock`}` : `${s.row.model}: never “${fromGate}”, no projection`}
            onclick={() => oninspect(s.row)}>
            {#if s.eligible}
              <span class="above tnum">{s.label}</span>
              <span class="fill" class:est={!s.complete}></span>
            {:else}
              <span class="fill placeholder"></span>
            {/if}
            <span class="name mono">{s.row.model}</span>
          </button>
        {/each}
      </div>
    </div>
  </div>
</section>
{/if}

<style>
  .cards { max-width: var(--maxw); margin: 26px auto 0; padding: 0 24px; }
  h2 { font-size: 17px; font-weight: 750; margin: 0; }
  .intro { font-size: 12px; margin: 2px 0 12px; }
  .grid { display: grid; gap: 18px; grid-template-columns: repeat(3, minmax(0, 1fr)); }
  @media (max-width: 960px) { .grid { grid-template-columns: minmax(0, 1fr); } }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px 10px; box-shadow: var(--shadow); min-width: 0; }
  header { margin-bottom: 8px; }
  h3 { font-size: 14px; font-weight: 750; margin: 0; }
  header p { font-size: 11px; margin: 3px 0 0; }
  .plot { --top: 20px; --bars: 120px; --lane: 196px; position: relative; display: flex; align-items: flex-end; gap: 6px; height: calc(var(--top) + var(--bars) + var(--lane)); padding: var(--top) 0 var(--lane); box-sizing: border-box; }
  .bar { position: relative; flex: 1 1 0; min-width: 0; height: var(--bars); border: none; background: none; padding: 0; cursor: pointer; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; font: inherit; color: inherit; }
  .bar:hover .fill { filter: brightness(1.12); }
  .fill { width: 100%; max-width: 44px; height: var(--h); min-height: 2px; background: var(--c); border-radius: 3px 3px 0 0; position: relative; display: flex; align-items: flex-end; justify-content: center; transition: height .2s; }
  .fill.est { background: repeating-linear-gradient(135deg, var(--c) 0 4px, color-mix(in srgb, var(--c) 30%, var(--surface)) 4px 8px); }
  .fill.placeholder { height: 12px; background: none; border: 1px dashed var(--border); border-bottom: none; }
  .bar.none .name { color: var(--faint); }
  .val { color: #fff; font-size: 11px; font-weight: 750; padding-bottom: 5px; text-shadow: 0 0 2px rgba(0,0,0,.25); white-space: nowrap; }
  .val.outside { position: absolute; bottom: 100%; padding-bottom: 3px; color: var(--text); text-shadow: none; }
  .above { position: absolute; bottom: calc(var(--h) + 3px); font-size: 11px; font-weight: 750; color: var(--text); white-space: nowrap; }
  .name { position: absolute; top: calc(100% + 6px); height: calc(var(--lane) - 10px); left: 50%;
    writing-mode: vertical-rl; transform: translateX(-50%) rotate(180deg); font-size: 10px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; text-align: right; }
</style>
