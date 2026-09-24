<script>
  // The body of the board under the headline strip (Andreas 2026-09-14, after
  // Artificial Analysis): a sticky left menu — Performance, Price, Speed,
  // Efficiency — and one big-headed section per item. Scrolling highlights the
  // section in view; a menu item is an anchor (#price) so a section can be
  // linked. Every chart here follows the shared model picker (decision 1a);
  // the headline three above stay on the whole board.
  import { headlineSeries, secondarySeries, battleSeries, reachedFloor, costOf, NO_PRICE, MIN_BATTLE_OBSERVATIONS, MIN_FIGHTS_FOR_PROJECTION, PERF_LINE, projectionCutoff, fmtMinutes, fmtUsd, fmtTokens } from '../lib/board.js'
  import { GATES } from '../lib/gates.js'
  import { selection } from '../lib/selection.svelte.js'
  import BarCard from './BarCard.svelte'
  import PlotCard from './PlotCard.svelte'
  // A model page (2026-09-14) renders these same sections over its field with
  // `picker` off (the field is fixed: every level of the model plus the best
  // level of every other) and `highlight` marking the model's own rows; the
  // eligibility rules below apply unchanged, so a level that has not reached
  // far enough stays off a card there too.
  let { pool = [], oninspect = () => {}, onpick = () => {}, picker = true, highlight = null, pinned = null } = $props()

  // `pinned` rows (a model page's own levels) stay in whatever the picker says
  // (Andreas 2026-09-14: "you should just not be able to remove those highlighted").
  const rows = $derived.by(() => {
    if (!picker) return pool
    const chosen = new Set(selection.apply(pool).map((r) => r.model))
    return pool.filter((r) => chosen.has(r.model) || (pinned && pinned(r)))
  })
  const gateIds = $derived(GATES.slice(0, pool[0]?.totalGates || 12).map((g) => g.id))
  const nGates = $derived(gateIds.length)
  // Which rows of the WHOLE pool each card can draw, independent of the
  // selection — the picker greys the rest (still tickable; Andreas 2026-09-14
  // "just for visibility"). Same rules as the entries below.
  const p1 = reachedFloor
  const canShow = $derived.by(() => {
    const h = headlineSeries(pool, gateIds, pool), s2 = secondarySeries(pool, gateIds, pool), b = battleSeries(pool, pool)
    const set = (series, extra = () => true) => new Set(series.filter((x) => x.eligible && extra(x.row)).map((x) => x.row.model))
    return {
      perTask: set(h.cost, p1), turns: set(s2.turnsPerTask, p1), inputs: set(s2.inputsPerTurn, p1), tokens: set(s2.outputTokens, p1),
      movement: set(b.movement, p1), wild: set(b.wildTurns), trainer: set(b.trainerTurns, p1),
    }
  })
  const dimUnless = (key) => (r) => !canShow[key].has(r.model)
  const head = $derived(headlineSeries(rows, gateIds, pool))
  const sec = $derived(secondarySeries(rows, gateIds, pool))
  const bat = $derived(battleSeries(rows, pool))
  // Every card but Performance, Cost per 10 turns and Turns per minute shows
  // only runs that cleared Route 1 (Andreas 2026-09-14): before it a run has
  // too little play to say anything about a task-level figure.
  // ONE rule for the whole board (Andreas 2026-09-16): a run has to have cleared
  // Route 1 — task 6 of 12, half the ladder — before any card will score it.
  // Both the number and the sentence come from PROJECT_FROM_GATE, so neither can
  // drift from the projection it gates.
  const past1 = (s) => reachedFloor(s.row)
  const cut = $derived(projectionCutoff(gateIds))
  const ROUTE1 = $derived(`cleared fewer than ${cut?.no ?? 6} of the ${nGates} tasks`)
  const FOREST_TASK = $derived(gateIds.indexOf('viridian_forest_reached') + 1)
  // One sentence per card, however many reasons it has (Andreas 2026-09-16):
  // "3 models not shown: 2 did not clear Route 1, 1 has no leg to measure."
  const offNote = (...reasons) => {
    const on = reasons.filter(([n]) => n > 0)
    if (!on.length) return ''
    const total = on.reduce((a, [n]) => a + n, 0)
    const why = on.map(([n, why]) => (on.length === 1 ? why : `${n} ${why}`)).join(', ')
    return `${total} ${picker ? 'selected ' : ''}model${total === 1 ? '' : 's'} not shown: ${why}.`
  }
  const early = $derived(rows.filter((r) => !reachedFloor(r)).length)

  const tip = (s, fmt) => {
    const base = `${s.row.model}: ${fmt(s.value)} per task`
    if (s.complete) return `${base} · measured over ${s.row.turns} turns`
    return `${base} · projected: ${Math.round(s.projected)} turns to beat Brock at ${s.pace.toFixed(2)}× the field's pace (${Math.round(s.estimated)} estimated${s.floored ? ', failed leg floored at turns spent' : ''})`
  }
  const performance = $derived(head.performance.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    above: s.complete ? `${s.row.turns}T` : null, tip: `${s.row.model}: ${s.label}${s.complete ? `, cleared in ${s.row.turns} turns` : ''}` })))
  // `s.value == null` on an eligible past-Route-1 row means one thing only: the
  // model has no list price. It is dropped rather than drawn at '—', which here
  // would be indistinguishable from "no projection yet", and counted below.
  const cost = $derived(head.cost.filter((s) => s.eligible && past1(s) && s.value != null).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtUsd) })))
  const freeOnCost = $derived(head.cost.filter((s) => s.eligible && past1(s) && costOf(s.row).total == null).length)
  const time = $derived(head.time.filter((s) => s.eligible && past1(s)).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete, tip: tip(s, fmtMinutes) })))
  // Cost per 10 turns and turns per minute are per-turn rates every run has
  // from its first turn, so they show EVERY run (Andreas 2026-09-14: excluding
  // them was a mistake); the Route 1 rule stays on the rest.
  const cost10 = $derived(sec.cost10.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, tip: `${s.row.model}: ${s.label} per 10 turns` })))
  const freeOnCost10 = $derived(sec.cost10.filter((s) => !s.eligible).length)
  const speed = $derived(sec.speed.map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, tip: `${s.row.model}: ${s.label} turns/min (${s.row.avgSPerTurn.toFixed(1)}s per turn)` })))
  const turnsPerTask = $derived(sec.turnsPerTask.filter((s) => s.eligible && past1(s)).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.complete,
    tip: `${s.row.model}: ${s.label} turns per task${s.complete ? '' : ` · projected ${Math.round(s.projected)} turns to beat Brock`}` })))
  const leftOff = $derived(sec.turnsPerTask.filter((s) => !s.eligible).length)
  // Inputs per turn: the tooltip names the run's three most-pressed inputs.
  const mix = (r) => {
    const c = r.inputCounts || {}
    const total = Object.values(c).reduce((a, b) => a + b, 0) || 1
    return Object.entries(c).slice(0, 3).map(([k, n]) => `${k} ${Math.round(n / total * 100)}%`).join(', ')
  }
  // Inputs per turn is a per-turn rate every run has from its first turn, so it
  // shows EVERY run (Andreas 2026-09-16) — the same reasoning as cost per 10
  // turns and turns per minute.
  const inputsPerTurn = $derived(sec.inputsPerTurn.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true,
    tip: `${s.row.model}: ${s.label} inputs per turn on average${mix(s.row) ? ` · most pressed: ${mix(s.row)}` : ''}` })))
  const noInputs = $derived(sec.inputsPerTurn.filter((s) => !s.eligible).length)
  const inputsNote = $derived(offNote([noInputs, 'kept no per-turn input record']))
  // Output tokens per turn: thinking + reply over every call; the tooltip gives
  // the thinking share where the route reports reasoning tokens.
  // A run that did not finish the ladder is HATCHED, the same fill the board
  // uses everywhere for "this is not a finished-run number" (Andreas
  // 2026-09-16): models think more as the game gets harder, so a run that ended
  // early averaged over the cheap early game only — its bar is low partly for
  // that reason.
  const outputTokens = $derived(sec.outputTokens.filter((s) => s.eligible && past1(s)).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.row.completion >= 100,
    tip: `${s.row.model}: ${fmtTokens(s.value)} output tokens per turn on average (thinking + reply, all calls)${s.row.thinkingShare != null ? ` · ${Math.round(s.row.thinkingShare * 100)}% of them thinking` : ''}${s.row.completion >= 100 ? '' : ` · run ended at ${Math.round(s.row.completion)}%: averaged over the early game only`}` })))
  const noTokens = $derived(sec.outputTokens.filter((s) => !s.eligible && past1(s)).length)
  // Battles + movement (2026-09-14). Fidelity words for the tooltips.
  const STEPS_FID = { trace: 'steps traced per input', video: 'steps counted from the recording', bound: 'steps are a lower bound between per-turn polls, so this is an upper bound', mixed: 'steps partly traced, partly bounded' }
  const BAT_FID = { live: 'battles polled every turn', backfill: 'battle counts from savepoints, turns from screenshots', savepoint: 'battle counts from savepoints' }
  const movement = $derived(bat.movement.filter((s) => s.eligible && past1(s)).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: s.row.completion >= 100, tag: s.row.wallsHit != null ? '†' : null,
    tip: `${s.row.model}: ${s.label} — shortest path ${s.row.shortestSteps} of ${s.row.overworldSteps} steps over its legs (the unfinished leg credited with the ground it gained) · ${STEPS_FID[s.fidelity] || ''}${s.row.completion < 100 ? ` · run ended at ${Math.round(s.row.completion)}%` : ''}` })))
  // The wall charge only exists for runs with a per-input trace, so this bar
  // mixes two scoring rules until every row has one. Say so on the bar rather
  // than let a charged run look worse than an unmeasured one for free.
  const MOVEMENT_RULE = '* Shortest walk ÷ steps taken, over every leg of the run; the leg it ended on counts only the ground it gained.'
  const movementCharged = $derived(bat.movement.filter((s) => s.eligible && s.row.wallsHit != null).length)
  const movementUncharged = $derived(bat.movement.filter((s) => s.eligible && s.row.wallsHit == null).length)
  // The dagger is the honest half of this card: a traced run pays for every
  // press into scenery, an untraced one cannot be measured that way, so the two
  // are not scored by quite the same rule until every run has a trace.
  const CHARGE_MIX = $derived(
    movementCharged && movementUncharged
      ? `† ${movementCharged} of ${movementCharged + movementUncharged} runs ${movementCharged === 1 ? 'was' : 'were'} measured press-by-press and pay${movementCharged === 1 ? 's' : ''} for walking into scenery too, which reads slightly harsher; the rest predate that trace (2026-09-14) and are charged for walked steps alone.`
      : ''
  )
  const movementNote = $derived([MOVEMENT_RULE, CHARGE_MIX, offNote([early, ROUTE1], [bat.movement.filter((s) => !s.eligible && past1(s)).length, 'had no leg to measure'])].filter(Boolean).join(' '))
  const wildTurns = $derived(bat.wildTurns.filter((s) => s.eligible).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: false,
    tip: `${s.row.model}: ${s.row.wildBattleTurns} turns started inside ${s.row.wildBattles} wild battle${s.row.wildBattles === 1 ? '' : 's'} = ${s.label} per battle · ${BAT_FID[s.row.battleFidelity] || ''}` })))
  const wildNote = $derived(offNote([early, ROUTE1], [bat.wildTurns.filter((s) => !s.eligible && past1(s)).length, 'met no wild Pokémon, or kept no per-turn battle state']))
  const trainerTurns = $derived(bat.trainerTurns.filter((s) => s.eligible && past1(s)).map((s) => ({ row: s.row, height: s.height, label: s.label, complete: true, partial: false,
    tip: `${s.row.model}: ${s.label} turns per trainer battle — ${s.measured} turns over ${s.attempts} fight${s.attempts === 1 ? '' : 's'} (${(s.row.trainerBattles || []).filter((g) => g.attempts > 0 && !s.uncounted.some((u) => u.group === g.group)).map((g) => `${g.name} ${g.turns}T${g.attempts > 1 ? ` in ${g.attempts} attempts` : ''}${g.won ? '' : ', not won'}`).join('; ')})${s.uncounted.length ? ` · not counted: ${s.uncounted.map((u) => `${u.name} ${u.turns}T (only ${u.n} run${u.n === 1 ? '' : 's'} met them)`).join(', ')}` : ''}${s.projected ? ` + ${s.missing.map((m) => `${m.name} projected at ${m.turns.toFixed(1)} (pace ${s.pace.toFixed(2)}× the field mean ${m.typical.toFixed(1)} over ${m.n} fights)`).join(', ')}` : ''}` })))
  // Only the rival in Oak's Lab stands before Viridian Forest, so the 2-fight
  // minimum is in practice "got into the forest" (checked against all 26 runs,
  // 2026-09-16: every run with 2+ fights reached task 10).
  const TRAINER_PROJ = $derived(`* A run needs ${MIN_FIGHTS_FOR_PROJECTION} trainer fights before it is scored here, and only one trainer stands before Viridian Forest — so in practice it has to get into the forest (task ${FOREST_TASK}). Trainers it never met are charged one fight each at its own pace (once ${MIN_BATTLE_OBSERVATIONS}+ runs have fought that trainer), so every run is judged on the same roster; Methodology shows which fights are measured and which estimated.`)
  const trainerNote = $derived([TRAINER_PROJ, offNote([early, ROUTE1], [bat.trainerTurns.filter((s) => !s.eligible && past1(s)).length, `had fewer than ${MIN_FIGHTS_FOR_PROJECTION} trainer fights, or kept no per-turn battle state`])].filter(Boolean).join(' '))
  const TOKENS_BIAS = '* Thinking plus the reply, over every call the turn made. Models think more as the game gets harder: over the runs with 80+ turns, the last 40 turns cost a median 1.65× the output tokens of the first 40 — so a hatched run averaged over the cheap early game and reads low for that reason.'
  const tokensNote = $derived([TOKENS_BIAS, offNote([early, ROUTE1], [noTokens, 'recorded no token usage'])].filter(Boolean).join(' '))
  // One hatch, one meaning, one sentence — everywhere the board hatches a bar
  // (Andreas 2026-09-16: "unify it, it just means projected/estimated").
  const HATCH = '* Hatched = estimated, not measured: the run did not finish the ladder, so its own pace is carried across the tasks it never reached.'
  // The three per-task cards (cost, time, turns) carry the same footnote in the
  // same words (Andreas 2026-09-16, "less is more when it comes to the text").
  const perTaskNote = $derived([
    HATCH,
    cut ? `A run that ${ROUTE1} — half the ladder — is not estimated at all${leftOff ? `: ${leftOff} ${picker ? 'selected ' : ''}model${leftOff === 1 ? '' : 's'} left off` : ''}.` : '',
    'Every task carries a turn cap, so a stalled run is stopped rather than left to run the number up; the slowest models bunch against that ceiling.',
  ].filter(Boolean).join(' '))

  // Price cards only. A free model keeps its Performance, Speed and Efficiency
  // bars — those are measurements — but $0.00 is the ABSENCE of a price, not the
  // lowest one, so it is left off the two cost cards instead of taking first
  // place on both by construction (Andreas 2026-09-16). Deliberately NOT folded
  // into perTaskNote: that footnote is shared with the time and turns cards,
  // where the same run is shown and the sentence would be false.
  const FREE_RULE = 'Models served free have no list price, so they are left off the price cards rather than shown at $0.00 — free is the absence of a price, not the lowest one.'
  const freeNote = (n) => (n ? ` ${n} ${picker ? 'selected ' : ''}model${n === 1 ? '' : 's'} not shown: no list price. ${FREE_RULE}` : '')
  // A model that played free and has since been announced under a real name is
  // the one case that gets a bar anyway: its price is known now, so the figure
  // is derivable from the tokens the run actually spent, and it prints like any
  // other cost (Andreas 2026-09-18). The provenance lives on the model's own
  // page, next to the ladder it applies to — not in a footnote under every card.
  const costNote = $derived(perTaskNote + freeNote(freeOnCost))
  const cost10Note = $derived(freeNote(freeOnCost10).trim())

  // The two bar fills, named once per section where they are used (Andreas
  // 2026-09-16) instead of spelled out inside every subtitle.
  const PROJ_KEY = { cls: 'est', text: 'estimated — the run did not finish the ladder' }
  const TRACE_KEY = { cls: 'tag', text: '† measured press-by-press, so walking into scenery is charged too' }
  const SECTIONS = [
    { id: 'performance', label: 'Performance', color: 'var(--accent)', blurb: 'How far each model gets up the task ladder, and how few turns a full clear takes.', keys: [] },
    { id: 'price', label: 'Price', color: 'var(--retry)', blurb: 'What one task costs the model, and the USD per ten turns behind it.', keys: [PROJ_KEY] },
    { id: 'speed', label: 'Speed', color: 'var(--amber)', blurb: 'How long one task takes in wall-clock time, and the turns per minute behind it.', keys: [PROJ_KEY] },
    { id: 'efficiency', label: 'Efficiency', color: 'var(--green)', blurb: 'What a model gets out of each turn: turns, inputs, output tokens, how directly it walks, and what battles cost it.', keys: [PROJ_KEY, TRACE_KEY] },
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
          {#if s.keys.length}
            <p class="keys faint">{#each s.keys as k (k.cls)}<span class="keyitem"><span class="key {k.cls}"></span>{k.text}</span>{/each}</p>
          {/if}
        </header>
        {#if s.id === 'performance'}
          <BarCard title="Performance" subtitle={`How far up the ${nGates} tasks · full clears rise above the line · Higher is better`}
            entries={performance} line={{ frac: PERF_LINE, label: '100%' }} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={300} {oninspect} />
        {:else if s.id === 'price'}
          <BarCard title="Cost per task*" subtitle="Average USD one task costs · Lower is better"
            entries={cost} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={300} {oninspect} dimmed={dimUnless('perTask')} note={costNote} />
          <PlotCard kind="cost" {pool} {onpick} {picker} {highlight} {pinned} />
          <BarCard title="Cost per 10 turns" subtitle="Average USD for ten turns, every call included · Lower is better" entries={cost10} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} note={cost10Note} />
        {:else if s.id === 'speed'}
          <BarCard title="Time per task*" subtitle="Average minutes one task takes · Lower is better"
            entries={time} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={300} {oninspect} dimmed={dimUnless('perTask')} note={perTaskNote} />
          <PlotCard kind="time" {pool} {onpick} {picker} {highlight} {pinned} />
          <BarCard title="Turns per minute" subtitle="Wall clock over the whole run · Higher is better" entries={speed} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} />
        {:else}
          <BarCard title="Turns per task*" subtitle="Average turns one task takes · Lower is better"
            entries={turnsPerTask} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={300} {oninspect} dimmed={dimUnless('turns')} note={perTaskNote} />
          <BarCard title="Inputs per turn" subtitle="Average game inputs one turn carries — buttons and waits · hover for the button mix"
            entries={inputsPerTurn} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} dimmed={dimUnless('inputs')} note={inputsNote} />
          <BarCard title="Output tokens per turn*" subtitle="Average completion tokens one turn costs the model · hover for the thinking share · Lower is better"
            entries={outputTokens} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} dimmed={dimUnless('tokens')} note={tokensNote} />
          <BarCard title="Movement efficiency*" subtitle="Shortest walk ÷ steps actually taken · hover for how the steps were counted · Higher is better"
            entries={movement} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} dimmed={dimUnless('movement')} note={movementNote} />
          <BarCard title="Turns per wild battle" subtitle="Turns spent inside wild battles ÷ wild battles met · Lower is better"
            entries={wildTurns} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} dimmed={dimUnless('wild')} note={wildNote} />
          <BarCard title="Turns per trainer battle*" subtitle="Average turns a trainer fight costs · Lower is better"
            entries={trainerTurns} {picker} pickerRows={pool} {highlight} {pinned} narrowFrom={18} bars={220} {oninspect} dimmed={dimUnless('trainer')} note={trainerNote} />
        {/if}
      </section>
    {/each}
  </div>
