<script>
  // Native report (Plan §P6 decision): meta KPIs + the REAL benchmark gate
  // scorecard (from referee.gates) + the FULL master→player trace (from
  // /api/runs/{id}/trace), in the SPA's visual identity — system prompts,
  // per-step thinking, tool calls + responses, master strategy + verdict,
  // player handback. (Round 9 E.) This IS the run report; the old standalone
  // HTML report (src/cli/report.py) was retired in favour of this view.
  import { GATES } from '../lib/gates.js'
  import { usd, dur, perTurn, dateShort, coerceHandback } from '../lib/format.js'
  import { mdToHtml } from '../lib/md.js'
  import * as api from '../lib/api.js'
  import Action, { actionTokens } from './Action.svelte'
  import Icon from './Icon.svelte'
  import ConversationDiagnostics from './ConversationDiagnostics.svelte'
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
  // "Cleared" is the projection's OWN status set (`projection._CLEARED_STATUSES`),
  // shipped on the trace so this header cannot disagree with the Completion %
  // the index computed from the same scorecard. It counted `done` AND `auto`;
  // this counted only `done`, so an auto-cleared gate read as not reached here
  // and as reached there. The literal is the loading/no-trace fallback only.
  const clearedStatuses = $derived(new Set(trace?.cleared_gate_statuses ?? ['done', 'auto']))
  const reachedN = $derived(gates.filter((g) => clearedStatuses.has(g.status)).length)
  const totalN = $derived(gates.length || GATES.length)
  const termination = $derived(summary?.referee?.termination_reason ?? null)

  // Was anything actually GATED on the ladder? Casual and calibration runs run
  // the referee observe-only (`referee.enforce: false`) — the ladder is still
  // scored into run_summary.json, but no deadline was armed and no run was
  // stopped for missing one. Showing a Completion % and a deadline scorecard
  // for such a run asserts a score it never received; History shows a dash for
  // exactly these runs. `referee_enforced: null` means the run dir has no
  // config.json, so the run's own kind decides.
  const gatesEnforced = $derived(trace?.referee_enforced === true || run?.kind === 'official')
  // Furthest rung an observe-only run actually reached, for the note that
  // replaces the scorecard. `referee.furthest` when the referee recorded one,
  // else the last gate the projection counts as cleared.
  const furthestGate = $derived.by(() => {
    const named = gates.find((g) => g.id === summary?.referee?.furthest)
    if (named) return named.name
    const cleared = gates.filter((g) => clearedStatuses.has(g.status))
    return cleared.length ? cleared[cleared.length - 1].name : ''
  })

  // Which harness drove this run. The report rendered config-3.13 (TaskMaster),
  // config-4.0 (self-directed) and the append harness through the same view and
  // never named the one that ran; the trace derives it from the run's recorded
  // config (agent_type + task_master), not from event vocabulary. The version
  // comes off the config stem the run recorded — a non-numeric stem
  // (config-append, config-new) shows the bare harness name.
  const harnessLabel = $derived.by(() => {
    const label = trace?.harness?.label
    if (!label) return ''
    const version = (run?.config ?? '').match(/\d+(?:\.\d+)*/)
    return version ? `${label} ${version[0]}` : label
  })
  const isAppendRun = $derived(trace?.harness?.id === 'append_compact')

  const verdict = $derived(() => {
    if (!summary) return ''
    if (totalN > 0 && reachedN >= totalN) return 'All gates cleared — full ladder'
    if (termination && termination.startsWith('missed_gate:')) {
      const missed = gates.find((g) => g.status === 'missed' || g.status === 'failed')
        || gates.find((g) => g.id === termination.split(':')[1])
      return `✗ Failed at ${missed?.name ?? termination.split(':')[1]}${missed?.deadline_turn != null ? ` (limit T${missed.deadline_turn})` : ''}`
    }
    const furthest = summary?.referee?.furthest
    const fg = gates.find((g) => g.id === furthest)
    return fg ? `Furthest: ${fg.name}` : `${reachedN}/${totalN} gates`  // gate names are sentences ("Reached Route 1"), so no "Reached" prefix
  })
  // `auto` is a CLEARED status (the projection counts it), so it gets the tick —
  // a gate inside the header's "N/M cleared" must not draw a pending dot.
  const stIcon = { done: '✓', auto: '✓', missed: '✗', failed: '✗', pending: '·', unmet: '·' }

  // two-level master→player trace (B1). Each group is a master/TaskMaster node
  // with its objective + rating + the screenshots it saw, nesting the player
  // turns it spawned. Casual / no-TaskMaster runs come back as a single
  // implicit group (task_index:null, empty master_model, no master images).
  const tasks = $derived(trace?.tasks ?? [])
  const hasTasks = $derived(trace?.has_tasks === true)
  function economicsHeadline(e) {
    if (!e || e.verdict === 'unknown') return 'Unknown'
    if (e.verdict === 'no_cache_offered') return 'No cache offered'
    if (e.verdict === 'no_discount_on_hits') return 'Hits not discounted'
    if (e.verdict === 'priced_no_hits') return 'Discount unused'
    return `$${Number(e.saved_usd).toFixed(4)} saved`
  }
  function economicsNote(e) {
    if (!e || e.verdict === 'unknown') return 'Record endpoint prices to value the cache'
    const disc = e.discount_fraction == null ? null : `${Math.round(e.discount_fraction * 100)}% off cached input`
    if (e.verdict === 'no_cache_offered') return 'Endpoint lists no cache-read price and reported no hits'
    if (e.verdict === 'no_discount_on_hits') return `${cacheCount(e.cached_tokens)} cached tokens billed at the full prompt price`
    if (e.verdict === 'priced_no_hits') return `${disc} available, but no request hit the cache`
    return `${cacheCount(e.cached_tokens)} cached tokens at ${disc}`
  }
  function impliedNote(c) {
    if (c.implied_read_fraction == null) return 'Record endpoint prices to infer cache use from cost'
    const a = c.implied_agreement || {}
    const total = Object.values(a).reduce((x, y) => x + y, 0)
    if (a.provider_not_reporting === total) return `${cacheCount(c.implied_cached_tokens)} tokens inferred from cost · provider reports no cache figures`
    if (a.matches === total) return `${cacheCount(c.implied_cached_tokens)} tokens inferred from cost · agrees with the provider on every request`
    const off = total - (a.matches || 0)
    return `${cacheCount(c.implied_cached_tokens)} tokens inferred from cost · differs from the provider on ${off} of ${total} requests`
  }
  const cachePct = (n) => n == null ? 'Not reported' : `${(n * 100).toFixed(1)}%`
  const cacheCount = (n) => n == null ? '—' : Number(n).toLocaleString()
  const cacheHits = (c) => c.request_hit_fraction == null ? 'Not reported' : `${Math.round(c.request_hit_fraction * c.measured_attempts)} / ${c.measured_attempts}`
  function cacheGroup(key) {
    const parts = key.split(' / ')
    return { phase: parts[0] === 'gameplay' ? 'Gameplay' : parts[0] === 'compaction' ? 'Compaction' : parts[0],
      provider: parts.slice(1, -1).join(' / '), segment: parts.at(-1).replace('segment ', '') }
  }
  const cacheRows = $derived.by(() => {
    const segments = new Map()
    for (const [key, c] of Object.entries(trace?.cache_breakdown ?? {})) {
      const group = cacheGroup(key)
      if (!segments.has(group.segment)) segments.set(group.segment, [])
      segments.get(group.segment).push({key, c, group})
    }
    return [...segments.values()].flatMap(rows => rows.map((row, i) => ({...row, costSpan: i === 0 ? rows.length : 0})))
  })
  function segmentCost(segment) {
    const c = trace?.segment_costs?.[segment]
    if (c?.total_cost_usd != null) return `$${c.total_cost_usd.toFixed(6)}`
    if (c?.measured_requests) return `≥ $${c.reported_cost_usd.toFixed(6)} (partial)`
    return 'Not reported'
  }

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

  // --- Trace step helpers ----------------------------------------------------
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
  // Spectate.fmtHandback exactly). succeeded→✓ / failed→✗ / partial→~ /
  // free-text→verbatim.
  function fmtVerdict(rawAssessment) {
    const raw = (rawAssessment ?? '').toString().trim()
    const lc = raw.toLowerCase()
    if (/^succe/.test(lc) || lc === 'true' || lc === 'complete') return { label: '✓ Task complete', tone: 'ok' }
    if (/^fail/.test(lc) || lc === 'false' || /not (complete|done|succeed)/.test(lc)) return { label: '✗ Task not complete', tone: 'no' }
    if (/^partial/.test(lc) || /partly/.test(lc)) return { label: '~ Partial', tone: 'partial' }
    if (raw) return { label: raw, tone: 'partial' }   // free-text → verbatim
    return { label: 'Returned to TaskMaster', tone: 'partial' }
  }

  // E9.5/E9.6 fix: `t.action` is BACKEND-PRE-STRINGIFIED (e.g. "[left, left, up]",
  // or the bare "?" sentinel on a handback turn). Derive the REAL display from the
  // SAME data the Output card uses — the player's final_result step args — so the
  // turn HEADER + the bottom "Action" exp-row show emoji buttons for normal turns
  // and the handback verdict for the turn that returned to the TaskMaster.
  function finalResultArgs(t) {
    const steps = t?.trace?.steps ?? []
    const fr = steps.find((s) => s.type === 'final_result')
    if (!fr) return null
    return parseArgs(fr.args)
  }
  // The collapsed turn header draws the buttons as <Action> glyphs when the
  // turn has real inputs (Andreas, 2026-09-07: "use icons instead of left
  // right"); this returns those tokens, or null so the header falls back to
  // the plain-text display below (handback verdicts and sentinel turns).
  function turnActionTokens(t) {
    const a = finalResultArgs(t)
    if (a && Array.isArray(a.inputs) && a.inputs.length) return actionTokens(a.inputs)
    return null
  }
  // Plain-text display: the expanded `.exp-row .ev` (a mixed string context)
  // and the header's fallback when there are no button tokens — real button
  // names via actionTokens(), never emoji.
  function turnActionDisplay(t) {
    const a = finalResultArgs(t)
    if (a && Array.isArray(a.inputs) && a.inputs.length) return actionTokens(a.inputs).join(' ')
    const hb = coerceHandback(a && a.return_to_taskmaster)
    if (hb) {
      const v = fmtVerdict(hb.self_assessment)
      return v?.label ?? 'Returned to TaskMaster'
    }
    // fallback: raw action, but the bare "?" sentinel → a dash, never a stray "?"
    if (t.action && t.action !== '?') return actionTokens(t.action).join(' ')
    return '—'
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
  const ratingIcon = { succeeded: '✓', failed: '✗', partial: '~' }

  // Task header badge (parity with report.py _task_badge_html). No rating yet =
  // the CURRENT (in-progress) task.
  function taskBadge(g) {
    const status = (g.rating?.status || '').toLowerCase()
    if (!g.rating) return { icon: '·', label: 'current', cls: 'badge-current' }
    if (status === 'succeeded') return { icon: '✓', label: 'succeeded', cls: 'badge-succeeded' }
    if (status === 'failed') return { icon: '✗', label: 'failed', cls: 'badge-failed' }
    if (status === 'partial') return { icon: '~', label: 'partial', cls: 'badge-partial' }
    return { icon: '–', label: status || '?', cls: 'badge-other' }
  }

  function turnUsage(t) {
    const cost = t.cost_usd != null ? `$${Number(t.cost_usd).toFixed(4)}` : ''
    const tin = t.request_tokens != null ? Number(t.request_tokens) : null
    const tout = t.response_tokens != null ? Number(t.response_tokens) : null
    const tok = (tin != null || tout != null) ? `${tin ?? '?'}→${tout ?? '?'} tok` : ''
    return [cost, tok].filter(Boolean).join(' · ')
  }
  // A null self-grade is legal on ANY turn (the field is optional, and the
  // append harness leaves it null on a segment's first turn too), so the label
  // must not assert which turn it is — it used to read "n/a (first turn)" for
  // every null, including turn 84's. An ABSENT key (config-5.1+, no verdict in
  // the output) is different again: the callers skip the row entirely.
  function turnGrade(succeeded) {
    if (succeeded === true) return '✓ succeeded'
    if (succeeded === false) return '✗ failed'
    return '– n/a'
  }
  // An uncommitted compaction (the request was accepted and traced, but the
  // run died before `compaction_complete` wrote the handover) still carries the
  // model's proposed handover in its final_result step. Surfacing it beats the
  // bare "No committed compaction output" that hid it — labelled, so nobody
  // reads a proposal as the memory the next segment actually started from.
  function proposedHandover(ptr) {
    const fr = (ptr?.steps ?? []).find((s) => s.type === 'final_result')
    const args = fr ? parseArgs(fr.args) : null
    return args && typeof args === 'object' && !Array.isArray(args) ? args : null
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
    <div class="empty"><p>No run selected.</p><button class="btn" onclick={() => onback()}><Icon name="back" size={13} /> Back</button></div>
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}><Icon name="back" size={13} /> Back</button>
      <span class="badge {run.kind}">{run.kind}</span>
      {#if harnessLabel}<span class="harness" title="The agent that drove this run, from its recorded config (agent_type + task_master)">{harnessLabel}</span>{/if}
      <button class="btn cont full-report" disabled={run.status === 'running' || summary?.protocol_probe} onclick={() => oncontinue(run)}><Icon name="rerun" size={13} /> Continue this run</button>
    </div>

    <!-- meta bar -->
    <header class="rhead">
      <h2 class="mono">{run.model}</h2>
      {#if summary?.protocol_probe}<p>Protocol test using a recorded screenshot. No emulator actions were executed.</p>{/if}
      <div class="meta faint">
        <span class="mono">{run.slug}</span> · {dateShort(run.startedAt)} · config <span class="mono">{run.config}</span>
        {#if run.benchmark}· benchmark <span class="mono">{run.benchmark}</span>{/if}
        {#if run.continuedFrom}· continued from <span class="mono">{run.continuedFrom}</span>{/if}
      </div>
      <div class="kpis">
        <!-- Completion is a BENCHMARK score. It shows only when the referee
             enforced the ladder; an observe-only run gets History's dash. -->
        <div class="k"><span class="kl">Completion</span>
          {#if gatesEnforced}
            <span class="kv" class:full={run.completion >= 100}>{run.completion}%</span>
          {:else}
            <span class="kv dash faint" title="Gates were observed, not enforced — this run was never scored against the ladder">—</span>
          {/if}
        </div>
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

    <!-- benchmark gate scorecard (real, from referee.gates). Only for a run the
         referee actually enforced — the deadline columns are a claim about what
         the run was judged against, and an observe-only run was judged against
         nothing. It still reached rungs, so say how far it got instead. -->
    {#if gates.length && !gatesEnforced}
      <p class="observed faint">Gates observed, not enforced — the referee scored the ladder but armed no deadline{#if furthestGate}&nbsp;· furthest: {furthestGate}{/if}</p>
    {:else if gates.length}
      <section class="score">
        <div class="score-head">
          <h3>Benchmark gates</h3>
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
        {#if trace?.cache}
          <details class="trace-step cache-overview">
            <summary><span class="step-label">Cache overview</span><span class="step-preview">{cachePct(trace.cache.input_read_fraction)} of measured input reused</span></summary>
            <div class="cache-body">
              <div class="cache-metrics">
                <div><span class="kl">Input from cache</span><strong>{cachePct(trace.cache.input_read_fraction)}</strong><span>{cacheCount(trace.cache.cached_tokens)} of {cacheCount(trace.cache.measured_input_tokens)} measured tokens</span></div>
                <div><span class="kl">Measured requests using cache</span><strong>{cacheHits(trace.cache)}</strong><span>{cachePct(trace.cache.request_hit_fraction)} had at least one cached token</span></div>
                <div><span class="kl">Requests with cache data</span><strong>{trace.cache.measured_attempts} / {trace.cache.attempts}</strong><span>{trace.cache.measured_attempts === trace.cache.attempts ? 'All requests measured' : 'Unreported requests excluded from percentages'}</span></div>
                <div><span class="kl">Cache economics</span><strong>{economicsHeadline(trace.cache.economics)}</strong><span>{economicsNote(trace.cache.economics)}</span></div>
                <div><span class="kl">Implied by billing</span><strong>{trace.cache.implied_read_fraction == null ? 'No pricing snapshot' : cachePct(trace.cache.implied_read_fraction)}</strong><span>{impliedNote(trace.cache)}</span></div>
              </div>
              <div class="cache-table-scroll">
                <table class="cache-table">
                  <caption>By conversation segment and request type</caption>
                  <thead><tr><th>Segment</th><th>Request</th><th>Provider</th><th>Input cached</th><th>Implied by billing</th><th>Cached / measured tokens</th><th>Requests using cache</th><th>Measured / total requests</th><th>Segment total cost</th></tr></thead>
                  <tbody>
                    {#each cacheRows as {key, c, group, costSpan} (key)}
                      <tr><td>{group.segment}</td><td>{group.phase}</td><td>{group.provider}</td><td>{cachePct(c.input_read_fraction)}</td><td>{c.implied_read_fraction == null ? '—' : cachePct(c.implied_read_fraction)}</td><td>{cacheCount(c.cached_tokens)} / {cacheCount(c.measured_input_tokens)}</td><td>{cacheHits(c)}</td><td>{c.measured_attempts} / {c.attempts}</td>{#if costSpan}<td class="segment-cost" rowspan={costSpan}>{segmentCost(group.segment)}</td>{/if}</tr>
                    {/each}
                  </tbody>
                </table>
              </div>
              <p class="cache-note">Input reuse is weighted by token count. A request can use some cache and still process many uncached tokens. These percentages do not measure money saved. "Implied by billing" backs the cache discount out of the billed prompt cost against the endpoint's list prices; it is the only cache signal for providers that report no cache figures. Segment cost includes gameplay and compaction model requests, including retries; OCR is excluded.</p>
              <details class="trace-step cache-raw"><summary><span class="step-label">Raw totals (JSON)</span></summary><pre>{JSON.stringify({ total: trace.cache, by_phase_provider_segment: trace.cache_breakdown, segment_costs: trace.segment_costs }, null, 2)}</pre></details>
            </div>
          </details>
        {/if}
        <h3>
          {#if hasTasks}TaskMaster trace{:else}Turn-by-turn{/if}
          <!-- compaction_count is computed by the builder and was rendered
               nowhere. It is the defining event of the append harness, so it
               belongs beside the turn count — omitted at zero so a legacy run's
               header is unchanged. -->
          <span class="faint">({trace.turn_count} turns{#if hasTasks}&nbsp;· {trace.task_count} tasks{/if}{#if trace.compaction_count > 0}&nbsp;· {trace.compaction_count} compaction{trace.compaction_count === 1 ? '' : 's'}{/if})</span>
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
                  <span class="m-title">Task {g.task_index} (Master){#if g.title}: {g.title}{/if}</span>
                  <span class="task-badge {badge.cls}">{badge.icon} {badge.label}</span>
                  <span class="m-meta tnum">{g.turns?.length ?? 0} turn{(g.turns?.length ?? 0) === 1 ? '' : 's'}</span>
                  {#if g.master_cost != null}<span class="m-meta mono">{usd(g.master_cost)}</span>{/if}
                </button>
                {#if gOpen}
                  <div class="master-body">
                    <!-- master label + FULL master trace (chronological: trace then output) -->
                    <div class="master-label">TaskMaster</div>

                    {#if mt}
                      <div class="trace-section">
                        <!-- the count is a fact worth stating only when there
                             is one; "(0 tool calls)" appeared on every legacy
                             task node, where the master calls no tools. -->
                        <div class="trace-header">TaskMaster trace{#if nToolCalls(mt.steps) > 0} ({nToolCalls(mt.steps)} tool call{nToolCalls(mt.steps) === 1 ? '' : 's'}){/if}</div>
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
                      {#if g.title}<div class="dec-row"><span class="dec-lab">Task</span><span class="dec-val">{g.title}</span></div>{/if}
                      {#if g.description}<div class="dec-row"><span class="dec-lab">Plan</span><div class="dec-desc">{g.description}</div></div>{/if}
                      {#if g.success_criteria}<div class="dec-row"><span class="dec-lab">Success criteria</span><span class="dec-val">{g.success_criteria}</span></div>{/if}
                      {#if rop}
                        <div class="dec-row"><span class="dec-lab">Rating of the previous task</span><span class="dec-val">{ratingIcon[(rop.status || '').toLowerCase()] ?? '–'} {rop.status}</span></div>
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
                        <span class="hb-verdict">{v.label}</span>
                        {#if g.player_task_summary}<span class="hb-summary">{g.player_task_summary}</span>{/if}
                      </div>
                    {:else if !g.rating}
                      <div class="handback partial">
                        <span class="hb-verdict">Current task — in progress (no verdict yet)</span>
                      </div>
                    {/if}
                  </div>
                {/if}
              </div>
            </div>
          {:else if !hasTasks}
            <!-- "no TaskMaster" is a description of an ABSENCE, which is right
                 for a legacy casual run and wrong for the append harness: it
                 has a strategy layer (the handover), just not a second agent. -->
            <div class="casual-head faint">{isAppendRun ? 'Self-directed · append-and-compact' : 'Casual run — no TaskMaster'}</div>
          {/if}

          <!-- nested player turns (collapsible) -->
          {#if !hasTasks || g.task_index == null || gOpen}
            <div class="turns" class:nested={hasTasks && g.task_index != null}>
              {#each g.timeline ?? g.turns ?? [] as t, ti (`${gk}:${t.kind ?? 'turn'}:${ti}`)}
                {@const tk = `${gk}:${t.kind ?? 'turn'}:${ti}`}
                {@const tOpen = openTurns.has(tk)}
                {@const ptr = t.trace}
                {#if t.kind === 'compaction'}
                  <div class="turn compaction" class:open={tOpen}>
                    <button class="thead" onclick={() => toggleTurn(tk)}>
                      <span class="arr">{tOpen ? '▾' : '▸'}</span>
                      <span class="tn mono">Compaction {t.number}</span>
                      <span class="tsum faint">After turn {t.after_turn}{t.complete ? '' : ' · incomplete'}</span>
                      <span class="tuse faint mono">{turnUsage(t)}</span>
                    </button>
                    {#if tOpen}
                      <div class="tbody">
                        {#if ptr}
                          <div class="trace-section">
                            <div class="trace-container">
                              <ConversationDiagnostics events={t.diagnostics.filter(e => !['compaction_trace', 'compaction_complete'].includes(e.type))} />
                              {#if ptr.user_input}
                                <details class="trace-step trace-input">
                                  <summary>
                                    <span class="step-label">Input</span>
                                    <span class="step-preview">{ptr.user_input.slice(0, 100).replace(/\n/g, ' ')}…</span>
                                  </summary>
                                  <pre class="step-content">{ptr.user_input}</pre>
                                </details>
                              {/if}
                              {#each ptr.steps ?? [] as step}
                                {#if step.thinking}
                                  <details class="trace-step trace-thinking-only">
                                    <summary><span class="step-label">Thinking</span></summary>
                                    <div class="step-content md">{@html mdToHtml(step.thinking)}</div>
                                  </details>
                                {/if}
                              {/each}
                              {#if ptr.output}
                                <details class="trace-step trace-output" open>
                                  <summary><span class="step-label">Output</span></summary>
                                  <div class="step-body">
                                    <div class="dec-row"><span class="dec-lab">Continuation summary</span><div class="dec-desc">{ptr.output.continuation_summary}</div></div>
                                    <div class="dec-row"><span class="dec-lab">Memory</span><pre class="dec-mem">{JSON.stringify(ptr.output.memory, null, 2)}</pre></div>
                                    <details><summary>Memory before</summary><pre class="step-content">{JSON.stringify(ptr.previous_memory, null, 2)}</pre></details>
                                    <details class="structured-output"><summary>Structured output (JSON)</summary><pre class="step-content">{JSON.stringify(ptr.output, null, 2)}</pre></details>
                                  </div>
                                </details>
                              {:else}
                                {@const proposed = proposedHandover(ptr)}
                                {#if proposed}
                                  <details class="trace-step trace-output proposed" open>
                                    <summary>
                                      <span class="step-label">Proposed handover</span>
                                      <span class="step-preview">proposed, not committed — the run ended before this handover was saved</span>
                                    </summary>
                                    <div class="step-body">
                                      {#if proposed.continuation_summary}<div class="dec-row"><span class="dec-lab">Continuation summary</span><div class="dec-desc">{proposed.continuation_summary}</div></div>{/if}
                                      {#if proposed.memory !== undefined}<div class="dec-row"><span class="dec-lab">Proposed memory</span><pre class="dec-mem">{fmtArgs(proposed.memory)}</pre></div>{/if}
                                      <div class="dec-row faint"><span class="dec-val">Never committed: the next segment kept the previous memory.</span></div>
                                      <details class="structured-output"><summary>Structured output (JSON)</summary><pre class="step-content">{JSON.stringify(proposed, null, 2)}</pre></details>
                                    </div>
                                  </details>
                                {:else}
                                  <p class="faint">No committed compaction output.</p>
                                {/if}
                              {/if}
                            </div>
                          </div>
                        {/if}
                      </div>
                    {/if}
                  </div>
                {:else}
                <div class="turn" class:open={tOpen}>
                  <button class="thead" onclick={() => toggleTurn(tk)}>
                    <span class="arr">{tOpen ? '▾' : '▸'}</span>
                    <span class="tn mono">Turn {t.turn}{t.fresh ? ' (fresh)' : ''}</span>
                    {#if turnActionTokens(t)}
                      <span class="tact acts" class:long={turnActionTokens(t).length > 17} title={turnActionDisplay(t)}>{#each turnActionTokens(t) as tok}<Action token={tok} />{/each}</span>
                    {:else}
                      <span class="tact">{turnActionDisplay(t)}</span>
                    {/if}
                    <span class="tsum faint">{t.reasoning}</span>
                    <span class="tuse faint mono">{turnUsage(t)}</span>
                  </button>
                  {#if tOpen}
                    <div class="tbody">
                      <!-- input → trace → output(decision)/screenshot at the BOTTOM (chronological) -->
                      {#if ptr}
                        <div class="trace-section">
                          {#if nToolCalls(ptr.steps) > 0}
                            <div class="trace-header">Trace ({nToolCalls(ptr.steps)} tool call{nToolCalls(ptr.steps) === 1 ? '' : 's'})</div>
                          {/if}
                          <div class="trace-container">
                            <ConversationDiagnostics events={t.diagnostics ?? []} />
                            {#if ptr.system_prompt}
                              <details class="trace-step trace-system">
                                <summary><span class="step-label">System Prompt</span></summary>
                                <pre class="step-content">{ptr.system_prompt}</pre>
                              </details>
                            {/if}
                            {#if ptr.segment_context}
                              <details class="trace-step trace-input">
                                <summary><span class="step-label">Conversation context</span></summary>
                                <pre class="step-content">{ptr.segment_context}</pre>
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
                                {@const hbObj = coerceHandback(p && p.return_to_taskmaster)}
                                <!-- the model's extended thinking gets its OWN prominent step
                                     (chronologically before the Output), matching the TaskMaster —
                                     not buried inside the Output card where it reads as absent. -->
                                {#if step.thinking}
                                  <details class="trace-step trace-thinking-only" open>
                                    <summary><span class="step-label">Thinking</span></summary>
                                    <div class="step-content md">{@html mdToHtml(step.thinking)}</div>
                                  </details>
                                {/if}
                                <details class="trace-step trace-output" open>
                                  <summary>
                                    <span class="step-label">Output</span>
                                    <span class="step-action-code">{#if p}{#each actionTokens(p.inputs ?? '') as tok}<Action token={tok} />{/each}{/if}</span>
                                  </summary>
                                  <div class="step-body">
                                    {#if p && (p.reasoning != null || p.last_turn_succeeded !== undefined)}
                                      <div class="step-decision">
                                        {#if p.last_turn_succeeded !== undefined}<div class="dec-row"><span class="dec-lab">Last turn</span><span class="dec-val">{turnGrade(p.last_turn_succeeded)}</span></div>{/if}
                                        {#if p.reasoning}<div class="dec-row"><span class="dec-lab">Reasoning</span><div class="dec-desc">{p.reasoning}</div></div>{/if}
                                        {#if hbObj}
                                          {@const hb = fmtVerdict(hbObj.self_assessment)}
                                          <div class="dec-row"><span class="dec-lab">Return to TaskMaster</span><span class="dec-val">{hb.label}{#if hbObj.task_summary} — {hbObj.task_summary}{/if}</span></div>
                                        {:else}
                                          <div class="dec-row"><span class="dec-lab">Action</span><span class="dec-val acts">{#each actionTokens(p.inputs ?? '') as tok}<Action token={tok} />{/each}</span></div>
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
                        <!-- config-5.1+ output has no verdict: the key is absent from the trace, so no row. -->
                        {#if t.last_turn_succeeded !== undefined}<div class="exp-row"><span class="el">Last turn</span><span class="ev">{turnGrade(t.last_turn_succeeded)}</span></div>{/if}
                        <div class="exp-row"><span class="el">Reasoning</span><span class="ev">{t.reasoning}</span></div>
                        <div class="exp-row"><span class="el">Action</span><span class="ev">{turnActionDisplay(t)}</span></div>
                        {#if t.screenshot}
                          <div class="exp-row"><span class="el">Screenshot</span>
                            <img class="turn-shot" src={shotUrl(t)} alt={`Turn ${t.turn} screenshot`} loading="lazy" />
                          </div>
                        {/if}
                      </div>
                    </div>
                  {/if}
                </div>
                {/if}
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
  /* Harness chip: reads as metadata next to the kind badge, not as a second
     status — outline rather than a filled wash, and not upper-cased so the
     version stays legible. */
  .harness {
    display: inline-flex; align-items: center;
    font-size: 10.5px; font-weight: 650; letter-spacing: .02em;
    padding: 2px 7px; border-radius: var(--radius-sm);
    border: 1px solid var(--border); color: var(--muted); cursor: help;
  }
  .load { margin: 12px 2px; font-size: 13px; }

  .rhead { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 22px; box-shadow: var(--shadow); }
  h2 { font-size: 20px; font-weight: 700; margin: 0 0 4px; }
  .meta { font-size: 12.5px; margin-bottom: 18px; }
  .kpis { display: grid; grid-template-columns: repeat(6, 1fr); gap: 14px 16px; }
  .k { display: flex; flex-direction: column; gap: 2px; }
  .kl { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .kv { font-size: 16px; font-weight: 700; }
  .kv.full { color: var(--green); }
  .kv.dash { cursor: help; }
  /* Replaces the scorecard on an observe-only run (same slot, same margin). */
  .observed { margin: 16px 2px 0; font-size: 12.5px; }

  .score { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 18px 20px; box-shadow: var(--shadow); margin-top: 16px; }
  .score-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  h3 { font-size: 15px; font-weight: 750; margin: 0; }
  .cleared { font-size: 12px; font-weight: 650; color: var(--muted); }
  .verdict { margin-left: auto; font-size: 12.5px; font-weight: 700; color: var(--muted); }
  .verdict.fail { color: var(--red); }
  .verdict.win { color: var(--green); }
  .gtable { display: flex; flex-direction: column; }
  .grow { display: grid; grid-template-columns: 22px 1fr 60px 50px; gap: 10px; align-items: center; padding: 6px 8px; border-radius: var(--radius-sm); font-size: 12.5px; }
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
  .master-block { background: var(--surface); border: 1px solid var(--tm-rule); border-left: 3px solid var(--tm); border-radius: var(--radius-sm); box-shadow: var(--shadow); overflow: hidden; }
  .master-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 13px; border: none; text-align: left; font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--tm); background: var(--tm-wash); cursor: pointer; }
  .master-head:hover { background: rgba(138, 106, 29, .17); }
  .master-block:has(.master-body) .master-head { border-bottom: 1px solid var(--tm-rule); }
  .arr.amber { color: var(--tm); }
  .m-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .m-meta { font-size: 10.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted); }
  .m-meta:first-of-type { margin-left: auto; }
  .task-badge { font-size: 10.5px; font-weight: 750; text-transform: uppercase; letter-spacing: .03em; padding: 1px 8px; border-radius: var(--radius-sm); background: var(--surface-2); color: var(--muted); white-space: nowrap; }
  .task-badge.badge-succeeded { background: var(--green-soft); color: var(--green); }
  .task-badge.badge-failed { background: var(--red-soft); color: var(--red); }
  .task-badge.badge-partial { background: var(--tm-wash); color: var(--tm); }
  .task-badge.badge-current { background: var(--surface-2); color: var(--muted); }

  .master-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
  .master-label { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: var(--tm); }

  /* deep trace container (master + player share these) */
  .trace-section { display: flex; flex-direction: column; gap: 6px; }
  .cache-body { padding: 14px; background: var(--surface); border-top: 1px solid var(--border); }
  .cache-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(185px, 1fr)); gap: 16px; margin-bottom: 18px; }
  .cache-metrics > div { display: flex; flex-direction: column; gap: 5px; }
  .cache-metrics strong { font-size: 20px; }
  .cache-metrics span:last-child, .cache-note { color: var(--muted); font-size: 11px; line-height: 1.5; }
  .cache-table-scroll { overflow-x: auto; }
  .cache-table { width: 100%; border-collapse: collapse; font-size: 11px; }
  .cache-table caption { text-align: left; font-weight: 700; padding-bottom: 8px; }
  .cache-table th { text-align: left; color: var(--muted); font-weight: 600; }
  .cache-table th, .cache-table td { padding: 8px 7px; border-bottom: 1px solid var(--border); }
  .cache-table td { white-space: nowrap; }
  .trace-header { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); }
  .trace-container { display: flex; flex-direction: column; gap: 5px; }
  .trace-step { background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 12.5px; }
  .trace-step > summary { cursor: pointer; padding: 7px 10px; display: flex; align-items: center; gap: 8px; list-style: none; }
  .trace-step > summary::-webkit-details-marker { display: none; }
  .trace-step > summary::before { content: '▸'; color: var(--faint); font-size: 10px; }
  .trace-step[open] > summary::before { content: '▾'; }
  .trace-step .step-label { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--accent); }
  .trace-system .step-label, .trace-input .step-label { color: var(--muted); }
  .trace-tool .step-label { color: var(--accent); }
  .trace-output .step-label { color: var(--green); }
  /* An uncommitted handover is not the run's memory — amber, not the green of
     a committed Output, so the two never read alike at a glance. */
  .trace-output.proposed .step-label { color: var(--tm); }
  .trace-thinking-only .step-label { color: var(--faint); }
  .step-tool-name { font-size: 11.5px; font-weight: 700; color: var(--ink); }
  .step-preview { font-size: 11px; color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* Action is 2.35em tall — shrink the container's font-size (not the glyph's
     own height) so it reads at the old emoji's footprint in this dense row. */
  .step-action-code { display: inline-flex; align-items: center; gap: 3px; font-size: 7.5px; }
  .step-content, .trace-step pre { margin: 0; padding: 8px 10px; white-space: pre-wrap; word-break: break-word; font-size: 11.5px; line-height: 1.5; color: var(--muted); background: var(--surface); border-top: 1px solid var(--border); border-radius: 0 0 var(--radius-sm) var(--radius-sm); max-height: 360px; overflow: auto; }
  .step-body { padding: 6px 10px 10px; display: flex; flex-direction: column; gap: 8px; }
  .step-body pre { border: 1px solid var(--border); border-radius: var(--radius-sm); }
  .sub-label { font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); margin-bottom: 3px; }
  .step-thinking .md { font-size: 12.5px; line-height: 1.55; color: var(--ink); }
  .step-thinking .md :global(strong) { color: var(--accent); }
  .step-thinking .md :global(p) { margin: 0 0 6px; }
  .step-content.md { white-space: normal; }
  .step-decision { display: flex; flex-direction: column; gap: 7px; }
  .trace-retry { padding: 7px 10px; }

  /* decision / output rows (master verdict + player decision) */
  .master-verdict { display: flex; flex-direction: column; gap: 7px; padding: 10px 12px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); }
  .dec-row { font-size: 12.5px; line-height: 1.5; }
  .dec-lab { display: block; font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: var(--faint); margin-bottom: 2px; }
  .dec-val { color: var(--ink); }
  /* Action is 2.35em tall — shrink the container's font-size (not the glyph's
     own height) so the decision row's glyphs don't dwarf the surrounding text. */
  .dec-val.acts { display: inline-flex; align-items: center; gap: 3px; font-size: 7.5px; }
  .dec-desc { white-space: pre-wrap; color: var(--muted); }
  .dec-mem { margin: 3px 0 0; padding: 5px 8px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); font-size: 11px; white-space: pre-wrap; }

  /* task verdict / handback banner */
  .handback { display: flex; flex-direction: column; gap: 3px; padding: 9px 12px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--surface-2); }
  .handback.ok { background: var(--green-soft); border-color: transparent; }
  .handback.no { background: var(--red-soft); border-color: transparent; }
  .handback.partial { background: var(--tm-wash); border-color: transparent; }
  .hb-verdict { font-size: 12.5px; font-weight: 750; }
  .hb-summary { font-size: 12px; color: var(--muted); line-height: 1.5; }

  .master-thumbs { display: flex; gap: 8px; flex-wrap: wrap; padding: 8px 10px; }
  .master-thumb { margin: 0; text-align: center; }
  .master-thumb img { width: 130px; image-rendering: pixelated; border: 1px solid var(--border); border-radius: var(--radius-sm); display: block; }
  .master-thumb figcaption { font-size: 9.5px; color: var(--tm); text-transform: uppercase; letter-spacing: .04em; margin-top: 3px; }

  .casual-head { font-size: 11px; text-transform: uppercase; letter-spacing: .04em; font-weight: 700; margin: 4px 2px 8px; }
  .turns.nested { margin: 0 0 12px 16px; padding-left: 10px; border-left: 2px solid var(--border); }
  .turn-shot { width: 240px; max-width: 100%; image-rendering: pixelated; border: 1px solid var(--border); border-radius: var(--radius-sm); display: block; margin-top: 2px; }
  .turn { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); margin-bottom: 8px; overflow: hidden; }
  /* The action and summary tracks are minmax(0, …) so a long button strip
     shrinks instead of pushing the cost column off the row: before 2026-09-07
     a 20-button turn hid its own cost and tokens. */
  .thead { width: 100%; display: grid; grid-template-columns: 18px max-content minmax(0, max-content) minmax(0, 1fr) max-content; gap: 10px; align-items: center; padding: 11px 14px; border: none; background: none; text-align: left; }
  .compaction > .thead { grid-template-columns: 18px max-content 1fr auto; }
  .thead:hover { background: var(--surface-2); }
  .arr { color: var(--faint); font-size: 10px; }
  .tn { font-size: 12px; font-weight: 700; color: var(--accent); }
  /* The cap is a fixed length on purpose: a percentage max-width on a grid item
     resolves against its own track, not the row, and 52% halved every strip. */
  .tact { font-size: 13px; white-space: nowrap; min-width: 0; max-width: 420px; overflow: hidden; text-overflow: ellipsis; }
  /* Action is 2.35em tall — shrink the container's font-size (not the glyph's
     own height) so the strip sits near the row's text height (8.5px → 20px
     glyphs at a ~23px pitch, so 18 fit the 420px cap). A strip longer than that fades out on the
     right; the full button list is in the title attribute and in the expanded
     Action row. */
  .tact.acts { display: inline-flex; align-items: center; gap: 3px; font-size: 8.5px; }
  .tact.acts.long { mask-image: linear-gradient(to right, #000 calc(100% - 28px), transparent); -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 28px), transparent); }
  .tsum { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tuse { font-size: 11px; }
  .tbody { padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 12px; }
  .exp { display: flex; flex-direction: column; gap: 10px; }
  .exp-row { display: flex; flex-direction: column; gap: 2px; }
  .el { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .ev { font-size: 13px; line-height: 1.5; }
  @media (max-width: 720px) { .kpis { grid-template-columns: repeat(3, 1fr); } }
</style>
