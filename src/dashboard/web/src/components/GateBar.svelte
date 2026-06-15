<script>
  import { GATES, TOTAL_GATES, gate } from '../lib/gates.js'
  // reached = number of gates stamped; furthest = gate id
  let { reached = 0, furthest = null, accent = 'var(--accent)' } = $props()
  const segs = GATES
  const name = $derived(furthest ? gate(furthest).name : 'No gates reached')
</script>

<div class="gatebar" title={`${reached}/${TOTAL_GATES} gates — furthest: ${name}`}>
  <div class="track">
    {#each segs as g, i}
      <span class="seg" class:on={i < reached} class:badge={g.badge}
            style={i < reached ? `background:${accent}` : ''}></span>
    {/each}
  </div>
  <div class="meta">
    <span class="furthest">{name}</span>
    <span class="count tnum">{reached}<span class="faint">/{TOTAL_GATES}</span></span>
  </div>
</div>

<style>
  .gatebar { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .track { display: flex; gap: 2px; height: 8px; }
  .seg {
    flex: 1; border-radius: 2px; background: #e9ecf2; transition: background .2s;
  }
  .seg.badge { border-radius: 2px; box-shadow: inset 0 0 0 1px #d7c98a55; }
  .seg.badge.on { box-shadow: inset 0 0 0 1.5px #ffffff88; }
  .meta { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
  .furthest { font-size: 12.5px; font-weight: 600; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .count { font-size: 12px; font-weight: 700; color: var(--muted); flex: none; }
</style>
