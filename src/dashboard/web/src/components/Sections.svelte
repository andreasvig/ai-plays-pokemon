<script>
  // The body of the board under the headline strip (Andreas 2026-09-14, after
  // Artificial Analysis): a sticky left menu — Performance, Price, Speed,
  // Efficiency — and one big-headed section per item. Scrolling highlights the
  // section in view; a menu item is an anchor (#price) so a section can be
  // linked. Every chart here follows the shared model picker (decision 1a);
  // the headline three above stay on the whole board.
  import { headlineSeries, secondarySeries, battleSeries, MIN_BATTLE_OBSERVATIONS, PERF_LINE, PROJECT_FROM_GATE, fmtMinutes, fmtUsd, fmtTokens } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  import { selection } from '../lib/selection.svelte.js'
  import BarCard from './BarCard.svelte'
  import PlotCard from './PlotCard.svelte'
  let { pool = [], oninspect = () => {}, onpick = () => {} } = $props()

  const rows = $derived(selection.apply(pool))
  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const nGates = $derived(gateIds.length)
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))
  const head = $derived(headlineSeries(rows, gateIds, pool))
  const sec = $derived(secondarySeries(rows, gateIds, pool))
  const bat = $derived(battleSeries(rows, pool))

  const tip = (s, fmt) => {
    const base = `${s.row.model}: ${fmt(s.value)} per task`
    if (s.complete) return `${base} · measured over ${s.row.turns} turns`
    return `${base} · projected: ${Math.round(s.projected)} turns to beat Brock at ${s.pace.toFixed(2)}× the field's pace (${Math.round(s.estimated)} estimated${s.floored ? ', failed leg floored at turns spent' : ''})`
  }
  const performance = $derived(head.performance.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    above: s.complete ? `${s.row.turns}T` : null, tip: `${s.row.model}: ${s.label}${s.complete ? `, cleared in ${s.row.turns} turns` : ''}` })))
  const cost = $derived(head.cost.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtUsd) })))
  const time = $derived(head.time.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtMinutes) })))
  const cost10 = $derived(sec.cost10.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, tip: `${s.row.model}: ${s.label} per 10 turns` })))
  const speed = $derived(sec.speed.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, tip: `${s.row.model}: ${s.label} turns/min (${s.row.avgSPerTurn.toFixed(1)}s per turn)` })))
  const turnsPerTask = $derived(sec.turnsPerTask.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete,
    tip: `${s.row.model}: ${s.label} turns per task${s.complete ? '' : ` · projected ${Math.round(s.projected)} turns to beat Brock`}` })))
  const leftOff = $derived(sec.turnsPerTask.filter((s) => !s.eligible).length)
  // Inputs per turn: the tooltip names the run's three most-pressed inputs.
  const mix = (r) => {
    const c = r.inputCounts || {}
    const total = Object.values(c).reduce((a, b) => a + b, 0) || 1
    return Object.entries(c).slice(0, 3).map(([k, n]) => `${k} ${Math.round(n / total * 100)}%`).join(', ')
  }
  const inputsPerTurn = $derived(sec.inputsPerTurn.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    tip: `${s.row.model}: ${s.label} inputs per turn on average${mix(s.row) ? ` · most pressed: ${mix(s.row)}` : ''}` })))
  const noInputs = $derived(sec.inputsPerTurn.filter((s) => !s.eligible).length)
  const inputsNote = $derived(noInputs ? `${noInputs} selected model${noInputs === 1 ? '' : 's'} not shown: the run predates per-turn input records.` : '')
  // Output tokens per turn: thinking + reply over every call; the tooltip gives
  // the thinking share where the route reports reasoning tokens.
  // A run that did not finish is DOTTED (Andreas 2026-09-14; the hatch means
  // projected, this value is measured): models think
  // more as the game gets harder, so a run that ended early averaged over the
  // cheap early game only — its bar is low partly for that reason.
  const outputTokens = $derived(sec.outputTokens.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: s.row.completion < 100,
    tip: `${s.row.model}: ${fmtTokens(s.value)} output tokens per turn on average (thinking + reply, all calls)${s.row.thinkingShare != null ? ` · ${Math.round(s.row.thinkingShare * 100)}% of them thinking` : ''}${s.row.completion >= 100 ? '' : ` · run ended at ${Math.round(s.row.completion)}%: averaged over the early game only`}` })))
  const noTokens = $derived(sec.outputTokens.filter((s) => !s.eligible).length)
  // Battles + movement (2026-09-14). Fidelity words for the tooltips.
  const STEPS_FID = { trace: 'steps traced per input', video: 'steps counted from the recording', bound: 'steps are a lower bound between per-turn polls, so this is an upper bound', mixed: 'steps partly traced, partly bounded' }
  const BAT_FID = { live: 'battles polled every turn', backfill: 'battle counts from savepoints, turns from screenshots', savepoint: 'battle counts from savepoints' }
  const offNote = (n, why) => n ? `${n} selected model${n === 1 ? '' : 's'} not shown: ${why}` : ''
  const movement = $derived(bat.movement.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: s.row.completion < 100,
    tip: `${s.row.model}: ${s.label} — shortest path ${s.row.shortestSteps} of ${s.row.overworldSteps} steps over the map legs it closed · ${STEPS_FID[s.fidelity] || ''}${s.row.completion < 100 ? ` · run ended at ${Math.round(s.row.completion)}%` : ''}` })))
  const movementNote = $derived(offNote(bat.movement.filter((s) => !s.eligible).length, 'no closed map leg to measure.'))
  const wildTurns = $derived(bat.wildTurns.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: s.row.completion < 100,
    tip: `${s.row.model}: ${s.row.wildBattleTurns} turns started inside ${s.row.wildBattles} wild battle${s.row.wildBattles === 1 ? '' : 's'} = ${s.label} per battle · ${BAT_FID[s.row.battleFidelity] || ''}` })))
  const wildNote = $derived(offNote(bat.wildTurns.filter((s) => !s.eligible).length, 'no wild battle, or no per-turn battle state.'))
  const battleShare = $derived(bat.battleShare.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: s.row.completion < 100,
    tip: `${s.row.model}: ${s.label} of its ${s.row.turns} turns started inside a battle · ${BAT_FID[s.row.battleFidelity] || ''}` })))
  const shareNote = $derived(offNote(bat.battleShare.filter((s) => !s.eligible).length, 'no per-turn battle state.'))
  const trainerTurns = $derived(bat.trainerTurns.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, partial: s.complete && s.row.completion < 100,
    tip: `${s.row.model}: ${s.label} turns per trainer battle — ${s.measured} turns over ${s.attempts} fight${s.attempts === 1 ? '' : 's'} (${(s.row.trainerBattles || []).filter((g) => g.attempts > 0).map((g) => `${g.name} ${g.turns}T${g.attempts > 1 ? ` in ${g.attempts} attempts` : ''}${g.won ? '' : ', not won'}`).join('; ')})${s.projected ? ` + ${s.missing.map((m) => `${m.name} projected at ${m.turns.toFixed(1)}${m.rate < 1 ? ` × ${Math.round(m.rate * 100)}% chance of meeting them` : ''} (pace ${s.pace.toFixed(2)}× the field mean ${m.typical.toFixed(1)} over ${m.n} fights)`).join(', ')}` : ''}` })))
  const trainerNote = $derived(offNote(bat.trainerTurns.filter((s) => !s.eligible).length, 'no trainer fought yet, or no per-turn battle state.'))
  const TOKENS_BIAS = '* Models think more as the game gets harder: over the runs with 80+ turns, the last 40 turns cost a median 1.65× the output tokens of the first 40. A run that ended early (dotted) averaged over the cheap early game only, so its bar is low partly for that reason.'
  const tokensNote = $derived(TOKENS_BIAS + (noTokens ? ` ${noTokens} selected model${noTokens === 1 ? '' : 's'} not shown: the run recorded no token usage.` : ''))
  const leftNote = $derived(leftOff ? `${leftOff} selected model${leftOff === 1 ? '' : 's'} not shown: never reached ${fromGate}, so nothing to project.` : '')

  const SECTIONS = [
    { id: 'performance', label: 'Performance', color: 'var(--accent)', blurb: 'How far each model gets through the first-badge ladder, and how few turns a full clear takes.' },
    { id: 'price', label: 'Price', color: 'var(--retry)', blurb: 'What a task costs: USD to finish the ladder ÷ gates, projected for partial runs, and the raw USD per ten turns behind it.' },
    { id: 'speed', label: 'Speed', color: 'var(--amber)', blurb: 'How long a task takes: wall-clock minutes to finish the ladder ÷ gates, projected for partial runs, and the turns per minute behind it.' },
    { id: 'efficiency', label: 'Efficiency', color: 'var(--green)', blurb: 'How much a model gets out of each turn: turns per task, projected for partial runs, how many game inputs it batches into one turn, how many output tokens (thinking and reply) a turn costs it, how directly it walks, and what battles cost it in turns.' },
  ]
  // The section in view: the last one whose top has passed the sticky menu's
  // line. Cheaper and steadier than an intersection ratio for tall sections.
  let active = $state('performance')
  let els = $state({})
  function onScroll() {
    const line = 96
    let cur = SECTIONS[0].id
    for (const s of SECTIONS) { const el = els[s.id]; if (el && el.getBoundingClientRect().top <= line) cur = s.id }
    if (cur !== active) { active = cur; if (typeof history !== 'undefined') history.replaceState(history.state, '', `${location.pathname}${location.search}#${cur}`) }
  }
  $effect(() => { onScroll() })
