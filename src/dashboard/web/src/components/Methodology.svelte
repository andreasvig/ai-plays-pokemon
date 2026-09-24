<script>
  // The board's Methodology page (renamed from "Estimation methods", Andreas
  // 2026-09-16, and given a table of contents over four sections: benchmark,
  // harness, performance projection, battle projections). The two big tables are
  // the original ask (2026-09-13: "a giant table which gives the numbers for the
  // estimation for all runs"). Every run,
  // every leg: actual turns and the ratio to the typical leg, the estimated
  // turns for legs the run never cleared, and the totals the cards divide by
  // the gate count. Same helpers as the cards (lib/board.js), so a cell here is
  // exactly what a card's bar is built from.
  import { estimationMatrix, trainerMatrix, vendorOf, fmtUsd, fmtMinutes, costOf, NO_PRICE, PROJECT_FROM_GATE, projectionCutoff, MIN_CLEARS_FOR_TYPICAL, MIN_BATTLE_OBSERVATIONS, MIN_FIGHTS_FOR_PROJECTION } from '../lib/board.js'
  import { GATES, gate } from '../lib/gates.js'
  import { BENCH_LABEL } from '../lib/version.js'
  import { REPO_URL } from '../lib/contact.js'
  let { rows = [], benchmarks = [], oninspect = () => {}, onchangelog = () => {} } = $props()

  const gateIds = $derived(GATES.slice(0, rows[0]?.totalGates || 12).map((g) => g.id))
  const matrix = $derived(estimationMatrix(rows, gateIds))
  const trainers = $derived(trainerMatrix(rows))
  const trainerRows = $derived([...trainers.rows].sort((a, b) => (b.eligible - a.eligible) || ((a.avg ?? 1e9) - (b.avg ?? 1e9))))
  const nGates = $derived(gateIds.length)
  // 'Reached Viridian City' → 'Viridian City', so the prose can say 'reached …' without doubling the verb.
  const fromGate = $derived((gate(PROJECT_FROM_GATE)?.name ?? PROJECT_FROM_GATE).replace(/^Reached /, ''))
  const eligible = $derived(matrix.rows.filter((x) => x.eligible).length)
  const complete = $derived(matrix.rows.filter((x) => x.complete).length)

  // Column headers: the ladder's short place names; the full checkpoint name is the tooltip.
  const SHORT = {
    left_bedroom: 'Bedroom', left_house: 'House', oaks_lab_entered: "Oak's Lab", starter_chosen: 'Starter',
    rival1_done: 'Rival 1', route1_reached: 'Route 1', viridian_reached: 'Viridian', parcel_delivered: 'Parcel',
    pokedex_received: 'Pokédex', viridian_forest_reached: 'V. Forest', pewter_reached: 'Pewter', brock_defeated: 'Brock',
  }
  const short = (g) => SHORT[g] ?? gate(g)?.name ?? g

  // turns | minutes | usd — minutes and USD per leg are the leg's turns × the
  // run's own average rate, which is also how the cards price a projection.
  let metric = $state('turns')
  const UNIT = { turns: 'turns', minutes: 'minutes', usd: 'USD' }
  // The per-turn RATE must come from the same basis as the rest of the row: a
  // run billed nothing has an avgCostPerTurn of $0.0003 (its OCR), so reading
  // that raw put twelve OCR-priced legs in a row whose total was the list price
  // — a hundredfold disagreement inside one line (caught rendered, 2026-09-18).
  const inMetric = (x, turns) => metric === 'turns' ? turns
    : metric === 'minutes' ? turns * (x.row.avgSPerTurn ?? 0) / 60
    : turns * (costOf(x.row).perTurn ?? 0)
  const fmt = (v) => v == null ? '—'
    : metric === 'turns' ? String(Math.round(v))
    : metric === 'minutes' ? fmtMinutes(v)
    : fmtUsd(v)
  // Under the USD metric a model with no cost at all has nothing to show in ANY
  // column — its played spend, its projection and its per-task figure are all
  // the absence of a price, not zero — so the whole row reads N/A rather than a
  // line of $0.00 (Andreas 2026-09-16). A run played free under a cloaked
  // listing DOES have a figure now, from the model's price today, and prints as
  // an ordinary row. Under turns and minutes both always were.
  const cell = (v, x) => {
    if (metric !== 'usd') return fmt(v)
    return costOf(x?.row).total == null ? NO_PRICE : fmt(v)
  }
  const totalIn = (x) => metric === 'turns' ? x.projected : metric === 'minutes' ? x.minutesToFinish : x.costToFinish
  const perTaskIn = (x) => metric === 'turns' ? x.turnsPerTask : metric === 'minutes' ? x.minutesPerTask : x.costPerTask
  const playedIn = (x) => metric === 'turns' ? x.played : metric === 'minutes' ? (x.row.durationS ?? 0) / 60 : costOf(x.row).total ?? 0

  // Cell tint: faster than typical shades green, slower shades red, log scale so 0.5× and 2× match.
  function tint(ratio) {
    if (ratio == null) return 'var(--surface)'
    const l = Math.log2(ratio), a = Math.round(Math.min(0.9, Math.abs(l) / 2.2) * 100)
    return l < 0 ? `color-mix(in srgb, var(--green-soft) ${a}%, var(--surface))` : `color-mix(in srgb, var(--red-soft) ${a}%, var(--surface))`
  }
  const x = (r) => r == null ? '—' : r.toFixed(2) + '×'
  const cut = $derived(projectionCutoff(gateIds))
  // The caps and the config name are PRINTED here, so they are read from the
  // benchmark registry (the same source LevelDetail uses, and the same file the
  // static site ships as data/benchmarks.json) rather than typed into the prose.
  // A cap edited in configs/ moves this page; if the registry is missing, the
  // sentence that quotes numbers is simply not rendered.
  const bench = $derived(benchmarks.find((b) => b.default) ?? benchmarks[0] ?? null)
  const CAPS = $derived((bench?.gates ?? []).map((g) => g.leg_cap_turns).filter((v) => v != null))
  const capTotal = $derived(bench?.leg_cap_total ?? CAPS.reduce((a, b) => a + b, 0))
  const officialConfig = $derived(bench?.official_config ?? null)
  const REPO_LABEL = REPO_URL.replace(/^https?:\/\//, '')
  const TOC = [
    { id: 'benchmark', label: 'Benchmark' },
    { id: 'harness', label: 'Harness' },
    { id: 'projection', label: 'Performance projection' },
    { id: 'battles', label: 'Battle projections' },
  ]
</script>

<section class="methods">
  <h2>Methodology</h2>
  <p class="intro faint">What the benchmark is, what every run shares, and how a run that stopped early still gets a number.</p>

  <!-- The old About page, merged in (Andreas 2026-09-16). The claim that matters
       most is the first one and it is the project's whole premise: the agent gets
       the screen and nothing else. -->
  <div class="lead">
    <p>PokeBench asks whether a language model can <em>play Pokémon FireRed at pace</em> — and it
      asks under the same constraint a person plays under. The model is handed the screen and
      nothing else: <b>no memory reads, no coordinates, no party or map data piped in as text</b>.
      If a human could not know it from looking, the model cannot either. Most "LLM plays Pokémon"
      setups read the game's RAM and feed the model the state; this one makes it look.</p>
    <p>The <b>harness around it is deliberately thin</b>. It shows the screen, passes the buttons
      back to the emulator, and keeps the conversation. It holds no map, no route, no walkthrough
      and no game-specific logic — what the model knows about Pokémon has to come from the model
      and from what it has already seen. So what the board ranks is the model playing the game,
      not a scaffold playing it for the model.</p>
    <p>Judging is the one place the game's memory is read, and the model never sees it. A
      <b>deterministic referee</b> watches out of band and stamps each task the instant it is
      genuinely reached, which is what makes two runs comparable at all.</p>
    <p>Everything described on this page is open: the harness, the referee, the ladder and this
      board are one repository, and a run is reproducible from it —
      <a class="repo" href={REPO_URL} target="_blank" rel="noreferrer noopener">{REPO_LABEL}</a>.</p>
    <p class="ver faint">{BENCH_LABEL} · see the <button class="link" onclick={onchangelog}>changelog</button> for what changed between versions.</p>
  </div>

  <nav class="toc" aria-label="On this page">
    {#each TOC as t (t.id)}<a href={`#${t.id}`}>{t.label}</a>{/each}
  </nav>

  <section id="benchmark" class="sec">
    <h3>Benchmark</h3>
    <ol class="steps">
      <li><b>The ladder.</b> {nGates} tasks, from leaving the bedroom to taking the Boulder Badge off Brock. Each has an exact signature in the game's own memory — a map id, a story flag, a variable, the party count — and the referee stamps it the moment that signature appears, on a read the agent cannot see and that never helps it. A stamp is first-seen and turn-numbered, so it survives a pause and a continue.</li>
      <li><b>The leg cap is the only bound.</b> Each task caps the turns a run may spend on the leg into it{#if CAPS.length}: {CAPS.join(' / ')} — {capTotal} turns in all{/if}. Spend a leg's budget without closing it and the run ends there. There are no cumulative deadlines on this ladder: a fast opening used to bank headroom that one section then burned for hundreds of turns, so the bound was moved onto the leg itself.</li>
      <li><b>Everything else is held still.</b> The same ROM (FireRed, USA/Europe Rev 1, pinned by SHA-1), the same starting save, the same frozen config{#if officialConfig}&nbsp;(<span class="mono">{officialConfig}</span>){/if}. The model and its reasoning level are the only variables.</li>
      <li><b>Scoring.</b> How far up the ladder a run got, first — including the part-way credit for the leg it died on, measured as real distance on the game's tile map, so two runs that stopped at the same task do not simply tie. Among runs that cleared all {nGates}, fewest turns. Wall-clock time is shown but never ranked: the score is turn-based, which is why an official run can be paused overnight and continued without affecting it.</li>
    </ol>
  </section>

  <section id="harness" class="sec">
    <h3>Harness</h3>
    <ol class="steps">
      <li><b>One conversation, compacted.</b> The run is a single append-only conversation. Every 20 turns — or sooner at the token cap — the model writes its own handover, a continuation summary plus a memory object, and the older conversation is replaced by it. Nothing is trimmed by a sliding window; the model decides what survives.</li>
      <li><b>A turn.</b> The model is shown the current screen (upscaled, with a tile grid drawn over it) and the text read off the screen since its last action by OCR. That is the entire input. It returns a sequence of inputs and its reasoning, ending in a concrete prediction of what the next screen will show — which it is asked to check against reality on the following turn.</li>
      <li><b>Inputs.</b> The eight buttons plus <i>wait</i>, which presses nothing and lets ~5 seconds of game run. One directional press moves one tile. A single turn can carry several inputs — that is what the <i>Inputs per turn</i> card measures, and it is the main reason two models can differ far more in turns than in game time.</li>
      <li><b>The clock.</b> The emulator is paused while the model thinks, so thinking never advances the game. Wall-clock time on the board is the run's real elapsed time, thinking included.</li>
      <li><b>Retries.</b> An answer in the wrong shape is re-asked immediately, a few times. A transient provider failure (429, 5xx, a timeout) is a separate budget with long backoff. Neither buys the run extra game turns.</li>
    </ol>
  </section>

  <section id="projection" class="sec">
  <h3>Performance projection</h3>
  <ol class="steps">
    <li><b>Legs.</b> The ladder is {nGates} tasks. A leg is the turns between two consecutive tasks; a run that cleared <i>k</i> tasks has <i>k</i> measured legs.</li>
    <li><b>Typical leg.</b> The mean turns on that leg over every run that cleared it, once at least {MIN_CLEARS_FOR_TYPICAL} runs have. Computed over all {rows.length} runs, all thinking levels.</li>
    <li><b>Pace.</b> A run's turns on its cleared legs ÷ the typical turns on those same legs. 0.80× means it clears legs in 80% of the typical turns.</li>
    <li><b>Missing legs.</b> Each leg the run never cleared is charged pace × typical. The leg it failed on is floored at the turns it actually burned there, so a run never gets credit for fewer turns than it spent.</li>
    <li><b>Eligibility.</b> Only runs that reached <b>{fromGate}</b> — task {cut?.no} of {nGates}, half the ladder — are projected; below that the legs are indoors, scripted and near-identical for every model, so they say nothing about pace. The rest are shown here but left off every card on the board.</li>
    <li><b>Time and cost.</b> The run's own total plus the estimated extra turns × its own seconds and USD per turn, then divided by the {nGates} tasks.</li>
  </ol>

  <div class="toolbar">
    <div class="seg" role="group" aria-label="Unit">
      {#each Object.entries(UNIT) as [k, label]}
        <button class:on={metric === k} onclick={() => (metric = k)}>{label}</button>
      {/each}
    </div>
    <p class="faint note">
      {complete} of {matrix.rows.length} runs cleared the ladder · {eligible - complete} more are projected · {matrix.rows.length - eligible} never reached {fromGate} and are dimmed.
      {#if metric !== 'turns'}A leg's {UNIT[metric]} = its turns × the run's own average per turn; the small ratio stays turns vs typical turns.{/if}
    </p>
  </div>

  <div class="scroll">
    <table class="m mono">
      <thead>
        <tr>
          <th class="run">Run</th>
          {#each gateIds as g, i}
            <th class="gate" title={gate(g)?.name}><span class="idx">{i + 1}</span>{short(g)}</th>
          {/each}
          <th class="gate fail" title="turns spent on the leg the run never finished">Failed leg</th>
          <th class="num">Played</th>
          <th class="num" title="turns on cleared legs ÷ typical turns on the same legs">Pace</th>
          <th class="num" title="played + estimated">To finish</th>
          <th class="num" title={`to finish ÷ ${nGates} tasks`}>Per task</th>
          <th class="num" title="turns actually played as a share of the projection">Played share</th>
        </tr>
      </thead>
      <tbody>
        {#each matrix.rows as x_ (x_.row.runId)}
          {@const r = x_.row}
          {@const est = Object.fromEntries(x_.estimates.map((e) => [e.gate, e]))}
          <tr class:dim={!x_.eligible}>
            <td class="run">
              <button class="model" onclick={() => oninspect(r)} title="open the run">
                <span class="dot" style={`--c:${vendorOf(r).color}`}></span>{r.model}
              </button>
              <span class="faint gates">{x_.cleared}/{nGates}</span>
            </td>
            {#each gateIds as g, i}
              {#if x_.legs[i]}
                {@const l = x_.legs[i]}
                <td class="cell" style={`--fill:${tint(l.ratio)}`}
                    title={`${r.model} · ${gate(g)?.name}: ${l.turns} turns${matrix.typical[g] != null ? ` · typical ${matrix.typical[g].toFixed(1)}` : ''}`}>
                  <span class="t">{cell(inMetric(x_, l.turns), x_)}</span><span class="r">{x(l.ratio)}</span>
                </td>
              {:else if est[g]}
                <td class="cell est"
                    title={`estimated: ${Math.round(est[g].turns)} turns at this run's pace${est[g].floored ? ' (floored at turns burned)' : ''}`}>
                  <span class="t">{cell(inMetric(x_, est[g].turns), x_)}</span><span class="r">est · {x(est[g].ratio)}</span>
                </td>
              {:else}
                <td class="none">·</td>
              {/if}
            {/each}
            {#if x_.tail}
              <td class="cell fail" title={`${x_.tail.turns} turns without reaching ${gate(x_.tail.gate)?.name}`}>
                <span class="t">{cell(inMetric(x_, x_.tail.turns), x_)}</span>
                <span class="r">{matrix.typical[x_.tail.gate] != null ? x(x_.tail.turns / matrix.typical[x_.tail.gate]) + ' ' : ''}{short(x_.tail.gate)}</span>
              </td>
            {:else}
              <td class="cell fail cleared faint">cleared</td>
            {/if}
            <td class="num">{cell(playedIn(x_), x_)}</td>
            <td class="num">{x(x_.pace)}</td>
            <td class="num"><b>{x_.eligible ? cell(totalIn(x_), x_) : '—'}</b></td>
            <td class="num">{x_.eligible ? cell(perTaskIn(x_), x_) : '—'}</td>
            <td class="num">
              {#if x_.playedShare != null}
                <span class="chip" class:ok={x_.playedShare >= 0.67}>{Math.round(x_.playedShare * 100)}%</span>
              {:else}—{/if}
            </td>
          </tr>
        {/each}
      </tbody>
      <tfoot>
        <tr class="ref">
          <td class="run">typical turns (mean)</td>
          {#each gateIds as g}<td class="cell">{matrix.typical[g] != null ? matrix.typical[g].toFixed(1) : '—'}</td>{/each}
          <td colspan="6"></td>
        </tr>
        <tr class="ref">
          <td class="run">runs that cleared it</td>
          {#each gateIds as g}<td class="cell">{matrix.clears[g]}</td>{/each}
          <td colspan="6"></td>
        </tr>
        <tr class="ref">
          <td class="run">slowest clear</td>
          {#each gateIds as g}<td class="cell">{matrix.slowest[g] ?? '—'}</td>{/each}
          <td colspan="6"></td>
        </tr>
      </tfoot>
    </table>
  </div>

  <p class="legend faint">
    <span class="sw" style="--fill:{tint(0.25)}"></span> faster than typical
    <span class="sw" style="--fill:{tint(4)}"></span> slower than typical
    <span class="sw est"></span> estimated, not played
    <span class="sep">·</span> ratio = leg turns ÷ typical turns
    <span class="sep">·</span> click a run to open it
  </p>

  </section>

  <section id="battles" class="sec">
  <h3>Battle projections</h3>
  <ol class="steps">
    <li><b>What counts.</b> The game's own battle counters (wild, trainer) and the trainer-defeated flags are read from memory every turn; older runs get them from their save states every 10 turns, with the turn-by-turn battle state read off each turn's screenshot (97.7% agreement on 610 labelled frames).</li>
    <li><b>Turn cost.</b> A turn belongs to the state it started in: a turn that began inside a battle is charged to that battle, a battle that began and ended inside one turn costs nothing. Losses and rematches are attempts, summed per trainer.</li>
    <li><b>Turns per trainer battle.</b> The card averages a run's turns over its trainer fights. A run enters once it has {MIN_FIGHTS_FOR_PROJECTION} trainer fights behind it (attempts counted) — one fight is too little to set a pace from. Every trainer the run did not fight — whether it stopped before them or walked past — is projected the way missing legs are, so every run is scored on the same roster: the run's <b>pace</b> — its turns on the trainers it did fight ÷ the field's typical turns on those same trainers — times the field's typical turns for that trainer. Typical = the mean over the runs that fought that trainer, used once {MIN_BATTLE_OBSERVATIONS}+ have. Each projected trainer counts as one fight. A fight against a trainer fewer than {MIN_BATTLE_OBSERVATIONS} runs have met is shown but left out of the average, since no other run is charged for that trainer. A projected trainer makes the bar hatched. The table below is the schema: every run, every trainer.</li>
    <li><b>Movement.</b> Shortest walk on the FireRed tile graph (ledges one-way, doors one step) from where a leg opened to its task — a map edge, or the NPC or trigger tiles of a story task — ÷ the overworld steps actually taken on that leg, summed over every leg of the run that has a recorded shortest path. The starter leg is scored to the Pokéball the run actually took, not the nearest of the three: picking the far ball is a choice, not a detour. The leg the run ended on counts too, credited only with the ground it gained (distance to the task when the leg opened minus the distance at the end, never below zero) against every step taken there — a run that wanders for hundreds of turns without closing a leg is not spared. Steps come from the per-input trace on new runs, from the recording on backfilled runs, else from the shortest path between per-turn polls, which is a lower bound — the card's tooltip says which.</li>
  </ol>

  <h3 class="tm-head">Trainers · turns spent on each</h3>
  <p class="faint note tm-note">Turns that started inside a fight with that trainer, every attempt summed. Under the number: the ratio to the trainer's typical turns (0.50× = half the typical), and "2 tries" when the run fought the trainer more than once. Hatched cells are projections for trainers the run did not fight: one fight at the run's pace × the trainer's typical turns. Typical = mean over the runs that fought the trainer; a column counts toward pace and projection once {MIN_BATTLE_OBSERVATIONS}+ runs have fought it. A dimmed cell is a fight that does not count: fewer than {MIN_BATTLE_OBSERVATIONS} runs have met that trainer. Dimmed runs have fewer than {MIN_FIGHTS_FOR_PROJECTION} trainer fights or no per-turn battle state.</p>
  <div class="scroll">
    <table class="m tm">
      <thead>
        <tr>
          <th class="run">Run</th>
          {#each trainers.columns as c}
            <th class="gate" class:mand={c.mandatory} title={c.name}><span class="idx">{c.mandatory ? 'mandatory' : 'optional'}</span>{c.short}</th>
          {/each}
          <th class="num" title="turns on fought trainers ÷ typical turns on the same trainers">Pace</th>
          <th class="num" title="turns over the trainer fights the run had">Fights</th>
          <th class="num" title="measured turns in trainer battles, + the projected turns for trainers not fought">Turns</th>
          <th class="num" title="turns per trainer battle, projected trainers included">Per fight</th>
        </tr>
      </thead>
      <tbody>
        {#each trainerRows as t (t.row.runId)}
          {@const r = t.row}
          <tr class:dim={!t.eligible}>
            <td class="run">
              <button class="model" onclick={() => oninspect(r)} title="open the run">
                <span class="dot" style={`--c:${vendorOf(r).color}`}></span>{r.model}
              </button>
              <span class="faint gates">{r.battleFidelity ?? '—'}</span>
            </td>
            {#each t.cells as c}
              {#if c.kind === 'fought'}
                {@const typ = trainers.columns.find((k) => k.group === c.group)?.typical}
                <td class="cell" class:lost={c.won === false} class:faint={!c.counted} style={`--fill:${tint(c.counted && typ > 0 && c.turns != null ? c.turns / typ : null)}`}
                    title={`${r.model}: ${c.turns ?? '?'} turns${c.attempts > 1 ? ` over ${c.attempts} tries` : ''}${typ != null ? ` · typical ${typ.toFixed(1)}` : ''}${c.won === false ? ' · not won' : ''}${c.counted ? '' : ` · not counted: fewer than ${MIN_BATTLE_OBSERVATIONS} runs met this trainer`}`}>
                  <span class="t">{c.turns ?? '?'}</span>
                  <span class="r">{c.counted && typ > 0 && c.turns != null ? x(c.turns / typ) : c.counted ? '' : 'not counted'}{c.attempts > 1 ? `${(c.counted && typ > 0) || !c.counted ? ' · ' : ''}${c.attempts} tries` : ''}</span>
                </td>
              {:else if c.kind === 'projected'}
                <td class="cell est" style={`--fill:${tint(c.ratio)}`} title={`projected: pace ${x(c.ratio)} × typical ${trainers.columns.find((k) => k.group === c.group)?.typical.toFixed(1)} turns`}><span class="t">{c.turns.toFixed(1)}</span><span class="r">est</span></td>
              {:else}
                <td class="none">·</td>
              {/if}
            {/each}
            <td class="num">{t.eligible ? x(t.pace) : '—'}</td>
            <td class="num" title={t.projectedCount ? `${t.attempts} fought + ${t.projectedCount} projected` : ''}>{t.attempts}{t.projectedCount ? ` +${t.projectedCount}` : ''}</td>
            <td class="num" title={t.projectedCount ? `${t.measured} measured + ${t.projected.toFixed(1)} projected` : ''}>{t.measured ?? '—'}{t.projectedCount ? ` +${t.projected.toFixed(1)}` : ''}</td>
            <td class="num"><b>{t.avg == null ? '—' : t.avg.toFixed(1)}</b></td>
          </tr>
        {/each}
      </tbody>
      <tfoot>
        <tr class="ref">
          <td class="run faint">typical (mean) · fights</td>
          {#each trainers.columns as c}
            <td class="num" class:faint={!c.usable} title={c.usable ? `mean over ${c.n} fights, used for pace and projection` : `${c.n} fight${c.n === 1 ? '' : 's'} — below ${MIN_BATTLE_OBSERVATIONS}, not used`}>{c.typical == null ? '—' : c.typical.toFixed(1)}<span class="r"> · {c.n}</span></td>
          {/each}
          <td class="num" colspan="4"></td>
        </tr>
      </tfoot>
    </table>
  </div>
  </section>
</section>


<style>
  .tm-head { font-size: 18px; font-weight: 760; margin: 32px 0 6px; }
  .tm-note { margin: 0 0 12px; max-width: 900px; }
  .m th.gate.mand { color: var(--text); }
  .m td.cell.lost { color: var(--retry); }
  .m tfoot .r { font-size: 10px; color: var(--muted); }
  .methods { max-width: 1440px; margin: 0 auto; padding: 40px 24px 56px; }
  @media (max-width: 720px) { .methods { padding: 22px 10px 40px; } }
  .methods h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 6px; }
  .intro { font-size: 14px; margin: 0 0 18px; }
  .lead { max-width: 760px; margin: 0 0 22px; }
  .lead p { font-size: 14.5px; line-height: 1.65; color: var(--muted); margin: 0 0 12px; }
  .lead em { font-style: italic; color: var(--text); }
  .lead b { color: var(--text); font-weight: 650; }
  .lead .ver { font-size: 12.5px; margin-top: 16px; }
  .lead .link { border: none; background: none; padding: 0; color: var(--accent); font: inherit; text-decoration: underline; cursor: pointer; }
  .lead .repo { color: var(--accent); text-decoration: none; font-weight: 650; word-break: break-word; }
  .lead .repo:hover { text-decoration: underline; }
  .toc { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 30px; }
  .toc a { font-size: 12px; font-weight: 650; color: var(--muted); text-decoration: none;
    padding: 5px 11px; border: 1px solid var(--border); border-radius: 999px; background: var(--surface); }
  .toc a:hover { color: var(--text); border-color: var(--muted); }
  /* The top bar is sticky, so an anchored heading has to clear it. */
  .sec { scroll-margin-top: 74px; }
  .sec h3 { font-size: 18px; font-weight: 760; margin: 0 0 10px; }
  .sec + .sec { margin-top: 30px; }

  .steps { max-width: 760px; margin: 0 0 28px; padding-left: 22px; }
  .steps li { font-size: 14px; line-height: 1.6; color: var(--muted); margin: 0 0 6px; }
  .steps b { color: var(--text); }
  .steps i { font-style: italic; }

  .toolbar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin: 0 0 12px; }
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
  .seg button { font: inherit; font-size: 12px; padding: 5px 11px; background: var(--surface); color: var(--muted); border: 0; cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--border); }
  .seg button.on { background: var(--accent-soft); color: var(--accent-ink); font-weight: 700; }
  .seg button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .note { font-size: 12.5px; margin: 0; }

  .scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .m { border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 100%; }
  .m th, .m td { border-bottom: 1px solid var(--border); border-right: 1px solid var(--border); white-space: nowrap; }
  .m th { position: sticky; top: 0; background: var(--wash); color: var(--muted); font-weight: 700; text-align: right; padding: 8px 6px; font-size: 11px; z-index: 1; }
  .m th.gate .idx { display: block; font-size: 9px; letter-spacing: .06em; color: var(--faint); }
  .m th.run, .m td.run { position: sticky; left: 0; text-align: left; background: var(--surface); z-index: 2; min-width: 190px; }
  .m th.run { background: var(--wash); z-index: 3; }
  .m td.run { padding: 4px 8px; }
  .model { font: inherit; background: none; border: 0; padding: 0; color: var(--text); cursor: pointer; display: inline-flex; align-items: center; gap: 6px; font-weight: 650; }
  .model:hover { text-decoration: underline; }
  .model:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); flex: none; }
  .gates { margin-left: 8px; font-size: 10.5px; }

  .m td.cell { background: var(--fill, var(--surface)); text-align: right; min-width: 58px; line-height: 1.15; padding: 4px 6px; font-variant-numeric: tabular-nums; }
  .m td.cell .t { font-weight: 750; display: block; }
  .m td.cell .r { font-size: 10px; color: var(--muted); display: block; }
  .m td.cell.est { background: repeating-linear-gradient(135deg, var(--wash) 0 3px, var(--surface) 3px 7px); color: var(--muted); }
  .m td.cell.est .t { font-weight: 600; }
  .m td.cell.fail { background: color-mix(in srgb, var(--amber) 10%, var(--surface)); border-left: 2px solid var(--border); }
  .m td.cell.cleared { text-align: center; font-weight: 500; }
  .m th.fail { border-left: 2px solid var(--border); }
  .m td.none { text-align: center; color: var(--faint); }
  .m td.num { text-align: right; padding: 4px 8px; font-variant-numeric: tabular-nums; }
  .m tr.dim td { opacity: .5; }
  .m tr.dim td.run { opacity: 1; color: var(--faint); }
  .m tr.dim .model { color: var(--faint); }
  .m tfoot tr.ref td { background: var(--wash); color: var(--text); font-weight: 700; }
  .m tfoot tr.ref td.run { font-weight: 600; color: var(--muted); }

  .chip { display: inline-block; font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 999px; background: var(--wash); color: var(--muted); }
  .chip.ok { background: var(--green-soft); color: var(--green); }

  .legend { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 12px; margin: 12px 0 0; }
  .sw { display: inline-block; width: 22px; height: 12px; border: 1px solid var(--border); border-radius: 2px; background: var(--fill, var(--surface)); margin-left: 8px; }
  .sw:first-child { margin-left: 0; }
  .sw.est { background: repeating-linear-gradient(135deg, var(--wash) 0 3px, var(--surface) 3px 7px); }
  .sep { color: var(--faint); }
</style>
