<script>
  // One bar card in the Artificial Analysis idiom (Andreas 2026-09-14): title
  // and one-line subtitle top-left, an optional model picker top-right, then
  // bars in vendor colour with the value printed inside, a vendor mark under
  // each bar and the run alias angled beneath it. Every bar card on the page
  // is this component so the strip reads as one system.
  //
  // entries: [{row, height (0–1), label, complete, above?, tip?}] — `height`
  // is the bar's share of the bar area, `label` the value inside it, `above`
  // an optional short text printed over the bar, `tip` the hover title.
  // A bar for a projected (not completed) run is hatched. `partial: true`
  // dots the bar instead: a MEASURED value from a run that ended early (the
  // output-tokens card, Andreas 2026-09-14) — a different claim from a
  // projection, so a different fill.
  // line: optional {frac, label} — a dashed reference line at `frac` of the
  // bar area (the performance card's 100%).
  import { vendorOf } from '../lib/board.js'
  import VendorMark from './VendorMark.svelte'
  import ModelPicker from './ModelPicker.svelte'
  // `href`: the title links to a board section (#price) — the headline strip's
  // cards open the section that holds their big version.
  // `narrowFrom`: bar count from which values print vertically — 9 fits a
  // third-width card, a full-width section card holds about twice as many.
  let { title, subtitle = '', entries = [], line = null, picker = false, pickerRows = [], bars = 190, oninspect = () => {}, note = '', href = null, narrowFrom = 9 } = $props()
  // A bar shorter than this share of the area prints its value above instead.
  const INSIDE_MIN = 0.16
  // Past `narrowFrom` bars the value is printed vertically (reads bottom-to-top)
  // instead of colliding with its neighbours.
  const narrow = $derived(entries.length >= narrowFrom)
</script>

<div class="card">
  <header>
    <div class="head">
      <h3>{#if href}<a {href}>{title}<span class="arrow" aria-hidden="true">↓</span></a>{:else}{title}{/if}</h3>
      {#if subtitle}<p class="faint">{subtitle}</p>{/if}
    </div>
    {#if picker}<ModelPicker rows={pickerRows} />{/if}
  </header>
  {#if entries.length}
    <div class="plot" class:narrow style={`--bars:${bars}px; --linef:${line?.frac ?? 0}`}>
      {#if line}<div class="refline"><span>{line.label}</span></div>{/if}
      {#each entries as e (e.row.runId)}
        <button class="bar" class:complete={e.complete} style={`--h:${(e.height * 100).toFixed(1)}%; --c:${vendorOf(e.row).color}`}
                title={e.tip ?? `${e.row.model}: ${e.label}`} onclick={() => oninspect(e.row)}>
          {#if e.above}<span class="above tnum">{e.above}</span>{/if}
          <span class="fill" class:est={!e.complete} class:dots={e.partial}>
            <span class="val tnum" class:outside={e.height < INSIDE_MIN}>{e.label}</span>
          </span>
          <span class="foot">
            <VendorMark row={e.row} size={15} />
            <span class="name mono">{e.row.model}</span>
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
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
  .head { min-width: 0; }
  h3 { font-size: 16px; font-weight: 750; margin: 0; letter-spacing: -.01em; }
  header p { font-size: 11.5px; margin: 4px 0 0; line-height: 1.45; }
  h3 a { color: inherit; text-decoration: none; }
  h3 a:hover { text-decoration: underline; }
  .arrow { font-size: 12px; color: var(--faint); margin-left: 6px; }

  /* --bars is the bar area, --lane the mark + angled alias under the baseline,
     --top headroom for a value printed above a short bar. The alias is rotated
     70° so a long one (gemini-3.5-flash-lite(minimal)) ends inside the lane;
     its tail crosses the neighbouring column, as on Artificial Analysis. */
  .plot { --top: 22px; --lane: 200px; position: relative; display: flex; align-items: flex-end; gap: 6px;
    height: calc(var(--top) + var(--bars) + var(--lane)); padding: var(--top) 0 var(--lane) 44px; box-sizing: border-box; }
  .refline { position: absolute; left: 0; right: 0; bottom: calc(var(--lane) + var(--bars) * var(--linef)); border-top: 1px dashed var(--faint); pointer-events: none; }
  .refline span { position: absolute; right: 0; top: -15px; font-size: 10px; color: var(--faint); font-weight: 700; }
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
  .fill.dots { background: radial-gradient(circle at 3px 3px, var(--c) 1.3px, transparent 1.6px) 0 0 / 6px 6px, color-mix(in srgb, var(--c) 14%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--c) 55%, var(--surface)); }
  .fill.dots .val:not(.outside) { color: var(--text); text-shadow: 0 0 4px var(--surface), 0 0 4px var(--surface), 0 0 2px var(--surface), 0 0 1px var(--surface); }
  .val { color: #fff; font-size: 11px; font-weight: 750; padding-bottom: 6px; text-shadow: 0 0 2px rgba(0,0,0,.35); white-space: nowrap; }
  /* Over a hatched (projected) bar white text has no solid ground: use ink with a paper halo. */
  .fill.est .val:not(.outside) { color: var(--text); text-shadow: 0 0 3px var(--surface), 0 0 3px var(--surface), 0 0 1px var(--surface); }
  .val.outside { position: absolute; bottom: 100%; padding-bottom: 3px; color: var(--text); text-shadow: none; }
  .narrow .val { writing-mode: vertical-rl; transform: rotate(180deg); padding: 6px 0 0; font-size: 9.5px; }
  .narrow .val.outside { padding: 0 0 4px; }
  .narrow .above { writing-mode: vertical-rl; transform: rotate(180deg); font-size: 9.5px; }
  .above { position: absolute; bottom: calc(var(--h) + 3px); font-size: 11px; font-weight: 750; color: var(--text); white-space: nowrap; }
  .foot { position: absolute; top: calc(100% + 7px); left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .name { position: absolute; top: 24px; right: 50%; transform-origin: top right; transform: rotate(-70deg) translateX(0);
    font-size: 10px; color: var(--muted); white-space: nowrap; line-height: 1; }
  .empty { font-size: 12px; margin: 18px 0; }
  .note { font-size: 11px; margin: 4px 0 0; }
</style>
