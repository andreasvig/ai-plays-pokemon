<script>
  // LIVE spectate (Plan §P6). The component tree is unchanged from the mock —
  // every panel the real static/index.html carries stays — but the data source
  // is swapped to the real streams:
  //   stats row  ← `stats` WS msg (+ client-clock elapsed)
  //   emulator   ← /runs/{id}/ws/screen binary PNG frames
  //   memory     ← `state_update` WS msg
  //   task       ← task_started/task_completed events (TaskMaster only; on a
  //                self-directed run — config-4.0+, no TaskMaster — the panel is
  //                not rendered at all and memory takes the row. See hasTaskMaster.)
  //   gate HUD   ← referee_checkpoint events ÷ /runs/{id}/api/config ladder
  //   trace feed ← the full event taxonomy, grouped by turn
  // The active run id comes from /api/emulator/status (App passes it in).
  import { gate, GATE_INDEX } from '../lib/gates.js'
  import { usd, dur, coerceHandback } from '../lib/format.js'
  import { windowFeed } from '../lib/feed.js'
  import JsonTree from './JsonTree.svelte'
  import TraceFeed from './TraceFeed.svelte'
  import SimpleView from './SimpleView.svelte'
  import Icon from './Icon.svelte'
  import * as api from '../lib/api.js'

  // Live feed windowing: render only the last MAX_LIVE_TASKS tasks (or, for
  // casual/no-TaskMaster runs, the last FALLBACK_LIVE_TURNS turns) — see feed.js.
  const MAX_LIVE_TASKS = 3
  const FALLBACK_LIVE_TURNS = 40

  // `run` = the active RunSummary (for model + kind badge); `activeRunId` = the
  // run-dir id to open the live streams against. Either may be null (no run).
  // `recording` / `forcedSimple` are set only on a recorder page (see
  // lib/record.js): the MP4 recorder loads /spectate with query params so its
  // headless browser lands on the right presentation with no human input.
  let {
    run = null, activeRunId = null, muted = true, ontogglemute, onnew, onback,
    recording = false, forcedSimple = null,
  } = $props()

  // ── live state, populated by the event/screen sockets ──
  let stats = $state({ turns: 0, cost: 0, input_tokens: 0, output_tokens: 0 })
  let memory = $state({})
  let task = $state(null)              // {title, description, success} | null

  // Self-directed runs (config-4.0+) have no TaskMaster and therefore emit no
  // task_started/task_completed events, so `task` stays null for the whole run.
  // The agent instead keeps the goal it set ITSELF in its memory dictionary under
  // `current_goal` — which the memory panel already renders — so there is no
  // separate goal panel on these runs: it would duplicate a memory key verbatim.
  // The memory dictionary takes the full width instead.
  //
  // Keyed on the CONFIG (`task_master.enabled`), not on `task == null`: `task`
  // is also null during the opening turns of a TaskMaster run, before the first
  // handoff, so keying on it would collapse the panel and then pop it back in.
  // Defaults to true so the panel never flickers away while the config loads.
  let hasTaskMaster = $state(true)
  let ladder = $state([])              // [{id, name, deadline_turn, group?}]
  let enforce = $state(false)
  let stamps = $state({})              // {checkpoint_id: turn} latched
  let currentTurn = $state(0)
  let feed = $state([])                // [{kind:'master'|'turn', ...}] chronological
  let hiddenTurns = $state(0)          // turns below the live window (shown as a note)
  let eventCount = $state(0)
  let screenConnected = $state(false)
  let eventsConnected = $state(false)
  let screenUrl = $state(null)
  let startedAtMs = $state(null)
  let elapsedS = $state(0)

  // ── simple view (plan §4.3) ──
  // The recording-optimised presentation. It is NOT a route: /spectate stays one
  // URL and the state lives here, persisted to localStorage so a reload
  // mid-recording comes back in the same view. Both sockets are owned by the
  // $effect below, which keys on activeRunId ONLY — toggling must never appear
  // in that effect's dependency set or the streams would tear down and re-open
  // on every flip.
  const SIMPLE_KEY = 'spectate.simple'
  function readSimple() {
    try { return localStorage.getItem(SIMPLE_KEY) === '1' } catch { return false }
  }
  // A recorder page is pinned to whichever presentation it was told to capture
  // and ignores (and never writes) the human's stored preference — recording the
  // detailed view must not flip his own tab to it on the next reload.
  // Reading the prop here captures its INITIAL value, which is exactly right:
  // a recorder page's view is fixed for the life of the file, and a human's
  // page never receives a non-null forcedSimple at all.
  // svelte-ignore state_referenced_locally
  let simple = $state(forcedSimple ?? readSimple())
  // {seq, data} handed to SimpleView. See setSimple/ingestEvent for the
  // one-write-per-task contract that makes it work.
  let lastEvent = $state(null)
  // {turn, presses, reasoning} — the last turn we already know about, so
  // toggling on at turn 40 doesn't open on an empty box (plan §8).
  let seed = $state(null)
  // Monotonic for the LIFETIME of this component, deliberately never reset —
  // not by resetLiveState, not by a run change. SimpleView drops any event whose
  // seq is not greater than the last one it saw, so a counter that restarted at
  // 1 while a mounted SimpleView had already reached 300 would silently swallow
  // every event of the new run.
  let evtSeq = 0

  function setSimple(on) {
    if (recording) return   // a recorder page's view is fixed for the whole file
    // Seed and lastEvent are written here, in the click task, BEFORE `simple`
    // flips — SimpleView mounts with both already in place. lastEvent is cleared
    // so a freshly mounted instance doesn't replay whatever event happened to be
    // the last one before the toggle (its own lastSeq starts at -1, so it would
    // otherwise treat a stale event as new and animate a turn the seed just
    // rendered).
    seed = on ? buildSeed() : null
    lastEvent = null
    simple = on
    try { localStorage.setItem(SIMPLE_KEY, on ? '1' : '0') } catch { /* private mode */ }
  }

  // Most recent turn we hold a decision for, rebuilt out of the same turnBoxes
  // accumulator the trace feed renders from — no new event, no new API field.
  // Max key rather than last insertion: a backlog replay can bind turns out of
  // order. Returns null when nothing has landed yet (SimpleView then just waits).
  function buildSeed() {
    let best = null
    for (const [turn, boxes] of turnBoxes) {
      if (typeof turn !== 'number' || (best != null && turn <= best)) continue
      if (boxes.some((b) => b.k === 'output' || b.k === 'action')) best = turn
    }
    if (best == null) return null
    const boxes = turnBoxes.get(best)
    let out = null
    let act = null
    for (const b of boxes) {
      if (b.k === 'output') out = b
      else if (b.k === 'action') act = b
    }
    return {
      turn: best,
      // `presses` may be a token list or a space-joined string; actionTokens()
      // accepts both. `reasoning` prefers the raw field stashed on the box over
      // its rendered `t`, which carries a "Last turn: succeeded — " prefix that
      // is trace-feed chrome, not the model's prose.
      presses: act ? act.t : '',
      reasoning: out ? (out.reasoning || out.t || '') : '',
    }
  }

  // per-turn box accumulation while streaming (turn → boxes[])
  let turnBoxes = new Map()
  // master (TaskMaster) trace accumulation. The master trace for task N arrives
  // BEFORE task_started{N}, which in turn arrives before that task's turns. So
  // we buffer the raw trace + objective by task_index, then bind the resulting
  // card to the FIRST turn of the task (the next turn_start after task_started).
  let masterTraces = new Map()   // task_index → {model_used, cost_usd, steps}
  let masterCards = new Map()    // task_index → {taskIndex, firstTurn, model, cost, steps, title, description, success}
  let pendingTaskIndex = null    // task_index whose first turn we still need to bind
  let prevScreenUrl = null
  // rebuilds are rAF-batched: a backlog storm (WS replay) collapses to ONE
  // rebuild per frame instead of one per event. Plain vars (not $state).
  let rebuildPending = false
  let rebuildRaf = null

  // ── derivations for the gate HUD ──
  // Gates only apply to OFFICIAL runs. When we KNOW the run is casual, hide every
  // gate reference; null run (early) is treated as non-casual so we don't crash.
  const showGates = $derived(run?.kind !== 'casual')
  const reached = $derived(Object.keys(stamps).length)
  const totalGates = $derived(ladder.length || 0)
  // next gate = first ladder rung NOT yet stamped; `tone` drives the Gates stat's
  // urgency colour as that gate's deadline nears (red <25 turns left, amber <60).
  const nextGate = $derived(ladder.find((g) => !(g.id in stamps)) || null)
  const deadline = $derived(nextGate?.deadline_turn ?? null)
  const turnsLeft = $derived(deadline != null ? deadline - currentTurn : null)
  const tone = $derived(
    turnsLeft == null ? 'ok' : turnsLeft < 25 ? 'red' : turnsLeft < 60 ? 'amber' : 'ok'
  )

  // Parse the run start time (local) out of the run-dir id, e.g.
  // "2026-06-16_09-35-12_config-3.13__gpt-5" → epoch ms. Returns null if the id
  // doesn't lead with a timestamp. Used so Elapsed always counts from the real
  // start, even when the RunSummary (run.startedAt) isn't loaded on re-entry.
  function startMsFromRunId(id) {
    const m = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})/.exec(id || '')
    if (!m) return null
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime()
  }

  // ── helpers to turn raw events into trace boxes ──
  function parseArgs(a) {
    if (typeof a === 'string') { try { return JSON.parse(a) } catch { return null } }
    return a
  }
  // Parse return_to_taskmaster into a verdict box. Real shape (north-star run):
  //   { self_assessment: 'succeeded'|'failed'|'partial'|<free text>, task_summary: str }
  // Map the known enum to a labeled verdict; free text becomes the verdict line.
  function fmtHandback(r) {
    if (!r || typeof r !== 'object') return { verdict: 'Returned to TaskMaster', tone: 'partial', summary: '' }
    const raw = (r.self_assessment ?? '').toString().trim()
    const lc = raw.toLowerCase()
    let verdict, tone
    if (/^succe/.test(lc) || lc === 'true' || lc === 'complete') { verdict = '✓ Task complete'; tone = 'ok' }
    else if (/^fail/.test(lc) || lc === 'false' || /not (complete|done|succeed)/.test(lc)) { verdict = '✗ Task not complete'; tone = 'no' }
    else if (/^partial/.test(lc) || /partly/.test(lc)) { verdict = '~ Partial'; tone = 'partial' }
    else if (raw) { verdict = raw; tone = 'partial' }      // free-text self-assessment → use verbatim
    else { verdict = 'Returned to TaskMaster'; tone = 'partial' }
    return { verdict, tone, summary: r.task_summary || '' }
  }

  // Extract ONLY the TaskMaster's verdict on the previous task from the raw
  // pydantic-ai trace. The thinking + intermediate tool calls/responses
  // (ask_perplexity etc.) are intentionally NOT surfaced in the LIVE spectate
  // view — they're far too verbose (Andreas 2026-06-17). The task
  // title/plan/success criteria and the verdict already render as the card's
  // own rows from the `final_result` decision; the strategic reasoning prose is
  // likewise omitted live. (The post-run Report still shows the full trace.)
  function parseMasterMessages(messages) {
    if (!Array.isArray(messages)) return { steps: [], rating: null }
    let rating = null
    for (const m of messages) {
      if (!m || typeof m !== 'object') continue
      // The TaskMaster's structured output is emitted as a `final_result` tool
      // call whose args are the decision JSON. We only mine it for the rating of
      // the previous task, so the card can show the verdict above the next task.
      if (m.role === 'tool_call' && m.tool_name === 'final_result') {
        const a = parseArgs(m.args)
        if (a && typeof a === 'object') {
          const r = a.rating_of_previous_task
          if (r != null && typeof r === 'object') {
            rating = { status: String(r.status || '').trim(), reasoning: String(r.reasoning || '').trim() }
          } else if (typeof r === 'string' && r.trim()) {
            rating = { status: '', reasoning: r.trim() }
          }
        }
      }
    }
    return { steps: [], rating }
  }

  function pushBox(turn, box) {
    const t = turn ?? currentTurn
    if (!turnBoxes.has(t)) turnBoxes.set(t, [])
    turnBoxes.get(t).push(box)
    scheduleRebuild()
  }
  // Coalesce a burst of events into a single rebuild per animation frame. The
  // events WS replays the full backlog on connect, so a fresh open fires
  // hundreds of events synchronously — without this each one triggered a full
  // O(N) rebuildFeed(), making open ~O(N^2). The scheduler is plumbed through
  // every call site instead of rebuildFeed() directly.
  function scheduleRebuild() {
    if (rebuildPending) return
    rebuildPending = true
    const raf = typeof requestAnimationFrame !== 'undefined' ? requestAnimationFrame : (cb) => setTimeout(cb, 0)
    rebuildRaf = raf(() => {
      rebuildPending = false
      rebuildRaf = null
      rebuildFeed()
    })
  }
  function rebuildFeed() {
    const { feed: f, cutoffTurn, hiddenTurns: h } = windowFeed({
      turnBoxes, masterCards, maxTasks: MAX_LIVE_TASKS, fallbackTurns: FALLBACK_LIVE_TURNS,
    })
    feed = f
    hiddenTurns = h
    // Prune accumulators below the cutoff so memory stays bounded on long runs.
    // cutoffTurn === 0 means "keep everything" → prune nothing.
    if (cutoffTurn > 0) {
      for (const t of [...turnBoxes.keys()]) {
        if (t < cutoffTurn) turnBoxes.delete(t)
      }
      for (const [idx, c] of [...masterCards.entries()]) {
        // never drop the card whose firstTurn === cutoffTurn (it opens the window)
        if (c.firstTurn != null && c.firstTurn < cutoffTurn) {
          masterCards.delete(idx)
          masterTraces.delete(idx)
        }
      }
    }
  }

  // Build the master card once both halves are in: the trace (model/cost/images)
  // and the objective the master set (title/description/success from task_started).
  // Reconnect-safe: the WS replays the full backlog, so dedup by task_index and
  // never overwrite a firstTurn that's already bound.
  function buildMasterCard(taskIndex) {
    const trace = masterTraces.get(taskIndex)
    const card = masterCards.get(taskIndex)
    if (!trace || !card) return
    card.model = trace.model_used || ''
    card.cost = trace.cost_usd
    card.steps = trace.steps || []
    card.rating = trace.rating || null   // verdict on the PREVIOUS task (shown above the new task)
    // NB: input images are intentionally NOT surfaced in the live feed (B9.7);
    // they live only in the history Report.
  }

  // map one raw event → zero or more trace boxes (parity with static/index.html)
  function ingestEvent(evt) {
    eventCount += 1
    // ONE write per task, and exactly one — this is the whole contract with
    // SimpleView. Svelte batches state, so two assignments inside one
    // synchronous block flush as a single update and the FIRST event is silently
    // dropped. Safe here because the only caller is the events WS `onmessage`
    // (api.openEventSocket), and the server sends one JSON frame per event —
    // including during the backlog replay on connect — so every ingestEvent()
    // call is its own task with an effect flush behind it. There is no
    // for-loop-over-a-backlog path in this component; if one is ever added it
    // must yield between iterations or SimpleView will miss phases.
    // Written unconditionally, not gated on `simple`: the counter has to stay
    // continuous across a toggle.
    lastEvent = { seq: ++evtSeq, data: evt }
    const t = evt.type

    if (t === 'turn_start') {
      if (typeof evt.turn === 'number') currentTurn = evt.turn
      if (!turnBoxes.has(evt.turn)) turnBoxes.set(evt.turn, [])
      // bind the just-started task's master card to its first turn (once)
      if (pendingTaskIndex != null) {
        const card = masterCards.get(pendingTaskIndex)
        if (card && card.firstTurn == null) card.firstTurn = evt.turn
        pendingTaskIndex = null
      }
      scheduleRebuild()
      return
    }
    if (t === 'task_master_trace') {
      // arrives BEFORE task_started{N}; buffer the trace half by task_index.
      const idx = evt.task_index
      const parsed = parseMasterMessages(evt.messages)
      masterTraces.set(idx, {
        model_used: evt.model_used,
        cost_usd: evt.cost_usd,
        steps: parsed.steps,
        rating: parsed.rating,
      })
      buildMasterCard(idx)
      scheduleRebuild()
      return
    }
    if (t === 'task_started') {
      task = { title: evt.title || '(untitled task)', description: evt.description || '', success: evt.success_criteria || '' }
      // objective half of the master card; first turn bound on next turn_start.
      const idx = evt.task_index
      if (idx != null) {
        if (!masterCards.has(idx)) {
          masterCards.set(idx, {
            taskIndex: idx, firstTurn: null,
            model: '', cost: null, steps: [], rating: null,
            title: task.title, description: task.description, success: task.success,
          })
        } else {
          const c = masterCards.get(idx)
          c.title = task.title; c.description = task.description; c.success = task.success
        }
        buildMasterCard(idx)
        pendingTaskIndex = idx
      }
      scheduleRebuild()
      return
    }
    if (t === 'task_completed') {
      // keep the title; mark it done in the panel via a status note
      if (task) task = { ...task, status: evt.status || 'completed' }
      return
    }
    if (t === 'referee_checkpoint') {
      if (evt.checkpoint_id != null && !(evt.checkpoint_id in stamps)) {
        stamps = { ...stamps, [evt.checkpoint_id]: evt.turn }
      }
      return
    }
    if (t === 'referee_gate_missed' || t === 'referee_terminate') {
      // surfaced via the HUD tone; nothing to add to the feed
      return
    }
    if (t === 'llm_thinking') { pushBox(evt.turn, { k: 'thinking', t: evt.content || '' }); return }
    if (t === 'llm_output') {
      const args = parseArgs(evt.args || '')
      if (args && typeof args === 'object') {
        const lines = []
        const grade = args.last_turn_succeeded
        let ok = null
        if (grade === true) { lines.push('Last turn: succeeded'); ok = true }
        else if (grade === false) { lines.push('Last turn: failed'); ok = false }
        if (args.reasoning) lines.push(args.reasoning)
        // `reasoning` is the model's prose without the grade prefix, carried
        // alongside the rendered `t` purely so buildSeed() has something clean
        // to hand the simple view. Nothing renders it directly.
        if (lines.length) pushBox(evt.turn, { k: 'output', ok, t: lines.join(' — '), reasoning: args.reasoning || '' })
        if (args.inputs && args.inputs.length) {
          const inputs = Array.isArray(args.inputs) ? args.inputs.join(' ') : args.inputs
          pushBox(evt.turn, { k: 'action', t: inputs })
        }
        const handback = coerceHandback(args.return_to_taskmaster)
        if (handback) pushBox(evt.turn, { k: 'handback', ...fmtHandback(handback) })
      }
      return
    }
    if (t === 'memory_update_output') {
      const raw = evt.content || '(no changes)'
      let display = raw
      if (raw !== '(no changes)' && raw.toLowerCase() !== 'none') {
        try { display = JSON.stringify(JSON.parse(raw)) } catch { /* keep raw */ }
      }
      pushBox(evt.turn, { k: 'memory', t: display })
      return
    }
    if (t === 'ocr_flush') {
      const n = evt.n_captures || 0
      const cleaned = evt.cleaned || ''
      if (n === 0 && !cleaned) return
      const cost = evt.cost_usd ? ` · $${Number(evt.cost_usd).toFixed(5)}` : ''
      pushBox(evt.turn, { k: 'ocr', t: cleaned || '(empty)', meta: `${n} captures · ${evt.duration || 0}s${cost}` })
      return
    }
    if (t === 'llm_text') { pushBox(evt.turn, { k: 'output', ok: null, t: evt.content || '' }); return }
    if (t === 'tool_call') { pushBox(evt.turn, { k: 'tool', name: evt.tool, args: JSON.stringify(evt.args), resp: null }); return }
    if (t === 'tool_response') {
      const resp = typeof evt.response === 'string' ? evt.response : JSON.stringify(evt.response)
      pushBox(evt.turn, { k: 'tool', name: '↳ response', args: '', resp })
      return
    }
    if (t === 'screen_settled') { pushBox(evt.turn, { k: 'settle', t: `Settled in ${evt.duration || 0}s` }); return }
    // Per-attempt LLM call retries (timeout / transient provider error). The
    // backend re-rolls the provider with escalating routing; surface each
    // attempt LOUDLY so a stalling turn is obvious live, not a silent freeze.
    if (t === 'agent_retry') {
      const n = evt.attempt, max = evt.max_attempts
      const why = evt.error_type === 'TimeoutError'
        ? `timed out after ${Math.round(evt.timeout_s || 0)}s`
        : `${evt.error_type || 'error'}${evt.error ? ` (${String(evt.error).slice(0, 80)})` : ''}`
      const next = (evt.retryable && n < max)
        ? ` — re-rolling provider (sort: ${evt.provider_sort || 'default'})…`
        : ' — no attempts left, falling through'
      pushBox(evt.turn, { k: 'retry', t: `Attempt ${n}/${max} ${why}${next}` })
      return
    }
    if (t === 'output_retry') { pushBox(evt.turn, { k: 'retry', t: evt.content ? `Output validation failed — retrying: ${evt.content}` : 'Output validation failed — retrying.' }); return }
    if (t === 'agent_error' || t === 'action_error') {
      pushBox(evt.turn, { k: 'error', t: evt.error || evt.message || JSON.stringify(evt) })
      return
    }
    // screenshot, state_change, button_sequence, turn_trace/explanation/usage,
    // screen_settling, run_start/end — ignored for the live feed (covered by
    // dedicated streaming events above or by the stats msg).
  }

  function resetLiveState() {
    stats = { turns: 0, cost: 0, input_tokens: 0, output_tokens: 0 }
    memory = {}
    task = null
    hasTaskMaster = true   // re-answered by the new run's /api/config
    stamps = {}
    currentTurn = 0
    eventCount = 0
    turnBoxes = new Map()
    masterTraces = new Map()
    masterCards = new Map()
    pendingTaskIndex = null
    feed = []
    hiddenTurns = 0
    // Same treatment as turnBoxes: the seed is derived from it, so it must not
    // survive into the next run. `evtSeq` is deliberately NOT reset (see above).
    seed = null
    lastEvent = null
    // cancel any in-flight rebuild so it doesn't fire against the reset state
    if (rebuildRaf != null && typeof cancelAnimationFrame !== 'undefined') cancelAnimationFrame(rebuildRaf)
    rebuildPending = false
    rebuildRaf = null
  }

  // ── socket lifecycle: open on activeRunId, tear down on change/unmount ──
  let evtSock = null
  let scrSock = null
  let cfgRunId = null

  $effect(() => {
    const id = activeRunId
    // tear down previous
    if (evtSock) { evtSock.close(); evtSock = null }
    if (scrSock) { scrSock.close(); scrSock = null }
    if (prevScreenUrl) { URL.revokeObjectURL(prevScreenUrl); prevScreenUrl = null }
    screenUrl = null
    screenConnected = false
    eventsConnected = false
    if (!id) { resetLiveState(); return }

    resetLiveState()
    // Elapsed must be measured from the REAL run start every time spectate is
    // (re)opened — not reset to "now" on re-entry. The run-dir id always encodes
    // the start (`YYYY-MM-DD_HH-MM-SS_…`), so derive it from the id; only fall
    // back to the RunSummary's startedAt / now if the id can't be parsed.
    startedAtMs = startMsFromRunId(id) ?? (run?.startedAt ? Date.parse(run.startedAt) : Date.now())

    // the events WS replays the full backlog on connect → reset accumulators
    // first so a reconnect rebuilds cleanly instead of double-appending.
    evtSock = api.openEventSocket(id, (msg) => {
      eventsConnected = true
      if (msg.type === 'event') ingestEvent(msg.data)
      else if (msg.type === 'state_update') memory = msg.data || {}
      else if (msg.type === 'stats') stats = { ...stats, ...msg.data }
    })
    scrSock = api.openScreenSocket(id, (url) => {
      screenConnected = true
      if (prevScreenUrl) URL.revokeObjectURL(prevScreenUrl)
      prevScreenUrl = url
      screenUrl = url
    })

    // gate ladder for the HUD (real, never hardcoded)
    cfgRunId = id
    api.fetchRunConfig(id).then((cfg) => {
      if (cfgRunId !== id) return
      hasTaskMaster = !!cfg.task_master
      if (cfg.referee && Array.isArray(cfg.referee.ladder)) {
        ladder = cfg.referee.ladder
        enforce = !!cfg.referee.enforce
      }
    }).catch(() => {})

    return () => {
      if (evtSock) { evtSock.close(); evtSock = null }
      if (scrSock) { scrSock.close(); scrSock = null }
      if (prevScreenUrl) { URL.revokeObjectURL(prevScreenUrl); prevScreenUrl = null }
    }
  })

  // ── elapsed = client clock from the run's start ──
  $effect(() => {
    if (!activeRunId) { elapsedS = 0; return }
    // + prior_duration_s makes a --continue keep counting from the source run's
    // elapsed time instead of restarting at 0 (seeded via the stats WS msg).
    const tick = () => { if (startedAtMs) elapsedS = Math.max(0, (Date.now() - startedAtMs) / 1000 + (stats.prior_duration_s || 0)) }
    tick()
    const iv = setInterval(tick, 1000)
    return () => clearInterval(iv)
  })

  const tokensLabel = $derived(
    `${Math.round((stats.input_tokens || 0) / 1000)}k`
  )
  const tokensSub = $derived(`${Math.round((stats.output_tokens || 0) / 1000)}k`)
