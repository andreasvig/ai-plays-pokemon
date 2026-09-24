<script>
  // One bar card in the Artificial Analysis idiom (Andreas 2026-09-14): title
  // and one-line subtitle top-left, an optional model picker top-right, then
  // bars in vendor colour with the value printed inside, a vendor mark under
  // each bar and the run alias angled beneath it. Every bar card on the page
  // is this component so the strip reads as one system.
  //
  // entries: [{row, height (0–1), label, complete, above?, tip?, tag?}] —
  // `height` is the bar's share of the bar area, `label` the value inside it,
  // `above` an optional short text printed over the bar, `tip` the hover title.
  // A bar whose run did not finish the ladder is HATCHED — whether the number is
  // projected or measured over the part it played (Andreas 2026-09-16: one fill,
  // not two; the dotted variant is gone).
  // `tag`: a short glyph printed after the model name, explained by a matching
  // line in `note` — the movement card marks the runs measured from the
  // per-input trace this way.
  // line: optional {frac, label} — a dashed reference line at `frac` of the
  // bar area (the performance card's 100%).
  import { vendorOf } from '../lib/board.js'
  import VendorMark from './VendorMark.svelte'
  import ModelPicker from './ModelPicker.svelte'
  // `href`: the title links to a board section (#price) — the headline strip's
  // cards open the section that holds their big version.
  // `narrowFrom`: bar count from which values print vertically — 9 fits a
  // third-width card, a full-width section card holds about twice as many.
  // `highlight`: (row) => boolean — when given, bars whose row fails it are
  // FADED (a model page, 2026-09-14: this model's levels in colour, the rest
  // of the field at low opacity, name kept, value dropped).
  let { title, subtitle = '', entries = [], line = null, picker = false, pickerRows = [], bars = 190, oninspect = () => {}, note = '', href = null, narrowFrom = 9, highlight = null, pinned = null, dimmed = null } = $props()
  const faded = (e) => !!highlight && !highlight(e.row)
  // A bar shorter than this share of the area prints its value above instead.
  const INSIDE_MIN = 0.16

  // --- geometry, derived from the width the plot ACTUALLY has ---
  // Andreas 2026-09-17: "we can use way more width for the bars … they should in
  // general be dynamic both to chosen models, and to resizing". Every number
  // below comes from `plotW` (bind:clientWidth → a ResizeObserver) and from the
  // entries themselves, so un-ticking models widens the bars, and a phone gets a
  // different lane, angle and type size from a desktop instead of a layout tuned
  // once for a 13-bar card and left there.
  let plotW = $state(0)
  // Mono advance per character, in em — the whole app is monospace, so text
  // widths are arithmetic and need no measuring pass (the same constant the
  // scatter's label placement uses).
  const CH = 0.6
  const RAD = Math.PI / 180
  // 370, not 420: at 420 this also caught the three headline cards, which are a
  // third of a desktop board and want the full-size names. Below ~370px of plot
  // a 28-character name at 10px costs an eighth of the width in left pad alone,
  // and that is the point where the trade is worth making.
  const tight = $derived(plotW > 0 && plotW < 370)
  const gap = $derived(tight ? 3 : 6)

  // The angled alias under each bar sets BOTH the depth of the lane it hangs in
  // and how far the leftmost one reaches back past the first bar — which is the
  // only reason the plot carries a left pad at all. A steeper angle on a phone
  // trades a little readability for ~20px of bar area and ~35px of height.
  const nameFS = $derived(tight ? 8.5 : 10)
  const angle = $derived(tight ? 76 : 70)
  const nameChars = $derived(entries.reduce((m, e) => Math.max(m, e.row.model.length + (e.tag ? 1 : 0)), 0))
  const nameLen = $derived(nameChars * nameFS * CH)
  // +36: the name hangs 31px below the baseline before it starts rotating
  // (.foot at 100%+7px, .name at 24px inside it), plus a descender and a little
  // air before the footnote under the card.
  const lane = $derived(Math.min(Math.round(nameLen * Math.sin(angle * RAD)) + 36, 240))
  // The name's right end sits on the first bar's CENTRE, so half a bar of the
  // reach is paid for by the bar itself; 10px is that allowance.
  const padL = $derived(Math.min(Math.max(Math.round(nameLen * Math.cos(angle * RAD)) - 10, 6), 64))
  const per = $derived(entries.length ? Math.max(plotW - padL, 0) / entries.length : 0)

  // Past `narrowFrom` bars — or whenever a bar is narrower than its own printed
  // value — the value turns vertical (reads bottom-to-top) instead of colliding
  // with its neighbours. The width test is what makes this follow the selection:
  // three models keep their horizontal "$0.28", thirteen do not.
  const valChars = $derived(entries.reduce((m, e) => Math.max(m, String(e.label ?? '').length, String(e.above ?? '').length), 0))
  // The +12 is clearance, not fit: "1.4" is 19.8px of type and a 24.5px bar
  // technically holds it, but with 2px to spare either side the row of values
  // reads as one smear (measured at 390px, Andreas 2026-09-17).
  const narrow = $derived(entries.length >= narrowFrom || (per > 0 && per < valChars * 11 * CH + 12))
