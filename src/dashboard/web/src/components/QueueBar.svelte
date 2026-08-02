<script>
  import Icon from './Icon.svelte'
  let { active = null, queue = [], onkill, onremove, onreorder, onnew, onspectate } = $props()
  let dragIndex = $state(null)
  let overIndex = $state(null)
  // Remove needs an explicit confirm (Andreas) — clicking ✕ arms an inline
  // "Remove this run?" prompt on that card; the actual onremove only fires on
  // the explicit Remove button. confirmId = the queueId currently awaiting
  // confirmation (one at a time).
  let confirmId = $state(null)
  // The active card's ✕ kill also needs an explicit confirm (Andreas) — arms an
  // inline "Stop this run?" prompt; onkill only fires on the explicit Stop button.
  let killArmed = $state(false)
  // Optimistic "stopping…" feedback: a stop only takes effect at the next turn
  // boundary (a clean savepoint — can be up to a full turn later for a slow
  // model), so without this the card sits on "running" and the stop looks dead.
  // We mark the run we asked to stop and show "stopping…" until it's gone.
  let stoppingId = $state(null)
  const isStopping = $derived(!!active && stoppingId !== null && active.runId === stoppingId)
  // Clear the flag once the stopped run is no longer the active one (it ended, or
  // the next run dequeued).
  $effect(() => { if (stoppingId !== null && active?.runId !== stoppingId) stoppingId = null })
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
          {#if isStopping}
            <span class="now stopping"><span class="dot"></span> stopping…</span>
          {:else}
            <span class="now"><span class="dot live"></span> running</span>
          {/if}
        </div>
        <div class="cmodel mono">{active.model}</div>
        <div class="cmeta faint">turn {active.currentTurn ?? 0}</div>
        {#if isStopping}
          <div class="stopping-note faint">stopping after this turn — saving a savepoint…</div>
        {:else if killArmed}
          <div class="confirm" onclick={(e) => e.stopPropagation()} role="presentation">
            <span class="confirm-q">Stop this run?</span>
            <div class="confirm-actions">
              <button class="cf-yes" onclick={(e) => { e.stopPropagation(); stoppingId = active.runId; onkill(); killArmed = false }}>Stop</button>
              <button class="cf-no" onclick={(e) => { e.stopPropagation(); killArmed = false }}>Cancel</button>
            </div>
          </div>
        {:else}
          <button class="kill" onclick={(e) => { e.stopPropagation(); killArmed = true }} title="Stop run — starts next"><Icon name="close" size={13} /> kill</button>
        {/if}
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
          <span class="grip" title="Drag to reorder"><Icon name="grip" size={13} /></span>
          <span class="badge {q.kind}">{label(q.kind)}</span>
          {#if q.continueFrom}<span class="cont faint" title="Continues an earlier run"><Icon name="rerun" size={12} /></span>{/if}
          <button class="rm" onclick={() => confirmId = q.queueId} title="Remove"><Icon name="close" size={11} /></button>
        </div>
        <div class="cmodel mono">{q.model}</div>
        <div class="cmeta faint">{#if q.kind === 'casual'}<span class="mono">{q.config}</span> · {q.maxTurns}t{#if q.stopAt} · ⇥ <span class="mono">{q.stopAt}</span>{/if}{#if q.rom} · <span class="mono">{q.rom}</span>{/if}{:else}pokebench-v1{/if}</div>
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
  .card.active { background: var(--accent-soft); border-color: var(--accent-rule); cursor: pointer; }
  .card.up { cursor: grab; }
  .card.up:active { cursor: grabbing; }
  .card.over { border-color: var(--accent); box-shadow: -2px 0 0 var(--accent) inset; }
  .card.dragging { opacity: .45; }
  .card.idle { display: flex; align-items: center; justify-content: center; font-size: 12px; }

  .ctop { display: flex; align-items: center; gap: 6px; margin-bottom: 5px; }
  .now { font-size: 10.5px; font-weight: 650; color: var(--green); display: inline-flex; align-items: center; gap: 4px; margin-left: auto; }
  .now.stopping { color: var(--amber); }
  .now.stopping .dot { background: var(--amber); animation: pulse 1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .stopping-note { margin-top: 7px; font-size: 10px; line-height: 1.35; color: var(--amber); }
  .grip { color: var(--faint); display: inline-flex; }
  .cont { display: inline-flex; }
  .rm { margin-left: auto; width: 18px; height: 18px; display: grid; place-items: center; padding: 0; border: none; background: none; color: var(--faint); border-radius: var(--radius-sm); }
  .rm:hover { background: var(--red-soft); color: var(--red); }
  .cmodel { font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cmeta { font-size: 10.5px; margin-top: 1px; }
  .badge.official, .badge.casual { font-size: 9px; padding: 1px 6px; }

  .kill { margin-top: 7px; width: 100%; border: 1px solid var(--red-rule); background: var(--surface); color: var(--red); font-weight: 650; font-size: 11px; padding: 4px; border-radius: var(--radius-sm); display: inline-flex; align-items: center; justify-content: center; gap: 5px; }
  .kill:hover { background: var(--red-soft); }

  .add { flex: none; align-self: stretch; border: 1px dashed var(--border); background: var(--surface-2); color: var(--muted); font-weight: 600; font-size: 12px; padding: 0 16px; border-radius: var(--radius-sm); white-space: nowrap; }
  .add:hover { border-color: var(--accent); color: var(--accent); }

  /* inline remove-confirm (replaces accidental one-click removal) */
  .confirm { margin-top: 7px; border-top: 1px solid var(--border-2); padding-top: 6px; }
  .confirm-q { display: block; font-size: 10.5px; font-weight: 650; color: var(--red); margin-bottom: 5px; }
  .confirm-actions { display: flex; gap: 6px; }
  .cf-yes, .cf-no { flex: 1; font-size: 10.5px; font-weight: 650; padding: 4px; border-radius: var(--radius-sm); border: 1px solid var(--border); }
  .cf-yes { border-color: var(--red-rule); background: var(--red-soft); color: var(--red); }
  .cf-yes:hover { border-color: var(--red); }
  .cf-no { background: var(--surface); color: var(--muted); }
  .cf-no:hover { background: var(--surface-2); }
</style>
