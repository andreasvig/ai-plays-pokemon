<script>
  import { CHANGELOG, BENCH_VERSION } from '../lib/version.js'
  // A version can carry several dated entries, so "current" is the NEWEST entry
  // of the current version, not every entry that names it — and the key has to
  // be the date, or two v1.1 entries collide in the keyed each.
  const current = CHANGELOG.find((e) => e.version === BENCH_VERSION) ?? null
</script>

<section class="changelog">
  <h2>Changelog</h2>
  <p class="intro faint">What changed in the benchmark between versions, and how rows from different versions compare.</p>
  {#each CHANGELOG as entry (entry.date + entry.version)}
    <article class="entry" class:current={entry === current}>
      <header>
        <span class="ver mono">{entry.label}</span>
        <span class="date mono faint">{entry.date}</span>
        {#if entry === current}<span class="now">current</span>{/if}
      </header>
      <h3>{entry.title}</h3>
      <ul>
        {#each entry.items as it}
          <li><b>{it.head}</b> {it.body}</li>
        {/each}
      </ul>
    </article>
  {/each}
</section>

<style>
  .changelog { max-width: 680px; margin: 0 auto; padding: 48px 24px; }
  @media (max-width: 720px) { .changelog { padding: 24px 12px 40px; } }
  .changelog h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 6px; }
  .intro { font-size: 14px; margin: 0 0 28px; }
  .entry { padding: 22px 0 26px; border-top: 1px solid var(--border); }
  .entry header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .ver { font-size: 12px; font-weight: 700; letter-spacing: .04em; background: var(--wash); padding: 2px 8px; border-radius: var(--radius-sm); }
  .entry.current .ver { color: var(--green); background: var(--green-soft); }
  .date { font-size: 12px; }
  .now { font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--faint); }
  .entry h3 { font-size: 17px; font-weight: 720; letter-spacing: -.01em; margin: 0 0 10px; }
  .entry ul { margin: 0; padding-left: 18px; }
  .entry li { font-size: 14.5px; line-height: 1.6; color: var(--muted); margin: 0 0 8px; }
  .entry li b { color: var(--text); }
</style>
