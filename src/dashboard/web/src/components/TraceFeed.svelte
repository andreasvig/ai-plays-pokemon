<script>
  import { usd, actionEmoji } from '../lib/format.js'
  import { mdToHtml } from '../lib/md.js'

  // turns: chronological feed of tagged entries — {kind:'turn', turn, boxes} and
  // {kind:'master', ...} (the TaskMaster card, interleaved at task boundaries
  // just above the first player turn of its task). The newest TURN is the
  // "current" turn and is expanded; the previous turn is also auto-opened. Older
  // turns collapse to a stacked header you click to open (mirrors the live
  // dashboard chat-scroll). Scrolls to the live turn.
  // hiddenTurns: count of older turns dropped below the live window (Spectate
  // keeps only the last few tasks live); shown as a muted note so the operator
  // knows the rail is windowed and the full trace lives in the run report.
  let { turns = [], hiddenTurns = 0 } = $props()

  // handback + error are present in the real event stream (static/index.html)
  // but were omitted from the mock; wired in here for P6 parity.
  const boxIcon = { thinking: '💭', output: '💬', action: '🎮', tool: '🔧', memory: '🧠', ocr: '📝', settle: '⏱', handback: '↩️', error: '❌' }
  const boxName = { thinking: 'Thinking', output: 'Output', action: 'Action', tool: 'Tool', memory: 'Memory', ocr: 'OCR', settle: 'Screen settling', handback: 'Return to TaskMaster', error: 'Error' }

  // TaskMaster's verdict on the PREVIOUS task → labeled chip + tone.
  const VERDICT = {
    succeeded: { label: '✅ Succeeded', tone: 'ok' },
    failed: { label: '❌ Failed', tone: 'no' },
    partial: { label: '🟡 Partial', tone: 'partial' },
    other: { label: '⚪ Other', tone: 'partial' },
  }
  function verdict(status) {
    return VERDICT[String(status || '').toLowerCase()] || { label: status || 'Rated', tone: 'partial' }
  }

  // turn ids (numbers), oldest→newest, ignoring master cards
  const turnIds = $derived(turns.filter((e) => e.kind === 'turn').map((e) => e.turn))
  const currentId = $derived(turnIds.length ? turnIds[turnIds.length - 1] : null)
  let open = $state(new Set())
  // A1: auto-open the last TWO turns (current + previous); guard when <2 exist.
  $effect(() => { open = new Set(turnIds.slice(-2)) })
  function toggle(id) { const n = new Set(open); n.has(id) ? n.delete(id) : n.add(id); open = n }

  // Sticky auto-scroll: only re-pin to the bottom when the user was ALREADY at
  // (or near) the bottom. An onscroll handler tracks `atBottom`; the effect that
  // reacts to `turns` only jumps to the bottom when that flag is set — so reading
  // older turns isn't yanked away on the next update.
  let scroller
  let atBottom = true
  function onScroll() {
    if (!scroller) return
    atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40
  }
  $effect(() => {
    turns // dependency: re-run when the feed changes
    if (scroller && atBottom) scroller.scrollTop = scroller.scrollHeight
  })
</script>