</script>

<svelte:window onscroll={onScroll} onresize={onScroll} />

<div class="body">
  <nav class="menu" aria-label="Board sections">
    {#each SECTIONS as s (s.id)}
      <a href={`#${s.id}`} class:on={active === s.id} style={`--c:${s.color}`}>{s.label}</a>
    {/each}
  </nav>

  <div class="content">
    {#each SECTIONS as s (s.id)}
      <section id={s.id} class="sec" bind:this={els[s.id]} style={`--c:${s.color}`}>
        <header class="sechead">
          <h2><span class="sq"></span>{s.label}</h2>
          <p class="faint">{s.blurb}</p>
        </header>
        {#if s.id === 'performance'}
          <BarCard title="Performance" subtitle="Gate completion · clears ranked by fewest turns above the line · Higher is better"
            entries={performance} line={{ frac: PERF_LINE, label: '100%' }} picker pickerRows={pool} narrowFrom={18} bars={300} {oninspect} />
        {:else if s.id === 'price'}
          <BarCard title="Cost per task" subtitle={`USD to beat Brock ÷ ${nGates} gates · partial runs projected (hatched) · Lower is better`}
            entries={cost} picker pickerRows={pool} narrowFrom={18} bars={300} {oninspect} />
          <PlotCard kind="cost" {pool} {onpick} />
          <BarCard title="Cost per 10 turns" subtitle="Average USD for ten turns, all calls included · Lower is better" entries={cost10} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} />
        {:else if s.id === 'speed'}
          <BarCard title="Time per task" subtitle={`Minutes to beat Brock ÷ ${nGates} gates · partial runs projected (hatched) · Lower is better`}
            entries={time} picker pickerRows={pool} narrowFrom={18} bars={300} {oninspect} />
          <PlotCard kind="time" {pool} {onpick} />
          <BarCard title="Turns per minute" subtitle="Wall clock, all turns of the run · Higher is better" entries={speed} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} />
        {:else}
          <BarCard title="Turns per task" subtitle={`Turns to beat Brock ÷ ${nGates} gates · partial runs projected (hatched) · Lower is better`}
            entries={turnsPerTask} picker pickerRows={pool} narrowFrom={18} bars={300} {oninspect} note={leftNote} />
          <BarCard title="Inputs per turn" subtitle="Average game inputs (buttons and waits) one turn carries · hover for the button mix · more per turn = fewer turns, if the batch lands"
            entries={inputsPerTurn} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={inputsNote} />
          <BarCard title="Output tokens per turn*" subtitle="Average completion tokens one turn costs the model — thinking plus the reply, every call included · runs that did not finish dotted · hover for the thinking share · fewer first"
            entries={outputTokens} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={tokensNote} />
          <BarCard title="Movement efficiency" subtitle="Shortest walk ÷ steps taken over the map legs the run closed · runs that did not finish dotted · hover for how the steps were counted · Higher is better"
            entries={movement} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={movementNote} />
          <BarCard title="Turns per wild battle" subtitle="Turns that started inside a wild battle ÷ wild battles met · runs that did not finish dotted · Lower is better"
            entries={wildTurns} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={wildNote} />
          <BarCard title="Battle share" subtitle="Share of all turns that started inside any battle, wild or trainer · runs that did not finish dotted · Lower is better"
            entries={battleShare} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={shareNote} />
          <BarCard title="Turns per trainer battle" subtitle={`Average turns a trainer fight costs, every attempt counted · shown once the first trainer is fought · trainers still ahead of the run are charged its pace × the field's mean turns for them, weighted by how often runs meet them (hatched), once ${MIN_BATTLE_OBSERVATIONS}+ runs have fought them · Lower is better`}
            entries={trainerTurns} picker pickerRows={pool} narrowFrom={18} bars={220} {oninspect} note={trainerNote} />
        {/if}
      </section>
    {/each}
  </div>
</div>

<style>
  .body { max-width: var(--maxw); margin: 36px auto 70px; padding: 0 24px; display: grid; grid-template-columns: 168px minmax(0, 1fr); gap: 32px; align-items: start; }
  .menu { position: sticky; top: 76px; display: flex; flex-direction: column; gap: 2px; }
  .menu a { display: block; padding: 8px 12px; border-left: 2px solid transparent; font-size: 13.5px; font-weight: 600; color: var(--muted); text-decoration: none; border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
  .menu a:hover { color: var(--text); background: var(--wash); }
  .menu a.on { color: var(--text); font-weight: 780; border-left-color: var(--c); }
  .menu a:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .content { display: flex; flex-direction: column; gap: 56px; min-width: 0; }
  .sec { display: flex; flex-direction: column; gap: 18px; scroll-margin-top: 84px; min-width: 0; }
  .sechead h2 { font-size: 28px; font-weight: 780; letter-spacing: -.02em; margin: 0; display: flex; align-items: center; gap: 12px; }
  .sq { width: 16px; height: 16px; background: var(--c); border-radius: 2px; flex: none; }
  .sechead p { font-size: 14px; margin: 6px 0 0 28px; max-width: 680px; line-height: 1.5; }
  @media (max-width: 960px) {
    .body { grid-template-columns: minmax(0, 1fr); gap: 20px; }
    .menu { top: 57px; flex-direction: row; flex-wrap: wrap; background: var(--bg); padding: 8px 0; z-index: 5; border-bottom: 1px solid var(--border); }
    .menu a { border-left: none; border-bottom: 2px solid transparent; border-radius: var(--radius-sm) var(--radius-sm) 0 0; }
    .menu a.on { border-bottom-color: var(--c); }
  }
</style>
