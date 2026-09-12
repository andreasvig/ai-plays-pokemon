<script>
  // Three headline bar cards above the board (Andreas 2026-09-12, modelled on
  // Artificial Analysis's Intelligence / Speed / Cost strip): performance with a
  // 100% line and clears rising above it, speed as turns per minute, cost per
  // ten turns. One bar per MODEL — the best-ranked thinking level — regardless
  // of the board's "include all thinking levels" toggle; the caller passes the
  // collapsed rows.
  import { headlineSeries, vendorOf, PERF_LINE } from '../lib/board.js'
  let { rows = [], oninspect = () => {} } = $props()

  const series = $derived(headlineSeries(rows))
  const vendors = $derived((() => {
    const seen = new Map()
    for (const r of rows) { const v = vendorOf(r); if (!seen.has(v.key)) seen.set(v.key, v) }
    return [...seen.values()]
  })())
  // A bar too short to hold its number gets the number above it instead.
  const INSIDE_MIN = 0.16
</script>

{#if rows.length}
<section class="cards" aria-label="Headline comparison">
  <div class="card">
    <header>
      <h3><span class="sw perf"></span>Performance</h3>
      <p class="faint">Gate completion · clears ranked by fewest turns above the line · Higher is better</p>
    </header>
    <div class="plot" style={`--linef:${PERF_LINE}`}>
      <div class="hundred"><span>100%</span></div>
      {#each series.performance as s (s.row.runId)}
        <button class="bar" class:complete={s.complete} style={`--h:${(s.height * 100).toFixed(1)}%; --c:${vendorOf(s.row).color}`}
          title={`${s.row.model}: ${s.label}${s.complete ? `, cleared in ${s.row.turns} turns` : ''}`} onclick={() => oninspect(s.row)}>
          {#if s.complete}<span class="above tnum">{s.row.turns}<small>T</small></span>{/if}
          <span class="fill">
            <span class="val tnum" class:outside={s.height < INSIDE_MIN}>{s.label}</span>
          </span>
          <span class="name mono">{s.row.model}</span>
        </button>
      {/each}
    </div>
  </div>

  <div class="card">
    <header>
      <h3><span class="sw speed"></span>Speed</h3>
      <p class="faint">Average turns per minute · Higher is better</p>
    </header>
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
    <header>
      <h3><span class="sw cost"></span>Cost per 10 turns</h3>
      <p class="faint">Average USD for ten turns, all calls included · Lower is better</p>
    </header>
    <div class="plot">
      {#each series.cost as s (s.row.runId)}
        <button class="bar" style={`--h:${(s.height * 100).toFixed(1)}%; --c:${vendorOf(s.row).color}`}
          title={`${s.row.model}: ${s.label} per 10 turns`} onclick={() => oninspect(s.row)}>
          <span class="above tnum">{s.label}</span>
          <span class="fill"></span>
          <span class="name mono">{s.row.model}</span>
        </button>
      {/each}
    </div>
  </div>

  <p class="legend faint">
    {#each vendors as v (v.key)}<span class="key" style={`--c:${v.color}`}></span>{v.label}{/each}
    <span class="sep">·</span> one bar per model, its best thinking level
  </p>
</section>
{/if}

<style>
  .cards { max-width: var(--maxw); margin: 26px auto 0; padding: 0 24px; display: grid; gap: 18px;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px 12px; box-shadow: var(--shadow); min-width: 0; }
  header { margin-bottom: 10px; }
  h3 { font-size: 17px; font-weight: 750; margin: 0; display: flex; align-items: center; gap: 8px; }
  .sw { width: 11px; height: 11px; border-radius: 2px; display: inline-block; }
  .sw.perf { background: var(--accent); }
  .sw.speed { background: var(--amber); }
  .sw.cost { background: var(--retry); }
  header p { font-size: 11.5px; margin: 4px 0 0; }

  /* The plot: bars sit on a baseline, names hang below it. --h is the bar's
     height as a percentage of the bar area; --c its vendor colour. */
  /* --bars is the bar area, --lane the name lane under the baseline. */
  /* --top is headroom for the label above the tallest bar. Names are the full
     alias with its thinking level (Andreas 2026-09-12: "i still want the
     thinking level in parentheses"); the lane is sized for the longest one in
     the roster, gemini-3.5-flash-lite(minimal). */
  .plot { --top: 22px; --bars: 190px; --lane: 196px; position: relative; display: flex; align-items: flex-end; gap: 6px; height: calc(var(--top) + var(--bars) + var(--lane)); padding: var(--top) 0 var(--lane); box-sizing: border-box; }
  .hundred { position: absolute; left: 0; right: 0; bottom: calc(var(--lane) + var(--bars) * var(--linef)); border-top: 1px dashed var(--faint); pointer-events: none; }
  .hundred span { position: absolute; right: 0; top: -15px; font-size: 10px; color: var(--faint); font-weight: 700; }
  .bar { position: relative; flex: 1 1 0; min-width: 0; height: var(--bars); border: none; background: none; padding: 0; cursor: pointer; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; font: inherit; color: inherit; }
  .bar:hover .fill { filter: brightness(1.12); }
  .fill { width: 100%; max-width: 44px; height: var(--h); min-height: 2px; background: var(--c); border-radius: 3px 3px 0 0; position: relative; display: flex; align-items: flex-end; justify-content: center; transition: height .2s; }
  .bar.complete .fill { box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); }
  .val { color: #fff; font-size: 11px; font-weight: 750; padding-bottom: 6px; text-shadow: 0 0 2px rgba(0,0,0,.25); white-space: nowrap; }
  .val.outside { position: absolute; bottom: 100%; padding-bottom: 3px; color: var(--text); text-shadow: none; }
  .above { position: absolute; bottom: calc(var(--h) + 3px); font-size: 11px; font-weight: 750; color: var(--text); white-space: nowrap; }
  .above small { font-size: 9px; color: var(--muted); margin-left: 1px; }
  /* Names read bottom-to-top under the baseline, clipped to the label lane. */
  .name { position: absolute; top: calc(100% + 6px); height: calc(var(--lane) - 10px); left: 50%;
    writing-mode: vertical-rl; transform: translateX(-50%) rotate(180deg); font-size: 10px; color: var(--muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1; text-align: right; }
  .legend { grid-column: 1 / -1; font-size: 11.5px; text-align: center; display: flex; align-items: center; justify-content: center; gap: 6px 10px; flex-wrap: wrap; margin: -4px 0 0; }
  .key { display: inline-block; width: 9px; height: 9px; background: var(--c); border-radius: 2px; vertical-align: -1px; margin-right: 4px; }
  .sep { opacity: .5; }
</style>
