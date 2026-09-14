<script>
  import { STATIC } from '../lib/static.js'
  import { BENCH_LABEL } from '../lib/version.js'
  import HeadlineCards from './HeadlineCards.svelte'
  import SecondaryCards from './SecondaryCards.svelte'
  let {
    stats = {}, oninspect,
    cardRows = [],
    allRows = [],
    benchmarks = [], benchmark = '', onbench = () => {},
  } = $props()

  const selectedGoal = $derived(benchmarks.find((b) => b.id === benchmark)?.goal ?? '')
  const selectedBench = $derived(benchmarks.find((b) => b.id === benchmark) ?? null)
  const legCaps = $derived((selectedBench?.gates ?? []).filter((g) => g.leg_cap_turns != null))
  const legCapTotal = $derived(selectedBench?.leg_cap_total ?? null)
</script>

<section class="hero">
  {#if STATIC}
    <!-- The public site (pokemon publish → GitHub Pages) is ONE benchmark, the
         first-badge ladder, and this is Andreas's description of it
         (2026-09-08). The local text below is the operator's view. -->
    <h1>{BENCH_LABEL}</h1>
    <p class="tagline">A minimal, vision-only harness for Pokémon FireRed: the model sees the screen and presses buttons,
      nothing else. It is graded on the <em>fewest agent turns to defeat the first badge</em>. Each leg
      between two milestones has a turn cap; a run that spends the cap without reaching the next milestone ends there.
      Runs that stop at the same milestone are separated by how close they walked to the next one.</p>
  {:else}
    <h1>PokeBench</h1>
    <!-- "Same harness, same config" is now a load-bearing claim rather than a
         boast: the board is partitioned to config-5.x official runs, so it is
         literally true of every row. Legacy config-3.13 runs keep their badge and
         their scorecard in History — they are not ranked here because `turns`
         (the tiebreak) counts game turns PLUS TaskMaster invocations on that
         harness and game turns only on this one. -->
    <p class="tagline">Can a language model play Pokémon FireRed <em>at pace</em>? A deterministic
      referee reads game memory out-of-band and stamps story gates; a turn cap on every leg
      between gates ends runs that get stuck on one section. Every run here is the same frozen append-and-compact harness
      (config-5.x), the same first-badge ladder and the same ROM — the model is the only variable.</p>
  {/if}
  <div class="chips">
    <span class="chip"><b>{stats.completers}</b> models at 100%</span>
    <span class="chip"><b>{stats.modelsRanked}</b> ranked</span>
    <span class="chip mono">{stats.benchmarkVersion}</span>
  </div>
  {#if legCaps.length}
    <!-- Leg caps, read from the ladder YAML through the benchmarks payload: one
         cell per gate, the cap being the most turns a run may spend on the leg
         INTO that gate. The total is the longest run the ladder allows. -->
    <div class="caps" aria-label="Turn cap per leg">
      <span class="caps-label">Turn cap per leg{#if legCapTotal != null}&nbsp;· <span class="tnum">{legCapTotal}</span> max{/if}</span>
      <ol class="caps-list">
        {#each legCaps as g, i (g.id)}
          <li class="cap" title={`${g.name}: at most ${g.leg_cap_turns} turns on the leg into this gate`}>
            <span class="cap-n tnum">{i + 1}</span>
            <span class="cap-name">{g.name}</span>
            <b class="cap-turns tnum">{g.leg_cap_turns}</b>
          </li>
        {/each}
      </ol>
    </div>
  {/if}
</section>

<!-- The ranked table that used to sit here is gone (Andreas 2026-09-14, "1a"):
     the cards and plots ARE the board, and the Estimation methods page is the
     table view. What survives of the old section is the benchmark picker,
     local only (the public site publishes a single benchmark). -->
{#if benchmarks.length && !STATIC}
  <section class="bench">
    <div class="bench-tabs" role="tablist" aria-label="Benchmark">
      {#each benchmarks as b (b.id)}
        <button role="tab" aria-selected={b.id === benchmark} class:on={b.id === benchmark} onclick={() => onbench(b.id)}>{b.name}</button>
      {/each}
    </div>
    {#if selectedGoal}<p class="bench-goal">{selectedGoal}</p>{/if}
  </section>
{/if}

<HeadlineCards rows={cardRows} pool={allRows} {oninspect} />

<!-- The per-turn measurements the cards used to lead with (Andreas 2026-09-13:
     "the old measurements should still be there, it should just be below like
     on Artificial Analysis"), plus average turns per task with projections. -->
<SecondaryCards pool={allRows} {oninspect} />

<style>
  .hero { max-width: var(--maxw); margin: 0 auto; padding: 40px 24px 8px; }
  h1 { font-size: 31px; font-weight: 700; letter-spacing: .01em; margin: 0 0 10px; }
  .tagline { max-width: 680px; font-size: 15px; line-height: 1.6; color: var(--muted); margin: 0; }
  .tagline em { color: var(--text); font-style: italic; }
  .chips { display: flex; gap: 8px; margin-top: 18px; flex-wrap: wrap; }
  .chip { font-size: 12px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); padding: 5px 10px; border-radius: var(--radius-sm); }
  .chip b { color: var(--text); font-weight: 750; }
  .caps { margin-top: 16px; }
  .caps-label { display: block; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: var(--faint); margin-bottom: 6px; }
  .caps-label .tnum { color: var(--muted); }
  .caps-list { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
  .cap { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 4px 8px 4px 6px; }
  .cap-n { font-size: 10px; color: var(--faint); min-width: 14px; text-align: right; }
  .cap-name { white-space: nowrap; }
  .cap-turns { color: var(--text); font-weight: 700; }

  .bench { max-width: var(--maxw); margin: 18px auto 0; padding: 0 24px; }
  .bench-tabs { display: inline-flex; background: var(--wash); border-radius: var(--radius); padding: 4px; gap: 3px; }
  .bench-tabs button {
    border: none; background: none; padding: 8px 16px; border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 650; color: var(--muted); transition: all .12s;
  }
  .bench-tabs button.on { background: var(--surface); color: var(--accent-ink); box-shadow: inset 0 0 0 1px var(--border); }
  .bench-goal { margin: 10px 2px 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 680px; }
</style>
