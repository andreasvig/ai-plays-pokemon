<script>
  // Every tile a run stood on, drawn on the FireRed world frame
  // (artifacts/route-fidelity/plan.md R4, on the page 2026-09-15). The data is
  // `data/runs/<id>/route.json`, the same document scripts/render_route.py
  // draws to PNG, so the two cannot disagree.
  //
  // Unlike the PNG we do NOT paint the walk graph's passable tiles: that needs
  // all 8473 nodes shipped to the browser. Map rectangles plus the route read
  // clearly on their own, and the payload stays the one file the run already
  // publishes. Only maps the run VISITED are drawn — how little of the world a
  // short run touches is itself the story.
  import { fetchRunRoute } from '../lib/api.js'

  let { runId = null, height = 560 } = $props()

  let route = $state(null)
  let loading = $state(false)
  let canvas = $state(null)

  $effect(() => {
    const id = runId
    route = null
    if (!id) return
    loading = true
    fetchRunRoute(id)
      .then((r) => { route = r })
      .finally(() => { loading = false })
  })

  const MARGIN = 3          // tiles of padding around the outdoor world
  const INSET_GAP = 3       // tiles between stacked indoor maps
  const SCALE = 4           // canvas px per tile before devicePixelRatio

  /** 0 → blue (first turn), .5 → green, 1 → red (last). Same ramp as the PNG. */
  function turnColour(t) {
    const k = Math.max(0, Math.min(1, t))
    return k < 0.5
      ? `rgb(40,${Math.round(80 + 280 * k)},${Math.round(220 - 280 * k)})`
      : `rgb(${Math.round(360 * (k - 0.5))},${Math.round(220 - 340 * (k - 0.5))},${Math.round(80 - 120 * (k - 0.5))})`
  }

  /** Where each map's (0,0) sits on the canvas, in tiles. */
  const layout = $derived.by(() => {
    if (!route?.maps) return null
    const maps = Object.entries(route.maps)
    const outdoor = maps.filter(([, m]) => Array.isArray(m.world))
    const indoor = maps.filter(([, m]) => !Array.isArray(m.world))
    if (!outdoor.length && !indoor.length) return null
    const xs = outdoor.flatMap(([, m]) => [m.world[0], m.world[0] + m.width])
    const ys = outdoor.flatMap(([, m]) => [m.world[1], m.world[1] + m.height])
    const x0 = (outdoor.length ? Math.min(...xs) : 0) - MARGIN
    const y0 = (outdoor.length ? Math.min(...ys) : 0) - MARGIN
    const worldW = outdoor.length ? Math.max(...xs) - Math.min(...xs) + 2 * MARGIN : 0
    const worldH = outdoor.length ? Math.max(...ys) - Math.min(...ys) + 2 * MARGIN : 0

    const at = {}
    for (const [k, m] of outdoor) at[k] = { x: m.world[0] - x0, y: m.world[1] - y0, m, inset: false }
    // Maps with no place in the world frame — every interior, and Viridian
    // Forest, which pret reaches by warp rather than by a connection — are
    // packed into columns beside it. Shelf packing, not a fixed grid: a column
    // is only as wide as the maps actually in it, so one large map does not
    // leave a gutter beside every small one.
    let colX = worldW + INSET_GAP
    let colY = MARGIN
    let colWidth = 0
    let tallest = 0
    for (const [k, m] of indoor) {
      if (colY > MARGIN && colY + m.height > Math.max(worldH, 1)) {
        colX += colWidth + INSET_GAP
        colY = MARGIN
        colWidth = 0
      }
      at[k] = { x: colX, y: colY, m, inset: true }
      colY += m.height + INSET_GAP + 2   // +2 leaves room for nothing but air
      colWidth = Math.max(colWidth, m.width)
      tallest = Math.max(tallest, colY)
    }
    return {
      at,
      w: Math.max(worldW, indoor.length ? colX + colWidth + MARGIN : 0),
      h: Math.max(worldH, tallest, 1),
      outdoor: outdoor.length,
      insets: indoor.length,
    }
  })

  /** Tile → canvas pixel centre, or null for a map the run never entered. */
  function xy(L, g, m, x, y) {
    const p = L.at[`${g}:${m}`]
    return p ? [(p.x + x + 0.5) * SCALE, (p.y + y + 0.5) * SCALE] : null
  }

  $effect(() => {
    const L = layout, r = route, el = canvas
    if (!el || !L || !r?.visits?.length) return
    const dpr = Math.min(2, (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) || 1)
    el.width = Math.ceil(L.w * SCALE * dpr)
    el.height = Math.ceil(L.h * SCALE * dpr)
    el.style.width = `${L.w * SCALE}px`
    el.style.height = `${L.h * SCALE}px`
    const c = el.getContext('2d')
    c.setTransform(dpr, 0, 0, dpr, 0, 0)
    c.clearRect(0, 0, L.w * SCALE, L.h * SCALE)

    // Canvas cannot read CSS custom properties, so sample them off the element.
    const cs = getComputedStyle(el)
    const ink = cs.getPropertyValue('--map-ink').trim() || '#9a9a96'
    const wash = cs.getPropertyValue('--map-wash').trim() || '#ececec'

    // map rectangles first, so the route draws over them
    for (const p of Object.values(L.at)) {
      c.fillStyle = wash
      c.fillRect(p.x * SCALE, p.y * SCALE, p.m.width * SCALE, p.m.height * SCALE)
      c.strokeStyle = ink
      c.lineWidth = 1
      c.strokeRect(p.x * SCALE + 0.5, p.y * SCALE + 0.5, p.m.width * SCALE - 1, p.m.height * SCALE - 1)
    }

    const visits = r.visits
    const t0 = visits[0][0]
    const t1 = Math.max(visits[visits.length - 1][0], t0 + 1)
    c.lineCap = 'round'
    c.lineJoin = 'round'
    for (let i = 0; i < visits.length - 1; i++) {
      const a = visits[i], b = visits[i + 1]
      const colour = turnColour((a[0] - t0) / (t1 - t0))
      // A scripted walk moved several tiles on one press; `fills` carries the
      // tiles in between so the line follows the ground, not a chord.
      const fill = r.fills?.[String(i)] || null
      const chain = [a.slice(2, 6), ...(fill || []), b.slice(2, 6)]
      for (let j = 0; j < chain.length - 1; j++) {
        const u = chain[j], v = chain[j + 1]
        const pu = xy(L, ...u), pv = xy(L, ...v)
        if (!pu || !pv) continue
        const sameMap = u[0] === v[0] && u[1] === v[1]
        const adjacent = sameMap && Math.abs(u[2] - v[2]) + Math.abs(u[3] - v[3]) <= 2
        // Two maps meeting in the world frame: one step across the border.
        const seam = !sameMap && Math.abs(pu[0] - pv[0]) + Math.abs(pu[1] - pv[1]) <= 2 * SCALE
        if (adjacent || seam) {
          c.strokeStyle = fill ? 'rgba(140,140,210,.55)' : colour
          c.lineWidth = fill ? 1.5 : 2
          c.beginPath(); c.moveTo(pu[0], pu[1]); c.lineTo(pv[0], pv[1]); c.stroke()
        } else {
          // A warp or a blackout: mark both ends, never a chord across the map.
          c.strokeStyle = colour
          c.lineWidth = 1.25
          for (const p of [pu, pv]) { c.beginPath(); c.arc(p[0], p[1], SCALE * 0.9, 0, 6.284); c.stroke() }
        }
      }
      if (a[6]) {   // a sample taken with a battle on
        const p = xy(L, ...a.slice(2, 6))
        if (p) { c.fillStyle = 'rgba(210,40,40,.75)'; c.beginPath(); c.arc(p[0], p[1], 1.6, 0, 6.284); c.fill() }
      }
    }
    // start and end
    for (const [v, colour] of [[visits[0], '#2850dc'], [visits[visits.length - 1], '#dc3214']]) {
      const p = xy(L, ...v.slice(2, 6))
      if (!p) continue
      c.strokeStyle = colour
      c.lineWidth = 2
      c.strokeRect(p[0] - SCALE, p[1] - SCALE, SCALE * 2, SCALE * 2)
    }
  })

  const tr = $derived(route?.transitions ?? {})
  // "teleport" is the rule-R7 relocation: a blackout dragging the player home.
  const blackouts = $derived(tr.teleport ?? 0)