</div>

<style>
  .body { --gut: 24px; max-width: var(--maxw); margin: 36px auto 70px; padding: 0 var(--gut); display: grid; grid-template-columns: 168px minmax(0, 1fr); gap: 32px; align-items: start; }
  .menu { position: sticky; top: 76px; display: flex; flex-direction: column; gap: 2px; }
  .menu a { display: block; padding: 8px 12px; border-left: 2px solid transparent; font-size: 13.5px; font-weight: 600; color: var(--muted); text-decoration: none; border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
  .menu a:hover { color: var(--text); background: var(--wash); }
  .menu a.on { color: var(--text); font-weight: 780; border-left-color: var(--c); }
  .menu a:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .content { display: flex; flex-direction: column; gap: 56px; min-width: 0; }
  .sec { display: flex; flex-direction: column; gap: 18px; scroll-margin-top: 84px; min-width: 0; }
  .sechead h2 { font-size: 28px; font-weight: 780; letter-spacing: -.02em; margin: 0; display: flex; align-items: center; gap: 12px; }
  .sq { width: 16px; height: 16px; background: var(--c); border-radius: 2px; flex: none; }
  .sechead p { font-size: 14px; margin: 6px 0 0 28px; max-width: 76ch; line-height: 1.5; }
  /* The fill key: the same two swatches BarCard paints its bars with. Each key
     is ONE flex item — swatch and sentence together — so a wrap cannot leave a
     lone swatch at the end of a line (Andreas 2026-09-17). */
  .sechead p.keys { font-size: 11.5px; margin-top: 6px; display: flex; align-items: baseline; gap: 4px 16px; flex-wrap: wrap; }
  .keyitem { display: inline-flex; align-items: baseline; }
  .key { --c: var(--muted); display: inline-block; width: 11px; height: 11px; border: 1px solid var(--border); border-radius: 2px; margin-right: 5px; vertical-align: -2px; flex: none; }
  .key.est { background: repeating-linear-gradient(135deg, var(--c) 0 3px, color-mix(in srgb, var(--c) 30%, var(--surface)) 3px 6px); }
  .key.tag { border: none; width: 0; margin-right: 0; }
  /* Below 960 the menu lies down and pins to the top of the screen. It has to
     read as CHROME, because content slides underneath it: at the page's own
     background it was invisible between cards and then appeared as a cream
     band sawing a card in half, taking the angled model names with it (Andreas
     2026-09-17, "the top menu looks terrible squeezed and overlap"). So it
     bleeds past the page gutter to both screen edges — no strip of card
     showing either side of it — and carries a full-width rule to sit on. */
  @media (max-width: 960px) {
    .body { grid-template-columns: minmax(0, 1fr); gap: 20px; }
    .menu { top: 57px; flex-direction: row; flex-wrap: wrap; background: var(--bg); z-index: 5;
      margin: 0 calc(-1 * var(--gut)); padding: 8px var(--gut); border-bottom: 1px solid var(--faint); }
    .menu a { border-left: none; border-bottom: 2px solid transparent; border-radius: var(--radius-sm) var(--radius-sm) 0 0; }
    .menu a.on { border-bottom-color: var(--c); }
  }
  /* Phone (Andreas 2026-09-17). The page gutter drops from 24px to 10 — on a
     390px screen those 48px were a sixth of everything the charts had to draw
     in. The topbar stops being sticky at this width (TopBar.svelte), so this
     menu is the ONE thing pinned to the top and sits at 0. */
  @media (max-width: 720px) {
    .body { --gut: 10px; margin: 14px auto 48px; gap: 14px; }
    .menu { top: 0; padding: 6px var(--gut); gap: 0 2px; }
    .menu a { padding: 7px 9px; font-size: 12.5px; }
    .content { gap: 38px; }
    .sec { gap: 14px; scroll-margin-top: 52px; }
    .sechead h2 { font-size: 21px; gap: 9px; }
    .sq { width: 13px; height: 13px; }
    .sechead p { font-size: 13px; margin-left: 0; }
  }
</style>
