<script>
  import { usd, dur, perTurn } from '../lib/format.js'
  // points: [{label, x, y, openSource, completed, slug, completion, furthestGateName, turns, avgCostPerTurn, avgSPerTurn}]
  let { points = [], xLabel = '', xFormat = (v) => v, xLog = false, onpick } = $props()

  const W = 760, H = 360
  const ML = 60, MR = 124, MT = 26, MB = 46
  const PW = W - ML - MR, PH = H - MT - MB
  const lg = (v) => Math.log10(Math.max(v, 1e-9))

  // --- domains auto-fit to the points actually present (mode/filter aware) ---
  const xd = $derived((() => {
    if (!points.length) return { min: 0, max: 1 }
    const xs = points.map((p) => p.x)
    let min = Math.min(...xs), max = Math.max(...xs)
    if (xLog) return { min: Math.pow(10, lg(min) - 0.18), max: Math.pow(10, lg(max) + 0.18) }
    const pad = (max - min) || max || 1
    return { min: Math.max(0, min - pad * 0.14), max: max + pad * 0.14 }
  })())
  const yd = $derived((() => {
    if (!points.length) return { min: 0, max: 150 }
    const ys = points.map((p) => p.y)
    let min = Math.min(...ys), max = Math.max(...ys)
    const pad = (max - min) || 12
    return { min: Math.max(0, min - pad * 0.14), max: Math.min(151, max + pad * 0.14) }
  })())

  const xs = (x) => xLog
    ? ML + (lg(x) - lg(xd.min)) / (lg(xd.max) - lg(xd.min)) * PW
    : ML + (x - xd.min) / (xd.max - xd.min) * PW
  const ys = (y) => MT + (1 - (y - yd.min) / (yd.max - yd.min)) * PH

  const xticks = $derived(Array.from({ length: 5 }, (_, i) =>
    xLog ? Math.pow(10, lg(xd.min) + (lg(xd.max) - lg(xd.min)) * i / 4)
         : xd.min + (xd.max - xd.min) * i / 4))
  const yticks = $derived(Array.from({ length: 4 }, (_, i) => yd.min + (yd.max - yd.min) * i / 3))
  const ylabel = (v) => v <= 100.5 ? `${Math.round(v)}%` : ''
  const bandVisible = $derived(yd.min < 100 && yd.max > 100)

  // Pareto frontier (lower x + higher y better): upper-left envelope
  const frontier = $derived((() => {
    const sorted = [...points].sort((a, b) => a.x - b.x)
    const keep = []; let best = -Infinity
    for (const p of sorted) { if (p.y > best) { keep.push(p); best = p.y } }
    return keep
  })())
  const frontierPath = $derived(frontier.map((p) => `${xs(p.x)},${ys(p.y)}`).join(' '))
  const onFrontier = (p) => frontier.includes(p)

  let hovered = $state(null)
</script>

<div class="wrap">
  <svg viewBox={`0 0 ${W} ${H}`} class="chart" role="img" aria-label={xLabel}>
    {#if bandVisible}
      <rect x={ML} y={MT} width={PW} height={ys(100) - MT} class="zone" />
      <text x={W - MR - 4} y={MT + 13} class="zonelabel" text-anchor="end">100% clears · ↑ fewest turns to complete</text>
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

    {#each points as p (p.label)}
      <g class="pt" class:oss={p.openSource} class:front={onFrontier(p)} class:hot={hovered === p}
         onmouseenter={() => hovered = p} onmouseleave={() => hovered = null}
         onclick={() => onpick && onpick(p.slug)} role="button" tabindex="0">
        <circle cx={xs(p.x)} cy={ys(p.y)} r={onFrontier(p) ? 6 : 5} />
        <text x={xs(p.x) + 9} y={ys(p.y) + 3.5} class="plabel">{p.label.replace(/\(.*/, '')}</text>
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

<style>
  .wrap { position: relative; }
  .chart { width: 100%; height: auto; display: block; }
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
  .pt .plabel { fill: var(--muted); font-size: 10px; font-weight: 600; }
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
