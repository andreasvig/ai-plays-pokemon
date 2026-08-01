<script>
  // SIMPLE VIEW — the recording-optimised presentation of a live run (build
  // plan docs/simple-view-plan.md, track B). The full Spectate view is an
  // instrument panel; this is the opposite: a 1:1 paper frame holding the game
  // screen and one box, meant to be screen-recorded and posted unedited.
  //
  // Ported from docs/simple-view-mock.html, a working reference tuned by eye
  // over ~6 iterations. The timings, geometry, palette and SVG paths are NOT
  // re-derived here — they come from the mock verbatim.
  //
  // Presentation only: no new backend events, no new API fields. Everything it
  // needs is already on the wire.
  import { onDestroy, tick, untrack } from 'svelte'
  import Action, { actionTokens } from './Action.svelte'
  import { splitParas, paraWords, streamStep, streamSlice } from '../lib/prose.js'

  let {
    // Current screen image src (blob URL from Spectate's screen socket).
    frame = null,
    // { seq, data } — the parent bumps `seq` on EVERY event; `data` is the raw
    // event object. We key off `seq` rather than object identity so a repeated
    // event (WS backlog replay) still lands.
    //
    // CONTRACT NOTE for the parent: write this ONCE PER TASK. Svelte batches
    // state, so two assignments inside one synchronous block flush as one and
    // the effect only ever sees the second — the first event is silently
    // dropped. One WS `onmessage` per assignment satisfies this naturally;
    // a replay loop that pushes a backlog in a for-loop does not.
    lastEvent = null,
    // { turn, presses, reasoning } — most recent known turn at mount, so
    // toggling on at turn 40 doesn't show an empty box (plan §8). Rendered
    // immediately with no animation; the phase machine takes over at the next
    // event.
    seed = null,
    onexit = () => {},
  } = $props()

  // ── timings, from the mock (plan §5.2). All tuned by eye with Andreas. ────
  const REDUCED =
    typeof matchMedia !== 'undefined' &&
    matchMedia('(prefers-reduced-motion: reduce)').matches
  // Reduced motion collapses the durations rather than branching the logic, so
  // there is exactly one code path to reason about.
  const T = REDUCED
    ? { morph: 1, glyph: 0, pressPad: 1, textMs: 1, dot: 340 }
    : { morph: 520, glyph: 85, pressPad: 120, textMs: 850, dot: 340 }
  const TICK = 16

  // Auto-fit bounds are FRACTIONS OF FRAME HEIGHT, never absolute px, so the
  // design holds identically at 1000px and at 4K (plan §6).
  const MIN_R = 0.0055
  const MAX_R = 0.019

  // ── state ────────────────────────────────────────────────────────────────
  // phase drives data-phase. `presses` and `text` are sub-states of the
  // llm_output promotion; `executing` covers the button presses + screen
  // settling; `idle` is "settled, nothing pending". The pending box is visible
  // in `thinking` only — that is Andreas's hard requirement.
  let phase = $state('idle')
  let pendingTurn = $state(null)
  let pendingVisible = $state(false)
  let dots = $state('')

  let card = $state(null) // {id, turn, tokens, animate}
  let outgoing = $state(false) // retiring slot phases up while the morph lands
  let morph = $state(null) // {turn, dots, top, left, w, h, landed}

  let fullParas = $state([])
  let visibleParas = $state([])
  let measuring = $state(false) // render the FULL text for one microtask, to fit
  let caretOn = $state(false)

  let fitPx = $state(null)
  let clipped = $state(false)
  let chrome = $state(false) // exit affordance visible (mouse moved recently)

  let ratchetPx = null // plain let: fitPx is the reactive mirror
  let slotSeq = 0
  let lastSeq = -1
  let dotTimer = null
  let chromeTimer = null
  let dead = false

  let stageEl, boxEl, pendingEl
  // These two live inside {#if}/{#key} blocks, so their bindings are torn down
  // and rebuilt per turn — they must be reactive or Svelte warns (and a stale
  // reference would make the fit measure a dead element).
  let morphEl = $state(null)
  let sayEl = $state(null)

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  // One frame, but never longer than 32ms. A bare rAF is not safe to await in
  // a serialised phase chain: in a headless or backgrounded tab, and under
  // Chrome's virtual-time clock, it can be seconds late, and every event
  // behind it in
  // the queue inherits that lag — measured at 3.5s per morph, which put the
  // component a full turn behind the event stream. The transition itself does
  // not depend on the frame: the getBoundingClientRect() before this call
  // already flushed style + layout, so the "from" geometry is recorded either
  // way.
  const raf = () =>
    new Promise((r) => {
      let fired = false
      const go = () => {
        if (fired) return
        fired = true
        r()
      }
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(go)
      setTimeout(go, 32)
    })

  function rectOf(el) {
    const a = el.getBoundingClientRect()
    const b = stageEl.getBoundingClientRect()
    return { top: a.top - b.top, left: a.left - b.left, w: a.width, h: a.height }
  }

  // ── auto-fit + ratchet (plan §3, §6) ─────────────────────────────────────
  // Binary search for the largest font-size at which the FULL reasoning fits.
  // Then ratchet: a turn that needs smaller sets the size for the rest of the
  // run and it never grows back, so the type doesn't "breathe" 1.8x during
  // playback. Resets on resize (the ceiling is frame-relative, so a resized
  // window invalidates the ratchet) and on a reseed.
  function fitSay() {
    const el = sayEl
    if (!el) return 0
    const H = stageEl?.clientHeight || 1000
    const MIN = H * MIN_R
    const MAX = H * MAX_R
    let lo = MIN
    let hi = MAX
    let best = MIN
    el.style.fontSize = hi + 'px'
    if (el.scrollHeight <= el.clientHeight) best = hi
    else
      while (hi - lo > H * 0.00025) {
        const mid = (lo + hi) / 2
        el.style.fontSize = mid + 'px'
        if (el.scrollHeight <= el.clientHeight) {
          best = mid
          lo = mid
        } else hi = mid
      }
    ratchetPx = Math.min(ratchetPx ?? best, best)
    el.style.fontSize = ratchetPx + 'px'
    // Never truncate. If even the floor overflows, SURFACE it — a silent clip
    // violates a locked decision and would be invisible in review.
    clipped = el.scrollHeight > el.clientHeight + 1
    fitPx = ratchetPx
    return ratchetPx
  }

  // Re-measure against the full body even mid-stream: flip to `measuring` for
  // one microtask so the fit never sees a half-streamed paragraph and hands
  // back a too-generous size. tick() resolves before paint, so nothing flashes.
  async function refit() {
    if (!sayEl || !fullParas.length) return
    measuring = true
    await tick()
    fitSay()
    measuring = false
    await tick()
    if (sayEl && fitPx != null) sayEl.style.fontSize = fitPx + 'px'
  }

  // ── the dots cycle: none -> . -> .. -> ... -> none ───────────────────────
  function startDots() {
    let n = 0
    stopDots()
    dots = ''
    dotTimer = setInterval(() => {
      n = (n + 1) % 4
      dots = '.'.repeat(n)
    }, T.dot)
  }
  function stopDots() {
    if (dotTimer) clearInterval(dotTimer)
    dotTimer = null
  }

  // ── the morph (plan §5.3) ────────────────────────────────────────────────
  // The pending box does not slide — it BECOMES the main box. A clone is
  // pinned at the pending box's exact rect, the real pending box vacates, the
  // retiring turn phases up, and the clone's top/height animate onto the main
  // box while its border goes dashed->solid. Seamlessness depends on .pending,
  // .morph and .slot sharing identical padding (1em 1.3em) and top alignment:
  // if the "TURN n" label jumps mid-handoff, that is the cause.
  async function morphIn(turn) {
    if (!stageEl || !pendingEl || !boxEl) return
    const from = rectOf(pendingEl)
    const to = rectOf(boxEl)
    morph = { turn, dots, top: from.top, left: from.left, w: from.w, h: from.h, landed: false }
    stopDots()
    pendingVisible = false
    outgoing = true
    await tick()
    if (morphEl) morphEl.getBoundingClientRect() // force layout before animating
    await raf()
    if (dead) return
    morph = { ...morph, landed: true, top: to.top, h: to.h }
    await sleep(T.morph)
  }

  // ── streaming (plan §5.4) ────────────────────────────────────────────────
  async function streamText(words) {
    const total = words.reduce((a, w) => a + w.length, 0)
    const step = streamStep(total, T.textMs, TICK)
    let pi = 0
    let wi = 0
    caretOn = true
    while (pi < words.length) {
      wi = Math.min(words[pi].length, wi + step)
      visibleParas = streamSlice(words, pi, wi)
      await sleep(TICK)
      if (dead) return
      if (wi >= words[pi].length) {
        pi++
        wi = 0
      }
    }
    caretOn = false
    visibleParas = fullParas
  }

  // ── the phase machine (plan §5) ──────────────────────────────────────────
  // Events are handled strictly in order through one promise chain. A promote
  // takes ~2s of wall clock; serialising means a turn_start that lands while
  // one is still streaming can never interleave two turns into the same box.
  let chain = Promise.resolve()
  function enqueue(fn) {
    chain = chain.then(fn).catch((e) => console.error('[SimpleView]', e))
    return chain
  }

  function handleEvent(data) {
    if (!data || dead) return
    const type = data.type
    if (type === 'turn_start') {
      enqueue(() => beginThinking(data.turn))
    } else if (type === 'llm_output') {
      const args = parseArgs(data.args)
      if (!args) return
      enqueue(() => promote(data.turn, args.inputs, args.reasoning))
    } else if (type === 'screen_settled') {
      // The executing window is over. The pending box STAYS hidden: no next
      // turn exists yet, and showing it here is exactly the thing Andreas
      // asked to never happen.
      enqueue(() => {
        phase = 'idle'
      })
    }
    // `button_sequence` is deliberately NOT handled. Its name lies: turn.py
    // logs it AFTER press_button_list() returns, so it marks the END of
    // pressing, not the start. Keying execution on it would put the pending
    // box on screen during the exact window it must be gone.
  }

  function parseArgs(a) {
    if (typeof a === 'string') {
      try {
        return JSON.parse(a)
      } catch {
        return null
      }
    }
    return a && typeof a === 'object' ? a : null
  }

  async function beginThinking(turn) {
    if (dead) return
    phase = 'thinking'
    pendingTurn = turn ?? null
    pendingVisible = true
    startDots()
  }

  async function promote(turn, inputs, reasoning) {
    if (dead) return
    const tokens = actionTokens(inputs)
    const paras = splitParas(reasoning)

    if (card) await morphIn(turn)
    else {
      // First turn of the session: there is no box to morph into, so the
      // pending strip has nothing to become — but it must still vacate. Without
      // this, turn 1 renders "TURN 1" twice, once in the box and once in the
      // pending strip below it, for the whole presses + text window.
      stopDots()
      pendingVisible = false
    }
    if (dead) return

    // Hand off in ONE update: the real box takes the turn and the clone goes,
    // at identical geometry, so there is no flicker between them.
    card = { id: ++slotSeq, turn, tokens, animate: true }
    morph = null
    outgoing = false
    fullParas = paras
    visibleParas = []
    caretOn = false

    // Fit on the FULL body before a single word streams. Fitting progressively
    // would shrink the type as words arrive and read as a layout bug. The
    // glyphs are already in the DOM at this point so a press row that wraps to
    // two lines is accounted for in the height the text has to live in.
    measuring = true
    await tick()
    fitSay()
    measuring = false

    phase = 'presses'
    await tick()
    // CSS does the stagger via animation-delay on each glyph; no per-glyph
    // timer. We only wait out the total.
    await sleep(tokens.length * T.glyph + T.pressPad)
    if (dead) return

    phase = 'text'
    await streamText(paraWords(paras))
    if (dead) return

    // EXECUTING: the buttons are being pressed and the screen is settling.
    phase = 'executing'
    pendingVisible = false
  }

  // ── entering mid-run (plan §8) ───────────────────────────────────────────
  function applySeed(s) {
    ratchetPx = null
    card = { id: ++slotSeq, turn: s.turn, tokens: actionTokens(s.presses), animate: false }
    morph = null
    outgoing = false
    pendingVisible = false
    phase = 'idle'
    fullParas = splitParas(s.reasoning)
    visibleParas = fullParas
    caretOn = false
    tick().then(() => {
      if (!dead) fitSay()
    })
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  $effect(() => {
    const ev = lastEvent
    if (!ev || typeof ev.seq !== 'number' || ev.seq === lastSeq) return
    lastSeq = ev.seq
    untrack(() => handleEvent(ev.data))
  })

  $effect(() => {
    const s = seed
    untrack(() => {
      if (s) applySeed(s)
      // A null seed means the parent has no known turn — which is what a run
      // change looks like from here (resetLiveState clears it). Plan §3 requires
      // the ratchet to reset per run, and this component has no activeRunId of
      // its own, so this transition is the only signal for it. Without this the
      // ceiling set by run A's worst turn silently caps run B for its whole life.
      else clearForNewRun()
    })
  })

  // Back to the state a fresh mount would have, minus the DOM teardown.
  function clearForNewRun() {
    ratchetPx = null
    fitPx = null
    card = null
    morph = null
    outgoing = false
    pendingVisible = false
    pendingTurn = null
    phase = 'idle'
    fullParas = []
    visibleParas = []
    caretOn = false
    clipped = false
  }

  // The box height is a percentage of the frame, so any stage resize
  // invalidates the fit AND the ratchet ceiling. A ResizeObserver catches the
  // cases a window `resize` event does not (a parent re-layout, a --boxh
  // override), which is why the mock keeps both.
  $effect(() => {
    if (!stageEl || typeof ResizeObserver === 'undefined') return
    let first = true
    const ro = new ResizeObserver(() => {
      if (first) {
        first = false
        return
      }
      ratchetPx = null
      refit()
    })
    ro.observe(stageEl)
    return () => ro.disconnect()
  })

  $effect(() => {
    const onResize = () => {
      ratchetPx = null
      refit()
      // The morph clone's geometry is pinned in px at the moment it is
      // created, so a resize inside its 520ms window would leave it the old
      // width, sitting narrower than the box it is supposed to be becoming.
      // Re-pin it against the live rects instead. (Headless --screenshot
      // resizes the viewport at capture time, which is how this surfaced.)
      if (morph && stageEl && boxEl && pendingEl) {
        const to = rectOf(boxEl)
        const from = rectOf(pendingEl)
        morph = morph.landed
          ? { ...morph, left: to.left, w: to.w, top: to.top, h: to.h }
          : { ...morph, left: from.left, w: from.w, top: from.top, h: from.h }
      }
    }
    // Chrome: the exit ✕ (and nothing else) appears while the mouse moves and
    // is gone 2s later, so the recording frame stays clean.
    const poke = () => {
      chrome = true
      clearTimeout(chromeTimer)
      chromeTimer = setTimeout(() => {
        chrome = false
      }, 2000)
    }
    addEventListener('resize', onResize)
    addEventListener('mousemove', poke)
    poke()
    return () => {
      removeEventListener('resize', onResize)
      removeEventListener('mousemove', poke)
      clearTimeout(chromeTimer)
    }
  })

  // A loaded screenshot changes what the shot row asks for, so re-fit once the
  // image is actually decoded rather than trusting the pre-load layout.
  function onShot() {
    refit()
  }

  onDestroy(() => {
    dead = true
    stopDots()
    clearTimeout(chromeTimer)
  })

  const fitAttr = $derived(fitPx == null ? '' : fitPx.toFixed(2))
  const shown = $derived(measuring ? fullParas : visibleParas)
</script>

<div class="stagewrap">
  <!-- data-* are the verification surface (plan §9): headless Chrome reads
       these instead of eyeballing screenshots. -->
  <div
    class="stage"
    bind:this={stageEl}
    data-phase={phase}
    data-fit={fitAttr}
    data-clipped={String(clipped)}
    data-turn={card ? String(card.turn) : ''}
    data-pending={pendingVisible ? 'visible' : 'hidden'}
  >
    <button
      class="exit"
      class:show={chrome}
      title="Exit simple view"
      aria-label="Exit simple view"
      onclick={() => onexit()}>✕</button
    >

    <div class="shotwrap">
      {#if frame}
        <img class="screen" src={frame} alt="" onload={onShot} />
      {/if}
    </div>

    <div class="box" bind:this={boxEl}>
      {#if card}
        {#key card.id}
          <div class="slot" class:out={outgoing}>
            <div class="turn">Turn <b>{card.turn}</b></div>
            <div class="acts">
              {#each card.tokens as tok, n (n)}
                <Action token={tok} delay={card.animate ? n * T.glyph : null} />
              {/each}
            </div>
            <div class="say" bind:this={sayEl}>
              {#each shown as p, i (i)}
                <p>{p}{#if caretOn && i === shown.length - 1}<span class="caret"></span>{/if}</p>
              {/each}
            </div>
          </div>
        {/key}
      {/if}
    </div>

    <!-- Kept in the DOM at opacity 0 rather than removed: it holds its slot in
         the flex column, so the main box never jumps when the model starts
         thinking, and the morph can read its rect. -->
    <div class="pending" class:idle={!pendingVisible} bind:this={pendingEl} aria-hidden={!pendingVisible}>
      Turn <b>{pendingTurn ?? '—'}</b> <span class="dots">{dots}</span>
    </div>

    {#if morph}
      <div
        class="morph"
        class:landed={morph.landed}
        bind:this={morphEl}
        style="top:{morph.top}px;left:{morph.left}px;width:{morph.w}px;height:{morph.h}px"
      >
        Turn <b>{morph.turn}</b> <span class="dots">{morph.dots}</span>
      </div>
    {/if}
  </div>
</div>

<style>
  /* Palette locked in plan §2 — paper, ink on warm white. Monospace
     throughout, no emoji anywhere (the glyphs are real GBA controls). */
  .stagewrap {
    --mono: ui-monospace, 'SF Mono', SFMono-Regular, 'JetBrains Mono', Menlo, monospace;
    --paper: #f2efe9;
    --card: #fbf9f5;
    --rule: #ddd7cc;
    --ink: #1f1c17;
    --body: #3b362e;
    --faint: #9a9184;
    --morph: 0.52s cubic-bezier(0.32, 0.72, 0, 1);
    position: fixed;
    inset: 0;
    z-index: 40;
    background: #0a0b0c;
    display: grid;
    place-items: center;
    overflow: hidden;
    font-family: var(--mono);
    color: var(--ink);
  }
  .stagewrap :global(*) {
    box-sizing: border-box;
  }

  /* recordable stage: 1:1, letterboxed, never scrolls */
  .stage {
    width: min(100vw, 100vh);
    height: min(100vw, 100vh);
    aspect-ratio: 1 / 1;
    background: var(--paper);
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1.5%;
    padding: 3.4%;
    overflow: hidden;
    --boxh: 24%;
    --striph: 7%;
  }

  /* exit: top-left, only while the mouse moves. Never in the recording. */
  .exit {
    position: absolute;
    top: 1.6%;
    left: 1.6%;
    z-index: 30;
    width: 2.1em;
    height: 2.1em;
    font-size: clamp(11px, 1.5vh, 19px);
    display: grid;
    place-items: center;
    border-radius: 50%;
    cursor: pointer;
    padding: 0;
    background: rgba(251, 249, 245, 0.78);
    border: 1px solid var(--rule);
    color: var(--ink);
    font-family: var(--mono);
    line-height: 1;
    opacity: 0;
    transition: opacity 0.22s;
    backdrop-filter: blur(3px);
  }
  .exit.show {
    opacity: 1;
  }
  .exit:hover {
    background: var(--card);
  }

  .shotwrap {
    flex: 1;
    min-height: 0;
    width: 100%;
    display: grid;
    place-items: center;
  }
  .screen {
    display: block;
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    image-rendering: pixelated;
    border: 1px solid var(--rule);
    border-radius: 2px;
  }

  /* ── main box ─────────────────────────────────────────────────────────── */
  .box {
    width: 100%;
    height: var(--boxh);
    flex: none;
    background: var(--card);
    border: 1px solid var(--rule);
    border-radius: 3px;
    position: relative;
    overflow: hidden;
    font-size: clamp(10px, 1.5vh, 19px);
  }
  .slot {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    gap: 0.5em;
    padding: 1em 1.3em;
  }
  /* the retired turn phases UP and out while the pending box morphs in */
  .slot.out {
    transition:
      transform var(--morph),
      opacity 0.34s ease-in;
    transform: translateY(-62%);
    opacity: 0;
  }

  .turn {
    font-size: 0.86em;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    color: var(--faint);
    flex: none;
    white-space: nowrap;
  }
  .turn b {
    color: var(--ink);
    font-weight: 700;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
  }

  .acts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.3em;
    color: var(--ink);
    flex: none;
    min-height: 2.35em;
  }

  /* NB: no `white-space: pre-wrap` here, ever. See lib/prose.js — the model's
     ~18 hard newlines made 946 chars demand 195px where 129px existed. */
  .say {
    flex: 1;
    min-height: 0;
    color: var(--body);
    line-height: 1.45;
    overflow: hidden;
  }
  .say p {
    margin: 0;
  }
  .say p + p {
    margin-top: 0.55em;
  }
  .caret {
    display: inline-block;
    width: 0.5em;
    height: 0.95em;
    vertical-align: -0.12em;
    background: var(--ink);
    opacity: 0.45;
    margin-left: 0.06em;
    animation: blink 0.85s steps(1) infinite;
  }
  @keyframes blink {
    50% {
      opacity: 0;
    }
  }

  /* ── pending box: on screen ONLY while the model is thinking. Same padding
        and top alignment as .slot, so growing into the main box is seamless.
        The label is sized × 0.86 to match `.turn` inside the box: plan §5.3
        requires "TURN n" not to shift during the handoff, and at a plain 1em it
        rendered 14% larger here and snapped smaller the instant the clone
        landed. Harmonised toward the settled size — that is what is on screen
        almost all the time. `.morph` must carry the identical value. */
  .pending {
    width: 100%;
    height: var(--striph);
    flex: none;
    border: 1px dashed var(--rule);
    border-radius: 3px;
    background: transparent;
    display: flex;
    align-items: flex-start;
    gap: 0.75em;
    padding: 1em 1.3em;
    /* × 0.86 to match `.turn` inside the box — see the note on .pending. */
    font-size: calc(clamp(10px, 1.5vh, 19px) * 0.86);
    color: var(--faint);
    letter-spacing: 0.13em;
    text-transform: uppercase;
    overflow: hidden;
    transition: opacity 0.3s;
  }
  .pending b {
    color: var(--ink);
    font-weight: 700;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
  }
  .pending.idle {
    opacity: 0;
  }
  .dots {
    color: var(--ink);
    opacity: 0.5;
    letter-spacing: 0.16em;
    min-width: 2.4em;
    line-height: 1.05;
  }

  /* the element that actually travels: a clone of .pending animated onto the
     main box's geometry. Removed the instant it lands. */
  .morph {
    position: absolute;
    z-index: 10;
    border: 1px dashed var(--rule);
    border-radius: 3px;
    background: transparent;
    overflow: hidden;
    display: flex;
    align-items: flex-start;
    gap: 0.75em;
    padding: 1em 1.3em;
    /* × 0.86 to match `.turn` inside the box — see the note on .pending. */
    font-size: calc(clamp(10px, 1.5vh, 19px) * 0.86);
    color: var(--faint);
    letter-spacing: 0.13em;
    text-transform: uppercase;
    transition:
      top var(--morph),
      height var(--morph),
      background-color var(--morph),
      border-color var(--morph);
  }
  /* Mirrors `.pending b`. Without it the <b> inherits .morph's --faint and the
     turn NUMBER snaps faint → ink the frame the clone lands, which reads as a
     flash on exactly the element plan §5.3 says must stay put. */
  .morph b {
    color: var(--ink);
    font-weight: 700;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
  }
  .morph.landed {
    background: var(--card);
    border-style: solid;
  }

  @media (prefers-reduced-motion: reduce) {
    .stagewrap {
      --morph: 1ms;
    }
    .slot.out {
      transition-duration: 1ms;
    }
    .caret {
      animation: none;
    }
    .exit {
      transition-duration: 1ms;
    }
    .pending {
      transition-duration: 1ms;
    }
  }
</style>
