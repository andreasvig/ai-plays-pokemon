<script>
  // The numbers behind the board's per-task cards (Andreas 2026-09-13: "a giant
  // table which gives the numbers for the estimation for all runs"). Every run,
  // every leg: actual turns and the ratio to the typical leg, the estimated
  // turns for legs the run never cleared, and the totals the cards divide by
  // the gate count. Same helpers as the cards (lib/board.js), so a cell here is
  // exactly what a card's bar is built from.
  import { estimationMatrix, vendorOf, fmtUsd, fmtMinutes, PROJECT_FROM_GATE, MIN_CLEARS_FOR_TYPICAL } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  let { rows = [], oninspect = () => {} } = $props()

  const gateIds = $derived(GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id))
  const matrix = $derived(estimationMatrix(rows, gateIds))
  const nGates = $derived(gateIds.length)
  // 'Reached Viridian City' → 'Viridian City', so the prose can say 'reached …' without doubling the verb.
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))
  const eligible = $derived(matrix.rows.filter((x) => x.eligible).length)
  const complete = $derived(matrix.rows.filter((x) => x.complete).length)

  // Column headers: the ladder's short place names; the full checkpoint name is the tooltip.
  const SHORT = {
    left_bedroom: 'Bedroom', left_house: 'House', oaks_lab_entered: "Oak's Lab", starter_chosen: 'Starter',
    rival1_done: 'Rival 1', route1_reached: 'Route 1', viridian_reached: 'Viridian', parcel_delivered: 'Parcel',
    pokedex_received: 'Pokédex', viridian_forest_reached: 'V. Forest', pewter_reached: 'Pewter', brock_defeated: 'Brock',
  }
  const short = (g) => SHORT[g] ?? gate(g)?.name ?? g

  // turns | minutes | usd — minutes and USD per leg are the leg's turns × the
  // run's own average rate, which is also how the cards price a projection.
  let metric = $state('turns')
  const UNIT = { turns: 'turns', minutes: 'minutes', usd: 'USD' }
  const inMetric = (x, turns) => metric === 'turns' ? turns
    : metric === 'minutes' ? turns * (x.row.avgSPerTurn ?? 0) / 60
    : turns * (x.row.avgCostPerTurn ?? 0)
  const fmt = (v) => v == null ? '—'
    : metric === 'turns' ? String(Math.round(v))
    : metric === 'minutes' ? fmtMinutes(v)
    : fmtUsd(v)
  const totalIn = (x) => metric === 'turns' ? x.projected : metric === 'minutes' ? x.minutesToFinish : x.costToFinish
  const perTaskIn = (x) => metric === 'turns' ? x.turnsPerTask : metric === 'minutes' ? x.minutesPerTask : x.costPerTask
  const playedIn = (x) => metric === 'turns' ? x.played : metric === 'minutes' ? (x.row.durationS ?? 0) / 60 : x.row.totalCostUsd ?? 0

  // Cell tint: faster than typical shades green, slower shades red, log scale so 0.5× and 2× match.
  function tint(ratio) {
    if (ratio == null) return 'var(--surface)'
    const l = Math.log2(ratio), a = Math.round(Math.min(0.9, Math.abs(l) / 2.2) * 100)
    return l < 0 ? `color-mix(in srgb, var(--green-soft) ${a}%, var(--surface))` : `color-mix(in srgb, var(--red-soft) ${a}%, var(--surface))`
  }
  const x = (r) => r == null ? '—' : r.toFixed(2) + '×'
</script>

