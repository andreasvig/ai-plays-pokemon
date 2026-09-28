<script>
  // Two handles on a turn axis (Andreas 2026-09-28: "a range with two dots I can
  // drag back and forth to see only that section"). It owns no data — it reports
  // a [from, to] and the map decides what that means.
  //
  // The gates are TICKS, not stops: Andreas chose free turns with the milestones
  // marked, so "turns 100-150" stays exact while "just the Viridian Forest leg"
  // is one drag. A handle within SNAP_PX of a tick lands on it; anywhere else it
  // lands where you let go. Snapping is applied in PIXELS rather than turns so
  // the pull feels the same on a 40-turn run and a 500-turn one.
  const SNAP_PX = 7

  import { normalizeRange } from '../lib/turnrange.js'

  let { min = 0, max = 100, value = null, ticks = [], onchange = () => {} } = $props()

  let track = $state(null)
  let dragging = $state(null)     // 'from' | 'to' while a handle is held
  // Which milestone the pointer is over. A tick is 2px of paint inside a wider
  // transparent hit area, and the card is suppressed while a handle is held —
  // dragging past the notches should not flash a card at each one.
  let hoverTick = $state(null)
  const span = $derived(Math.max(1, max - min))
  const from = $derived(value ? value[0] : min)
  const to = $derived(value ? value[1] : max)
  const whole = $derived(from <= min && to >= max)
  const pct = (t) => ((t - min) / span) * 100

  /** Pointer x → turn, snapped to a nearby gate. */
  function turnAt(clientX) {
    const r = track.getBoundingClientRect()
    if (!r.width) return min
    const raw = min + ((clientX - r.left) / r.width) * span
    const perPx = span / r.width
    let best = Math.round(Math.max(min, Math.min(max, raw)))
    let bestD = SNAP_PX * perPx
    for (const g of ticks) {
      if (g.turn == null || g.turn < min || g.turn > max) continue
      const d = Math.abs(g.turn - raw)
      if (d < bestD) { bestD = d; best = g.turn }
    }
    return best
  }

  // Clamped, never swapped: dragging the left handle past the right one parks it
  // against its neighbour instead of quietly turning into the other handle,
  // which is disorienting when you are watching the map and not the bar.
  function move(which, turn) {
    const next = which === 'from' ? [Math.min(turn, to), to] : [from, Math.max(turn, from)]
    // normalizeRange is what decides that the whole span is NOT a range, so the
    // reset button, a handle dragged to the end, and a hand-typed URL all agree.
    onchange(normalizeRange(next, min, max))
  }

  function grab(which, e) {
    dragging = which
    e.currentTarget.setPointerCapture?.(e.pointerId)
    e.preventDefault()
  }
  function onmove(e) {
    if (dragging && track) { hoverTick = null; move(dragging, turnAt(e.clientX)); return }
    // A move anywhere else on the page means the pointer is not on a tick.
    if (hoverTick && !e.target?.closest?.('.tick')) hoverTick = null
  }
  const release = () => (dragging = null)

  // The card follows the pointer's CURRENT target rather than a chain of
  // enter/leave pairs: one missed leave — the pointer jumping onto a handle,
  // out of the panel, or off the window between two frames — used to leave a
  // card hanging there. Every move over the rail either names a tick or clears
  // it, and leaving the rail clears it outright.
  function overTick(e) {
    if (dragging) return
    const el = e.target?.closest?.('.tick')
    hoverTick = el ? ticks.find((g) => String(g.turn) === el.dataset.turn) ?? null : null
  }

  /** A click on the rail moves whichever handle is nearer. */
  function ontrack(e) {
    if (dragging || !track) return
    const turn = turnAt(e.clientX)
    move(Math.abs(turn - from) <= Math.abs(turn - to) ? 'from' : 'to', turn)
  }

  function onkey(which, e) {
    const cur = which === 'from' ? from : to
    const step = e.shiftKey ? 10 : 1
    const go = { ArrowLeft: cur - step, ArrowRight: cur + step, ArrowDown: cur - step, ArrowUp: cur + step,
                 Home: min, End: max, PageDown: cur - 25, PageUp: cur + 25 }[e.key]
    if (go == null) return
    e.preventDefault()
    move(which, Math.max(min, Math.min(max, go)))
  }
</script>

<svelte:window onpointermove={onmove} onpointerup={release} onpointercancel={release} />

