<script>
  import { STATIC } from '../lib/static.js'
  import { BENCH_LABEL } from '../lib/version.js'
  import HeadlineCards from './HeadlineCards.svelte'
  let {
    oninspect,
    cardRows = [],
    allRows = [],
    benchmarks = [], benchmark = '', onbench = () => {},
  } = $props()

  const selectedGoal = $derived(benchmarks.find((b) => b.id === benchmark)?.goal ?? '')
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
      referee reads game memory out-of-band and stamps the story tasks; a turn cap on every leg
      between tasks ends runs that get stuck on one section. Every run here is the same frozen append-and-compact harness
      (config-5.x), the same first-badge ladder and the same ROM — the model is the only variable.</p>
  {/if}
</section>

<!-- The ranked table that used to sit here is gone (Andreas 2026-09-14, "1a"):
     the cards and plots ARE the board, and the Methodology page is the
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


<style>
  .hero { max-width: var(--maxw); margin: 0 auto; padding: 40px 24px 8px; }
  h1 { font-size: 31px; font-weight: 700; letter-spacing: .01em; margin: 0 0 10px; }
  .tagline { max-width: 680px; font-size: 15px; line-height: 1.6; color: var(--muted); margin: 0; }
  .tagline em { color: var(--text); font-style: italic; }
  .bench { max-width: var(--maxw); margin: 18px auto 0; padding: 0 24px; }
  .bench-tabs { display: inline-flex; background: var(--wash); border-radius: var(--radius); padding: 4px; gap: 3px; }
  .bench-tabs button {
    border: none; background: none; padding: 8px 16px; border-radius: var(--radius-sm);
    font-size: 13px; font-weight: 650; color: var(--muted); transition: all .12s;
  }
  .bench-tabs button.on { background: var(--surface); color: var(--accent-ink); box-shadow: inset 0 0 0 1px var(--border); }
  .bench-goal { margin: 10px 2px 0; font-size: 13px; line-height: 1.5; color: var(--muted); max-width: 680px; }

  @media (max-width: 720px) {
    .hero { padding: 22px 10px 4px; }
    h1 { font-size: 24px; }
    .tagline { font-size: 14px; }
    .bench { margin-top: 14px; padding: 0 10px; }
    .bench-tabs { display: flex; flex-wrap: wrap; }
    .bench-tabs button { padding: 7px 12px; font-size: 12.5px; }
  }
</style>
