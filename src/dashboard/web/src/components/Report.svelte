<script>
  // Native restyled report (Plan §P6 decision): meta KPIs + the REAL benchmark
  // gate scorecard (from referee.gates) + the REAL per-turn trace (from
  // summary.turns), fetched from /api/runs/{id}/summary (the raw nested doc).
  // A "View full HTML report" link opens /api/runs/{id}/report — the exhaustive
  // event-level report.py output — preserving it without iframing it as primary.
  import { GATES } from '../lib/gates.js'
  import { usd, dur, perTurn, dateShort } from '../lib/format.js'
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

  // default-open the FIRST group + its first turn; collapse the rest.
  let openGroups = $state(new Set())
  let openTurns = $state(new Set())   // keys: `${groupKey}:${turn}`
  function groupKey(g, i) { return g.task_index != null ? `t${g.task_index}` : `g${i}` }
  $effect(() => {
    if (!tasks.length) return
    const first = tasks[0]
    const gk = groupKey(first, 0)
    openGroups = new Set([gk])
    openTurns = first.turns?.length ? new Set([`${gk}:${first.turns[0].turn}`]) : new Set()
  })
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

  function fmtAction(a) {
    if (Array.isArray(a)) return a.join(' ')
    return a ?? ''
  }
  function turnGrade(t) {
    if (t.last_turn_succeeded === true) return '✓ succeeded'
    if (t.last_turn_succeeded === false) return '✗ failed'
    return '— n/a'
  }
  function turnUsage(t) {
    const cost = t.cost_usd != null ? `$${Number(t.cost_usd).toFixed(4)}` : ''
    const tin = t.request_tokens != null ? Number(t.request_tokens) : null
    const tout = t.response_tokens != null ? Number(t.response_tokens) : null
    const tok = (tin != null || tout != null) ? `${tin ?? '?'}→${tout ?? '?'} tok` : ''
    return [cost, tok].filter(Boolean).join(' · ')
  }
  function shotUrl(t) {
    return `/api/runs/${encodeURIComponent(run.runId)}/screenshots/${t.screenshot}`
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

    <!-- two-level master→player trace (B1) + image traces (B2) -->
    {#if tasks.length}
      <section class="trace">
        <h3>
          {#if hasTasks}TaskMaster trace{:else}Turn-by-turn{/if}
          <span class="faint">({trace.turn_count} turns{#if hasTasks} · {trace.task_count} tasks{/if} · full event-level detail in the HTML report)</span>
        </h3>
        {#each tasks as g, gi (groupKey(g, gi))}
          {@const gk = groupKey(g, gi)}
          {@const gOpen = openGroups.has(gk)}
          {#if hasTasks && g.task_index != null}
            <!-- master/TaskMaster node = group header (amber strategy card) -->
            <div class="group">
              <div class="master-block">
                <button class="master-head" onclick={() => toggleGroup(gk)}>
                  <span class="arr amber">{gOpen ? '▾' : '▸'}</span>
                  <span class="m-title">🧭 Task {g.task_index}{#if g.title}: {g.title}{/if}</span>
                  {#if g.master_model}<span class="m-meta mono">{g.master_model}</span>{/if}
                  {#if g.master_cost != null}<span class="m-meta mono">{usd(g.master_cost)}</span>{/if}
                  {#if g.rating}<span class="m-rate {g.rating.status}">{g.rating.status}</span>{/if}
                </button>
                {#if gOpen}
                  <div class="master-body">
                    {#if g.description}<div class="m-row"><span class="m-lab">🧭 Plan</span><div class="m-desc">{g.description}</div></div>{/if}
                    {#if g.success_criteria}<div class="m-row"><span class="m-lab">🎯 Success criteria</span><span class="mono">{g.success_criteria}</span></div>{/if}
                    {#if g.rating}<div class="m-row"><span class="m-lab">⚖️ Rating</span><span class="m-rate {g.rating.status}">{g.rating.status}</span>{#if g.rating.reasoning} — {g.rating.reasoning}{/if}</div>{/if}
                    {#if g.player_self_assessment}<div class="m-row"><span class="m-lab">🪞 Player self-assessment</span><div class="m-desc">{g.player_self_assessment}</div></div>{/if}
                    {#if g.master_input_images?.length}
                      <div class="m-row"><span class="m-lab">📷 Screens the master saw</span>
                        <div class="master-thumbs">
                          {#each g.master_input_images as im}
                            <figure class="master-thumb">
                              <img src={im.data_url} alt={im.label || ''} />
                              {#if im.label}<figcaption>{im.label}</figcaption>{/if}
                            </figure>
                          {/each}
                        </div>
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
                <div class="turn" class:open={tOpen}>
                  <button class="thead" onclick={() => toggleTurn(tk)}>
                    <span class="arr">{tOpen ? '▾' : '▸'}</span>
                    <span class="tn mono">Turn {t.turn}</span>
                    <span class="tact mono">{fmtAction(t.action)}</span>
                    <span class="tsum faint">{t.reasoning}</span>
                    <span class="tuse faint mono">{turnUsage(t)}</span>
                  </button>
                  {#if tOpen}
                    <div class="tbody">
                      <div class="exp">
                        <div class="exp-row"><span class="el">Last turn</span><span class="ev">{turnGrade(t)}</span></div>
                        <div class="exp-row"><span class="el">Reasoning</span><span class="ev">{t.reasoning}</span></div>
                        <div class="exp-row"><span class="el">Action</span><span class="ev mono">{fmtAction(t.action)}</span></div>
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
  .bar .cont { }
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

  /* master/TaskMaster node = group header — amber "strategy" layer, heavier than turns */
  .group { margin-bottom: 8px; }
  .master-block { background: var(--surface); border: 1px solid #f0d9a0; border-left: 3px solid #ffce54; border-radius: var(--radius-sm); box-shadow: var(--shadow); overflow: hidden; }
  .master-head { width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 13px; border: none; text-align: left; font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #b8860b; background: rgba(255, 206, 84, 0.12); border-bottom: 1px solid transparent; cursor: pointer; }
  .master-head:hover { background: rgba(255, 206, 84, 0.2); }
  .master-block:has(.master-body) .master-head { border-bottom-color: #f0d9a0; }
  .arr.amber { color: #c79a18; }
  .m-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .m-meta { font-size: 10.5px; font-weight: 600; text-transform: none; letter-spacing: 0; color: var(--muted); }
  .m-meta:first-of-type { margin-left: auto; }
  .m-rate { font-size: 10.5px; font-weight: 750; text-transform: uppercase; letter-spacing: .03em; padding: 1px 7px; border-radius: 10px; background: var(--surface-2); color: var(--muted); }
  .m-rate.pass, .m-rate.success, .m-rate.done { background: var(--green-soft); color: var(--green); }
  .m-rate.fail, .m-rate.failed, .m-rate.missed { background: var(--red-soft); color: var(--red); }
  .master-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 11px; }
  .m-row { font-size: 13px; line-height: 1.55; }
  .m-lab { display: block; font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #c79a18; margin-bottom: 3px; }
  .m-desc { white-space: pre-wrap; color: var(--muted); }
  .master-thumbs { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
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
  .tact { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
  .tsum { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tuse { font-size: 11px; }
  .tbody { padding: 4px 16px 16px; }
  .exp { display: flex; flex-direction: column; gap: 10px; }
  .exp-row { display: flex; flex-direction: column; gap: 2px; }
  .el { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .ev { font-size: 13px; line-height: 1.5; }
  @media (max-width: 720px) { .kpis { grid-template-columns: repeat(3, 1fr); } }
</style>
