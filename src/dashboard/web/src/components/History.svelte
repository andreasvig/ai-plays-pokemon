<script>
  import { usd, dur, perTurn, ago, dateShort } from '../lib/format.js'
  let { runs = [], oninspect, oncontinue } = $props()

  let kindFilter = $state('all')
  let statusFilter = $state('all')
  let query = $state('')
  let sort = $state('recent')

  const filtered = $derived(
    runs
      .filter((r) => kindFilter === 'all' || r.kind === kindFilter)
      .filter((r) => statusFilter === 'all' || r.status === statusFilter)
      .filter((r) => !query || r.model.toLowerCase().includes(query.toLowerCase()))
      .sort((a, b) => {
        if (sort === 'completion') return ((b.kind === 'official' ? b.completion : 0) - (a.kind === 'official' ? a.completion : 0)) || a.turns - b.turns
        if (sort === 'cost') return b.totalCostUsd - a.totalCostUsd
        if (sort === 'duration') return b.durationS - a.durationS
        return Date.parse(b.startedAt) - Date.parse(a.startedAt)
      })
  )
</script>

<section class="wrap">
  <div class="head">
    <h2>Run history</h2>
    <span class="faint">{filtered.length} of {runs.length} runs</span>
  </div>

  <div class="controls">
    <input class="search" placeholder="Filter by model…" bind:value={query} />
    <div class="segs">
      {#each ['all', 'official', 'casual'] as k}
        <button class:on={kindFilter === k} onclick={() => kindFilter = k}>{k}</button>
      {/each}
    </div>
    <select bind:value={statusFilter} class="sel">
      <option value="all">any status</option>
      <option value="completed">completed</option>
      <option value="terminated">terminated</option>
      <option value="cancelled">cancelled</option>
      <option value="crashed">crashed</option>
      <option value="running">running</option>
    </select>
    <select bind:value={sort} class="sel">
      <option value="recent">sort: recent</option>
      <option value="completion">sort: completion</option>
      <option value="cost">sort: cost</option>
      <option value="duration">sort: duration</option>
    </select>
  </div>

  <div class="lhead">
    <span></span>
    <span>Model</span>
    <span>Completion</span>
    <span class="r">Turns</span>
    <span class="r">Time</span>
    <span class="r">Cost</span>
    <span class="r">Status</span>
    <span></span>
  </div>

  <ul class="rows">
    {#each filtered as r (r.runId)}
      <!-- svelte-ignore a11y_no_noninteractive_element_to_interactive_role -->
      <li class="row" onclick={() => oninspect(r)} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); oninspect(r) } }} role="button" tabindex="0">
        <span class="c-kind"><span class="badge {r.kind}">{r.kind === 'official' ? 'OFF' : 'CAS'}</span></span>
        <span class="c-model">
          <span class="mname mono">{r.model}</span>
          <span class="meta faint">{dateShort(r.startedAt)} <span class="rel">({ago(r.startedAt)})</span> · <span class="mono">{r.config}</span>{#if r.continuedFrom} · ↪ continued{/if}</span>
        </span>
        <span class="c-comp">
          {#if r.kind === 'official'}
            <span class="pct" class:full={r.completion >= 100}>{r.completion}%</span>
            {#if r.completion < 100}<span class="gate faint">{r.furthestGateName?.replace(/ \(.*\)$/, '')}</span>{/if}
          {:else}
            <span class="pct dash faint">—</span>
          {/if}
        </span>
        <span class="c-turns tnum r"><b>{r.turns}</b>{#if r.maxTurns}<span class="sub">/{r.maxTurns}</span>{/if}</span>
        <span class="c-time tnum r"><b>{dur(r.durationS)}</b><span class="sub">{perTurn(r.avgSPerTurn)}/t</span></span>
        <span class="c-cost tnum r"><b>{usd(r.totalCostUsd)}</b><span class="sub">{usd(r.avgCostPerTurn)}/t</span></span>
        <span class="c-status r"><span class="status {r.status}">{r.status === 'running' ? '● running' : r.status}</span></span>
        <span class="c-act">
          <button class="mini" onclick={(e) => { e.stopPropagation(); oninspect(r) }} title="Inspect report">↗</button>
          <button class="mini" disabled={r.status === 'running'} onclick={(e) => { e.stopPropagation(); oncontinue(r) }} title="Continue run">⟳</button>
        </span>
      </li>
    {/each}
  </ul>
</section>

<style>
  .wrap { max-width: var(--maxw); margin: 0 auto; padding: 32px 24px 60px; }
  .head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px; }
  h2 { font-size: 22px; font-weight: 780; margin: 0; letter-spacing: -.02em; }

  .controls { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
  .search { flex: 1; min-width: 180px; font-family: inherit; font-size: 13px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); }
  .search:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
  .segs { display: flex; background: #eef1f5; border-radius: 8px; padding: 3px; gap: 2px; }
  .segs button { border: none; background: none; padding: 5px 12px; border-radius: 6px; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: capitalize; }
  .segs button.on { background: var(--surface); color: var(--text); box-shadow: var(--shadow); }
  .sel { font-family: inherit; font-size: 12.5px; padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface); color: var(--muted); }

  .lhead, .row {
    display: grid;
    grid-template-columns: 44px minmax(180px, 1.4fr) minmax(120px, 1fr) 78px 92px 96px 96px 64px;
    align-items: center; gap: 12px;
  }
  .lhead { padding: 0 14px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; }
  .lhead .r { text-align: right; }
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .row {
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 10px 14px; cursor: pointer; transition: border-color .1s, background .1s;
  }
  .row:hover { border-color: #d3d9e3; background: var(--surface-2); }

  .c-model { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .mname { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .meta { font-size: 11px; }
  .meta .rel { color: var(--faint); }
  .pct.dash { color: var(--faint); font-weight: 600; }
  .c-comp { display: flex; flex-direction: column; gap: 0; min-width: 0; }
  .pct { font-size: 13.5px; font-weight: 700; }
  .pct.full { color: var(--green); }
  .gate { font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .r { text-align: right; }
  .c-turns, .c-time, .c-cost { display: flex; flex-direction: column; align-items: flex-end; }
  .c-turns b, .c-time b, .c-cost b { font-size: 13px; font-weight: 700; }
  .sub { font-size: 10px; color: var(--muted); }
  .c-act { display: flex; gap: 4px; justify-content: flex-end; }
  .mini { width: 26px; height: 26px; border: 1px solid var(--border); background: var(--surface); border-radius: 6px; color: var(--muted); font-size: 13px; }
  .mini:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .mini:disabled { opacity: .4; cursor: not-allowed; }
  .badge.official, .badge.casual { font-size: 9.5px; padding: 2px 6px; }
</style>