</script>

{#if loading}
  <p class="faint small">Drawing the route…</p>
{:else if route?.visits?.length && layout}
  <figure class="routemap" style={`--h:${height}px`}>
    <div class="frame">
      <canvas bind:this={canvas} aria-label="Every tile this run stood on, drawn on the FireRed map"></canvas>
    </div>
    <figcaption>
      <span class="ramp" aria-hidden="true"></span>
      <span class="faint">first turn → last</span>
      <span class="dot">·</span>
      <b>{route.visits.length.toLocaleString()}</b> tiles stood on across <b>{layout.outdoor + layout.insets}</b> maps
      <span class="dot">·</span>
      {#if tr.step}<b>{tr.step.toLocaleString()}</b> steps{/if}
      {#if tr.warp}<span class="dot">·</span><b>{tr.warp}</b> door{tr.warp === 1 ? '' : 's'}{/if}
      {#if tr.jump}<span class="dot">·</span><b>{tr.jump}</b> ledge{tr.jump === 1 ? '' : 's'}{/if}
      {#if blackouts}<span class="dot">·</span><b class="bad">{blackouts}</b> blackout{blackouts === 1 ? '' : 's'}{/if}
      {#if tr.break}<span class="dot">·</span><b class="bad">{tr.break}</b> unexplained jump{tr.break === 1 ? '' : 's'}{/if}
      {#if layout.insets}<span class="dot">·</span><span class="faint">{layout.insets} map{layout.insets === 1 ? '' : 's'} with no place in the world frame — interiors, and Viridian Forest — drawn beside it, not to position</span>{/if}
    </figcaption>
  </figure>
{/if}

<style>
  .routemap { margin: 0; }
  .frame {
    /* The journey Pallet → Pewter is 48 tiles wide and 220 tall, so the map is
       a portrait strip: cap the height and let it scroll rather than stretch. */
    max-height: var(--h);
    overflow: auto;
    background: var(--wash);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 6px;
    display: flex;
    justify-content: center;
  }
  canvas {
    --map-ink: var(--border);
    --map-wash: var(--surface);
    image-rendering: pixelated;
    display: block;
  }
  figcaption { margin-top: 6px; font-size: 11.5px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  figcaption b { color: var(--text); font-weight: 650; }
  figcaption b.bad { color: var(--red); }
  .dot { color: var(--faint); }
  .ramp {
    width: 44px; height: 7px; border-radius: 4px; display: inline-block;
    background: linear-gradient(90deg, rgb(40,80,220), rgb(40,220,80), rgb(220,50,20));
  }
  .small { font-size: 12px; }
</style>
