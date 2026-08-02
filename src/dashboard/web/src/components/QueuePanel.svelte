<script>
  let { active = null, queue = [], onkill, onremove, onreorder, onnew } = $props()
  let dragIndex = $state(null)
  let overIndex = $state(null)

  function drop(i) {
    if (dragIndex !== null && dragIndex !== i) onreorder(dragIndex, i)
    dragIndex = null; overIndex = null
  }
  const label = (k) => k === 'official' ? 'benchmark' : 'custom'
</script>

<div class="queue">
  <div class="qhead">
    <h3>Queue</h3>
    <button class="btn ghost sm" onclick={() => onnew()}>+ Add</button>
  </div>

  {#if active}
    <div class="qcard active">
      <div class="ctop">
        <span class="badge {active.kind}">{label(active.kind)}</span>
        <span class="now"><span class="dot live"></span> running</span>
      </div>
      <div class="cmodel mono">{active.model}</div>
      <div class="cmeta faint">turn {active.currentTurn} · <span class="mono">{active.config}</span></div>
      <button class="kill" onclick={() => onkill()}>Kill run →<span class="sub">starts next</span></button>
    </div>
  {/if}

  {#if queue.length}
    <ul class="qlist">
      {#each queue as q, i (q.queueId)}
        <li class="qcard" class:over={overIndex === i} class:dragging={dragIndex === i}
            draggable="true"
            ondragstart={() => dragIndex = i}
            ondragover={(e) => { e.preventDefault(); overIndex = i }}
            ondragleave={() => { if (overIndex === i) overIndex = null }}
            ondrop={() => drop(i)}
            ondragend={() => { dragIndex = null; overIndex = null }}>
          <span class="grip" title="Drag to reorder">⠿</span>
          <div class="qbody">
            <div class="ctop">
              <span class="badge {q.kind}">{label(q.kind)}</span>
              {#if q.continueFrom}<span class="cont faint">↪ continue</span>{/if}
            </div>
            <div class="cmodel mono">{q.model}</div>
            <div class="cmeta faint">
              {#if q.kind === 'casual'}<span class="mono">{q.config}</span> · {q.maxTurns}t{#if q.stopAt} · ⇥ <span class="mono">{q.stopAt}</span>{/if}{#if q.rom} · 🎮 <span class="mono">{q.rom}</span>{/if}{:else}pokebench-v1{/if}
            </div>
          </div>
          <button class="rm" onclick={() => onremove(q.queueId)} title="Remove from queue">✕</button>
        </li>
      {/each}
    </ul>
  {:else}
    <p class="empty faint">Nothing queued.</p>
  {/if}
</div>

<style>
  .queue { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; box-shadow: var(--shadow); }
  .qhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
  h3 { font-size: 13px; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0; }
  .btn.sm { padding: 3px 9px; font-size: 12px; }

  .qlist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .qcard {
    border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px 12px;
    background: var(--surface-2); position: relative; transition: border-color .12s, transform .06s, box-shadow .12s;
  }
  .qcard.active { background: var(--accent-soft); border-color: #c7c8f7; margin-bottom: 12px; }
  .qcard.over { border-color: var(--accent); box-shadow: 0 -2px 0 var(--accent) inset; }
  .qcard.dragging { opacity: .5; }
  .qlist .qcard { display: grid; grid-template-columns: 16px 1fr 22px; gap: 8px; align-items: center; cursor: grab; }
  .qlist .qcard:active { cursor: grabbing; }

  .grip { color: var(--faint); font-size: 13px; text-align: center; user-select: none; }
  .ctop { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
  .now { font-size: 11px; font-weight: 650; color: var(--green); display: inline-flex; align-items: center; gap: 5px; margin-left: auto; }
  .cont { font-size: 10px; font-weight: 650; }
  .cmodel { font-size: 12.5px; font-weight: 600; }
  .cmeta { font-size: 11px; margin-top: 1px; }
  .badge.official, .badge.casual { font-size: 9.5px; padding: 2px 7px; }

  .kill { margin-top: 10px; width: 100%; border: 1px solid #f0c5c5; background: var(--surface); color: var(--red); font-weight: 650; font-size: 12px; padding: 7px; border-radius: 7px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
  .kill:hover { background: var(--red-soft); }
  .kill .sub { font-size: 10px; color: var(--faint); font-weight: 500; }

  .rm { width: 22px; height: 22px; border: none; background: none; color: var(--faint); border-radius: 5px; font-size: 11px; }
  .rm:hover { background: var(--red-soft); color: var(--red); }
  .empty { font-size: 12px; text-align: center; padding: 12px 0; }
</style>
