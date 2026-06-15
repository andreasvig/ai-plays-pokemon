<script>
  import { usd, dur, perTurn } from '../lib/format.js'
  let {
    rows = [], stats = {}, oninspect,
    oss = $bindable('all'), maxPrice = $bindable(0), priceMax = 1,
  } = $props()

  let expanded = $state(false)
  const medal = (r) => r === 1 ? 'var(--gold)' : r === 2 ? 'var(--silver)' : r === 3 ? 'var(--bronze)' : 'var(--faint)'

  const ranked = $derived(rows.map((r, i) => ({ ...r, displayRank: i + 1 })))
  const shown = $derived(expanded ? ranked : ranked.slice(0, 10))
  const priceFiltered = $derived(maxPrice < priceMax - 1e-9)
</script>

<section class="hero">
  <h1>PokeBench</h1>
  <p class="tagline">Can a language model play Pokémon FireRed <em>at pace</em>? A deterministic
    referee reads game memory out-of-band and stamps story gates; a progressive deadline ladder
    ends runs that fall behind. Same harness, same config, same ROM — the model is the only variable.</p>
  <div class="chips">
    <span class="chip"><b>{stats.completers}</b> models at 100%</span>
    <span class="chip"><b>{stats.modelsRanked}</b> ranked</span>
    <span class="chip mono">{stats.benchmarkVersion}</span>
  </div>
</section>

