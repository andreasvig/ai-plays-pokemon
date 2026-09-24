<script>
  import { gateShort } from '../lib/format.js'
  import { placeLabels, edgePoint } from '../lib/labels.js'
  // points: [{label, x, y, color, completed, slug, completion, furthestGateName, …}]
  // `left`: models with no x value at all ({label, slug, color}), listed beside
  // the plot rather than drawn (no ghost markers). `tip`: per-point
  // [label, value] rows for the tooltip, supplied by the caller. `left` is
  // COUNTED, never drawn — the card's legend says how many were left off
  // (Andreas 2026-09-16: the list beside the plot is gone). `projected`
  // shows in the tooltip only — every marker is drawn solid (Andreas
  // 2026-09-16: "remove the hollow notation, all should be full").
  //
  // A dot carries its MODEL's colour — the same vendor colour its bar has on
  // every card (Andreas 2026-09-16: "same color as the model bar used
  // elsewhere"), so proprietary-vs-open-source is no longer a colour at all.
  let { points = [], xLabel = '', xFormat = (v) => v, xLog = false, onpick } = $props()

  // The plot is drawn at its REAL pixel size rather than at a fixed 760×410
  // scaled down to fit (Andreas 2026-09-17: "the graphs look very bad, they
  // should in general be dynamic both to chosen models, and to resizing"). A
  // fixed viewBox squeezed into a 300px phone card shrank every tick, axis and
  // name to ~4px — unreadable for exactly the reason a screenshot is unreadable
  // when you halve it. `cw` is the container's own width (bind:clientWidth, a
  // ResizeObserver underneath), so one viewBox unit is one CSS pixel at EVERY
  // width and the type stays the size it is set in.
  let cw = $state(0)
  const W = $derived(Math.max(300, Math.round(cw) || 760))
  const narrowW = $derived(W < 560)
  // ABOVE 560 nothing changes. A fixed 760×410 viewBox stretched to fill a card
  // is arithmetically the same as scaling every length by cardWidth/760, so `k`
  // IS the old behaviour, written out — on a 1071px card the type still renders
  // at 1.41× the size it is set in, exactly as it did before. Below 760 the
  // scale stops at 1 rather than continuing down, because that downward half is
  // what produced 4px axis labels on a phone.
  const k = $derived(Math.max(W / 760, 1))
  // A phone gets a TALLER box instead. The points have to spread somewhere, and
  // height is the axis a phone has to spare.
  const H = $derived(Math.round(narrowW ? Math.min(W * 1.2, 470) : 410 * k))
  // MR is a parking lane for the names belonging to right-hand points. A phone
  // has no width to park in, so the lane goes and placeLabels keeps those names
  // inside the plot instead — 22px is just the clearance the last x tick label
  // needs, since an SVG clips to its viewBox.
  const ML = $derived(narrowW ? 34 : 60 * k)
  const MR = $derived(narrowW ? 22 : 118 * k)
  const MT = $derived(narrowW ? 30 : 26 * k)
  const MB = $derived(narrowW ? 42 : 46 * k)
  // Label text: the app is monospace throughout, so an advance of 0.6em per
  // character is the real width — no measuring pass needed.
  const FS = $derived(narrowW ? 8 : 9 * k)
  const LH = $derived(FS + 3)
  const CHAR = $derived(FS * 0.6)
  const TICK_FS = $derived(narrowW ? 9 : 10.5 * k)
  const ZONE_FS = $derived(narrowW ? 9 : 10 * k)
  const PW = $derived(W - ML - MR)
  const PH = $derived(H - MT - MB)
  const lg = (v) => Math.log10(Math.max(v, 1e-9))

  // A model that cleared at least the first task has completion > 0. The ones
  // that never did pin to the 0% floor and pile up along the bottom axis, so
  // they are left out of the plot entirely; the domains and the frontier only
  // consider the points that actually scored.
  const cleared = (p) => (p.completion ?? p.y) > 0
  const plotted = $derived(points.filter(cleared))

  // --- domains auto-fit to the plotted points (mode/filter aware) ---
  const xd = $derived((() => {
    if (!plotted.length) return { min: 0, max: 1 }
    const xs = plotted.map((p) => p.x)
    let min = Math.min(...xs), max = Math.max(...xs)
    if (xLog) return { min: Math.pow(10, lg(min) - 0.18), max: Math.pow(10, lg(max) + 0.18) }
    const pad = (max - min) || max || 1
    return { min: Math.max(0, min - pad * 0.14), max: max + pad * 0.14 }
  })())
  const yd = $derived((() => {
    if (!plotted.length) return { min: 0, max: 150 }
    const ys = plotted.map((p) => p.y)
    let min = Math.min(...ys), max = Math.max(...ys)
    const pad = (max - min) || 12
    return { min: Math.max(0, min - pad * 0.14), max: Math.min(151, max + pad * 0.14) }
  })())

  const xs = (x) => xLog
    ? ML + (lg(x) - lg(xd.min)) / (lg(xd.max) - lg(xd.min)) * PW
    : ML + (x - xd.min) / (xd.max - xd.min) * PW

  const bandVisible = $derived(yd.min < 100 && yd.max > 100)

  // When both zones are present, the y-axis is PIECEWISE so the 100% line pins
  // to the vertical centre (a clean 50/50 split): the bottom half spans 0–100%
  // progress, the top half spans the 100%-clears band ranked by fewest turns.
  // Padded top so the best clear sits below the band label. Falls back to a
  // plain linear scale when only one zone shows.
  const ydSupMax = $derived((() => {
    const sup = plotted.filter((p) => p.y > 100).map((p) => p.y)
    const m = sup.length ? Math.max(...sup) : 150
    return m + Math.max((m - 100) * 0.25, 8)
  })())
  const ys = (y) => {
    if (!bandVisible) return MT + (1 - (y - yd.min) / (yd.max - yd.min)) * PH
    const mid = MT + PH / 2
    if (y <= 100) return (MT + PH) - (y / 100) * (PH / 2)
    return mid - Math.min((y - 100) / (ydSupMax - 100), 1) * (PH / 2)
  }

  // Four ticks on a phone, five otherwise: the labels are $0.048-wide and five
  // of them touch below ~420px of plot.
  const nx = $derived(narrowW ? 4 : 5)
  const xticks = $derived(Array.from({ length: nx }, (_, i) =>
    xLog ? Math.pow(10, lg(xd.min) + (lg(xd.max) - lg(xd.min)) * i / (nx - 1))
         : xd.min + (xd.max - xd.min) * i / (nx - 1)))
  // Band shown → fixed bottom-half ticks (0/50%); the 100% divider is drawn
  // separately. Otherwise the usual 4 evenly-spaced ticks over the domain.
  const yticks = $derived(bandVisible
    ? [0, 50]
    : Array.from({ length: 4 }, (_, i) => yd.min + (yd.max - yd.min) * i / 3))
  const ylabel = (v) => v <= 100.5 ? `${Math.round(v)}%` : ''

  // Pareto frontier (lower x + higher y better): upper-left envelope
  const frontier = $derived((() => {
    const sorted = [...plotted].sort((a, b) => a.x - b.x)
    const keep = []; let best = -Infinity
    for (const p of sorted) { if (p.y > best) { keep.push(p); best = p.y } }
    return keep
  })())
  const frontierPath = $derived(frontier.map((p) => `${xs(p.x)},${ys(p.y)}`).join(' '))
  const onFrontier = (p) => frontier.includes(p)

  // The alias is printed exactly as the bar cards print it — "gpt-6-astra(low)",
  // not "gpt-6-astra · low" (Andreas 2026-09-16: "the current thinking levels
  // are too disconnected").
  const fmtLabel = (s) => s

  // Which side of its dot a label would sit on if nothing were in the way.
  const isRight = (p) => xs(p.x) > ML + PW * 0.6

  // Dot radii: the frontier's points read a touch heavier than the rest.
  const rOf = (p) => (onFrontier(p) ? 4.2 : 3.2)

  // Placement (lib/labels.js): every label is pushed clear of every other label,
  // every dot and the frontier polyline, then springs back toward its dot.
  const boxes = $derived.by(() => {
    if (!plotted.length) return new Map()
    const dots = plotted.map((p) => ({ x: xs(p.x), y: ys(p.y), r: rOf(p) }))
    const segments = []
    for (let i = 1; i < frontier.length; i++)
      segments.push([xs(frontier[i - 1].x), ys(frontier[i - 1].y), xs(frontier[i].x), ys(frontier[i].y)])
    // The 100% divider is as heavy a line as the frontier — a name laid across
    // it reads as struck through, so it repels labels too.
    if (bandVisible) segments.push([ML, ys(100), W - MR, ys(100)])
    return placeLabels(
      plotted.map((p) => ({
        key: p.label, ax: xs(p.x), ay: ys(p.y), side: isRight(p) ? 'left' : 'right',
        w: fmtLabel(p.label).length * CHAR, h: LH,
      })),
      { dots, segments, bounds: { x0: ML + 2, y0: MT + 2, x1: W - 3, y1: MT + PH + 15 } },
    )
  })
  const boxOf = (p) => boxes.get(p.label)
    ?? { x: xs(p.x) + 7, y: ys(p.y) - LH / 2, w: fmtLabel(p.label).length * CHAR, h: LH, moved: false }

  let hovered = $state(null)