</script>

<section class="letterbox">
  <div class="frame">
  {#if !activeRunId}
    <div class="empty">
      <p class="big">Waiting for a live run</p>
      <p class="faint">No run is active right now. Queue a run to start spectating — the next item starts automatically and streams here live.</p>
      <button class="btn" onclick={() => onnew()}>+ New run</button>
      <button class="btn ghost" onclick={() => onback()}><Icon name="back" size={13} /> Back to leaderboard</button>
    </div>
  {:else if simple}
    <!-- The simple view replaces the whole instrument panel, bar included. Its
         root is position:fixed;inset:0;z-index:40, so it does not sit in this
         flow at all — it covers the viewport, letterbox and all. Exit is its own
         ✕ (visible only while the mouse moves), which calls onexit. The sockets
         above are untouched: they belong to an $effect keyed on activeRunId, and
         nothing here re-runs it. -->
    <SimpleView frame={screenUrl} {lastEvent} {seed} onexit={() => setSimple(false)} />
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}><Icon name="back" size={13} /> Leaderboard</button>
      <span class="pill"><span class="dot live"></span> live</span>
      <button class="mutebtn" class:muted onclick={() => ontogglemute && ontogglemute()}
              title={muted ? 'Game audio muted — click to unmute' : 'Game audio on — click to mute'}
              aria-label={muted ? 'Unmute game audio' : 'Mute game audio'}><Icon name={muted ? 'muted' : 'audio'} size={16} /></button>
      <button class="btn ghost" onclick={() => setSimple(true)}
              title="Simple view — full-screen, recording-optimised"><Icon name="tv" size={14} /> Simple view</button>
      {#if run}<span class="badge {run.kind}">{run.kind === 'official' ? 'benchmark' : 'casual'}</span>{/if}
      <span class="model mono">{run?.model ?? activeRunId}</span>
      <span class="conn">
        <span class="c-ind" class:off={!screenConnected}><span class="dot" class:live={screenConnected}></span> screen</span>
        <span class="c-ind" class:off={!eventsConnected}><span class="dot" class:live={eventsConnected}></span> events</span>
        <span class="c-evt faint mono">{eventCount.toLocaleString()} events</span>
      </span>
    </div>

    <div class="layout">
      <!-- main: BIG emulator + HUD + stats + task + memory -->
      <div class="main">
        <div class="stats" style={`grid-template-columns: repeat(4, 1fr)${showGates ? ' 30%' : ''}`}>
          <div class="stat"><span class="sl">Turn</span><span class="sv tnum">{stats.turns || currentTurn}</span></div>
          <div class="stat"><span class="sl">Cost</span><span class="sv tnum">{usd(stats.cost)}</span></div>
          <div class="stat"><span class="sl">Tokens</span><span class="sv tnum">{tokensLabel}<span class="su">/{tokensSub}</span></span></div>
          <div class="stat"><span class="sl">Elapsed</span><span class="sv tnum">{dur(elapsedS)}</span></div>
          {#if showGates}
            <div class="stat gates {tone}">
              <span class="sl">Gates</span>
              <span class="sv tnum">{reached}/{totalGates || '—'}</span>
              {#if nextGate}
                <span class="gnext">next gate to complete{#if deadline != null} before turn {deadline}{/if}: {nextGate.name}{#if turnsLeft != null} · {turnsLeft} turns left{/if}</span>
              {:else}
                <span class="gnext">all gates reached</span>
              {/if}
            </div>
          {/if}
        </div>

        <div class="gba">
          {#if screenUrl}
            <img class="screen" src={screenUrl} alt="emulator screen" />
          {:else}
            <div class="ph">emulator screen<br /><span class="faint">connecting to live stream…</span></div>
          {/if}
        </div>

        <div class="panels" class:solo={!hasTaskMaster}>
          {#if hasTaskMaster}
          <div class="panel task">
            <div class="p-h">Current task</div>
            <div class="p-scroll">
              {#if task}
                <div class="t-title">{task.title}{#if task.status}<span class="t-done faint"> · {task.status}</span>{/if}</div>
                {#if task.description}<div class="t-lab">Description</div><p class="t-body">{task.description}</p>{/if}
                {#if task.success}<div class="t-lab">Success criteria</div><p class="t-body mono">{task.success}</p>{/if}
              {:else}
                <p class="t-body faint">Waiting for the first TaskMaster handoff…</p>
              {/if}
            </div>
          </div>
          {/if}
          <div class="panel mem">
            <div class="p-h">Memory dictionary</div>
            <div class="p-scroll">
              {#if memory && Object.keys(memory).length}<JsonTree data={memory} />{:else}<p class="t-body faint">(empty)</p>{/if}
            </div>
          </div>
        </div>

      </div>

      <!-- side: live trace feed (own component) -->
      <TraceFeed turns={feed} {hiddenTurns} />
    </div>
  {/if}
  </div>
</section>

<style>
  /* Round 11 — fit-to-screen kiosk: a viewport-filling dark letterbox that
     centers a 16:9 frame. The frame fills a real 16:9 TV and letterboxes
     (centered, dark bars) on any other aspect ratio. Nothing scrolls but the
     internal panels + trace feed. */
  .letterbox {
    width: 100vw; height: 100vh; overflow: hidden;
    display: flex; align-items: center; justify-content: center;
    background: var(--dark);
  }
  .frame {
    width: min(100vw, calc(100vh * 16 / 9));
    height: min(100vh, calc(100vw * 9 / 16));
    display: flex; flex-direction: column; overflow: hidden;
    padding: 14px 18px; background: var(--bg);
  }
  .empty { flex: 1; min-height: 0; text-align: center; display: flex; flex-direction: column; gap: 10px; align-items: center; justify-content: center; }
  .empty .faint { max-width: 440px; line-height: 1.5; }
  .big { font-size: 18px; font-weight: 700; margin: 0; }
  .bar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex: none; }
  .bar .model { font-size: 14px; font-weight: 650; }
  .mutebtn {
    border: 1px solid var(--border); background: var(--surface); border-radius: var(--radius-sm);
    line-height: 1; padding: 6px 7px; display: grid; place-items: center; cursor: pointer; transition: opacity .12s, background .12s;
  }
  .mutebtn:hover { background: var(--wash); }
  .mutebtn.muted { opacity: .55; }
  .conn { margin-left: auto; display: flex; align-items: center; gap: 12px; }
  .c-ind { font-size: 11px; font-weight: 650; color: var(--green); display: inline-flex; align-items: center; gap: 5px; }
  .c-ind.off { color: var(--faint); }
  .c-ind.off .dot { background: var(--faint); }
  .c-evt { font-size: 11px; }

  /* The two-column grid fills the frame's leftover height; min-height:0 lets
     the grid children shrink (essential or the emulator pushes past the frame). */
  /* Trace rail gets ~55% more width than before (was clamp(300,30%,500)) — the
     live trace is the thing you read on a TV; the emulator hero is still ample. */
  .layout { display: grid; grid-template-columns: minmax(0, 1fr) clamp(360px, 44%, 780px); gap: 20px; align-items: stretch; flex: 1; min-height: 0; height: 100%; overflow: hidden; }
  .main { display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow: hidden; }
  /* emulator flexes to fill leftover height and shrinks on short screens;
     .screen uses object-fit:contain so it never overflows. */
  .gba { flex: 1; min-height: 0; aspect-ratio: 240/160; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .screen { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }
  .ph { color: var(--faint); text-align: center; font-size: 15px; line-height: 1.7; }

  .stats { flex: none; display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; align-items: stretch; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 12px; box-shadow: var(--shadow); }
  /* Gates stat carries the next-gate line + deadline urgency tone (the old big
     HUD folded in here); it occupies ~30% of the stats row (set inline). */
  .stat.gates { display: flex; flex-direction: column; }
  .stat.gates.amber { border-color: var(--tm-rule); }
  .stat.gates.red { border-color: var(--red-rule); }
  .gnext { font-size: 10px; line-height: 1.35; font-weight: 600; color: var(--muted); margin-top: 3px; }
  .stat.gates.amber .gnext { color: var(--tm); }
  .stat.gates.red .gnext { color: var(--red); }
  .sl { display: block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .sv { font-size: 18px; font-weight: 750; }
  .su { font-size: 11px; color: var(--muted); font-weight: 600; }

  /* Current task gets more room than the memory dictionary — 60/40 split.
     `.solo` = self-directed run (no TaskMaster): the task panel isn't rendered
     at all, so the memory dictionary takes the whole row. */
  .panels { flex: none; display: grid; grid-template-columns: 3fr 2fr; gap: 14px; align-items: stretch; }
  .panels.solo { grid-template-columns: 1fr; }
  .panel { display: flex; flex-direction: column; min-height: 0; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; box-shadow: var(--shadow); }
  .p-h { flex: none; font-size: 11px; font-weight: 750; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin-bottom: 10px; }
  .t-title { font-size: 14px; font-weight: 700; margin-bottom: 8px; }
  .t-done { font-weight: 500; }
  .t-lab { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; margin-top: 8px; }
  .t-body { font-size: 12.5px; line-height: 1.5; color: var(--muted); margin: 3px 0 0; }
  /* internal scroll only — capped (vh-relative) so the panels never force the
     frame to overflow; on short screens they scroll within their box. */
  .p-scroll { flex: 1; min-height: 0; max-height: 22vh; overflow-y: auto; overflow-x: auto; padding-right: 4px; }

  /* The kiosk frame is ALWAYS landscape 16:9 (min(100vw,100vh*16/9) wide), so
     the two-column hero+rail always fits inside it regardless of the outer
     viewport. We deliberately do NOT collapse to one column on a narrow outer
     viewport (the old max-width:1080px rule did, which shoved the emulator out
     of the no-scroll frame at 4:3 / ≤1080px). The rail uses clamp() above so it
     scales down on small frames instead, keeping the emulator column visible. */
</style>