</script>

<div class="card">
  <!-- Title and picker share one line; the subtitle sits UNDER both and gets the
       whole card width. Inside the flex row it was the only shrinkable item, so
       on a phone the picker squeezed it to about twelve characters a line
       (Andreas 2026-09-17: "all of the explanation … wrap weirdly and early"). -->
  <header>
    <div class="topline">
      <h3>{#if href}<a {href}>{title}<span class="arrow" aria-hidden="true">↓</span></a>{:else}{title}{/if}</h3>
      {#if picker}<ModelPicker rows={pickerRows} {pinned} {dimmed} />{/if}
    </div>
    {#if subtitle}<p class="faint">{subtitle}</p>{/if}
  </header>
  {#if entries.length}
    <div class="plot" class:narrow bind:clientWidth={plotW}
         style={`--bars:${bars}px; --linef:${line?.frac ?? 0}; --lane:${lane}px; --padl:${padL}px; --gap:${gap}px; --namefs:${nameFS}px; --rot:${-angle}deg`}>
      <!-- Line and tag are siblings, not parent and child: the dashed rule is
           meant to run BEHIND the bars, but the tag was inheriting that and its
           "1" disappeared under the last bar whenever few models were selected
           and the bars were tall (Andreas 2026-09-17). -->
      {#if line}
        <div class="refline"></div>
        <span class="reftag">{line.label}</span>
      {/if}
      {#each entries as e (e.row.runId)}
        <button class="bar" class:complete={e.complete} class:faded={faded(e)} style={`--h:${(e.height * 100).toFixed(1)}%; --c:${vendorOf(e.row).color}`}
                title={e.tip ?? `${e.row.model}: ${e.label}`} onclick={() => oninspect(e.row)}>
          {#if e.above}<span class="above tnum">{e.above}</span>{/if}
          <span class="fill" class:est={!e.complete}>
            <span class="val tnum" class:outside={e.height < INSIDE_MIN}>{e.label}</span>
          </span>
          <span class="foot">
            <VendorMark row={e.row} size={15} />
            <span class="name mono">{e.row.model}{#if e.tag}<span class="tag">{e.tag}</span>{/if}</span>
          </span>
        </button>
      {/each}
    </div>
  {:else}
    <p class="empty faint">No model selected. Use the picker to add one.</p>
  {/if}
  {#if note}<p class="note faint">{note}</p>{/if}
</div>

<style>
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px 18px 12px; box-shadow: var(--shadow); min-width: 0; }
  header { margin-bottom: 10px; }
  .topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; letter-spacing: -.01em; min-width: 0; }
  header p { font-size: 11.5px; margin: 5px 0 0; line-height: 1.45; max-width: 76ch; }
  h3 a { color: inherit; text-decoration: none; }
  h3 a:hover { text-decoration: underline; }
  .arrow { font-size: 12px; color: var(--faint); margin-left: 6px; }

  /* --bars is the bar area, --lane the mark + angled alias under the baseline,
     --top headroom for a value printed above a short bar. The alias is rotated
     70° so a long one (gemini-3.5-flash-lite(minimal)) ends inside the lane;
     its tail crosses the neighbouring column, as on Artificial Analysis. */
  .plot { --top: 22px; --lane: 200px; --padl: 44px; --gap: 6px; position: relative; display: flex; align-items: flex-end; gap: var(--gap);
    height: calc(var(--top) + var(--bars) + var(--lane)); padding: var(--top) 0 var(--lane) var(--padl); box-sizing: border-box; }
  .refline { position: absolute; left: 0; right: 0; bottom: calc(var(--lane) + var(--bars) * var(--linef)); border-top: 1px dashed var(--faint); pointer-events: none; }
  /* Above the bars (z-index), and carrying a paper halo — the same trick the
     value over a hatched bar uses — so it reads whatever it lands on. */
  .reftag { position: absolute; right: 0; bottom: calc(var(--lane) + var(--bars) * var(--linef) + 3px); z-index: 3;
    font-size: 10px; color: var(--faint); font-weight: 700; pointer-events: none;
    text-shadow: 0 0 3px var(--surface), 0 0 3px var(--surface), 0 0 2px var(--surface); }
  .bar { position: relative; flex: 1 1 0; min-width: 0; height: var(--bars); border: none; background: none; padding: 0; cursor: pointer;
    display: flex; flex-direction: column; justify-content: flex-end; align-items: center; font: inherit; color: inherit; }
  .bar:hover .fill { filter: brightness(1.12); }
  .bar:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .fill { width: 100%; max-width: 40px; height: var(--h); min-height: 2px; background: var(--c); border-radius: 3px 3px 0 0; position: relative;
    display: flex; align-items: flex-end; justify-content: center; transition: height .2s; }
  .bar.complete .fill { box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); }
  /* A projected bar is hatched in the vendor colour (Andreas 2026-09-13). */
  .fill.est { background: repeating-linear-gradient(135deg, var(--c) 0 4px, color-mix(in srgb, var(--c) 30%, var(--surface)) 4px 8px); }
  /* A dotted bar: measured, but over a run that ended early (Andreas 2026-09-14). */
  .val { color: #fff; font-size: 11px; font-weight: 750; padding-bottom: 6px; text-shadow: 0 0 2px rgba(0,0,0,.35); white-space: nowrap; }
  /* Over a hatched (projected) bar white text has no solid ground: use ink with a paper halo. */
  .fill.est .val:not(.outside) { color: var(--text); text-shadow: 0 0 3px var(--surface), 0 0 3px var(--surface), 0 0 1px var(--surface); }
  .val.outside { position: absolute; bottom: 100%; padding-bottom: 3px; color: var(--text); text-shadow: none; }
  .narrow .val { writing-mode: vertical-rl; transform: rotate(180deg); padding: 6px 0 0; font-size: 9.5px; }
  .narrow .val.outside { padding: 0 0 4px; }
  .narrow .above { writing-mode: vertical-rl; transform: rotate(180deg); font-size: 9.5px; }
  .above { position: absolute; bottom: calc(var(--h) + 3px); font-size: 11px; font-weight: 750; color: var(--text); white-space: nowrap; }
  .foot { position: absolute; top: calc(100% + 7px); left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .name { position: absolute; top: 24px; right: 50%; transform-origin: top right; transform: rotate(var(--rot, -70deg)) translateX(0);
    font-size: var(--namefs, 10px); color: var(--muted); white-space: nowrap; line-height: 1; }
  /* The tag glyph rides with the rotated name and is explained in the note. */
  .tag { color: var(--accent); font-weight: 750; margin-left: 2px; }
  /* Faded: the field behind a model page's own bars. The bar and mark drop to
     ~30 %, the value label goes, the name stays so the field is still readable. */
  .bar.faded .fill, .bar.faded .above { opacity: .3; }
  .bar.faded .val { visibility: hidden; }
  .bar.faded .foot { opacity: .45; }
  .bar.faded:hover .fill { opacity: .6; filter: none; }
  .empty { font-size: 12px; margin: 18px 0; }
  .note { font-size: 11px; margin: 4px 0 0; line-height: 1.5; }

  /* On a phone the card's own padding was 36 of the ~300px there were to spend.
     Giving it back is the cheapest width the bars can get (Andreas 2026-09-17). */
  @media (max-width: 720px) {
    .card { padding: 12px 10px 10px; }
    h3 { font-size: 15px; }
    .topline { gap: 8px; }
  }
</style>
