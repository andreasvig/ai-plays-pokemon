<script>
  import { usd, dur, perTurn } from '../lib/format.js'
  // points: [{label, x, y, openSource, completed, slug, completion, furthestGateName, turns, avgCostPerTurn, avgSPerTurn}]
  let { points = [], xLabel = '', xFormat = (v) => v, xLog = false, onpick } = $props()

  const W = 760, H = 360
  const ML = 60, MR = 124, MT = 26, MB = 46
  const PW = W - ML - MR, PH = H - MT - MB
  const lg = (v) => Math.log10(Math.max(v, 1e-9))

  // A model that cleared at least the first checkpoint has completion > 0. The
  // ones that never cleared checkpoint 1 all pin to the 0% floor and pile up /
  // overlap along the bottom axis — so they're pulled OUT of the scatter and
  // shown as a compact list to the left (see markup), and the plot + domains +
  // frontier only consider the points that actually scored.
  const cleared = (p) => (p.completion ?? p.y) > 0
  const plotted = $derived(points.filter(cleared))
  const notCleared = $derived(points.filter((p) => !cleared(p)))

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

  const xticks = $derived(Array.from({ length: 5 }, (_, i) =>
    xLog ? Math.pow(10, lg(xd.min) + (lg(xd.max) - lg(xd.min)) * i / 4)
         : xd.min + (xd.max - xd.min) * i / 4))
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

  // Show the thinking/effort tier in the label: a model alias carries it as a
  // parenthesised suffix, e.g. "gemini-3-flash(high)" → "gemini-3-flash · high".
  // Aliases without a tier (e.g. "claude-haiku-4-5") are left untouched.
  const fmtLabel = (s) => s.replace(/\(([^)]*)\)/, ' · $1')

  // Which side of its dot a label sits on (mirrors the per-point render below).
  const isRight = (p) => xs(p.x) > ML + PW * 0.6

  // Label repel: dots cluster in y (esp. near the frontier), so naive labels at
  // a fixed dot offset overlap. Per side (left/right anchored), sort by y and
  // push any label that's within LABEL_GAP of the one above it downward; if the
  // column then overflows the plot, shift it back up and re-spread. Result: a
  // collision-free vertical column of labels, each connected to its dot by a
  // faint leader when it had to move. Keyed by label → baseline y.
  const LABEL_GAP = 11.5
  const labelY = $derived.by(() => {
    const map = new Map()
    const top = MT + 9, bottom = MT + PH + 11
    for (const side of [true, false]) {
      const col = plotted
        .filter((p) => isRight(p) === side)
        .map((p) => ({ label: p.label, y: ys(p.y) + 3.3 }))
        .sort((a, b) => a.y - b.y)
      if (!col.length) continue
      for (let i = 1; i < col.length; i++)
        if (col[i].y - col[i - 1].y < LABEL_GAP) col[i].y = col[i - 1].y + LABEL_GAP
      const overflow = col[col.length - 1].y - bottom
      if (overflow > 0)
        for (const c of col) c.y = Math.max(top, c.y - overflow)
      for (let i = col.length - 2; i >= 0; i--)
        if (col[i + 1].y - col[i].y < LABEL_GAP) col[i].y = col[i + 1].y - LABEL_GAP
      for (const c of col) map.set(c.label, c.y)
    }
    return map
  })

  let hovered = $state(null)
</script>

