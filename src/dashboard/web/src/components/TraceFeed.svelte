<script>
  import { usd, dur } from '../lib/format.js'
  import { mdToHtml } from '../lib/md.js'
  import { compactionElapsedS, turnPreview } from '../lib/live.js'
  import Action, { actionTokens } from './Action.svelte'
  import JsonTree from './JsonTree.svelte'

  // turns: chronological feed of tagged entries — {kind:'turn', turn, boxes} and
  // {kind:'master', ...} (the TaskMaster card, interleaved at task boundaries
  // just above the first player turn of its task). The newest TURN is the
  // "current" turn and is expanded; the previous turn is also auto-opened. Older
  // turns collapse to a stacked header you click to open (mirrors the live
  // dashboard chat-scroll). Scrolls to the live turn.
  // hiddenTurns: count of older turns dropped below the live window (Spectate
  // keeps only the last few tasks live); shown as a muted note so the operator
  // knows the rail is windowed and the full trace lives in the run report.
  // nowMs: the parent's 1s client clock. Only a RUNNING compaction reads it —
  // it has no turn boundary to redraw its elapsed timer.
  let { turns = [], hiddenTurns = 0, nowMs = null } = $props()

  // handback + error are present in the real event stream (static/index.html)
  // but were omitted from the mock; wired in here for P6 parity.
  //
  // `settle` means EXACTLY ONE thing: the emulator wait after buttons were
  // pressed. Model requests and compactions used to be pushed as `settle` too,
  // so the defining behaviour of the append harness was labelled "Screen
  // settling" on every turn — hence `diag` (one model request's transport
  // diagnostics), `withheld` (billed reasoning the endpoint did not return),
  // `warning` (endpoint_warning) and `terminal` (the run's own stop condition).
  const boxName = { thinking: 'Thinking', output: 'Output', action: 'Action', tool: 'Tool', memory: 'Memory', ocr: 'OCR', settle: 'Screen settling', handback: 'Return to TaskMaster', retry: 'Retry', error: 'Error', diag: 'Request', withheld: 'Reasoning', warning: 'Endpoint warning', terminal: 'Run ended' }

  // TaskMaster's verdict on the PREVIOUS task → labeled chip + tone.
  const VERDICT = {
    succeeded: { label: 'Succeeded', tone: 'ok' },
    failed: { label: 'Failed', tone: 'no' },
    partial: { label: 'Partial', tone: 'partial' },
    other: { label: 'Other', tone: 'partial' },
  }
  function verdict(status) {
    return VERDICT[String(status || '').toLowerCase()] || { label: status || 'Rated', tone: 'partial' }
  }

  // turn ids (numbers), oldest→newest, ignoring master cards and compaction rows
  const turnIds = $derived(turns.filter((e) => e.kind === 'turn').map((e) => e.turn))
  const currentId = $derived(turnIds.length ? turnIds[turnIds.length - 1] : null)
  let open = $state(new Set())
  // A1: auto-open the last TWO turns (current + previous); guard when <2 exist.
  $effect(() => { open = new Set(turnIds.slice(-2)) })
  function toggle(id) { const n = new Set(open); n.has(id) ? n.delete(id) : n.add(id); open = n }

  // Compaction rows carry NO turn number, so they cannot live in `open` (which
  // is keyed by turn and rebuilt from turnIds). Their default is "open while it
  // is running, and for the newest one" — a compaction is the one thing you
  // actually want to watch happen — and a click flips that default. Keyed on
  // the block's stable id, so a toggle survives the next rebuild; `open` above
  // deliberately does not, and that behaviour is unchanged.
  const lastCompactionId = $derived(
    turns.filter((e) => e.kind === 'compaction').map((e) => e.id).at(-1) ?? null
  )
  let compToggled = $state(new Set())
  function compIsOpen(c) {
    const dflt = !c.complete || c.id === lastCompactionId
    return compToggled.has(c.id) ? !dflt : dflt
  }
  function toggleComp(id) {
    const n = new Set(compToggled)
    n.has(id) ? n.delete(id) : n.add(id)
    compToggled = n
  }
  // A compaction runs for minutes, so past a minute it reads as m/s (`dur`)
  // rather than "252.0s". The trailing ellipsis marks the one still going.
  function elapsedLabel(c) {
    const s = compactionElapsedS(c, nowMs)
    if (s == null) return ''
    const label = s < 60 ? `${s.toFixed(c.complete ? 1 : 0)}s` : dur(s)
    return c.complete ? label : `${label}…`
  }

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
    {#snippet boxList(boxes)}
  <!-- One event box. Shared by the gameplay-turn rows and the compaction
       block so a new kind is wired ONCE, not per row. -->
  {#each boxes as b}
    <div class="ebox {b.k}">
      <div class="ebox-h">{boxName[b.k] ?? b.k}{#if b.meta}<span class="ebox-meta faint">{b.meta}</span>{/if}</div>
      <div class="ebox-b">
        {#if b.k === 'action'}<span class="act">{#each actionTokens(b.t) as tok}<Action token={tok} />{/each}</span>
        {:else if b.k === 'tool'}{#if b.args}<div class="mono call">{b.name}({b.args})</div>{/if}{#if b.resp != null}<div class="resp faint">→ {b.resp}</div>{/if}
        {:else if b.k === 'memory'}<span class="mono">{b.t}</span>
        {:else if b.k === 'diag'}<span class="mono diag-b">{b.t}</span>
        {:else if b.k === 'withheld'}<span class="withheld-b">{b.t}</span>
        {:else if b.k === 'thinking'}<div class="md">{@html mdToHtml(b.t)}</div>
        {:else if b.k === 'handback'}<div class="hb-verdict {b.tone}">{b.verdict}</div>{#if b.summary}<div class="hb-summary">{b.summary}</div>{/if}
        {:else if b.k === 'output'}{#if b.ok != null}<div class="out-tag"><span class="ok-tag" class:ok={b.ok} class:no={!b.ok}>{b.ok ? '✓ ok' : '✗ failed'}</span></div>{/if}{#if b.t}<div class="out-body">{b.t}</div>{/if}
        {:else}{b.t}{/if}
      </div>
    </div>
  {/each}
{/snippet}

    {#each turns as entry (entry.id)}
      {#if entry.kind === 'compaction'}
        <!-- A compaction is NOT a turn: its own row, its own counter, no turn
             number — the same shape the run report gives it
             (trace_build._add_conversation_timeline → Report.svelte's
             `kind === 'compaction'` row). The gameplay turns either side keep
             their numbers, and the Turn stat does not move while this runs. -->
        {@const cOpen = compIsOpen(entry)}
        <div class="comp-block" class:running={!entry.complete} class:collapsed={!cOpen}>
          <button class="comp-head" onclick={() => toggleComp(entry.id)}>
            <span class="arr">{cOpen ? '▾' : '▸'}</span>
            <span class="c-n mono">Compaction {entry.number}</span>
            <span class="c-after faint">after turn {entry.afterTurn}{#if entry.reason}&nbsp;· {entry.reason.replace(/_/g, ' ')}{/if}</span>
            {#if !entry.complete}<span class="c-live"><span class="dot live"></span>compacting</span>{/if}
            <span class="c-timer faint mono">{elapsedLabel(entry)}</span>
          </button>
          {#if cOpen}
            <div class="boxes">
              {@render boxList(entry.boxes)}
              {#if entry.complete}
                <div class="ebox memory">
                  <div class="ebox-h">Handover</div>
                  <div class="ebox-b">{#if entry.summary}<div class="out-body">{entry.summary}</div>{:else}<span class="faint">(no continuation summary)</span>{/if}</div>
                </div>
                <div class="ebox memory">
                  <div class="ebox-h">Memory after compaction</div>
                  <div class="ebox-b">
                    {#if entry.memory && Object.keys(entry.memory).length}<JsonTree data={entry.memory} />{:else}<span class="faint">(empty)</span>{/if}
                    {#if entry.previousMemory}
                      <details class="mem-before"><summary>Memory before</summary><JsonTree data={entry.previousMemory} /></details>
                    {/if}
                  </div>
                </div>
              {:else}
                <div class="ebox settle">
                  <div class="ebox-h">Status</div>
                  <div class="ebox-b faint">Writing the handover — the conversation is being replaced. Gameplay resumes at turn {entry.beforeTurn}.</div>
                </div>
              {/if}
            </div>
          {/if}
        </div>
      {:else if entry.kind === 'master'}
        <div class="master-block">
          <div class="master-head">TaskMaster{#if entry.model}<span class="m-meta mono">{entry.model}</span>{/if}{#if entry.cost != null}<span class="m-meta mono">{usd(entry.cost)}</span>{/if}</div>
          <div class="master-body">
            {#if entry.rating}
              {@const verd = verdict(entry.rating.status)}
              <div class="m-row m-rating {verd.tone}">
                <span class="m-lab">Verdict on previous task</span>
                <span class="m-verdict">{verd.label}</span>
                {#if entry.rating.reasoning}<div class="m-desc">{entry.rating.reasoning}</div>{/if}
              </div>
            {/if}
            {#if entry.title}<div class="m-row"><span class="m-lab">Task</span>{entry.title}</div>{/if}
            {#if entry.description}<div class="m-row"><span class="m-lab">Plan</span><div class="m-desc">{entry.description}</div></div>{/if}
            {#if entry.success}<div class="m-row"><span class="m-lab">Success criteria</span><span class="mono">{entry.success}</span></div>{/if}
          </div>
        </div>
      {:else}
        {@const turn = entry}
        {@const isOpen = open.has(turn.turn)}
        {@const isCurrent = turn.turn === currentId}
        {@const nRetry = turn.boxes.filter((b) => b.k === 'retry').length}
        <div class="turn-block" class:current={isCurrent} class:collapsed={!isOpen} class:retried={nRetry > 0}>
          <button class="turn-head" onclick={() => toggle(turn.turn)}>
            <span class="arr">{isOpen ? '▾' : '▸'}</span>
            <span class="t-n mono">Turn {turn.turn}</span>
            {#if nRetry}<span class="retry-tag">{nRetry} {nRetry === 1 ? 'retry' : 'retries'}</span>{/if}
            {#if isCurrent}<span class="cur-tag"><span class="dot live"></span>current</span>{/if}
            <!-- A collapsed row previewed `thinking` only, so a turn whose
                 endpoint returned no readable reasoning showed a bare "…"
                 (finding #22). turnPreview falls through to the decision's own
                 prose, then to the reason nothing was said. -->
            {#if !isOpen}<span class="t-sum faint">{turnPreview(turn.boxes)}</span>{/if}
          </button>
          {#if isOpen}
            <div class="boxes">{@render boxList(turn.boxes)}</div>
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
  .master-block { background: var(--surface); border: 1px solid var(--tm-rule); border-left: 3px solid var(--tm); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; flex: none; }
  .master-head { display: flex; align-items: center; gap: 8px; padding: 9px 13px; font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--tm); background: var(--tm-wash); border-bottom: 1px solid var(--tm-rule); }
  .m-meta { margin-left: 8px; font-size: 10.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted); }
  .m-meta:first-of-type { margin-left: auto; }
  .master-body { padding: 11px 14px; display: flex; flex-direction: column; gap: 10px; }
  .m-row { font-size: 13px; line-height: 1.55; }
  .m-lab { display: block; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--tm); margin-bottom: 3px; }
  .m-desc { white-space: pre-wrap; color: var(--muted); }
  /* Verdict on the previous task — shown ABOVE the new task. */
  .m-verdict { font-size: 13px; font-weight: 750; }
  .m-rating.ok .m-verdict { color: var(--green); }
  .m-rating.no .m-verdict { color: var(--red); }
  .m-rating.partial .m-verdict { color: var(--tm); }

  .turn-block { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; flex: none; }
  .turn-block.current { border-color: var(--accent-rule); }
  .turn-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: none; background: none; text-align: left; }
  .turn-block.collapsed .turn-head:hover { background: var(--surface-2); }
  .arr { color: var(--faint); font-size: 10px; flex: none; }
  .t-n { font-size: 12px; font-weight: 750; color: var(--accent); flex: none; }
  .cur-tag { font-size: 10px; font-weight: 700; color: var(--green); display: inline-flex; align-items: center; gap: 4px; }
  .retry-tag { font-size: 10px; font-weight: 800; color: var(--retry); background: var(--retry-wash); border: 1px solid var(--retry-rule); border-radius: var(--radius-sm); padding: 1px 7px; letter-spacing: .02em; }
  .turn-block.retried { border-left: 3px solid var(--retry); }
  .t-sum { font-size: 11.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .boxes { padding: 2px 11px 10px; display: flex; flex-direction: column; gap: 7px; }
  .ebox { border-left: 3px solid var(--border); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; background: var(--surface-2); padding: 6px 10px; }
  .ebox-h { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: var(--muted); display: flex; align-items: center; gap: 5px; margin-bottom: 2px; }
  .ebox-meta { margin-left: auto; font-size: 9.5px; text-transform: none; letter-spacing: 0; }
  .ebox-b { font-size: 12px; line-height: 1.45; }
  .ebox.thinking { border-color: var(--ev-thinking); }
  .ebox.output   { border-color: var(--ev-output); }
  .ebox.action   { border-color: var(--red); }
  .ebox.tool     { border-color: var(--amber); }
  .ebox.memory   { border-color: var(--green); }
  .ebox.ocr      { border-color: var(--ev-ocr); }
  .ebox.settle   { border-color: var(--ev-settle); }
  .ebox.handback { border-color: var(--ev-handback); }
  .ebox.retry    { border-color: var(--retry); background: var(--retry-wash); }
  .ebox.retry .ebox-h { color: var(--retry); }
  .ebox.error    { border-color: var(--red); background: var(--red-soft); }
  /* One model request's transport diagnostics — informational, so it reads
     quieter than everything the model actually said. */
  .ebox.diag     { border-color: var(--border-2, var(--border)); }
  .diag-b { font-size: 11px; color: var(--muted); }
  /* Billed reasoning the endpoint refused to return. Muted on purpose: it is
     the ABSENCE of content, not content. */
  .ebox.withheld { border-color: var(--border-2, var(--border)); }
  .withheld-b { font-size: 11.5px; color: var(--faint); font-style: italic; }
  .ebox.warning  { border-color: var(--amber); background: var(--tm-wash); }
  .ebox.warning .ebox-h { color: var(--tm); }
  /* The run's own stop condition firing. Loud — it is the last thing that
     happens. */
  .ebox.terminal { border-color: var(--red); background: var(--red-soft); }
  .ebox.terminal .ebox-h { color: var(--red); }
  .ebox.terminal .ebox-b { font-weight: 650; }

  /* ── compaction block: a row of its own, with no turn number ──────────
     Green (the memory colour) rather than the accent turn colour, because what
     it produces IS the memory + handover. Visually a sibling of the turn
     cards, never nested in one. */
  .comp-block { background: var(--surface); border: 1px solid var(--green); border-left: 3px solid var(--green); border-radius: var(--radius); box-shadow: var(--shadow); overflow: hidden; flex: none; }
  .comp-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 9px 12px; border: none; background: none; text-align: left; }
  .comp-block.collapsed .comp-head:hover { background: var(--surface-2); }
  .c-n { font-size: 12px; font-weight: 750; color: var(--green); flex: none; }
  .c-after { font-size: 11px; }
  .c-live { font-size: 10px; font-weight: 700; color: var(--green); display: inline-flex; align-items: center; gap: 4px; }
  .c-timer { margin-left: auto; font-size: 11px; flex: none; }
  .comp-block.running { border-color: var(--green); }
  .mem-before { margin-top: 6px; font-size: 11px; color: var(--muted); }
  /* Action is 2.35em tall — considerably bigger than the emoji it replaced.
     Shrink the container's font-size (not the glyph's own height) so it reads
     at roughly the old emoji's footprint inside a compact trace row. */
  .act { display: inline-flex; align-items: center; gap: 3px; font-size: 7.5px; }
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
  .hb-verdict.partial { color: var(--tm); }
  .hb-summary { font-size: 12px; line-height: 1.5; color: var(--muted); }
</style>
