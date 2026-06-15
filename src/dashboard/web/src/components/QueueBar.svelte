<script>
  let { active = null, queue = [], onkill, onremove, onreorder, onnew, onspectate } = $props()
  let dragIndex = $state(null)
  let overIndex = $state(null)
  // Remove needs an explicit confirm (Andreas) — clicking ✕ arms an inline
  // "Remove this run?" prompt on that card; the actual onremove only fires on
  // the explicit Remove button. confirmId = the queueId currently awaiting
  // confirmation (one at a time).
  let confirmId = $state(null)
  function drop(i) {
    if (dragIndex !== null && dragIndex !== i) onreorder(dragIndex, i)
    dragIndex = null; overIndex = null
  }
  const label = (k) => k === 'official' ? 'benchmark' : 'custom'
</script>

<section class="qbar">
  <span class="qtitle">Queue</span>
  <div class="track">
    {#if active}
      <div class="card active" onclick={() => onspectate()} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onspectate() } }} role="button" tabindex="0">
        <div class="ctop">
          <span class="badge {active.kind}">{label(active.kind)}</span>
          <span class="now"><span class="dot live"></span> running</span>
        </div>
        <div class="cmodel mono">{active.model}</div>
        <div class="cmeta faint">turn {active.currentTurn ?? 0}</div>
        <button class="kill" onclick={(e) => { e.stopPropagation(); onkill() }} title="Kill run — starts next">✕ kill</button>
      </div>
    {:else}
      <div class="card idle"><span class="faint">idle — nothing running</span></div>
    {/if}

    {#each queue as q, i (q.queueId)}
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div class="card up" class:over={overIndex === i} class:dragging={dragIndex === i}
           draggable="true"
           ondragstart={() => dragIndex = i}
           ondragover={(e) => { e.preventDefault(); overIndex = i }}
           ondragleave={() => { if (overIndex === i) overIndex = null }}
           ondrop={() => drop(i)}
           ondragend={() => { dragIndex = null; overIndex = null }}>
        <div class="ctop">
          <span class="grip" title="Drag to reorder">⠿</span>
          <span class="badge {q.kind}">{label(q.kind)}</span>
          {#if q.continueFrom}<span class="cont faint">↪</span>{/if}
          <button class="rm" onclick={() => confirmId = q.queueId} title="Remove">✕</button>
        </div>
        <div class="cmodel mono">{q.model}</div>
        <div class="cmeta faint">{#if q.kind === 'casual'}<span class="mono">{q.config}</span> · {q.maxTurns}t{:else}pokebench-v1{/if}</div>
        {#if confirmId === q.queueId}
          <div class="confirm">
            <span class="confirm-q">Remove this run?</span>
            <div class="confirm-actions">
              <button class="cf-yes" onclick={() => { onremove(q.queueId); confirmId = null }}>Remove</button>
              <button class="cf-no" onclick={() => confirmId = null}>Cancel</button>
            </div>
          </div>
        {/if}
      </div>
    {/each}

    <button class="add" onclick={() => onnew()}>+ Add run</button>
  </div>
</section>

<style>
  .qbar { max-width: var(--maxw); margin: 0 auto; padding: 14px 24px 0; display: flex; align-items: stretch; gap: 12px; }
  .qtitle { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); align-self: center; flex: none; }
  .track { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; flex: 1; align-items: stretch; }

  .card { flex: none; width: 178px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 9px 11px; background: var(--surface); position: relative; transition: border-color .12s, box-shadow .12s, opacity .12s; }
  .card.active { background: var(--accent-soft); border-color: #c7c8f7; box-shadow: var(--shadow); cursor: pointer; }
  .card.up { cursor: grab; }
  .card.up:active { cursor: grabbing; }
  .card.over { border-color: var(--accent); box-shadow: -2px 0 0 var(--accent) inset; }
  .card.dragging { opacity: .45; }
  .card.idle { display: flex; align-items: center; justify-content: center; font-size: 12px; }

  .ctop { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
  .now { font-size: 10.5px; font-weight: 650; color: var(--green); display: inline-flex; align-items: center; gap: 4px; margin-left: auto; }
  .grip { color: var(--faint); font-size: 11px; }
  .cont { font-size: 11px; }
  .rm { margin-left: auto; width: 18px; height: 18px; border: none; background: none; color: var(--faint); border-radius: 4px; font-size: 10px; }
  .rm:hover { background: var(--red-soft); color: var(--red); }
  .cmodel { font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cmeta { font-size: 10.5px; margin-top: 1px; }
  .badge.official, .badge.casual { font-size: 9px; padding: 1px 6px; }

  .kill { margin-top: 7px; width: 100%; border: 1px solid #f0c5c5; background: var(--surface); color: var(--red); font-weight: 650; font-size: 11px; padding: 4px; border-radius: 6px; }
  .kill:hover { background: var(--red-soft); }

  .add { flex: none; align-self: stretch; border: 1px dashed var(--border); background: var(--surface-2); color: var(--muted); font-weight: 600; font-size: 12px; padding: 0 16px; border-radius: var(--radius-sm); white-space: nowrap; }
  .add:hover { border-color: var(--accent); color: var(--accent); }

  /* inline remove-confirm (replaces accidental one-click removal) */
  .confirm { margin-top: 7px; border-top: 1px solid var(--border-2); padding-top: 6px; }
  .confirm-q { display: block; font-size: 10.5px; font-weight: 650; color: var(--red); margin-bottom: 5px; }
  .confirm-actions { display: flex; gap: 6px; }
  .cf-yes, .cf-no { flex: 1; font-size: 10.5px; font-weight: 650; padding: 4px; border-radius: 6px; border: 1px solid var(--border); }
  .cf-yes { border-color: #f0c5c5; background: var(--red-soft); color: var(--red); }
  .cf-yes:hover { background: #f9dada; }
  .cf-no { background: var(--surface); color: var(--muted); }
  .cf-no:hover { background: var(--surface-2); }
</style>