</script>

<div class="wrap">
  <div class="chartcol" bind:clientWidth={cw}>
  <svg viewBox={`0 0 ${W} ${H}`} class="chart" role="img" aria-label={xLabel}
       style={`--plfs:${FS}px; --tkfs:${TICK_FS}px; --zlfs:${ZONE_FS}px`}>
    {#if bandVisible}
      <rect x={ML} y={MT} width={PW} height={ys(100) - MT} class="zone" />
      <text x={ML + 6} y={MT + 13} class="zonelabel" text-anchor="start">{narrowW ? '100% clears · ↑ fewer turns' : '100% clears · ↑ fewest turns to complete'}</text>
    {/if}

    {#each yticks as t}
      <line x1={ML} y1={ys(t)} x2={W - MR} y2={ys(t)} class="grid" />
      <text x={ML - 9} y={ys(t) + 3.5} class="ytick" text-anchor="end">{ylabel(t)}</text>
    {/each}
    {#if bandVisible}
      <line x1={ML} y1={ys(100)} x2={W - MR} y2={ys(100)} class="grid divider" />
      <text x={ML - 9} y={ys(100) + 3.5} class="ytick acc" text-anchor="end">100%</text>
    {/if}

    <line x1={ML} y1={MT + PH} x2={W - MR} y2={MT + PH} class="axis" />
    {#each xticks as t}
      <line x1={xs(t)} y1={MT + PH} x2={xs(t)} y2={MT + PH + 5} class="axis" />
      <text x={xs(t)} y={MT + PH + 18} class="xtick" text-anchor="middle">{xFormat(t)}</text>
    {/each}
    <text x={ML + PW / 2} y={H - 5} class="axislabel" text-anchor="middle">{xLabel}{xLog ? ' (log)' : ''}  →</text>
    <!-- The rotated y title is dropped on a phone: it and the "100%" tick both
         want the same 34px of left margin and were printed over each other. The
         legend under the plot already says what the y axis is, in words. -->
    {#if !narrowW}
      <text transform={`translate(14 ${MT + PH / 2}) rotate(-90)`} class="axislabel" text-anchor="middle">performance ↑</text>
    {/if}

    {#if frontier.length > 1}<polyline points={frontierPath} class="frontier" />{/if}

    <!-- Leaders first, so every line runs UNDER the dots and the names. A label
         that never left its dot needs no line. -->
    {#each plotted as p (p.label)}
      {@const b = boxOf(p)}
      {#if b.moved}
        {@const e = edgePoint(b, xs(p.x), ys(p.y))}
        <line x1={xs(p.x)} y1={ys(p.y)} x2={e[0]} y2={e[1]} class="leader" class:faded={p.faded} style={`--c:${p.color}`} />
      {/if}
    {/each}

    {#each plotted as p (p.label)}
      {@const b = boxOf(p)}
      <g class="pt" class:front={onFrontier(p)} class:hot={hovered === p} class:faded={p.faded}
         style={`--c:${p.color}`}
         onmouseenter={() => hovered = p} onmouseleave={() => hovered = null}
         onclick={() => onpick && onpick(p.slug)} onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && onpick) { e.preventDefault(); onpick(p.slug) } }} role="button" tabindex="0">
        <circle cx={xs(p.x)} cy={ys(p.y)} r={rOf(p)} />
        <text x={b.x} y={b.y + LH - 3} class="plabel">{fmtLabel(p.label)}</text>
      </g>
    {/each}
  </svg>

  {#if hovered}
    <div class="tip" style={`left:${xs(hovered.x) / W * 100}%; top:${ys(hovered.y) / H * 100}%`}>
      <div class="tip-m mono">{hovered.label}</div>
      <div class="tip-row"><span>completion</span><b class:full={hovered.completion >= 100}>{hovered.completion}%</b></div>
      {#if hovered.completion < 100}<div class="tip-row"><span>last gate</span><b>{gateShort(hovered.furthestGateName)}</b></div>{/if}
      {#if hovered.completion < 100 && hovered.legGateName && hovered.legFraction != null}<div class="tip-row"><span>next gate</span><b>{Math.round(hovered.legFraction * 100)}% to {gateShort(hovered.legGateName)}</b></div>{/if}
      {#each hovered.tip ?? [] as [k, v]}<div class="tip-row"><span>{k}</span><b class="tnum">{v}</b></div>{/each}
      {#if hovered.projected}<div class="tip-note">projected from the tasks it cleared, at its own pace and rates</div>{/if}
      <div class="tip-go">click to open run →</div>
    </div>
  {/if}
  </div>
</div>

<style>
  .wrap { position: relative; display: flex; align-items: stretch; gap: 12px; }
  .chartcol { position: relative; flex: 1 1 auto; min-width: 0; }
  .chart { width: 100%; height: auto; display: block; }
  /* The leader carries the dot's own colour so a name that had to travel is
     still visibly tied to its marker. */
  .leader { stroke: var(--c, var(--border-2)); stroke-width: 1.2; opacity: .55; }
  .leader.faded { opacity: .15; }
  .zone { fill: var(--accent-soft); opacity: .45; }
  .zonelabel { fill: var(--accent); font-size: var(--zlfs, 10px); font-weight: 700; }
  .grid { stroke: var(--border-2); stroke-width: 1; }
  .grid.divider { stroke: var(--accent); stroke-dasharray: 4 3; opacity: .55; }
  .axis { stroke: var(--border); stroke-width: 1; }
  .ytick, .xtick { fill: var(--faint); font-size: var(--tkfs, 10.5px); font-variant-numeric: tabular-nums; }
  .ytick.acc { fill: var(--accent); font-weight: 700; }
  .axislabel { fill: var(--muted); font-size: var(--tkfs, 10.5px); font-weight: 600; }
  .frontier { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 6 4;
    stroke-linecap: butt; stroke-linejoin: miter; opacity: .8; }
  .pt { cursor: pointer; }
  .pt circle { fill: var(--c); stroke: var(--bg); stroke-width: 1; }
  .pt .plabel { fill: var(--c); font-size: var(--plfs, 9px); font-weight: 650; }
  .pt.front circle { stroke: var(--c); stroke-width: 1.5; }
  .pt.hot circle { stroke: var(--text); stroke-width: 2; }
  .pt.hot .plabel { fill: var(--text); font-weight: 750; }
  /* Faded: the field behind a model page's own points (2026-09-14). */
  .pt.faded { opacity: .3; }
  .pt.faded.hot { opacity: 1; }

  .tip {
    position: absolute; transform: translate(-50%, -116%); pointer-events: none;
    background: var(--dark); color: var(--dark-text); border-radius: var(--radius); padding: 9px 11px;
    box-shadow: var(--shadow-lg); min-width: 150px; z-index: 5;
  }
  .tip-m { font-size: 12px; font-weight: 700; margin-bottom: 6px; }
  .tip-row { display: flex; justify-content: space-between; gap: 14px; font-size: 11px; line-height: 1.7; }
  .tip-row span { color: var(--dark-faint); }
  .tip-row b { font-weight: 650; }
  .tip-row b.full { color: var(--dark-green); }
  .tip-note { font-size: 10px; color: var(--dark-faint); margin-top: 4px; max-width: 190px; line-height: 1.4; }
  .tip-go { font-size: 10px; color: var(--dark-accent); margin-top: 6px; font-weight: 600; }
</style>
