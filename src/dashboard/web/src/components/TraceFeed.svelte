<script>
  // turns: oldest -> newest. The newest (last) is the "current" turn and is
  // expanded; older turns collapse to a stacked header you click to open
  // (mirrors the live dashboard chat-scroll). Scrolls to the live turn.
  let { turns = [] } = $props()

  // handback + error are present in the real event stream (static/index.html)
  // but were omitted from the mock; wired in here for P6 parity.
  const boxIcon = { thinking: '💭', output: '💬', action: '🎮', tool: '🔧', memory: '🧠', ocr: '📝', settle: '⏱', handback: '↩️', error: '❌' }
  const boxName = { thinking: 'Thinking', output: 'Output', action: 'Action', tool: 'Tool', memory: 'Memory', ocr: 'OCR', settle: 'Screen settling', handback: 'Return to TaskMaster', error: 'Error' }

  const currentId = $derived(turns.length ? turns[turns.length - 1].turn : null)
  let open = $state(new Set())
  $effect(() => { open = new Set(currentId != null ? [currentId] : []) })
  function toggle(id) { const n = new Set(open); n.has(id) ? n.delete(id) : n.add(id); open = n }

  let scroller
  $effect(() => { if (scroller) scroller.scrollTop = scroller.scrollHeight })
</script>

<div class="tracefeed">
  <div class="feed-h">Live trace</div>
  <div class="scroll" bind:this={scroller}>
    {#each turns as turn (turn.turn)}
      {@const isOpen = open.has(turn.turn)}
      {@const isCurrent = turn.turn === currentId}
      <div class="turn-block" class:current={isCurrent} class:collapsed={!isOpen}>
        <button class="turn-head" onclick={() => toggle(turn.turn)}>
          <span class="arr">{isOpen ? '▾' : '▸'}</span>
          <span class="t-n mono">Turn {turn.turn}</span>
          {#if isCurrent}<span class="cur-tag"><span class="dot live"></span>current</span>{/if}
          {#if !isOpen}<span class="t-sum faint">{turn.boxes.find((b) => b.k === 'thinking')?.t.slice(0, 52)}…</span>{/if}
        </button>
        {#if isOpen}
          <div class="boxes">
            {#each turn.boxes as b}
              <div class="ebox {b.k}">
                <div class="ebox-h"><span class="ico">{boxIcon[b.k]}</span>{boxName[b.k]}{#if b.meta}<span class="ebox-meta faint">{b.meta}</span>{/if}</div>
                <div class="ebox-b">
                  {#if b.k === 'action'}<span class="mono act">{b.t}</span>
                  {:else if b.k === 'tool'}{#if b.args}<div class="mono call">{b.name}({b.args})</div>{/if}{#if b.resp != null}<div class="resp faint">→ {b.resp}</div>{/if}
                  {:else if b.k === 'memory'}<span class="mono">{b.t}</span>
                  {:else if b.k === 'output'}{#if b.ok != null}<span class="ok-tag" class:ok={b.ok} class:no={!b.ok}>{b.ok ? '✓ ok' : '✗ failed'}</span>{/if}{b.t}
                  {:else}{b.t}{/if}
                </div>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .tracefeed { display: flex; flex-direction: column; position: sticky; top: 70px; max-height: calc(100vh - 90px); }
  .feed-h { font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); padding: 2px 0 8px; flex: none; }
  .scroll { overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding-right: 4px; }

  .turn-block { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; flex: none; }
  .turn-block.current { border-color: #c7c8f7; }
  .turn-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: none; background: none; text-align: left; }
  .turn-block.collapsed .turn-head:hover { background: var(--surface-2); }
  .arr { color: var(--faint); font-size: 10px; flex: none; }
  .t-n { font-size: 12px; font-weight: 750; color: var(--accent); flex: none; }
  .cur-tag { font-size: 10px; font-weight: 700; color: var(--green); display: inline-flex; align-items: center; gap: 4px; }
  .t-sum { font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .boxes { padding: 2px 11px 10px; display: flex; flex-direction: column; gap: 7px; }
  .ebox { border-left: 3px solid var(--border); border-radius: 0 6px 6px 0; background: var(--surface-2); padding: 6px 10px; }
  .ebox-h { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); display: flex; align-items: center; gap: 5px; margin-bottom: 2px; }
  .ebox-meta { margin-left: auto; font-size: 9.5px; text-transform: none; letter-spacing: 0; }
  .ebox-b { font-size: 12px; line-height: 1.45; }
  .ico { font-size: 10px; }
  .ebox.thinking { border-color: #8b7cf0; }
  .ebox.output   { border-color: #14a8c4; }
  .ebox.action   { border-color: var(--red); }
  .ebox.tool     { border-color: var(--amber); }
  .ebox.memory   { border-color: var(--green); }
  .ebox.ocr      { border-color: #e8804a; }
  .ebox.settle   { border-color: #d8a93b; }
  .ebox.handback { border-color: #6ca4ff; }
  .ebox.error    { border-color: var(--red); background: var(--red-soft, #fdeeee); }
  .act { font-size: 13px; letter-spacing: 2px; }
  .call { font-size: 11.5px; }
  .resp { font-size: 11px; margin-top: 2px; }
  .ok-tag { font-size: 10px; font-weight: 700; margin-right: 5px; }
  .ok-tag.ok { color: var(--green); } .ok-tag.no { color: var(--red); }
</style>
