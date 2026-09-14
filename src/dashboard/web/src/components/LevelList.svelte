<script>
  // A model page's thinking levels (decisions 3A / 4B, 2026-09-14): one
  // collapsible line per level the catalog knows, highest first, with the
  // run's headline stats — completion with the furthest gate, turns, time,
  // cost, when and on which config — and "(not benchmarked yet)" greyed for a
  // level nobody ran. Expanding a level shows its run (LevelDetail). `open` is
  // the set of expanded levels, owned by the page so a bar click can open one.
  import { levelLabel } from '../lib/board.js'
  import { dateShort, dur, usd, legLabel } from '../lib/format.js'
  import LevelDetail from './LevelDetail.svelte'
  let { levels = [], open = new Set(), ontoggle = () => {}, benchmarks = [], onreport = null } = $props()
  const key = (l) => l.level ?? '_'
</script>

<section class="levels" aria-label="Thinking levels">
  <div class="lhead">
    <span></span><span>Thinking level</span><span>Completion</span><span class="r">Turns</span><span class="r">Time</span><span class="r">Cost</span><span class="r">Run</span>
  </div>
  <ul class="rows">
    {#each levels as l (key(l))}
      {@const r = l.row}
      {@const isOpen = !l.absent && open.has(key(l))}
      <li class="level" class:absent={l.absent} class:open={isOpen} id={`level-${key(l)}`}>
        <button class="row" disabled={l.absent} aria-expanded={isOpen} onclick={() => ontoggle(key(l))}>
          <span class="chev" aria-hidden="true">{l.absent ? '' : isOpen ? '▾' : '▸'}</span>
          <span class="lname mono">{levelLabel(l.level)}{#if l.absent}<span class="nb"> (not benchmarked yet)</span>{/if}</span>
          {#if r}
            <span class="comp">
              <span class="pct" class:full={r.completion >= 100}>{r.completion}%</span>
              <span class="track" aria-hidden="true"><span class="fill" style={`width:${Math.min(100, r.completion)}%`}></span></span>
              {#if r.completion < 100}<span class="gate faint">{legLabel(r)}</span>{/if}
            </span>
            <span class="tnum r"><b>{r.turns}</b></span>
            <span class="tnum r"><b>{dur(r.durationS)}</b></span>
            <span class="tnum r"><b>{usd(r.totalCostUsd)}</b></span>
            <span class="meta faint r">{dateShort(r.startedAt)} · <span class="mono">{r.config}</span>{#if r.continuedFrom} · ↪{/if}</span>
          {:else}
            <span class="comp faint">—</span><span class="r faint">—</span><span class="r faint">—</span><span class="r faint">—</span><span class="r faint"></span>
          {/if}
        </button>
        {#if isOpen && r}
          <LevelDetail row={r} {benchmarks} {onreport} />
        {/if}
      </li>
    {/each}
  </ul>
</section>

<style>
  .levels { min-width: 0; }
  .lhead, .row { display: grid; grid-template-columns: 18px minmax(120px, .9fr) minmax(160px, 1.3fr) 72px 92px 92px minmax(120px, .9fr); align-items: center; gap: 12px; }
  .lhead { padding: 0 14px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; }
  .r { text-align: right; }
  .rows { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
  .level { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); }
  .level.open { border-color: var(--faint); }
  .row { width: 100%; text-align: left; border: none; background: none; padding: 11px 14px; font: inherit; color: inherit; cursor: pointer; border-radius: var(--radius-sm); }
  .row:hover:not(:disabled) { background: var(--surface-2); }
  .row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .row:disabled { cursor: default; }
  .level.absent { background: transparent; border-style: dashed; }
  .level.absent .lname { color: var(--faint); }
  .nb { font-family: inherit; font-weight: 500; }
  .chev { color: var(--faint); font-size: 12px; }
  .lname { font-size: 13.5px; font-weight: 650; }
  .comp { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .pct { font-size: 13.5px; font-weight: 700; }
  .pct.full { color: var(--green); }
  .track { display: block; height: 4px; background: var(--wash); border-radius: 2px; overflow: hidden; max-width: 180px; }
  .fill { display: block; height: 100%; background: var(--accent); border-radius: 2px; }
  .pct.full + .track .fill { background: var(--green); }
  .gate { font-size: 10.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row b { font-size: 13px; font-weight: 700; }
  .meta { font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .level :global(.detail) { padding: 0 14px 14px; }
  @media (max-width: 960px) {
    .lhead { display: none; }
    .lhead, .row { grid-template-columns: 18px minmax(100px, 1fr) minmax(120px, 1.2fr) 60px 80px; }
    .row > :nth-child(6), .row > :nth-child(7) { display: none; }
  }
</style>