<section class="board">
  <div class="board-head">
    <h2>Leaderboard</h2>
    <div class="filters">
      <div class="segs">
        <button class:on={oss === 'all'} onclick={() => oss = 'all'}>All models</button>
        <button class:on={oss === 'oss'} onclick={() => oss = 'oss'}>Open-source</button>
      </div>
      <div class="slider" class:active={priceFiltered}>
        <div class="sl-top">
          <span class="sl-label">max cost / turn</span>
          <div class="sl-field">
            <span class="dollar">$</span>
            <input class="num tnum" type="number" min="0" max={priceMax} step="0.001" bind:value={maxPrice} />
          </div>
        </div>
        <input class="range" type="range" min="0" max={priceMax} step={priceMax / 200} bind:value={maxPrice} />
      </div>
    </div>
  </div>
  <p class="rule-note faint">Ranked by gate completion, then fewest turns. 100%-clears compared head-to-head on turns. Best official run per model.</p>

  <div class="lhead">
    <span class="c-rank">#</span>
    <span class="c-model">Model</span>
    <span class="c-comp">Completion</span>
    <span class="c-turns r">Turns</span>
    <span class="c-time r">Time</span>
    <span class="c-cost r">Cost</span>
  </div>

  <ol class="rows">
    {#each shown as r (r.runId)}
      <li class="row" class:top={r.displayRank <= 3} onclick={() => oninspect(r)} role="button" tabindex="0">
        <span class="c-rank"><span class="ranknum" style={`color:${medal(r.displayRank)}`}>{r.displayRank}</span></span>
        <span class="c-model">
          <span class="mname mono">{r.model}</span>
          {#if r.openSource}<span class="oss" title="Open-weights">OSS</span>{/if}
        </span>
        <span class="c-comp">
          <span class="pct" class:full={r.completion >= 100}>{r.completion}%</span>
          {#if r.completion < 100}<span class="gate faint">{r.furthestGateName?.replace(/ \(.*\)$/, '')}</span>{/if}
        </span>
        <span class="c-turns tnum r"><b>{r.turns}</b></span>
        <span class="c-time tnum r"><b>{dur(r.durationS)}</b><span class="sub">{perTurn(r.avgSPerTurn)}/turn</span></span>
        <span class="c-cost tnum r"><b>{usd(r.totalCostUsd)}</b><span class="sub">{usd(r.avgCostPerTurn)}/turn</span></span>
      </li>
    {/each}
  </ol>

  {#if ranked.length > 10}
    <button class="showmore" onclick={() => expanded = !expanded}>
      {expanded ? 'Show top 10' : `Show all ${ranked.length}`}
    </button>
  {/if}
</section>

<style>
  .hero { max-width: var(--maxw); margin: 0 auto; padding: 40px 24px 8px; }
  h1 { font-size: 40px; font-weight: 800; letter-spacing: -.03em; margin: 0 0 10px; }
  .tagline { max-width: 680px; font-size: 15px; line-height: 1.6; color: var(--muted); margin: 0; }
  .tagline em { color: var(--text); font-style: italic; }
  .chips { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
  .chip { font-size: 12px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); padding: 5px 11px; border-radius: 999px; }
  .chip b { color: var(--text); font-weight: 750; }

  .board { max-width: var(--maxw); margin: 18px auto 50px; padding: 0 24px; }
  .board-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 4px; flex-wrap: wrap; }
  h2 { font-size: 18px; font-weight: 750; margin: 0; }
  .rule-note { font-size: 12px; margin: 0 0 14px; }
  .filters { display: flex; align-items: center; gap: 14px; }
  .segs { display: flex; background: #eef1f5; border-radius: 8px; padding: 3px; gap: 2px; }
  .segs button { border: none; background: none; padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; color: var(--muted); }
  .segs button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); }

  .slider { display: flex; flex-direction: column; gap: 4px; }
  .sl-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .sl-label { font-size: 10.5px; color: var(--faint); font-weight: 650; text-transform: uppercase; letter-spacing: .03em; }
  .slider.active .sl-label { color: var(--accent); }
  .sl-field { display: flex; align-items: center; gap: 1px; border: 1px solid var(--border); border-radius: 6px; padding: 1px 5px; background: var(--surface); }
  .slider.active .sl-field { border-color: var(--accent); }
  .dollar { font-size: 11px; color: var(--faint); }
  .num { width: 56px; border: none; background: none; font-family: inherit; font-size: 12px; font-weight: 700; color: var(--text); padding: 2px 0; }
  .num:focus { outline: none; }
  .range { width: 170px; accent-color: var(--accent); cursor: pointer; }

  .lhead, .row {
    display: grid;
    grid-template-columns: 38px minmax(170px, 1.2fr) minmax(150px, 1fr) 64px 110px 110px;
    align-items: center; gap: 14px;
  }
  .lhead { padding: 0 16px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; }
  .r { text-align: right; }
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .row {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 13px 16px; box-shadow: var(--shadow); cursor: pointer;
    transition: border-color .12s, box-shadow .12s, transform .06s;
  }
  .row:hover { border-color: #d3d9e3; box-shadow: var(--shadow-lg); }
  .row:active { transform: translateY(1px); }
  .row.top { background: linear-gradient(180deg, #fff, #fcfdff); }

  .ranknum { font-size: 18px; font-weight: 800; font-variant-numeric: tabular-nums; }
  .c-model { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .mname { font-size: 13.5px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .oss { font-size: 9px; font-weight: 800; letter-spacing: .03em; color: var(--accent-ink); background: var(--accent-soft); padding: 2px 5px; border-radius: 4px; flex: none; }
  .c-comp { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .pct { font-size: 15px; font-weight: 750; }
  .pct.full { color: var(--green); }
  .gate { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .c-turns b { font-size: 14px; font-weight: 700; }
  .c-time, .c-cost { display: flex; flex-direction: column; align-items: flex-end; }
  .c-time b, .c-cost b { font-size: 14px; font-weight: 700; }
  .sub { font-size: 10.5px; color: var(--muted); }

  .showmore { display: block; margin: 16px auto 0; border: 1px solid var(--border); background: var(--surface); color: var(--muted); font-size: 12.5px; font-weight: 600; padding: 8px 18px; border-radius: 999px; }
  .showmore:hover { border-color: #d3d9e3; color: var(--text); }
</style>
