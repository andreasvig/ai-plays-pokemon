<script>
  // Native restyled report (Plan §P6 decision): meta KPIs + the REAL benchmark
  // gate scorecard (from referee.gates) + the FULL master→player trace (from
  // /api/runs/{id}/trace), restyled in the SPA's visual identity but at PARITY
  // with the old report.py output — system prompts, per-step thinking, tool
  // calls + responses, master strategy + verdict, player handback. (Round 9 E.)
  // A "View full HTML report" link opens /api/runs/{id}/report — the exhaustive
  // event-level report.py output — preserving it without iframing it as primary.
  import { GATES } from '../lib/gates.js'
  import { usd, dur, perTurn, dateShort, actionEmoji } from '../lib/format.js'
  import { mdToHtml } from '../lib/md.js'
  import * as api from '../lib/api.js'
  let { run = null, onback, oncontinue } = $props()

  let summary = $state(null)     // raw nested run_summary.json (KPIs + referee.gates)
  let trace = $state(null)       // two-level master→player trace (B1/B2)
  let loading = $state(false)
  let loadError = $state(null)

  // fetch the nested summary (KPIs + gate scorecard) AND the two-level trace
  // (master-as-top-level task tree + images) whenever the run changes.
  $effect(() => {
    const id = run?.runId
    summary = null; trace = null; loadError = null
    if (!id) return
    loading = true
    Promise.all([
      api.fetchRunSummary(id).then((s) => { summary = s }),
      api.fetchRunTrace(id).then((t) => { trace = t }).catch(() => { trace = null }),
    ])
      .catch((e) => { loadError = String(e) })
      .finally(() => { loading = false })
  })

  // real gate scorecard from referee.gates (falls back to the GATES ladder when
  // a run has no referee block — e.g. a casual run); each gate carries
  // {id, name, deadline_turn, turn, status} from the referee.
  const gates = $derived(summary?.referee?.gates ?? [])
  const reachedN = $derived(gates.filter((g) => g.status === 'done').length)
  const totalN = $derived(gates.length || GATES.length)
  const termination = $derived(summary?.referee?.termination_reason ?? null)

  const verdict = $derived(() => {
    if (!summary) return ''
    if (totalN > 0 && reachedN >= totalN) return '🏁 All gates cleared — full ladder'
    if (termination && termination.startsWith('missed_gate:')) {
      const missed = gates.find((g) => g.status === 'missed' || g.status === 'failed')
        || gates.find((g) => g.id === termination.split(':')[1])
      return `✗ Failed at ${missed?.name ?? termination.split(':')[1]}${missed?.deadline_turn != null ? ` (limit T${missed.deadline_turn})` : ''}`
    }
    const furthest = summary?.referee?.furthest
    const fg = gates.find((g) => g.id === furthest)
    return fg ? `Reached ${fg.name}` : `${reachedN}/${totalN} gates`
  })
  const stIcon = { done: '✓', missed: '✗', failed: '✗', pending: '·', unmet: '·' }

  // two-level master→player trace (B1). Each group is a master/TaskMaster node
  // with its objective + rating + the screenshots it saw, nesting the player
  // turns it spawned. Casual / no-TaskMaster runs come back as a single
  // implicit group (task_index:null, empty master_model, no master images).
  const tasks = $derived(trace?.tasks ?? [])
  const hasTasks = $derived(trace?.has_tasks === true)

  // E9.1/E9.3: start with ALL groups + ALL turns COLLAPSED (no auto-open). The
  // deep trace sub-sections (system prompt / input) are also default-collapsed
  // via native <details>, so the report is navigable, not a wall of text.
  let openGroups = $state(new Set())
  let openTurns = $state(new Set())   // keys: `${groupKey}:${turn}`
  function groupKey(g, i) { return g.task_index != null ? `t${g.task_index}` : `g${i}` }
  function toggleGroup(k) {
    const s = new Set(openGroups)
    s.has(k) ? s.delete(k) : s.add(k)
    openGroups = s
  }
  function toggleTurn(k) {
    const s = new Set(openTurns)
    s.has(k) ? s.delete(k) : s.add(k)
    openTurns = s
  }

  const reportUrl = $derived(run?.runId ? `/api/runs/${encodeURIComponent(run.runId)}/report` : '#')

  // --- Trace step helpers (parity with report.py _render_trace_html) ---------
  // `args` may be a dict OR a JSON string OR free text. Pretty-print dicts as
  // 2-space JSON; show strings as-is. Used for tool-call args + final_result.
  function fmtArgs(args) {
    if (args == null) return ''
    if (typeof args === 'string') {
      const t = args.trim()
      if (t.startsWith('{') || t.startsWith('[')) {
        try { return JSON.stringify(JSON.parse(t), null, 2) } catch { return args }
      }
      return args
    }
    try { return JSON.stringify(args, null, 2) } catch { return String(args) }
  }
  // Parse args to an object when possible (for final_result decision fields).
  function parseArgs(args) {
    if (args && typeof args === 'object') return args
    if (typeof args === 'string') {
      const t = args.trim()
      if (t.startsWith('{') || t.startsWith('[')) {
        try { return JSON.parse(t) } catch { return null }
      }
    }
    return null
  }

  // E9.5: map a self_assessment string → labeled verdict (mirrors Phase 2
  // Spectate.fmtHandback exactly). succeeded→✅ / failed→❌ / partial→🟡 /
  // free-text→verbatim.
  function fmtVerdict(rawAssessment) {
    const raw = (rawAssessment ?? '').toString().trim()
    const lc = raw.toLowerCase()
    if (/^succe/.test(lc) || lc === 'true' || lc === 'complete') return { label: '✅ Task complete', tone: 'ok' }
    if (/^fail/.test(lc) || lc === 'false' || /not (complete|done|succeed)/.test(lc)) return { label: '❌ Task not complete', tone: 'no' }
    if (/^partial/.test(lc) || /partly/.test(lc)) return { label: '🟡 Partial', tone: 'partial' }
    if (raw) return { label: raw, tone: 'partial' }   // free-text → verbatim
    return { label: '🟡 Returned to TaskMaster', tone: 'partial' }
  }

  // The master's final_result carries `rating_of_previous_task` (its chrono-honest
  // verdict on the PREVIOUS task). Pull it out for the master output card.
  function masterRatingOfPrevious(mt) {
    for (const s of mt?.steps ?? []) {
      if (s.type === 'final_result') {
        const p = parseArgs(s.args)
        if (p && typeof p.rating_of_previous_task === 'object' && p.rating_of_previous_task) {
          return p.rating_of_previous_task
        }
      }
    }
    return null
  }
  const ratingIcon = { succeeded: '✅', failed: '❌', partial: '🟡' }

  // Task header badge (parity with report.py _task_badge_html). No rating yet =
  // the CURRENT (in-progress) task.
  function taskBadge(g) {
    const status = (g.rating?.status || '').toLowerCase()
    if (!g.rating) return { icon: '⏳', label: 'current', cls: 'badge-current' }
    if (status === 'succeeded') return { icon: '✅', label: 'succeeded', cls: 'badge-succeeded' }
    if (status === 'failed') return { icon: '❌', label: 'failed', cls: 'badge-failed' }
    if (status === 'partial') return { icon: '🟡', label: 'partial', cls: 'badge-partial' }
    return { icon: '➖', label: status || '?', cls: 'badge-other' }
  }

  function turnUsage(t) {
    const cost = t.cost_usd != null ? `$${Number(t.cost_usd).toFixed(4)}` : ''
    const tin = t.request_tokens != null ? Number(t.request_tokens) : null
    const tout = t.response_tokens != null ? Number(t.response_tokens) : null
    const tok = (tin != null || tout != null) ? `${tin ?? '?'}→${tout ?? '?'} tok` : ''
    return [cost, tok].filter(Boolean).join(' · ')
  }
  function turnGrade(succeeded) {
    if (succeeded === true) return '✅ succeeded'
    if (succeeded === false) return '❌ failed'
    return '➖ n/a (first turn)'
  }
  function shotUrl(t) {
    return `/api/runs/${encodeURIComponent(run.runId)}/screenshots/${t.screenshot}`
  }
  function nToolCalls(steps) {
    return (steps ?? []).filter((s) => s.type === 'tool_call').length
  }