<div class="tracefeed">
  <div class="feed-h">Live trace</div>
  <div class="scroll" bind:this={scroller} onscroll={onScroll}>
    {#if hiddenTurns > 0}
      <div class="hidden-note">↑ {hiddenTurns} earlier turn{hiddenTurns === 1 ? '' : 's'} hidden — full trace in the run report</div>
    {/if}
    {#each turns as entry (entry.id)}
      {#if entry.kind === 'master'}
        <div class="master-block">
          <div class="master-head">🧭 TaskMaster{#if entry.model}<span class="m-meta mono">{entry.model}</span>{/if}{#if entry.cost != null}<span class="m-meta mono">{usd(entry.cost)}</span>{/if}</div>
          <div class="master-body">
            {#if entry.rating}
              {@const verd = verdict(entry.rating.status)}
              <div class="m-row m-rating {verd.tone}">
                <span class="m-lab">📊 Verdict on previous task</span>
                <span class="m-verdict">{verd.label}</span>
                {#if entry.rating.reasoning}<div class="m-desc">{entry.rating.reasoning}</div>{/if}
              </div>
            {/if}
            {#if entry.title}<div class="m-row"><span class="m-lab">📋 Task</span>{entry.title}</div>{/if}
            {#if entry.description}<div class="m-row"><span class="m-lab">🧭 Plan</span><div class="m-desc">{entry.description}</div></div>{/if}
            {#if entry.success}<div class="m-row"><span class="m-lab">🎯 Success criteria</span><span class="mono">{entry.success}</span></div>{/if}
          </div>
        </div>
      {:else}
        {@const turn = entry}
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
                    {#if b.k === 'action'}<span class="act">{actionEmoji(b.t)}</span>
                    {:else if b.k === 'tool'}{#if b.args}<div class="mono call">{b.name}({b.args})</div>{/if}{#if b.resp != null}<div class="resp faint">→ {b.resp}</div>{/if}
                    {:else if b.k === 'memory'}<span class="mono">{b.t}</span>
                    {:else if b.k === 'thinking'}<div class="md">{@html mdToHtml(b.t)}</div>
                    {:else if b.k === 'handback'}<div class="hb-verdict {b.tone}">{b.verdict}</div>{#if b.summary}<div class="hb-summary">{b.summary}</div>{/if}
                    {:else if b.k === 'output'}{#if b.ok != null}<div class="out-tag"><span class="ok-tag" class:ok={b.ok} class:no={!b.ok}>{b.ok ? '✓ ok' : '✗ failed'}</span></div>{/if}{#if b.t}<div class="out-body">{b.t}</div>{/if}
                    {:else}{b.t}{/if}
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    {/each}
  </div>
</div>

<style>
  /* Round 11: TraceFeed lives inside the fixed kiosk frame (Spectate is its only
     consumer). It fills its grid cell and the inner .scroll is the ONLY scroller —
     dropped the page-scroll sticky/top + viewport max-height. */
  .tracefeed { display: flex; flex-direction: column; height: 100%; max-height: none; min-height: 0; }
  .feed-h { font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); padding: 2px 0 8px; flex: none; }
  .scroll { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding-right: 4px; }
  .hidden-note { flex: none; font-size: 11px; color: var(--faint); text-align: center; padding: 6px 4px; }

  /* TaskMaster card — amber "strategy" layer, visually heavier than turn cards */
  .master-block { background: var(--surface); border: 1px solid #f0d9a0; border-left: 3px solid #ffce54; border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; flex: none; }
  .master-head { display: flex; align-items: center; gap: 8px; padding: 9px 13px; font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #b8860b; background: rgba(255, 206, 84, 0.12); border-bottom: 1px solid #f0d9a0; }
  .m-meta { margin-left: 8px; font-size: 10.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted); }
  .m-meta:first-of-type { margin-left: auto; }
  .master-body { padding: 11px 14px; display: flex; flex-direction: column; gap: 10px; }
  .m-row { font-size: 13px; line-height: 1.55; }
  .m-lab { display: block; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #c79a18; margin-bottom: 3px; }
  .m-desc { white-space: pre-wrap; color: var(--muted); }
  /* Verdict on the previous task — shown ABOVE the new task. */
  .m-verdict { font-size: 13px; font-weight: 750; }
  .m-rating.ok .m-verdict { color: var(--green); }
  .m-rating.no .m-verdict { color: var(--red); }
  .m-rating.partial .m-verdict { color: #c79a18; }

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
  .act { font-size: 18px; letter-spacing: 3px; }
  .call { font-size: 11.5px; }
  .resp { font-size: 11px; margin-top: 2px; }

  /* B9.4 — looser output box: gap between the ok/fail tag line and reasoning. */
  .ebox.output .ebox-b { display: flex; flex-direction: column; gap: 7px; }
  .out-tag { line-height: 1; }
  .out-body { line-height: 1.5; }
  .ok-tag { font-size: 10px; font-weight: 700; }
  .ok-tag.ok { color: var(--green); } .ok-tag.no { color: var(--red); }

  /* B9.3 — markdown thinking: bold section headers + paragraph gaps. */
  .md :global(p) { margin: 0 0 8px; line-height: 1.5; }
  .md :global(p:last-child) { margin-bottom: 0; }
  .md :global(strong) { font-weight: 750; color: var(--text); }

  /* B9.9 — handback rendered as a clear verdict, not raw text. */
  .hb-verdict { font-size: 13px; font-weight: 750; margin-bottom: 6px; }
  .hb-verdict.ok { color: var(--green); }
  .hb-verdict.no { color: var(--red); }
  .hb-verdict.partial { color: #c79a18; }
  .hb-summary { font-size: 12px; line-height: 1.5; color: var(--muted); }
</style>
