<script>
  // The "N of M models" dropdown in a card's top-right corner (Andreas
  // 2026-09-14, after Artificial Analysis). Every picker edits the ONE shared
  // selection (lib/selection.svelte.js), so a pick here moves every other
  // picker-bearing card too. `rows` are the candidates: every board row, all
  // thinking levels, in rank order.
  import { selection } from '../lib/selection.svelte.js'
  import { presets } from '../lib/selection.js'
  import { searchModels } from '../lib/modelSearch.js'
  import VendorMark from './VendorMark.svelte'
  let { rows = [] } = $props()

  let open = $state(false)
  let query = $state('')
  let root = $state(null)
  const chosen = $derived(new Set(selection.apply(rows).map((r) => r.model)))
  const shown = $derived(searchModels(rows, query))
  const options = $derived(presets(rows))
  const isPreset = (p) => {
    const want = new Set(p.picked == null ? selection.apply(rows).map((r) => r.model) : p.picked)
    return p.picked == null ? selection.picked == null : want.size === chosen.size && [...want].every((a) => chosen.has(a))
  }
  function onWindowClick(e) { if (open && root && !root.contains(e.target)) open = false }
  function onKey(e) { if (e.key === 'Escape') open = false }
</script>

<svelte:window onclick={onWindowClick} onkeydown={onKey} />

<div class="picker" bind:this={root}>
  <button class="trigger" class:open aria-haspopup="listbox" aria-expanded={open} onclick={() => (open = !open)}>
    <span class="tnum">{chosen.size} of {rows.length} models</span>
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5 5 6.5 8 3.5" fill="none" stroke="currentColor" stroke-width="1.5" /></svg>
  </button>
  {#if open}
    <div class="panel" role="dialog" aria-label="Choose models">
      <input class="search mono" type="search" placeholder="Search models…" bind:value={query} />
      <div class="presets">
        {#each options as p (p.key)}
          <button class:on={isPreset(p)} onclick={() => selection.set(p.picked)}>{p.label}</button>
        {/each}
      </div>
      <ul class="list" role="listbox" aria-multiselectable="true">
        {#each shown as r (r.model)}
          <li>
            <label class="opt" class:on={chosen.has(r.model)}>
              <input type="checkbox" checked={chosen.has(r.model)} onchange={() => selection.toggle(r.model, rows)} />
              <VendorMark row={r} size={14} />
              <span class="alias mono">{r.model}</span>
              <span class="pct tnum" class:full={r.completion >= 100}>{r.completion}%</span>
            </label>
          </li>
        {/each}
        {#if !shown.length}<li class="empty faint">No model matches “{query}”</li>{/if}
      </ul>
    </div>
  {/if}
</div>

<style>
  .picker { position: relative; flex: none; }
  .trigger { display: inline-flex; align-items: center; gap: 7px; font: inherit; font-size: 11.5px; font-weight: 600; color: var(--text);
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 5px 9px; cursor: pointer; white-space: nowrap; }
  .trigger:hover, .trigger.open { border-color: var(--faint); }
  .trigger:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .panel { position: absolute; right: 0; top: calc(100% + 6px); width: 300px; max-height: 380px; display: flex; flex-direction: column;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow-lg); z-index: 20; padding: 8px; gap: 8px; }
  .search { font: inherit; font-size: 12px; padding: 6px 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--bg); color: var(--text); }
  .search:focus { outline: 2px solid var(--accent); outline-offset: -1px; }
  .presets { display: flex; flex-wrap: wrap; gap: 4px; }
  .presets button { font: inherit; font-size: 10.5px; font-weight: 600; padding: 3px 8px; border-radius: 999px; border: 1px solid var(--border); background: var(--surface); color: var(--muted); cursor: pointer; }
  .presets button.on { background: var(--accent-soft); color: var(--accent-ink); border-color: var(--accent-rule); }
  .list { list-style: none; margin: 0; padding: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }
  .opt { display: flex; align-items: center; gap: 8px; padding: 5px 6px; border-radius: var(--radius-sm); cursor: pointer; font-size: 11.5px; color: var(--muted); }
  .opt:hover { background: var(--wash); }
  .opt.on { color: var(--text); }
  .opt input { margin: 0; accent-color: var(--accent); }
  .alias { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pct { font-size: 10.5px; color: var(--faint); }
  .pct.full { color: var(--green); font-weight: 700; }
  .empty { padding: 8px 6px; font-size: 11.5px; }
</style>
