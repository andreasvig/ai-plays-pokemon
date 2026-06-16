<script>
  // LIVE spectate (Plan §P6). The component tree is unchanged from the mock —
  // every panel the real static/index.html carries stays — but the data source
  // is swapped to the real streams:
  //   stats row  ← `stats` WS msg (+ client-clock elapsed)
  //   emulator   ← /runs/{id}/ws/screen binary PNG frames
  //   memory     ← `state_update` WS msg
  //   task       ← task_started/task_completed events (TaskMaster) w/ graceful fallback
  //   gate HUD   ← referee_checkpoint events ÷ /runs/{id}/api/config ladder
  //   trace feed ← the full event taxonomy, grouped by turn
  // The active run id comes from /api/emulator/status (App passes it in).
  import { gate, GATE_INDEX } from '../lib/gates.js'
  import { usd, dur } from '../lib/format.js'
  import JsonTree from './JsonTree.svelte'
  import TraceFeed from './TraceFeed.svelte'
  import * as api from '../lib/api.js'

  // `run` = the active RunSummary (for model + kind badge); `activeRunId` = the
  // run-dir id to open the live streams against. Either may be null (no run).
  let { run = null, activeRunId = null, onnew, onback } = $props()

  // ── live state, populated by the event/screen sockets ──
  let stats = $state({ turns: 0, cost: 0, input_tokens: 0, output_tokens: 0 })
  let memory = $state({})
  let task = $state(null)              // {title, description, success} | null
  let ladder = $state([])              // [{id, name, deadline_turn, group?}]
  let enforce = $state(false)
  let stamps = $state({})              // {checkpoint_id: turn} latched
  let currentTurn = $state(0)
  let feed = $state([])                // [{kind:'master'|'turn', ...}] chronological
  let eventCount = $state(0)
  let screenConnected = $state(false)
  let eventsConnected = $state(false)
  let screenUrl = $state(null)
  let startedAtMs = $state(null)
  let elapsedS = $state(0)

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

  // ── derivations for the gate HUD ──
  // Gates only apply to OFFICIAL runs. When we KNOW the run is casual, hide every
  // gate reference; null run (early) is treated as non-casual so we don't crash.
  const showGates = $derived(run?.kind !== 'casual')
  const reached = $derived(Object.keys(stamps).length)
  const totalGates = $derived(ladder.length || 0)
  // next gate = first ladder rung NOT yet stamped
  const nextGate = $derived(ladder.find((g) => !(g.id in stamps)) || null)
  const deadline = $derived(nextGate?.deadline_turn ?? null)
  const turnsLeft = $derived(deadline != null ? deadline - currentTurn : null)
  const budgetPct = $derived(
    deadline ? Math.min(100, (currentTurn / deadline) * 100) : 0
  )
  const tone = $derived(
    turnsLeft == null ? 'ok' : turnsLeft < 25 ? 'red' : turnsLeft < 60 ? 'amber' : 'ok'
  )

  // mini gate ladder window around the current rung (done/current/upcoming)
  const ladderWindow = $derived(() => {
    const rows = ladder.map((g) => ({
      ...g,
      status: g.id in stamps ? 'done' : g.id === nextGate?.id ? 'current' : 'upcoming',
      turn: g.id in stamps ? stamps[g.id] : null,
    }))
    return rows.slice(Math.max(0, reached - 2), reached + 3)
  })

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
    if (!r || typeof r !== 'object') return { verdict: '🟡 Returned to TaskMaster', tone: 'partial', summary: '' }
    const raw = (r.self_assessment ?? '').toString().trim()
    const lc = raw.toLowerCase()
    let verdict, tone
    if (/^succe/.test(lc) || lc === 'true' || lc === 'complete') { verdict = '✅ Task complete'; tone = 'ok' }
    else if (/^fail/.test(lc) || lc === 'false' || /not (complete|done|succeed)/.test(lc)) { verdict = '❌ Task not complete'; tone = 'no' }
    else if (/^partial/.test(lc) || /partly/.test(lc)) { verdict = '🟡 Partial'; tone = 'partial' }
    else if (raw) { verdict = raw; tone = 'partial' }      // free-text self-assessment → use verbatim
    else { verdict = '🟡 Returned to TaskMaster'; tone = 'partial' }
    return { verdict, tone, summary: r.task_summary || '' }
  }

  // Turn raw pydantic-ai trace messages (master) into compact displayable steps,
  // mirroring the player turn box shapes (thinking / tool call / tool response).
  function parseMasterMessages(messages) {
    if (!Array.isArray(messages)) return []
    const steps = []
    for (const m of messages) {
      if (!m || typeof m !== 'object') continue
      const role = m.role
      if (role === 'thinking') {
        if (m.content) steps.push({ k: 'thinking', t: String(m.content) })
      } else if (role === 'tool_call') {
        // The TaskMaster's structured output is emitted as a `final_result` tool
        // call whose args are the decision JSON. Do NOT dump that raw JSON — the
        // task title/plan/success are already shown as the card's rows above.
        // Surface only the fields not shown elsewhere: the reasoning and any
        // rating of the previous task.
        if (m.tool_name === 'final_result') {
          const a = parseArgs(m.args)
          if (a && typeof a === 'object') {
            if (a.reasoning) steps.push({ k: 'reasoning', t: String(a.reasoning) })
            const rating = a.rating_of_previous_task
            if (rating != null && String(rating).trim()) steps.push({ k: 'rating', t: String(rating) })
          }
          continue
        }
        const args = parseArgs(m.args)
        const argStr = args == null ? '' : (typeof args === 'string' ? args : JSON.stringify(args))
        steps.push({ k: 'tool', name: m.tool_name || 'tool', args: argStr, resp: null })
      } else if (role === 'tool_result') {
        if (m.tool_name === 'final_result') continue   // "Final result processed." — noise
        const c = typeof m.content === 'string' ? m.content : JSON.stringify(m.content)
        steps.push({ k: 'tool', name: '↳ response', args: '', resp: c })
      }
      // system / user / assistant carry the prompt — not shown live.
    }
    return steps
  }

  function pushBox(turn, box) {
    const t = turn ?? currentTurn
    if (!turnBoxes.has(t)) turnBoxes.set(t, [])
    turnBoxes.get(t).push(box)
    rebuildFeed()
  }
  function rebuildFeed() {
    const turns = [...turnBoxes.keys()].sort((a, b) => a - b)
    // master cards keyed by the first turn of their task, rendered just BEFORE
    // that turn's block (chronological / task order).
    const mastersByTurn = new Map()
    for (const c of masterCards.values()) {
      if (c.firstTurn != null) mastersByTurn.set(c.firstTurn, c)
    }
    const out = []
    for (const t of turns) {
      const m = mastersByTurn.get(t)
      if (m) out.push({ kind: 'master', id: 'm' + m.taskIndex, ...m })
      out.push({ kind: 'turn', id: 't' + t, turn: t, boxes: turnBoxes.get(t) })
    }
    feed = out
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
    // NB: input images are intentionally NOT surfaced in the live feed (B9.7);
    // they live only in the history Report.
  }

  // map one raw event → zero or more trace boxes (parity with static/index.html)
  function ingestEvent(evt) {
    eventCount += 1
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
      rebuildFeed()
      return
    }
    if (t === 'task_master_trace') {
      // arrives BEFORE task_started{N}; buffer the trace half by task_index.
      const idx = evt.task_index
      masterTraces.set(idx, {
        model_used: evt.model_used,
        cost_usd: evt.cost_usd,
        steps: parseMasterMessages(evt.messages),
      })
      buildMasterCard(idx)
      rebuildFeed()
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
            model: '', cost: null, steps: [],
            title: task.title, description: task.description, success: task.success,
          })
        } else {
          const c = masterCards.get(idx)
          c.title = task.title; c.description = task.description; c.success = task.success
        }
        buildMasterCard(idx)
        pendingTaskIndex = idx
      }
      rebuildFeed()
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
        if (lines.length) pushBox(evt.turn, { k: 'output', ok, t: lines.join(' — ') })
        if (args.inputs && args.inputs.length) {
          const inputs = Array.isArray(args.inputs) ? args.inputs.join(' ') : args.inputs
          pushBox(evt.turn, { k: 'action', t: inputs })
        }
        if (args.return_to_taskmaster) pushBox(evt.turn, { k: 'handback', ...fmtHandback(args.return_to_taskmaster) })
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
    if (t === 'output_retry') { pushBox(evt.turn, { k: 'error', t: evt.content || 'Validation failed — retrying.' }); return }
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
    stamps = {}
    currentTurn = 0
    eventCount = 0
    turnBoxes = new Map()
    masterTraces = new Map()
    masterCards = new Map()
    pendingTaskIndex = null
    feed = []
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
    const tick = () => { if (startedAtMs) elapsedS = Math.max(0, (Date.now() - startedAtMs) / 1000) }
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
      <button class="btn ghost" onclick={() => onback()}>← Back to leaderboard</button>
    </div>
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}>← Leaderboard</button>
      <span class="pill"><span class="dot live"></span> live</span>
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
        <div class="stats" style={`grid-template-columns: repeat(${showGates ? 5 : 4}, 1fr)`}>
          <div class="stat"><span class="sl">Turn</span><span class="sv tnum">{stats.turns || currentTurn}</span></div>
          <div class="stat"><span class="sl">Cost</span><span class="sv tnum">{usd(stats.cost)}</span></div>
          <div class="stat"><span class="sl">Tokens</span><span class="sv tnum">{tokensLabel}<span class="su">/{tokensSub}</span></span></div>
          <div class="stat"><span class="sl">Elapsed</span><span class="sv tnum">{dur(elapsedS)}</span></div>
          {#if showGates}<div class="stat"><span class="sl">Gates</span><span class="sv tnum">{reached}/{totalGates || '—'}</span></div>{/if}
        </div>

        <div class="gba">
          {#if screenUrl}
            <img class="screen" src={screenUrl} alt="emulator screen" />
          {:else}
            <div class="ph">emulator screen<br /><span class="faint">connecting to live stream…</span></div>
          {/if}
        </div>

        <div class="panels">
          <div class="panel task">
            <div class="p-h">🧭 Current task</div>
            <div class="p-scroll">
              {#if task}
                <div class="t-title">{task.title}{#if task.status}<span class="t-done faint"> · {task.status}</span>{/if}</div>
                {#if task.description}<div class="t-lab">Description</div><p class="t-body">{task.description}</p>{/if}
                {#if task.success}<div class="t-lab">🎯 Success criteria</div><p class="t-body mono">{task.success}</p>{/if}
              {:else}
                <p class="t-body faint">No task yet — waiting for the first TaskMaster handoff (or this run isn't TaskMaster-scaffolded).</p>
              {/if}
            </div>
          </div>
          <div class="panel mem">
            <div class="p-h">🧠 Memory dictionary</div>
            <div class="p-scroll">
              {#if memory && Object.keys(memory).length}<JsonTree data={memory} />{:else}<p class="t-body faint">(empty)</p>{/if}
            </div>
          </div>
        </div>

        {#if showGates}
        <div class="hud {tone}">
          <div class="hud-top">
            <span class="hl">Next gate</span>
            <span class="hv">{nextGate?.name ?? '— all gates reached'}</span>
            {#if deadline != null}
              <span class="hv-left tnum">{turnsLeft} turns left · limit T{deadline}</span>
            {/if}
          </div>
          {#if deadline != null}
            <div class="hud-track"><span class="hud-fill" style={`width:${budgetPct}%`}></span></div>
          {/if}
          <div class="ladder">
            {#each ladderWindow() as g}
              <div class="lg {g.status}">
                <span class="lg-i">{g.status === 'done' ? '✓' : g.status === 'current' ? '▶' : '·'}</span>
                <span class="lg-n">{g.name}</span>
                <span class="lg-t tnum faint">{g.turn != null ? 'T' + g.turn : g.deadline_turn != null ? 'T' + g.deadline_turn : '—'}</span>
              </div>
            {/each}
          </div>
        </div>
        {/if}
      </div>

      <!-- side: live trace feed (own component) -->
      <TraceFeed turns={feed} />
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
    background: #0d0f14;
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
  .gba { flex: 1; min-height: 0; aspect-ratio: 240/160; background: #11141b; border-radius: var(--radius); display: flex; align-items: center; justify-content: center; box-shadow: var(--shadow-lg); overflow: hidden; }
  .screen { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; }
  .ph { color: #7b8696; text-align: center; font-size: 16px; line-height: 1.7; }

  .hud { flex: none; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; box-shadow: var(--shadow); }
  .hud.amber { border-color: #f0d9a0; } .hud.red { border-color: #f0c5c5; }
  .hud-top { display: flex; align-items: baseline; gap: 12px; }
  .hl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); font-weight: 700; }
  .hv { font-size: 14px; font-weight: 700; }
  .hv-left { margin-left: auto; font-size: 12.5px; color: var(--muted); font-weight: 650; }
  .hud-track { height: 7px; background: #eef1f5; border-radius: 4px; overflow: hidden; margin: 10px 0; }
  .hud-fill { display: block; height: 100%; background: var(--accent); }
  .hud.amber .hud-fill { background: var(--amber); } .hud.red .hud-fill { background: var(--red); }
  .ladder { display: flex; flex-direction: column; gap: 1px; border-top: 1px solid var(--border-2); padding-top: 8px; }
  .lg { display: grid; grid-template-columns: 18px 1fr auto; gap: 8px; align-items: center; font-size: 12px; padding: 2px 0; }
  .lg-i { text-align: center; font-weight: 800; color: var(--faint); }
  .lg.done .lg-i { color: var(--green); }
  .lg.current { font-weight: 700; }
  .lg.current .lg-i { color: var(--accent); }
  .lg.upcoming { color: var(--muted); }
  .lg-t { font-size: 11px; }

  .stats { flex: none; display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; }
  .stat { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 11px 12px; box-shadow: var(--shadow); }
  .sl { display: block; font-size: 9.5px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .sv { font-size: 18px; font-weight: 750; }
  .su { font-size: 11px; color: var(--muted); font-weight: 600; }

  /* Current task gets more room than the memory dictionary — 60/40 split. */
  .panels { flex: none; display: grid; grid-template-columns: 3fr 2fr; gap: 14px; align-items: stretch; }
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
