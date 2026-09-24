<script>
  // One model, every thinking level — the public site's run view since
  // 2026-09-14 (artifacts/model-pages/plan.md; after Artificial Analysis, but
  // all levels share one page). Top: the three headline cards over the whole
  // field with this model's levels in colour and every other model faded at
  // its best level (decision 2A). Then the collapsible level list (3A / 4B),
  // highest level first, greyed where nobody ran the level. Below: every other
  // board section over the same field, this model highlighted, eligibility
  // rules unchanged.
  import { baseModel, modelField, levelRows, ofModel, levelOf, vendorOf } from '../lib/board.js'
  import { dateShort } from '../lib/format.js'
  import HeadlineCards from './HeadlineCards.svelte'
  import LevelList from './LevelList.svelte'
  import Sections from './Sections.svelte'
  import VendorMark from './VendorMark.svelte'
  import Icon from './Icon.svelte'
  let { base = '', rows = [], catalog = [], benchmarks = [], oninspect = () => {}, onreport = null, onback = () => {}, onmethods = () => {} } = $props()

  const mine = $derived(rows.filter(ofModel(base)))
  const entry = $derived(catalog.find((m) => m.model === base) ?? null)
  const field = $derived(modelField(rows, base))
  const levels = $derived(levelRows(rows, entry, base))
  const benchmarked = $derived(levels.filter((l) => !l.absent).length)
  const highlight = $derived(ofModel(base))
  const vendor = $derived(mine.length ? vendorOf(mine[0]) : null)
  const bestRank = $derived(mine.length ? Math.min(...mine.map((r) => r.rank ?? Infinity)) : null)

  // Expanded levels: all closed to begin with (Andreas 2026-09-14); a click on
  // a level row or on one of this model's own bars opens that level.
  let open = $state(new Set())
  $effect(() => { base; open = new Set() })   // a new model → everything closed again
  function toggle(k) { const next = new Set(open); if (next.has(k)) next.delete(k); else next.add(k); open = next }
  function inspect(r) {
    if (!highlight(r)) return oninspect(r)
    const k = levelOf(r.model) ?? '_'
    if (!open.has(k)) open = new Set([...open, k])
    document.getElementById(`level-${k}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
</script>

<section class="page">
  <div class="bar">
    <button class="btn ghost" onclick={() => onback()}><Icon name="back" size={13} /> Board</button>
  </div>
  <header class="mhead">
    {#if mine.length}<VendorMark row={mine[0]} size={34} />{/if}
    <div class="titles">
      <h1>{base}</h1>
      <p class="sub faint">
        {#if vendor}{vendor.label}{/if}
        {#if entry?.openrouter_id}<span class="sep">·</span><span class="mono">{entry.openrouter_id}</span>{/if}
        {#if entry?.released}<span class="sep">·</span>released {dateShort(entry.released)}{/if}
        <span class="sep">·</span>{benchmarked} of {levels.length} thinking level{levels.length === 1 ? '' : 's'} benchmarked
        {#if bestRank != null && Number.isFinite(bestRank)}<span class="sep">·</span>best level ranked #{bestRank} on the board{/if}
      </p>
    </div>
  </header>

  {#if !mine.length}
    <p class="none">No published run for <span class="mono">{base}</span>.</p>
  {:else}
    <HeadlineCards rows={field} pool={rows} oninspect={inspect} {highlight} />

    <section class="block">
      <header class="sechead">
        <h2>Thinking levels</h2>
        <p class="faint">Every level this model can run at, highest first. Expand one for the run: each gate's turn, the wall time and cost when it was reached, turns on the leg against the leg cap, how directly it walked, and the recording.</p>
      </header>
      <LevelList {levels} {open} ontoggle={toggle} {benchmarks} {onreport} />
    </section>

    <section class="block">
      <header class="sechead">
        <h2>Against the field</h2>
        <p class="faint">The board's sections with this model's levels in colour and every other model faded at its best level; the picker on each card changes the field, this model's levels stay. A level that has not reached far enough for a reliable figure is left off a card here exactly as on the board — see <button class="link" onclick={() => onmethods()}>Methodology</button>.</p>
      </header>
    </section>
    <!-- The whole board, not the collapsed field: the picker must offer every
         model at every level; the pinned rows keep this model's levels in. -->
    <Sections pool={rows} oninspect={inspect} onpick={(slug) => { const r = rows.find((x) => x.slug === slug); if (r) inspect(r) }} {highlight} pinned={highlight} />
  {/if}
</section>

<style>
  .page { min-width: 0; }
  .bar { max-width: var(--maxw); margin: 0 auto; padding: 18px 24px 0; }
  .mhead { max-width: var(--maxw); margin: 0 auto; padding: 14px 24px 0; display: flex; align-items: center; gap: 16px; }
  .titles { min-width: 0; }
  h1 { font-size: 30px; font-weight: 780; letter-spacing: -.02em; margin: 0; font-family: var(--mono); }
  .sub { font-size: 13px; margin: 6px 0 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
  .sep { opacity: .5; }
  .none { max-width: var(--maxw); margin: 24px auto; padding: 0 24px; color: var(--muted); }
  .block { max-width: var(--maxw); margin: 40px auto 0; padding: 0 24px; display: flex; flex-direction: column; gap: 16px; }
  .sechead h2 { font-size: 24px; font-weight: 780; letter-spacing: -.02em; margin: 0; }
  .sechead p { font-size: 14px; margin: 6px 0 0; max-width: 760px; line-height: 1.5; }
  .link { border: none; background: none; padding: 0; color: var(--accent); font: inherit; text-decoration: underline; cursor: pointer; }
  .page :global(.body) { margin-top: 18px; }

  @media (max-width: 720px) {
    .bar { padding: 12px 10px 0; }
    .mhead { padding: 12px 10px 0; gap: 12px; }
    h1 { font-size: 22px; }
    .none, .block { padding: 0 10px; }
    .block { margin-top: 28px; gap: 12px; }
    .sechead h2 { font-size: 19px; }
    .sechead p { font-size: 13px; }
  }
</style>