</script>

<section class="wrap">
  {#if !run}
    <div class="empty"><p>No run selected.</p><button class="btn" onclick={() => onback()}>← Back</button></div>
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}>← Back</button>
      <span class="badge {run.kind}">{run.kind}</span>
      <a class="btn ghost full-report" href={reportUrl} target="_blank" rel="noopener">⤢ View full HTML report</a>
      <button class="btn cont" disabled={run.status === 'running'} onclick={() => oncontinue(run)}>⟳ Continue this run</button>
    </div>

    <!-- meta bar -->
    <header class="rhead">
      <h2 class="mono">{run.model}</h2>
      <div class="meta faint">
        <span class="mono">{run.slug}</span> · {dateShort(run.startedAt)} · config <span class="mono">{run.config}</span>
        {#if run.continuedFrom}· continued from <span class="mono">{run.continuedFrom}</span>{/if}
      </div>
      <div class="kpis">
        <div class="k"><span class="kl">Completion</span><span class="kv" class:full={run.completion >= 100}>{run.completion}%</span></div>
        <div class="k"><span class="kl">Turns</span><span class="kv tnum">{run.turns}{#if run.maxTurns}<span class="faint"> / {run.maxTurns}</span>{/if}</span></div>
        <div class="k"><span class="kl">Total cost</span><span class="kv tnum">{usd(run.totalCostUsd)}</span></div>
        <div class="k"><span class="kl">Cost / turn</span><span class="kv tnum">{usd(run.avgCostPerTurn)}</span></div>
        <div class="k"><span class="kl">Duration</span><span class="kv tnum">{dur(run.durationS)}</span></div>
        <div class="k"><span class="kl">Sec / turn</span><span class="kv tnum">{perTurn(run.avgSPerTurn)}</span></div>
      </div>
    </header>

    {#if loading}
      <p class="faint load">Loading run details…</p>
    {:else if loadError}
      <p class="faint load">Could not load run details ({loadError}). The KPIs above are from the index.</p>
    {/if}

    <!-- benchmark gate scorecard (real, from referee.gates) -->
    {#if gates.length}
      <section class="score">
        <div class="score-head">
          <h3>🏁 Benchmark gates</h3>
          <span class="cleared">{reachedN}/{totalN} cleared</span>
          <span class="verdict" class:fail={termination && termination.startsWith('missed_gate:')} class:win={reachedN >= totalN && totalN > 0}>{verdict()}</span>
        </div>
        <div class="gtable">
          {#each gates as g (g.id)}
            <div class="grow {g.status}" class:grp={g.group}>
              <span class="gst {g.status}">{stIcon[g.status] ?? '·'}</span>
              <span class="gname">{g.name}</span>
              <span class="gturn tnum">{g.turn != null ? 'T' + g.turn : '—'}</span>
              <span class="glim tnum faint">{g.deadline_turn != null ? 'T' + g.deadline_turn : '—'}</span>
            </div>
          {/each}
        </div>
      </section>
    {/if}

    <!-- FULL two-level master→player trace (B1 + Round 9 E parity) -->
    {#if tasks.length}
      <section class="trace">
        <h3>
          {#if hasTasks}TaskMaster trace{:else}Turn-by-turn{/if}
          <span class="faint">({trace.turn_count} turns{#if hasTasks} · {trace.task_count} tasks{/if})</span>
        </h3>
        {#each tasks as g, gi (groupKey(g, gi))}
          {@const gk = groupKey(g, gi)}
          {@const gOpen = openGroups.has(gk)}
          {@const badge = taskBadge(g)}
          {@const mt = g.master_trace}
          {@const rop = mt ? masterRatingOfPrevious(mt) : null}
          {#if hasTasks && g.task_index != null}
            <!-- master/TaskMaster node = group header (amber strategy card) -->
            <div class="group">
              <div class="master-block">
                <button class="master-head" onclick={() => toggleGroup(gk)}>
                  <span class="arr amber">{gOpen ? '▾' : '▸'}</span>
                  <span class="m-title">🧭 Task {g.task_index} (Master){#if g.title}: {g.title}{/if}</span>
                  <span class="task-badge {badge.cls}">{badge.icon} {badge.label}</span>
                  <span class="m-meta tnum">{g.turns?.length ?? 0} turn{(g.turns?.length ?? 0) === 1 ? '' : 's'}</span>
                  {#if g.master_cost != null}<span class="m-meta mono">{usd(g.master_cost)}</span>{/if}
                </button>
                {#if gOpen}
                  <div class="master-body">
                    <!-- master label + FULL master trace (chronological: trace then output) -->
                    <div class="master-label">🧭 TaskMaster</div>

                    {#if mt}
                      <div class="trace-section">
                        <div class="trace-header">TaskMaster trace ({nToolCalls(mt.steps)} tool call{nToolCalls(mt.steps) === 1 ? '' : 's'})</div>
                        <div class="trace-container">
                          {#if mt.system_prompt}
                            <details class="trace-step trace-system">
                              <summary><span class="step-label">System Prompt</span></summary>
                              <pre class="step-content">{mt.system_prompt}</pre>
                            </details>
                          {/if}
                          {#if mt.user_input || g.master_input_images?.length}
                            <details class="trace-step trace-input">
                              <summary>
                                <span class="step-label">Input</span>
                                <span class="step-preview">{(mt.user_input || '').slice(0, 100).replace(/\n/g, ' ')}…</span>
                              </summary>
                              {#if g.master_input_images?.length}
                                <div class="master-thumbs">
                                  {#each g.master_input_images as im}
                                    <figure class="master-thumb">
                                      <img src={im.data_url} alt={im.label || ''} />
                                      {#if im.label}<figcaption>{im.label}</figcaption>{/if}
                                    </figure>
                                  {/each}
                                </div>
                              {/if}
                              {#if mt.user_input}<pre class="step-content">{mt.user_input}</pre>{/if}
                            </details>
                          {/if}
                          {#each mt.steps ?? [] as step}
                            {#if step.type === 'tool_call'}
                              <details class="trace-step trace-tool">
                                <summary>
                                  <span class="step-label">Tool</span>
                                  <span class="step-tool-name mono">{step.tool_name}</span>
                                </summary>
                                <div class="step-body">
                                  {#if step.thinking}
                                    <div class="step-thinking"><div class="sub-label">Thinking</div><div class="md">{@html mdToHtml(step.thinking)}</div></div>
                                  {/if}
                                  <div class="step-call"><div class="sub-label">Call</div><pre class="mono">{step.tool_name}({fmtArgs(step.args)})</pre></div>
                                  {#if step.response}
                                    <div class="step-response"><div class="sub-label">Response</div><pre>{step.response}</pre></div>
                                  {/if}
                                </div>
                              </details>
                            {:else if step.type === 'final_result'}
                              <!-- master's final_result = the TaskSpec, surfaced as the output card
                                   below; keep only its planning thinking here (parity skip_final_result). -->
                              {#if step.thinking}
                                <details class="trace-step trace-thinking-only">
                                  <summary><span class="step-label">Thinking</span></summary>
                                  <div class="step-content md">{@html mdToHtml(step.thinking)}</div>
                                </details>
                              {/if}
                            {:else if step.type === 'thinking_only'}
                              <details class="trace-step trace-thinking-only">
                                <summary><span class="step-label">Thinking</span></summary>
                                <div class="step-content md">{@html mdToHtml(step.thinking)}</div>
                              </details>
                            {:else if step.type === 'retry'}
                              <div class="trace-step trace-retry"><span class="step-label">Retry</span><pre>{fmtArgs(step.args)}</pre></div>
                            {/if}
                          {/each}
                        </div>
                      </div>
                    {/if}

                    <!-- master OUTPUT card at the BOTTOM (chronological): the task it
                         set + its rating of the PREVIOUS task. -->
                    <div class="master-verdict">
                      {#if g.title}<div class="dec-row"><span class="dec-lab">📋 Task</span><span class="dec-val">{g.title}</span></div>{/if}
                      {#if g.description}<div class="dec-row"><span class="dec-lab">🧭 Plan</span><div class="dec-desc">{g.description}</div></div>{/if}
                      {#if g.success_criteria}<div class="dec-row"><span class="dec-lab">🎯 Success criteria</span><span class="dec-val">{g.success_criteria}</span></div>{/if}
                      {#if rop}
                        <div class="dec-row"><span class="dec-lab">⚖️ Rating of the previous task</span><span class="dec-val">{ratingIcon[(rop.status || '').toLowerCase()] ?? '➖'} {rop.status}</span></div>
                        {#if rop.reasoning}<div class="dec-row"><span class="dec-lab">Reasoning</span><div class="dec-desc">{rop.reasoning}</div></div>{/if}
                      {:else}
                        <div class="dec-row faint"><span class="dec-val">First task — no previous task to rate.</span></div>
                      {/if}
                    </div>

                    <!-- E9.5/E9.6: this task's own verdict (the player's handback). Backfilled
                         onto the task; null on the CURRENT in-progress task (shown as the badge). -->
                    {#if g.player_self_assessment || g.player_task_summary}
                      {@const v = fmtVerdict(g.player_self_assessment)}
                      <div class="handback {v.tone}">
                        <span class="hb-verdict">↩️ {v.label}</span>
                        {#if g.player_task_summary}<span class="hb-summary">{g.player_task_summary}</span>{/if}
                      </div>
                    {:else if !g.rating}
                      <div class="handback partial">
                        <span class="hb-verdict">⏳ Current task — in progress (no verdict yet)</span>
                      </div>
                    {/if}
                  </div>
                {/if}
              </div>
            </div>
          {:else if !hasTasks}
            <div class="casual-head faint">Casual run — no TaskMaster</div>
          {/if}

          <!-- nested player turns (collapsible) -->
          {#if !hasTasks || g.task_index == null || gOpen}
            <div class="turns" class:nested={hasTasks && g.task_index != null}>
              {#each g.turns ?? [] as t (t.turn)}
                {@const tk = `${gk}:${t.turn}`}
                {@const tOpen = openTurns.has(tk)}
                {@const ptr = t.trace}
                <div class="turn" class:open={tOpen}>
                  <button class="thead" onclick={() => toggleTurn(tk)}>
                    <span class="arr">{tOpen ? '▾' : '▸'}</span>
                    <span class="tn mono">Turn {t.turn}</span>
                    <span class="tact">{actionEmoji(t.action)}</span>
                    <span class="tsum faint">{t.reasoning}</span>
                    <span class="tuse faint mono">{turnUsage(t)}</span>
                  </button>
                  {#if tOpen}
                    <div class="tbody">
                      <!-- input → trace → output(decision)/screenshot at the BOTTOM (chronological) -->
                      {#if ptr}
                        <div class="trace-section">
                          <div class="trace-header">Trace ({nToolCalls(ptr.steps)} tool call{nToolCalls(ptr.steps) === 1 ? '' : 's'})</div>
                          <div class="trace-container">
                            {#if ptr.system_prompt}
                              <details class="trace-step trace-system">
                                <summary><span class="step-label">System Prompt</span></summary>
                                <pre class="step-content">{ptr.system_prompt}</pre>
                              </details>
                            {/if}
                            {#if ptr.user_input}
                              <details class="trace-step trace-input">
                                <summary>
                                  <span class="step-label">Input</span>
                                  <span class="step-preview">{(ptr.user_input || '').slice(0, 100).replace(/\n/g, ' ')}…</span>
                                </summary>
                                <pre class="step-content">{ptr.user_input}</pre>
                              </details>
                            {/if}
                            {#each ptr.steps ?? [] as step}
                              {#if step.type === 'tool_call'}
                                <details class="trace-step trace-tool">
                                  <summary>
                                    <span class="step-label">Tool</span>
                                    <span class="step-tool-name mono">{step.tool_name}</span>
                                  </summary>
                                  <div class="step-body">
                                    {#if step.thinking}
                                      <div class="step-thinking"><div class="sub-label">Thinking</div><div class="md">{@html mdToHtml(step.thinking)}</div></div>
                                    {/if}
                                    <div class="step-call"><div class="sub-label">Call</div><pre class="mono">{step.tool_name}({fmtArgs(step.args)})</pre></div>
                                    {#if step.response}
                                      <div class="step-response"><div class="sub-label">Response</div><pre>{step.response}</pre></div>
                                    {/if}
                                  </div>
                                </details>
                              {:else if step.type === 'final_result'}
                                <!-- player's final_result = the DECISION for this turn -->
                                {@const p = parseArgs(step.args)}
                                <details class="trace-step trace-output" open>
                                  <summary>
                                    <span class="step-label">Output</span>
                                    <span class="step-action-code">{p ? actionEmoji(p.inputs ?? '') : ''}</span>
                                  </summary>
                                  <div class="step-body">
                                    {#if step.thinking}
                                      <div class="step-thinking"><div class="sub-label">Thinking</div><div class="md">{@html mdToHtml(step.thinking)}</div></div>
                                    {/if}
                                    {#if p && (p.reasoning != null || p.last_turn_succeeded !== undefined)}
                                      <div class="step-decision">
                                        <div class="dec-row"><span class="dec-lab">Last turn</span><span class="dec-val">{turnGrade(p.last_turn_succeeded)}</span></div>
                                        {#if p.reasoning}<div class="dec-row"><span class="dec-lab">Reasoning</span><div class="dec-desc">{p.reasoning}</div></div>{/if}
                                        {#if p.return_to_taskmaster}
                                          {@const hb = fmtVerdict(p.return_to_taskmaster.self_assessment)}
                                          <div class="dec-row"><span class="dec-lab">↩️ Return to TaskMaster</span><span class="dec-val">{hb.label}{#if p.return_to_taskmaster.task_summary} — {p.return_to_taskmaster.task_summary}{/if}</span></div>
                                        {:else}
                                          <div class="dec-row"><span class="dec-lab">Action</span><span class="dec-val">{actionEmoji(p.inputs ?? '')}</span></div>
                                        {/if}
                                        {#if p.memory_updates && String(p.memory_updates).trim().toLowerCase() !== 'none'}
                                          <div class="dec-row"><span class="dec-lab">Memory update</span><pre class="dec-mem">{fmtArgs(p.memory_updates)}</pre></div>
                                        {/if}
                                      </div>
                                    {:else}
                                      <pre class="mono">{fmtArgs(step.args)}</pre>
                                    {/if}
                                  </div>
                                </details>
                              {:else if step.type === 'thinking_only'}
                                <details class="trace-step trace-thinking-only">
                                  <summary><span class="step-label">Thinking</span></summary>
                                  <div class="step-content md">{@html mdToHtml(step.thinking)}</div>
                                </details>
                              {:else if step.type === 'retry'}
                                <div class="trace-step trace-retry"><span class="step-label">Retry</span><pre>{fmtArgs(step.args)}</pre></div>
                              {/if}
                            {/each}
                          </div>
                        </div>
                      {/if}

                      <!-- decision summary + screenshot at the BOTTOM (chronological) -->
                      <div class="exp">
                        <div class="exp-row"><span class="el">Last turn</span><span class="ev">{turnGrade(t.last_turn_succeeded)}</span></div>
                        <div class="exp-row"><span class="el">Reasoning</span><span class="ev">{t.reasoning}</span></div>
                        <div class="exp-row"><span class="el">Action</span><span class="ev">{actionEmoji(t.action)}</span></div>
                        {#if t.screenshot}
                          <div class="exp-row"><span class="el">Screenshot</span>
                            <img class="turn-shot" src={shotUrl(t)} alt={`Turn ${t.turn} screenshot`} loading="lazy" />
                          </div>
                        {/if}
                      </div>
                    </div>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        {/each}
      </section>
    {/if}
  {/if}
</section>

<style>
  .wrap { max-width: 880px; margin: 0 auto; padding: 24px; }
  .empty { text-align: center; padding: 80px 0; }
  .bar { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .bar .full-report { margin-left: auto; text-decoration: none; }
  .load { margin: 12px 2px; font-size: 13px; }

  .rhead { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 22px; box-shadow: var(--shadow); }
  h2 { font-size: 20px; font-weight: 700; margin: 0 0 4px; }
  .meta { font-size: 12.5px; margin-bottom: 18px; }
  .kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px 16px; }
  .k { display: flex; flex-direction: column; gap: 2px; }
  .kl { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .kv { font-size: 16px; font-weight: 700; }
  .kv.full { color: var(--green); }

  .score { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); margin-top: 16px; }
  .score-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  h3 { font-size: 15px; font-weight: 750; margin: 0; }
  .cleared { font-size: 12px; font-weight: 650; color: var(--muted); }
  .verdict { margin-left: auto; font-size: 12.5px; font-weight: 700; color: var(--muted); }
  .verdict.fail { color: var(--red); }
  .verdict.win { color: var(--green); }
  .gtable { display: flex; flex-direction: column; }
  .grow { display: grid; grid-template-columns: 22px 1fr 60px 50px; gap: 10px; align-items: center; padding: 6px 8px; border-radius: 6px; font-size: 12.5px; }
  .grow.grp { padding-left: 18px; }
  .grow.done { background: var(--green-soft); }
  .grow.missed, .grow.failed { background: var(--red-soft); }
  .gst { text-align: center; font-weight: 800; color: var(--faint); }
  .gst.done { color: var(--green); } .gst.missed, .gst.failed { color: var(--red); }
  .gname { font-weight: 550; }
  .gturn { text-align: right; font-weight: 650; }
  .glim { text-align: right; font-size: 11.5px; }

  .trace { margin-top: 24px; }
  .trace h3 .faint { font-weight: 500; font-size: 12px; }

  /* master/TaskMaster node = group header — amber "strategy" layer */
  .group { margin-bottom: 8px; }
  .master-block { background: var(--surface); border: 1px solid #f0d9a0; border-left: 3px solid #ffce54; border-radius: var(--radius-sm); box-shadow: var(--shadow); overflow: hidden; }
  .master-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 13px; border: none; text-align: left; font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #b8860b; background: rgba(255, 206, 84, 0.12); cursor: pointer; }
  .master-head:hover { background: rgba(255, 206, 84, 0.2); }
  .master-block:has(.master-body) .master-head { border-bottom: 1px solid #f0d9a0; }
  .arr.amber { color: #c79a18; }
  .m-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .m-meta { font-size: 10.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted); }
  .m-meta:first-of-type { margin-left: auto; }
  .task-badge { font-size: 10.5px; font-weight: 750; text-transform: uppercase; letter-spacing: .03em; padding: 1px 8px; border-radius: 10px; background: var(--surface-2); color: var(--muted); white-space: nowrap; }
  .task-badge.badge-succeeded { background: var(--green-soft); color: var(--green); }
  .task-badge.badge-failed { background: var(--red-soft); color: var(--red); }
  .task-badge.badge-partial { background: rgba(255, 206, 84, 0.2); color: #b8860b; }
  .task-badge.badge-current { background: var(--surface-2); color: var(--muted); }

  .master-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
  .master-label { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: #c79a18; }

  /* deep trace container (master + player share these) */
  .trace-section { display: flex; flex-direction: column; gap: 6px; }
  .trace-header { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); }
  .trace-container { display: flex; flex-direction: column; gap: 5px; }
  .trace-step { background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; font-size: 12.5px; }
  .trace-step > summary { cursor: pointer; padding: 7px 10px; display: flex; align-items: center; gap: 8px; list-style: none; }
  .trace-step > summary::-webkit-details-marker { display: none; }
  .trace-step > summary::before { content: '▸'; color: var(--faint); font-size: 10px; }
  .trace-step[open] > summary::before { content: '▾'; }
  .trace-step .step-label { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--accent); }
  .trace-system .step-label, .trace-input .step-label { color: var(--muted); }
  .trace-tool .step-label { color: var(--accent); }
  .trace-output .step-label { color: var(--green); }
  .trace-thinking-only .step-label { color: var(--faint); }
  .step-tool-name { font-size: 11.5px; font-weight: 700; color: var(--ink); }
  .step-preview { font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .step-action-code { font-size: 12px; }
  .step-content, .trace-step pre { margin: 0; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 11.5px; line-height: 1.5; color: var(--muted); background: var(--surface); border-top: 1px solid var(--border); border-radius: 0 0 6px 6px; max-height: 360px; overflow: auto; }
  .step-body { padding: 6px 10px 10px; display: flex; flex-direction: column; gap: 8px; }
  .step-body pre { border: 1px solid var(--border); border-radius: 5px; }
  .sub-label { font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); margin-bottom: 3px; }
  .step-thinking .md { font-size: 12.5px; line-height: 1.55; color: var(--ink); }
  .step-thinking .md :global(strong) { color: var(--accent); }
  .step-thinking .md :global(p) { margin: 0 0 6px; }
  .step-content.md { white-space: normal; }
  .step-decision { display: flex; flex-direction: column; gap: 7px; }
  .trace-retry { padding: 7px 10px; }

  /* decision / output rows (master verdict + player decision) */
  .master-verdict { display: flex; flex-direction: column; gap: 7px; padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 7px; }
  .dec-row { font-size: 12.5px; line-height: 1.5; }
  .dec-lab { display: block; font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); margin-bottom: 2px; }
  .dec-val { color: var(--ink); }
  .dec-desc { white-space: pre-wrap; color: var(--muted); }
  .dec-mem { margin: 3px 0 0; padding: 5px 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 5px; font-size: 11px; white-space: pre-wrap; }

  /* task verdict / handback banner */
  .handback { display: flex; flex-direction: column; gap: 3px; padding: 9px 12px; border-radius: 7px; border: 1px solid var(--border); background: var(--surface-2); }
  .handback.ok { background: var(--green-soft); border-color: transparent; }
  .handback.no { background: var(--red-soft); border-color: transparent; }
  .handback.partial { background: rgba(255, 206, 84, 0.16); border-color: transparent; }
  .hb-verdict { font-size: 12.5px; font-weight: 750; }
  .hb-summary { font-size: 12px; color: var(--muted); line-height: 1.5; }

  .master-thumbs { display: flex; gap: 8px; flex-wrap: wrap; padding: 8px 10px; }
  .master-thumb { margin: 0; text-align: center; }
  .master-thumb img { width: 130px; image-rendering: pixelated; border: 1px solid var(--border); border-radius: 4px; display: block; }
  .master-thumb figcaption { font-size: 9.5px; color: #c79a18; text-transform: uppercase; letter-spacing: .04em; margin-top: 3px; }

  .casual-head { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; font-weight: 700; margin: 4px 2px 8px; }
  .turns.nested { margin: 0 0 12px 16px; padding-left: 10px; border-left: 2px solid var(--border); }
  .turn-shot { width: 240px; max-width: 100%; image-rendering: pixelated; border: 1px solid var(--border); border-radius: 6px; display: block; margin-top: 2px; }
  .turn { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); margin-bottom: 8px; overflow: hidden; }
  .thead { width: 100%; display: grid; grid-template-columns: 18px 56px auto 1fr auto; gap: 10px; align-items: center; padding: 11px 14px; border: none; background: none; text-align: left; }
  .thead:hover { background: var(--surface-2); }
  .arr { color: var(--faint); font-size: 10px; }
  .tn { font-size: 12px; font-weight: 700; color: var(--accent); }
  .tact { font-size: 13px; white-space: nowrap; }
  .tsum { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tuse { font-size: 11px; }
  .tbody { padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
  .exp { display: flex; flex-direction: column; gap: 10px; }
  .exp-row { display: flex; flex-direction: column; gap: 2px; }
  .el { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .ev { font-size: 13px; line-height: 1.5; }
  @media (max-width: 720px) { .kpis { grid-template-columns: repeat(3, 1fr); } }
</style>
