<script>
  import { GATES, gate, GATE_INDEX } from '../lib/gates.js'
  import { usd, dur, perTurn, dateShort } from '../lib/format.js'
  let { run = null, onback, oncontinue } = $props()

  // synthesize a gate scorecard for the report (real one comes from referee)
  function scorecard(r) {
    const reached = r.gatesReached
    const failedIdx = r.status === 'terminated' ? reached : -1
    return GATES.map((g, i) => {
      if (i < reached) return { ...g, status: 'done', turn: Math.min(r.turns, Math.round(g.deadline * 0.74)) }
      if (i === failedIdx) return { ...g, status: 'failed', turn: null }
      return { ...g, status: 'pending', turn: null }
    })
  }
  const card = $derived(run ? scorecard(run) : [])
  const verdict = $derived(() => {
    if (!run) return ''
    if (run.completion >= 100) return '🏁 All gates cleared — full ladder'
    if (run.status === 'terminated') {
      const next = GATES[GATE_INDEX[run.furthestGate] + 1]
      return `✗ Failed at ${next?.name} (limit T${next?.deadline})`
    }
    return `Reached ${run.furthestGateName}`
  })
  const stIcon = { done: '✓', failed: '✗', pending: '·' }

  const sampleTurns = [
    { n: 1, act: '↑ ↑ ← ← ← ← ←', ok: null, reason: "First turn. In Red's bedroom — walk up two tiles then left to reach the PC.", usage: '1.5k in / 0.3k out · $0.030' },
    { n: 2, act: '↑ ↑ ← ← ↑ a', ok: false, reason: 'The bed blocked the path left. Re-routing around it to face the PC.', usage: '1.6k in / 0.4k out · $0.055' },
    { n: 8, act: '← ← ↓ ↓ ← ←', ok: true, reason: 'Onto the stairs — transitioning to 1F.', usage: '1.4k in / 0.3k out · $0.034' },
    { n: 9, act: '→ ↓ ↓ ↓ ↓ ← ← ←', ok: true, reason: 'Through the living room, out the door into Pallet Town.', usage: '1.5k in / 0.3k out · $0.041' },
  ]
  let openTurn = $state(8)
</script>

<section class="wrap">
  {#if !run}
    <div class="empty"><p>No run selected.</p><button class="btn" onclick={() => onback()}>← Back</button></div>
  {:else}
    <div class="bar">
      <button class="btn ghost" onclick={() => onback()}>← Back</button>
      <span class="badge {run.kind}">{run.kind}</span>
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

    <!-- benchmark gate scorecard -->
    <section class="score">
      <div class="score-head">
        <h3>🏁 Benchmark gates</h3>
        <span class="cleared">{run.gatesReached}/{GATES.length} cleared</span>
        <span class="verdict" class:fail={run.status === 'terminated'} class:win={run.completion >= 100}>{verdict()}</span>
      </div>
      <div class="gtable">
        {#each card as g (g.id)}
          <div class="grow {g.status}" class:grp={g.group}>
            <span class="gst {g.status}">{stIcon[g.status]}</span>
            <span class="gname">{g.name}</span>
            <span class="gturn tnum">{g.turn != null ? 'T' + g.turn : '—'}</span>
            <span class="glim tnum faint">T{g.deadline}</span>
          </div>
        {/each}
      </div>
    </section>

    <!-- per-turn trace -->
    <section class="trace">
      <h3>Turn-by-turn <span class="faint">(sample — full report renders the run's events.jsonl)</span></h3>
      {#each sampleTurns as t}
        <div class="turn" class:open={openTurn === t.n}>
          <button class="thead" onclick={() => openTurn = openTurn === t.n ? -1 : t.n}>
            <span class="arr">{openTurn === t.n ? '▾' : '▸'}</span>
            <span class="tn mono">Turn {t.n}</span>
            <span class="tact mono">{t.act}</span>
            <span class="tsum faint">{t.reason}</span>
            <span class="tuse faint mono">{t.usage}</span>
          </button>
          {#if openTurn === t.n}
            <div class="tbody">
              <div class="shot"><div class="shot-ph faint">screenshot</div></div>
              <div class="exp">
                <div class="exp-row"><span class="el">Last turn</span><span class="ev">{t.ok === true ? '✓ succeeded' : t.ok === false ? '✗ failed' : '— first turn'}</span></div>
                <div class="exp-row"><span class="el">Reasoning</span><span class="ev">{t.reason}</span></div>
                <div class="exp-row"><span class="el">Action</span><span class="ev mono">{t.act}</span></div>
              </div>
            </div>
          {/if}
        </div>
      {/each}
    </section>
  {/if}
</section>

<style>
  .wrap { max-width: 880px; margin: 0 auto; padding: 24px; }
  .empty { text-align: center; padding: 80px 0; }
  .bar { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
  .bar .cont { margin-left: auto; }

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
  .grow.failed { background: var(--red-soft); }
  .gst { text-align: center; font-weight: 800; color: var(--faint); }
  .gst.done { color: var(--green); } .gst.failed { color: var(--red); }
  .gname { font-weight: 550; }
  .gturn { text-align: right; font-weight: 650; }
  .glim { text-align: right; font-size: 11.5px; }

  .trace { margin-top: 24px; }
  .trace h3 .faint { font-weight: 500; font-size: 12px; }
  .turn { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); margin-bottom: 8px; overflow: hidden; }
  .thead { width: 100%; display: grid; grid-template-columns: 18px 56px auto 1fr auto; gap: 10px; align-items: center; padding: 11px 14px; border: none; background: none; text-align: left; }
  .thead:hover { background: var(--surface-2); }
  .arr { color: var(--faint); font-size: 10px; }
  .tn { font-size: 12px; font-weight: 700; color: var(--accent); }
  .tact { font-size: 12px; color: var(--muted); }
  .tsum { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tuse { font-size: 11px; }
  .tbody { display: grid; grid-template-columns: 240px 1fr; gap: 16px; padding: 4px 16px 16px; }
  .shot { aspect-ratio: 240/160; background: #11141b; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
  .shot-ph { color: #7b8696; font-size: 12px; }
  .exp { display: flex; flex-direction: column; gap: 10px; }
  .exp-row { display: flex; flex-direction: column; gap: 2px; }
  .el { font-size: 10px; text-transform: uppercase; letter-spacing: .03em; color: var(--faint); font-weight: 700; }
  .ev { font-size: 13px; line-height: 1.5; }
  @media (max-width: 720px) { .kpis { grid-template-columns: repeat(3, 1fr); } .tbody { grid-template-columns: 1fr; } }
</style>