<div class="wrap">
  {#if notCleared.length}
    <aside class="nolist">
      <div class="nolist-h">Didn't clear<br />checkpoint 1</div>
      <div class="nolist-items">
        {#each notCleared as p (p.label)}
          <button class="nolist-item" class:oss={p.openSource}
                  onclick={() => onpick && onpick(p.slug)}
                  title={`${p.label} — click to open run`}>{fmtLabel(p.label)}</button>
        {/each}
      </div>
    </aside>
  {/if}
  <div class="chartcol">
  <svg viewBox={`0 0 ${W} ${H}`} class="chart" role="img" aria-label={xLabel}>
    {#if bandVisible}
      <rect x={ML} y={MT} width={PW} height={ys(100) - MT} class="zone" />
      <text x={ML + 6} y={MT + 13} class="zonelabel" text-anchor="start">100% clears · ↑ fewest turns to complete</text>
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
    <text transform={`translate(14 ${MT + PH / 2}) rotate(-90)`} class="axislabel" text-anchor="middle">performance ↑</text>

    {#if frontier.length > 1}<polyline points={frontierPath} class="frontier" />{/if}

    {#each plotted as p (p.label)}
      {@const rightSide = isRight(p)}
      {@const cx = xs(p.x)}
      {@const cy = ys(p.y)}
      {@const lx = rightSide ? cx - 9 : cx + 9}
      {@const ly = labelY.get(p.label) ?? cy + 3.3}
      <g class="pt" class:oss={p.openSource} class:front={onFrontier(p)} class:hot={hovered === p}
         onmouseenter={() => hovered = p} onmouseleave={() => hovered = null}
         onclick={() => onpick && onpick(p.slug)} onkeydown={(e) => { if ((e.key === 'Enter' || e.key === ' ') && onpick) { e.preventDefault(); onpick(p.slug) } }} role="button" tabindex="0">
        {#if Math.abs(ly - (cy + 3.3)) > 4}
          <line x1={rightSide ? cx - 5 : cx + 5} y1={cy} x2={lx} y2={ly - 3.3} class="leader" />
        {/if}
        <circle cx={cx} cy={cy} r={onFrontier(p) ? 6 : 5} />
        <text x={lx} y={ly}
              class="plabel" text-anchor={rightSide ? 'end' : 'start'}>{fmtLabel(p.label)}</text>
      </g>
    {/each}
  </svg>

  {#if hovered}
    <div class="tip" style={`left:${xs(hovered.x) / W * 100}%; top:${ys(hovered.y) / H * 100}%`}>
      <div class="tip-m mono">{hovered.label}</div>
      <div class="tip-row"><span>completion</span><b class:full={hovered.completion >= 100}>{hovered.completion}%</b></div>
      {#if hovered.completion < 100}<div class="tip-row"><span>last gate</span><b>{hovered.furthestGateName?.replace(/ \(.*\)$/, '')}</b></div>{/if}
      <div class="tip-row"><span>turns</span><b class="tnum">{hovered.turns}</b></div>
      <div class="tip-row"><span>cost / turn</span><b class="tnum">{usd(hovered.avgCostPerTurn)}</b></div>
      <div class="tip-row"><span>sec / turn</span><b class="tnum">{perTurn(hovered.avgSPerTurn)}</b></div>
      <div class="tip-go">click to open run →</div>
    </div>
  {/if}
  </div>
</div>

<style>
  .wrap { position: relative; display: flex; align-items: stretch; gap: 12px; }
  .chartcol { position: relative; flex: 1 1 auto; min-width: 0; }
  .nolist { flex: 0 0 118px; align-self: center; display: flex; flex-direction: column; gap: 6px; padding: 6px 0; }
  .nolist-h { font-size: 9px; font-weight: 700; color: var(--faint); text-transform: uppercase; letter-spacing: .04em; line-height: 1.3; }
  .nolist-items { display: flex; flex-direction: column; gap: 3px; }
  .nolist-item { text-align: left; border: none; background: none; padding: 0; font-size: 9.5px; font-weight: 600; color: var(--muted); cursor: pointer; line-height: 1.25; white-space: normal; }
  .nolist-item:hover { color: var(--text); text-decoration: underline; }
  .nolist-item.oss { color: #0d9488; }
  .chart { width: 100%; height: auto; display: block; }
  .leader { stroke: var(--border-2); stroke-width: 1; opacity: .8; }
  .zone { fill: var(--accent-soft); opacity: .45; }
  .zonelabel { fill: var(--accent); font-size: 10px; font-weight: 700; }
  .grid { stroke: var(--border-2); stroke-width: 1; }
  .grid.divider { stroke: var(--accent); stroke-dasharray: 4 3; opacity: .55; }
  .axis { stroke: var(--border); stroke-width: 1; }
  .ytick, .xtick { fill: var(--faint); font-size: 10.5px; font-variant-numeric: tabular-nums; }
  .ytick.acc { fill: var(--accent); font-weight: 700; }
  .axislabel { fill: var(--muted); font-size: 10.5px; font-weight: 600; }
  .frontier { fill: none; stroke: var(--accent); stroke-width: 2; stroke-dasharray: 6 4; opacity: .8; }
  .pt { cursor: pointer; }
  .pt circle { fill: var(--accent); stroke: #fff; stroke-width: 1.5; transition: r .1s; }
  .pt .plabel { fill: var(--muted); font-size: 8.5px; font-weight: 600; }
  .pt.oss circle { fill: #0d9488; }
  .pt.oss .plabel { fill: #0d9488; }
  .pt.front circle { stroke: var(--accent); stroke-width: 2; }
  .pt.hot circle { r: 7.5; filter: drop-shadow(0 1px 3px rgba(79,70,229,.5)); }
  .pt.hot .plabel { fill: var(--text); font-weight: 700; }

  .tip {
    position: absolute; transform: translate(-50%, -116%); pointer-events: none;
    background: #1b2030; color: #fff; border-radius: 8px; padding: 9px 11px;
    box-shadow: var(--shadow-lg); min-width: 150px; z-index: 5;
  }
  .tip-m { font-size: 12px; font-weight: 700; margin-bottom: 6px; }
  .tip-row { display: flex; justify-content: space-between; gap: 14px; font-size: 11px; line-height: 1.7; }
  .tip-row span { color: #9aa3b8; }
  .tip-row b { font-weight: 650; }
  .tip-row b.full { color: #4ade80; }
  .tip-go { font-size: 10px; color: #8b94e8; margin-top: 6px; font-weight: 600; }
</style>