<section class="methods">
  <h2>Estimation methods</h2>
  <p class="intro faint">How the board turns a partial run into a time and cost per task, and every number that goes into it.</p>

  <ol class="steps">
    <li><b>Legs.</b> The ladder is {nGates} gates. A leg is the turns between two consecutive gate stamps; a run that cleared <i>k</i> gates has <i>k</i> measured legs.</li>
    <li><b>Typical leg.</b> The mean turns on that leg over every run that cleared it, once at least {MIN_CLEARS_FOR_TYPICAL} runs have. Computed over all {rows.length} runs, all thinking levels.</li>
    <li><b>Pace.</b> A run's turns on its cleared legs ÷ the typical turns on those same legs. 0.80× means it clears legs in 80% of the typical turns.</li>
    <li><b>Missing legs.</b> Each leg the run never cleared is charged pace × typical. The leg it failed on is floored at the turns it actually burned there, so a run never gets credit for fewer turns than it spent.</li>
    <li><b>Eligibility.</b> Only runs that reached <b>{fromGate}</b> are projected; earlier legs are too short and too alike to say anything about pace. Others are shown here but left off the per-task cards.</li>
    <li><b>Time and cost.</b> The run's own total plus the estimated extra turns × its own seconds and USD per turn, then ÷ {nGates} for the per-task figure.</li>
  </ol>

  <div class="toolbar">
    <div class="seg" role="group" aria-label="Unit">
      {#each Object.entries(UNIT) as [k, label]}
        <button class:on={metric === k} onclick={() => (metric = k)}>{label}</button>
      {/each}
    </div>
    <p class="faint note">
      {complete} of {matrix.rows.length} runs cleared the ladder · {eligible - complete} more are projected · {matrix.rows.length - eligible} never reached {fromGate} and are dimmed.
      {#if metric !== 'turns'}A leg's {UNIT[metric]} = its turns × the run's own average per turn; the small ratio stays turns vs typical turns.{/if}
    </p>
  </div>

  <div class="scroll">
    <table class="m mono">
      <thead>
        <tr>
          <th class="run">Run</th>
          {#each gateIds as g, i}
            <th class="gate" title={gate(g)?.name}><span class="idx">{i + 1}</span>{short(g)}</th>
          {/each}
          <th class="gate fail" title="turns spent on the leg the run never finished">Failed leg</th>
          <th class="num">Played</th>
          <th class="num" title="turns on cleared legs ÷ typical turns on the same legs">Pace</th>
          <th class="num" title="played + estimated">To finish</th>
          <th class="num" title={`to finish ÷ ${nGates} gates`}>Per task</th>
          <th class="num" title="turns actually played as a share of the projection">Played share</th>
        </tr>
      </thead>
      <tbody>
        {#each matrix.rows as x_ (x_.row.runId)}
          {@const r = x_.row}
          {@const est = Object.fromEntries(x_.estimates.map((e) => [e.gate, e]))}
          <tr class:dim={!x_.eligible}>
            <td class="run">
              <button class="model" onclick={() => oninspect(r)} title="open the run">
                <span class="dot" style={`--c:${vendorOf(r).color}`}></span>{r.model}
              </button>
              <span class="faint gates">{x_.cleared}/{nGates}</span>
            </td>
            {#each gateIds as g, i}
              {#if x_.legs[i]}
                {@const l = x_.legs[i]}
                <td class="cell" style={`--fill:${tint(l.ratio)}`}
                    title={`${r.model} · ${gate(g)?.name}: ${l.turns} turns${matrix.typical[g] != null ? ` · typical ${matrix.typical[g].toFixed(1)}` : ''}`}>
                  <span class="t">{fmt(inMetric(x_, l.turns))}</span><span class="r">{x(l.ratio)}</span>
                </td>
              {:else if est[g]}
                <td class="cell est"
                    title={`estimated: ${Math.round(est[g].turns)} turns at this run's pace${est[g].floored ? ' (floored at turns burned)' : ''}`}>
                  <span class="t">{fmt(inMetric(x_, est[g].turns))}</span><span class="r">est · {x(est[g].ratio)}</span>
                </td>
              {:else}
                <td class="none">·</td>
              {/if}
            {/each}
            {#if x_.tail}
              <td class="cell fail" title={`${x_.tail.turns} turns without reaching ${gate(x_.tail.gate)?.name}`}>
                <span class="t">{fmt(inMetric(x_, x_.tail.turns))}</span>
                <span class="r">{matrix.typical[x_.tail.gate] != null ? x(x_.tail.turns / matrix.typical[x_.tail.gate]) + ' ' : ''}{short(x_.tail.gate)}</span>
              </td>
            {:else}
              <td class="cell fail cleared faint">cleared</td>
            {/if}
            <td class="num">{fmt(playedIn(x_))}</td>
            <td class="num">{x(x_.pace)}</td>
            <td class="num"><b>{x_.eligible ? fmt(totalIn(x_)) : '—'}</b></td>
            <td class="num">{x_.eligible ? fmt(perTaskIn(x_)) : '—'}</td>
            <td class="num">
              {#if x_.playedShare != null}
                <span class="chip" class:ok={x_.playedShare >= 0.67}>{Math.round(x_.playedShare * 100)}%</span>
              {:else}—{/if}
            </td>
          </tr>
        {/each}
      </tbody>
      <tfoot>
        <tr class="ref">
          <td class="run">typical turns (mean)</td>
          {#each gateIds as g}<td class="cell">{matrix.typical[g] != null ? matrix.typical[g].toFixed(1) : '—'}</td>{/each}
          <td colspan="6"></td>
        </tr>
        <tr class="ref">
          <td class="run">runs that cleared it</td>
          {#each gateIds as g}<td class="cell">{matrix.clears[g]}</td>{/each}
          <td colspan="6"></td>
        </tr>
        <tr class="ref">
          <td class="run">slowest clear</td>
          {#each gateIds as g}<td class="cell">{matrix.slowest[g] ?? '—'}</td>{/each}
          <td colspan="6"></td>
        </tr>
      </tfoot>
    </table>
  </div>

  <p class="legend faint">
    <span class="sw" style="--fill:{tint(0.25)}"></span> faster than typical
    <span class="sw" style="--fill:{tint(4)}"></span> slower than typical
    <span class="sw est"></span> estimated, not played
    <span class="sep">·</span> ratio = leg turns ÷ typical turns
    <span class="sep">·</span> click a run to open it
  </p>
</section>

<style>
  .methods { max-width: 1440px; margin: 0 auto; padding: 40px 24px 56px; }
  .methods h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 6px; }
  .intro { font-size: 14px; margin: 0 0 22px; }

  .steps { max-width: 760px; margin: 0 0 28px; padding-left: 22px; }
  .steps li { font-size: 14px; line-height: 1.6; color: var(--muted); margin: 0 0 6px; }
  .steps b { color: var(--text); }
  .steps i { font-style: italic; }

  .toolbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin: 0 0 12px; }
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
  .seg button { font: inherit; font-size: 12px; padding: 5px 11px; background: var(--surface); color: var(--muted); border: 0; cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--border); }
  .seg button.on { background: var(--accent-soft); color: var(--accent-ink); font-weight: 700; }
  .seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .note { font-size: 12.5px; margin: 0; }

  .scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .m { border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 100%; }
  .m th, .m td { border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); white-space: nowrap; }
  .m th { position: sticky; top: 0; background: var(--wash); color: var(--muted); font-weight: 700; text-align: right; padding: 8px 6px; font-size: 11px; z-index: 1; }
  .m th.gate .idx { display: block; font-size: 9px; letter-spacing: .06em; color: var(--faint); }
  .m th.run, .m td.run { position: sticky; left: 0; text-align: left; background: var(--surface); z-index: 2; min-width: 190px; }
  .m th.run { background: var(--wash); z-index: 3; }
  .m td.run { padding: 4px 8px; }
  .model { font: inherit; background: none; border: 0; padding: 0; color: var(--text); cursor: pointer; display: inline-flex; align-items: center; gap: 6px; font-weight: 650; }
  .model:hover { text-decoration: underline; }
  .model:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); flex: none; }
  .gates { margin-left: 8px; font-size: 10.5px; }

  .m td.cell { background: var(--fill, var(--surface)); text-align: right; min-width: 58px; line-height: 1.15; padding: 4px 6px; font-variant-numeric: tabular-nums; }
  .m td.cell .t { font-weight: 750; display: block; }
  .m td.cell .r { font-size: 10px; color: var(--muted); display: block; }
  .m td.cell.est { background: repeating-linear-gradient(135deg, var(--wash) 0 3px, var(--surface) 3px 7px); color: var(--muted); }
  .m td.cell.est .t { font-weight: 600; }
  .m td.cell.fail { background: color-mix(in srgb, var(--amber) 10%, var(--surface)); border-left: 2px solid var(--border); }
  .m td.cell.cleared { text-align: center; font-weight: 500; }
  .m th.fail { border-left: 2px solid var(--border); }
  .m td.none { text-align: center; color: var(--faint); }
  .m td.num { text-align: right; padding: 4px 8px; font-variant-numeric: tabular-nums; }
  .m tr.dim td { opacity: .5; }
  .m tr.dim td.run { opacity: 1; color: var(--faint); }
  .m tr.dim .model { color: var(--faint); }
  .m tfoot tr.ref td { background: var(--wash); color: var(--text); font-weight: 700; }
  .m tfoot tr.ref td.run { font-weight: 600; color: var(--muted); }

  .chip { display: inline-block; font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 999px; background: var(--wash); color: var(--muted); }
  .chip.ok { background: var(--green-soft); color: var(--green); }

  .legend { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; margin: 12px 0 0; }
  .sw { display: inline-block; width: 22px; height: 12px; border: 1px solid var(--border); border-radius: 2px; background: var(--fill, var(--surface)); margin-left: 8px; }
  .sw:first-child { margin-left: 0; }
  .sw.est { background: repeating-linear-gradient(135deg, var(--wash) 0 3px, var(--surface) 3px 7px); }
  .sep { color: var(--faint); }
</style>