<div class="range">
  <div class="head">
    <span class="label">Turns</span>
    <span class="read mono">{from}–{to}</span>
    <span class="of faint">of {min}–{max}</span>
    {#if !whole}
      <button class="reset" onclick={() => onchange(null)}>whole run</button>
    {/if}
  </div>

  <div class="rail" bind:this={track} onpointerdown={ontrack} role="presentation"
    onpointermove={overTick} onpointerleave={() => (hoverTick = null)}>
    <div class="bar"></div>
    <div class="sel" style={`left:${pct(from)}%;right:${100 - pct(to)}%`}></div>
    {#each ticks as g (g.turn + g.name)}
      {#if g.turn != null && g.turn >= min && g.turn <= max}
        <!-- The hit area is the element; the notch is its ::after. A 2px target
             is not hoverable, and widening the paint would make the bar look
             like it has twelve fat stripes on it. Pointer events still reach
             the rail underneath, so clicking a milestone moves the nearer
             handle onto it. -->
        <span class="tick" class:in={g.turn >= from && g.turn <= to}
          style={`left:${pct(g.turn)}%`} data-turn={g.turn} role="presentation"></span>
      {/if}
    {/each}

    {#if hoverTick && !dragging}
      {@const x = pct(hoverTick.turn)}
      <div class="gatecard" class:left={x < 14} class:right={x > 86} style={`left:${x}%`}>
        <b>{hoverTick.name}</b>
        <span class="faint">
          {#if hoverTick.index != null && hoverTick.total != null}task {hoverTick.index} of {hoverTick.total} · {/if}
          reached on turn {hoverTick.turn}{#if hoverTick.legTurns != null}, {hoverTick.legTurns} turn{hoverTick.legTurns === 1 ? '' : 's'} on the leg{/if}
        </span>
      </div>
    {/if}
    {#each [['from', from], ['to', to]] as [which, turn] (which)}
      <button class="dot" class:held={dragging === which}
        style={`left:${pct(turn)}%`}
        role="slider" tabindex="0"
        aria-label={which === 'from' ? 'First turn shown' : 'Last turn shown'}
        aria-valuemin={min} aria-valuemax={max} aria-valuenow={turn}
        onpointerdown={(e) => grab(which, e)}
        onkeydown={(e) => onkey(which, e)}>
        <span class="bubble mono" class:show={dragging === which}>{turn}</span>
      </button>
    {/each}
  </div>
</div>

<style>
  .range { padding: 10px 4px 2px; }
  .head { display: flex; align-items: baseline; gap: 8px; font-size: 12px; margin-bottom: 10px; }
  .label { font-weight: 650; }
  .read { font-weight: 700; font-variant-numeric: tabular-nums; }
  .of { font-size: 11.5px; }
  .reset {
    margin-left: auto; border: 1px solid var(--border); background: var(--surface);
    border-radius: var(--radius-sm); padding: 2px 8px; font: inherit; font-size: 11.5px;
    color: var(--muted); cursor: pointer;
  }
  .reset:hover { color: var(--text); }
  .rail { position: relative; height: 22px; cursor: pointer; touch-action: none; }
  .bar {
    position: absolute; left: 0; right: 0; top: 9px; height: 4px;
    background: var(--wash); border-radius: 2px;
  }
  .sel { position: absolute; top: 9px; height: 4px; background: var(--accent, #3b6ef5); border-radius: 2px; }
  .tick {
    position: absolute; top: 0; width: 14px; height: 22px; margin-left: -7px;
    background: none; cursor: pointer;
  }
  .tick::after {
    content: ''; position: absolute; left: 6px; top: 4px; width: 2px; height: 14px;
    background: var(--border); border-radius: 1px;
  }
  .tick.in::after { background: var(--accent-ink, #23417f); opacity: .55; }
  .tick:hover::after { background: var(--text); opacity: 1; height: 18px; top: 2px; }
  .gatecard {
    position: absolute; bottom: 26px; transform: translateX(-50%); z-index: 5;
    display: grid; gap: 1px; white-space: nowrap; pointer-events: none;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
    box-shadow: var(--shadow); padding: 5px 9px; font-size: 11.5px; line-height: 1.35;
  }
  .gatecard b { font-size: 12px; font-weight: 700; }
  /* A milestone near either end would hang off the panel; anchor it instead. */
  .gatecard.left { transform: none; }
  .gatecard.right { transform: translateX(-100%); }
  .dot {
    position: absolute; top: 2px; width: 18px; height: 18px; margin-left: -9px; padding: 0;
    border-radius: 50%; border: 2px solid var(--accent, #3b6ef5); background: var(--surface);
    box-shadow: var(--shadow); cursor: grab; touch-action: none;
  }
  .dot.held { cursor: grabbing; transform: scale(1.12); }
  .dot:focus-visible { outline: 2px solid var(--accent, #3b6ef5); outline-offset: 2px; }
  .bubble {
    position: absolute; bottom: 22px; left: 50%; transform: translateX(-50%);
    background: var(--text); color: var(--surface); border-radius: 5px;
    padding: 1px 5px; font-size: 11px; font-variant-numeric: tabular-nums;
    opacity: 0; transition: opacity .1s; pointer-events: none; white-space: nowrap;
  }
  .dot:hover .bubble, .dot:focus-visible .bubble, .bubble.show { opacity: 1; }

  @media (max-width: 720px) {
    .dot { width: 22px; height: 22px; margin-left: -11px; top: 0; }
    .rail { height: 24px; }
  }
</style>
