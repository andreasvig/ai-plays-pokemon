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
  import { usd, dur } from '../lib/format.js'
  import { SHOW_NONE } from '../lib/record.js'

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
    // ── header overlay (2026-09-08, reverses plan §10's "no cost readouts":
    // still off by default, so the bare frame is unchanged unless asked for) ──
    // Run facts for the strip above the game screen. `show` says which of them
    // print: {model, elapsed, cost} booleans. The parent owns persistence
    // (localStorage on a human's tab, the record spec's `show=` URL param on a
    // recorder page) and receives edits through `onshow`.
    model = null,
    elapsedS = 0,
    cost = null,
    show = SHOW_NONE,
    onshow = () => {},
    // A recorder page: no ✕, no gear, no settings — its frame is fixed for the
    // life of the file and there is no mouse to reveal them anyway.
    locked = false,
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

  // Compaction (append harness): between `compaction_start` and
  // `compaction_complete` the model is writing its own handover and the game
  // screen is frozen — multiple MINUTES on a long segment. Without a phase of
  // its own that window is indistinguishable from a slow think: the same dot
  // cycle on the same "TURN n" strip, with nothing moving (finding #14). It
  // reuses the pending strip rather than adding a box, so the layout does not
  // shift; only the label and the clock change.
  let compacting = $state(false)
  let compactAfter = $state(null)
  let compactS = $state(0)
  let compactTimer = null

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
  let settingsOpen = $state(false) // the gear's panel; keeps the chrome shown

  let ratchetPx = null // plain let: fitPx is the reactive mirror
  let slotSeq = 0
  let lastSeq = -1
  let dotTimer = null
  let chromeTimer = null
  let dead = false

  let stageEl, boxEl, pendingEl
  let shotEl = $state(null) // inside an {#if}, so reactive like morphEl/sayEl
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

  // Wall clock for the compaction strip. Ticks twice a second so a whole-second
  // label never sits one second behind what the viewer feels.
  function startCompactClock() {
    stopCompactClock()
    const t0 = Date.now()
    compactS = 0
    compactTimer = setInterval(() => {
      compactS = Math.round((Date.now() - t0) / 1000)
    }, 500)
  }
  function stopCompactClock() {
    if (compactTimer) clearInterval(compactTimer)
    compactTimer = null
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
    } else if (type === 'compaction_start') {
      enqueue(() => beginCompaction(data.after_turn, data.turn))
    } else if (type === 'compaction_complete') {
      // Back to thinking about the turn the compaction interrupted — the
      // gameplay request for `data.turn` is issued the moment it returns.
      enqueue(() => endCompaction(data.turn))
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

  async function beginCompaction(afterTurn, turn) {
    if (dead) return
    compacting = true
    compactAfter = afterTurn ?? (typeof turn === 'number' ? turn - 1 : null)
    phase = 'compacting'
    pendingVisible = true
    startDots()
    startCompactClock()
  }

  async function endCompaction(turn) {
    if (dead) return
    stopCompactClock()
    compacting = false
    compactAfter = null
    await beginThinking(turn)
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
    stopCompactClock()
    compacting = false
    compactAfter = null
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
      measureShot()
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
    // Chrome: the exit ✕ and the gear appear while the mouse moves and are
    // gone 2s later, so the recording frame stays clean. An open settings
    // panel pins them: hiding the panel under the reader's cursor would make
    // the toggles unclickable.
    const poke = () => {
      chrome = true
      clearTimeout(chromeTimer)
      chromeTimer = setTimeout(() => {
        if (!settingsOpen) chrome = false
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
    measureShot()
  }

  // The header card is as wide as the PICTURE, not the image element. The
  // element is 100% wide and `object-fit: contain` letterboxes the frame inside
  // it, so the visible edges sit inside the element's by a margin that depends
  // on how much height the shot area has AND on the frame's own aspect (mGBA
  // pads its window capture with black bars that vary with its window size).
  // No CSS width can follow that; measure the rendered picture instead and
  // hand it to the card, the turn box and the pending strip as --shotw. Written
  // only on a real change so a steady stream of same-size frames does not churn
  // style. The morph clone reads the live rects, so it follows for free; the
  // text fit is re-run because a narrower box holds fewer characters per line.
  let shotW = null
  function measureShot() {
    const el = shotEl
    if (!el || !stageEl || !el.naturalWidth || !el.naturalHeight) return
    const r = el.getBoundingClientRect()
    if (!r.width || !r.height) return
    const scale = Math.min(r.width / el.naturalWidth, r.height / el.naturalHeight)
    const w = Math.round(el.naturalWidth * scale)
    if (shotW != null && Math.abs(w - shotW) <= 1) return
    shotW = w
    stageEl.style.setProperty('--shotw', w + 'px')
    ratchetPx = null
    refit()
  }

  onDestroy(() => {
    dead = true
    stopDots()
    stopCompactClock()
    clearTimeout(chromeTimer)
  })

  const fitAttr = $derived(fitPx == null ? '' : fitPx.toFixed(2))
  const shown = $derived(measuring ? fullParas : visibleParas)

  // Which overlay items print, in strip order. Empty → no strip at all, so a
  // run with everything off renders the exact frame the view shipped with.
  const shownMeta = $derived(['model', 'elapsed', 'cost'].filter((k) => show?.[k]))
  const SHOW_LABELS = { model: 'Model name', elapsed: 'Total time', cost: 'Total cost' }

  function toggleShow(key) {
    onshow({ ...SHOW_NONE, ...show, [key]: !show?.[key] })
  }
  function closeSettings() {
    settingsOpen = false
    // Start the hide timer again: the panel had been pinning the chrome.
    clearTimeout(chromeTimer)
    chromeTimer = setTimeout(() => {
      chrome = false
    }, 2000)
  }
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
    data-show={shownMeta.join(',')}
    data-settings={settingsOpen ? 'open' : 'closed'}
  >
    {#if !locked}
      <button
        class="exit"
        class:show={chrome}
        title="Exit simple view"
        aria-label="Exit simple view"
        onclick={() => onexit()}>✕</button
      >
      <!-- Same reveal rule as the ✕: only while the mouse moves. -->
      <button
        class="gear"
        class:show={chrome}
        class:active={settingsOpen}
        title="Simple view settings"
        aria-label="Simple view settings"
        aria-expanded={settingsOpen}
        onclick={() => (settingsOpen ? closeSettings() : (settingsOpen = true))}
      >
        <svg viewBox="0 0 24 24" width="1em" height="1em" aria-hidden="true">
          <path
            d="M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7Zm8.6 2.2-1.9-.4a6.9 6.9 0 0 0-.7-1.7l1.1-1.6a.8.8 0 0 0-.1-1l-1-1a.8.8 0 0 0-1-.1l-1.6 1.1a6.9 6.9 0 0 0-1.7-.7l-.4-1.9a.8.8 0 0 0-.8-.6h-1.4a.8.8 0 0 0-.8.6l-.4 1.9a6.9 6.9 0 0 0-1.7.7L6.6 4.9a.8.8 0 0 0-1 .1l-1 1a.8.8 0 0 0-.1 1l1.1 1.6a6.9 6.9 0 0 0-.7 1.7l-1.9.4a.8.8 0 0 0-.6.8v1.4c0 .4.3.7.6.8l1.9.4c.2.6.4 1.2.7 1.7l-1.1 1.6a.8.8 0 0 0 .1 1l1 1c.3.3.7.3 1 .1l1.6-1.1c.5.3 1.1.5 1.7.7l.4 1.9c.1.4.4.6.8.6h1.4c.4 0 .7-.3.8-.6l.4-1.9c.6-.2 1.2-.4 1.7-.7l1.6 1.1c.3.2.7.2 1-.1l1-1c.3-.3.3-.7.1-1l-1.1-1.6c.3-.5.5-1.1.7-1.7l1.9-.4c.4-.1.6-.4.6-.8v-1.4a.8.8 0 0 0-.6-.8Z"
            fill="currentColor"
          />
        </svg>
      </button>
      {#if settingsOpen}
        <div class="settings" role="dialog" aria-label="Simple view settings">
          <div class="shead">
            <span>Show in frame</span>
            <button class="sclose" aria-label="Close settings" onclick={closeSettings}>✕</button>
          </div>
          {#each Object.keys(SHOW_LABELS) as key (key)}
            <label class="sopt">
              <input type="checkbox" checked={!!show?.[key]} onchange={() => toggleShow(key)} />
              <span>{SHOW_LABELS[key]}</span>
            </label>
          {/each}
          <p class="shint">Also available per recording, under “Record this run to MP4”.</p>
        </div>
      {/if}
    {/if}

    {#if shownMeta.length || !locked}
      <!-- Header card: the opted-in run facts in a paper box like the turn box,
           sitting just above the screen and sharing its edges (both are 100% of
           the stage's content width). Same trick as `.pending` below: on a
           human's tab it is ALWAYS in the flow, at opacity 0 when nothing is
           shown, so toggling an item never resizes the game screen (it did
           when the row came and went — Andreas 2026-09-08). A recorder page
           has no toggles, so there it exists only when something is shown and
           the bare frame keeps its original geometry. -->
      <div class="meta" class:idle={!shownMeta.length} aria-hidden={!shownMeta.length}>
        {#if show.model}<span class="mmodel">{model ?? '—'}</span>{/if}
        <span class="mright">
          {#if show.elapsed}<span class="mitem"><span class="ml">Time</span> <b>{dur(elapsedS)}</b></span>{/if}
          {#if show.cost}<span class="mitem"><span class="ml">Cost</span> <b>{usd(cost)}</b></span>{/if}
        </span>
      </div>
    {/if}

    <div class="shotwrap">
      {#if frame}
        <img class="screen" src={frame} alt="" onload={onShot} bind:this={shotEl} />
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
    <div class="pending" class:idle={!pendingVisible} class:compacting bind:this={pendingEl} aria-hidden={!pendingVisible}>
      {#if compacting}
        Compacting memory{#if compactAfter != null}&nbsp;after turn <b>{compactAfter}</b>{/if}&nbsp;<span class="dots">{dots}</span> <span class="celapsed">{compactS}s</span>
      {:else}
        Turn <b>{pendingTurn ?? '—'}</b> <span class="dots">{dots}</span>
      {/if}
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
    /* 7% in the mock; trimmed 2026-09-08 when the header card entered the
       column, so the screen gives up less height. One 0.86em line + 1em top
       padding still fits with room. */
    --striph: 5.5%;
  }

  /* exit: bottom-left, only while the mouse moves. Never in the recording.
     Lives in the stage's bottom padding band: the top band belongs to the
     header strip, and a button over the model name looked broken. */
  .exit {
    position: absolute;
    bottom: 0.5%;
    left: 1.6%;
    z-index: 30;
    width: 1.75em;
    height: 1.75em;
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

  /* gear: sits right of the ✕, same size, same reveal. */
  .gear {
    position: absolute;
    bottom: 0.5%;
    left: calc(1.6% + 1.75em + 0.4em);
    z-index: 30;
    width: 1.75em;
    height: 1.75em;
    font-size: clamp(11px, 1.5vh, 19px);
    display: grid;
    place-items: center;
    border-radius: 50%;
    cursor: pointer;
    padding: 0;
    background: rgba(251, 249, 245, 0.78);
    border: 1px solid var(--rule);
    color: var(--ink);
    line-height: 1;
    opacity: 0;
    transition: opacity 0.22s;
    backdrop-filter: blur(3px);
  }
  .gear.show,
  .gear.active {
    opacity: 1;
  }
  .gear:hover,
  .gear.active {
    background: var(--card);
  }

  /* settings panel: opens upward from the buttons, paper on paper, mono. */
  .settings {
    position: absolute;
    bottom: calc(0.5% + 1.75em + 0.6em);
    left: 1.6%;
    z-index: 31;
    font-size: clamp(11px, 1.5vh, 19px);
    min-width: 13em;
    padding: 0.8em 1em 0.9em;
    background: var(--card);
    border: 1px solid var(--rule);
    border-radius: 3px;
    box-shadow: 0 6px 24px rgba(31, 28, 23, 0.14);
    display: flex;
    flex-direction: column;
    gap: 0.55em;
    color: var(--ink);
  }
  .shead {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1em;
    font-size: 0.78em;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    color: var(--faint);
  }
  .sclose {
    background: none;
    border: 0;
    padding: 0 0.1em;
    cursor: pointer;
    color: var(--faint);
    font-family: var(--mono);
    font-size: 1em;
    line-height: 1;
  }
  .sclose:hover {
    color: var(--ink);
  }
  .sopt {
    display: flex;
    align-items: center;
    gap: 0.6em;
    font-size: 0.86em;
    cursor: pointer;
    color: var(--body);
  }
  .sopt input {
    margin: 0;
    width: 1em;
    height: 1em;
    accent-color: var(--ink);
  }
  .shint {
    margin: 0.2em 0 0;
    font-size: 0.7em;
    line-height: 1.4;
    color: var(--faint);
  }

  /* ── header strip ─────────────────────────────────────────────────────── */
  .meta {
    /* Picture width when measured (see measureShot), full width before the
       first frame lands. */
    width: var(--shotw, 100%);
    max-width: 100%;
    flex: none;
    display: flex;
    justify-content: space-between;
    gap: 1.5em;
    background: var(--card);
    border: 1px solid var(--rule);
    border-radius: 3px;
    /* Horizontal padding equals the turn box's 1.3em AT THE BOX'S FONT, so the
       model name starts on the same x as "TURN n" below. */
    padding: 0 calc(clamp(10px, 1.5vh, 19px) * 1.3);
    /* Fixed height, content centred: an empty (idle) card and a full one are
       the same size, so the game screen keeps its rect when items toggle. */
    height: 2.1em;
    align-items: center;
    /* 1.2× the box's font: one step up from the TURN label so it reads at a
       glance in a posted clip. */
    font-size: calc(clamp(10px, 1.5vh, 19px) * 1.2);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--faint);
    line-height: 1.2;
    overflow: hidden;
    transition: opacity 0.3s;
  }
  .meta.idle {
    opacity: 0;
  }
  .mmodel {
    color: var(--ink);
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .mright {
    margin-left: auto;
    display: flex;
    gap: 1.4em;
    white-space: nowrap;
  }
  .meta b {
    color: var(--ink);
    font-weight: 700;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
    text-transform: none;
  }

  .shotwrap {
    flex: 1;
    min-height: 0;
    width: 100%;
    display: grid;
    /* An explicit definite row. With the default auto row the image's
       `max-height: 100%` has nothing to resolve against and silently does
       nothing, so the frame overflowed the shot area and got clipped by
       .stage's overflow:hidden. */
    grid-template-rows: minmax(0, 1fr);
    place-items: center;
  }
  /* Fill the whole shot area and letterbox inside it, rather than sitting at
     the frame's intrinsic size. `width/height: auto` + `max-*: 100%` only ever
     scales a frame DOWN, so the live socket's small GBA frames rendered
     postage-stamp-sized with a large dead gap above the box — the mock never
     showed this because its saved trace PNGs are big enough to hit the cap.
     `width: 100%` + `max-height` scales UP to the available width and clamps
     when that would overflow. The clamp does NOT recompute the width, so the
     element box ends up ~1% off the frame's true ratio — `object-fit: contain`
     absorbs that, keeping the pixels undistorted. Pixel art shows stretching
     immediately, so this is not a rounding detail. */
  .screen {
    display: block;
    width: 100%;
    height: auto;
    max-height: 100%;
    object-fit: contain;
    image-rendering: pixelated;
    /* No border. The mock had a 1px --rule frame; on the real emulator output
       it just reads as a seam, because mGBA's own letterboxing already gives
       the frame hard black edges. */
  }

  /* ── main box ─────────────────────────────────────────────────────────── */
  .box {
    /* Same measured picture width as the header card (see measureShot), so all
       three paper elements share the screen's edges. */
    width: var(--shotw, 100%);
    max-width: 100%;
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
    width: var(--shotw, 100%);
    max-width: 100%;
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
  /* Solid rule + ink label while compacting: the dashed "waiting for a turn"
     strip must not be the only thing on screen during a multi-minute pause
     that is NOT a turn. */
  .pending.compacting {
    border-style: solid;
    color: var(--body);
  }
  .celapsed {
    margin-left: auto;
    color: var(--faint);
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
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
    .exit,
    .gear {
      transition-duration: 1ms;
    }
    .pending {
      transition-duration: 1ms;
    }
  }
</style>
