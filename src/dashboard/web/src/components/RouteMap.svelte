<script>
  // Every tile a run stood on, drawn on the real FireRed artwork
  // (artifacts/game-map-render/plan.md; the plain-rectangle version it replaces
  // was on the page for one day, 2026-09-15).
  //
  // The maps are static PNGs rendered offline from pret's own tilesets and
  // shipped in the bundle, so a run publishes nothing but its route.json and a
  // viewer downloads only the maps that run entered.
  //
  // The frame is a VIEWPORT, not a scroll box (Andreas, 2026-09-15: "make the
  // map a bit more zoomed in and then make it more like a google map where you
  // drag around and zoom in and out — this would also allow us to render this
  // more detailed"). The canvas is exactly the size of the panel and the world
  // moves under it: drag to pan, wheel or pinch to zoom, double-click to zoom
  // in. That is what lets it open at 2× instead of the fit-to-panel 1× it used
  // to need, which is the whole point — at 1× four cables in a corridor are
  // 3.5 px apart and you have to take my word for it.
  //
  // Interiors are NOT on the world map. Every building that is a place rather
  // than a corridor gets a marker on its door tile, and the marker opens the
  // building — all its floors — over the map (M10-M12).
  import { fetchRunRoute } from '../lib/api.js'
  import { tilePxOf, pngOrigin, loadAtlas, loadTrainers, loadMapImage, worldLayout, clusterLayout, markersFor, exitsFor, floorsFor, battlesFor, drawRoute, drawArrows, drawSize, buildingLabel, visitAt } from '../lib/mapatlas.js'
  import { motionClock } from '../lib/motion.js'
  import { latticeAtlas, mergeAtlas, isLattice, drawLattice } from '../lib/lattice.js'
  import { borderSides, borderRows, hasBorder } from '../lib/borders.js'
  import { kindIsKnown, battleAria } from '../lib/battle.js'
  import BattleCard from './BattleCard.svelte'

  // `onturn` is the local report link: the run detail passes a handler that
  // opens that turn's trace, and the published site passes nothing, so the map
  // is a picture there and a way into the transcript here.
  // No `height`: the panel is a SQUARE sized off the viewport (Andreas,
  // 2026-09-16: "make the whole map a square and make the size scale to the
  // height of the screen or 95% of the height"), trimmed to 88vh a moment later
  // ("shorten the map panel height by 5-10%"). The side is the lesser of the
  // column it sits in and 88vh, so it never pushes the page sideways and never
  // grows past the screen. Both dimensions are MEASURED rather than assumed —
  // they change with the window, and a stale one paints the canvas at a size
  // the frame does not have.
  let { runId = null, onturn = null } = $props()

  // The journey Pallet → Pewter is a 224-tile-tall strip, so a floor of 0.4×
  // made the `fit` button a lie: it clamped there and left most of the route
  // off the frame. The floor has to be below whatever `fit` computes, or the
  // control does not do the one thing it is named for.
  const MIN_Z = 0.08, MAX_Z = 8, OPEN_Z = 2
  /** A map's fringe, in tiles: `rows` of its own border block, which for a
   *  gen-3 tree block is 2 tiles each. One row by default, because that is what
   *  Andreas asked for after two automatic attempts — "can't we just manually
   *  augment the places where 1 row of trees would help enormously?"
   *
   *  A single row is drawn at full strength and simply ends, the way the trees
   *  around a town end. Anything thicker keeps its innermost row solid and
   *  fades the rest out, so a deliberate wide fringe still does not read as the
   *  edge of another map. */
  const bleedOf = (m, rows) => rows * Math.max(1, m?.border?.w ?? 2, m?.border?.h ?? 2)

  let route = $state(null)
  let atlas = $state(null)
  let trainers = $state(null)
  let loading = $state(false)
  let canvas = $state(null)
  let frame = $state(null)
  let ready = $state(0)            // bumped when the images for this route are in
  let hover = $state(null)         // {cx, cy, turn, key, tile}
  // Which cluster the map has walked into — null is the world. The map used to
  // open a building in a modal over itself; since 2026-09-16 the panel BECOMES
  // the building and you walk back out through its door, the chip top-left, or
  // Escape (Andreas: "the whole map should change to that sub-map with an arrow
  // to go back, or the ability to just press on the door to get back out").
  let inside = $state(null)        // { building, label }
  let back = null                  // the world's view, kept for the way out
  // A battle card shows on hover, but a CLICK pins it open (Andreas 2026-09-16:
  // "when you click on a battle it stays until you click away or on another
  // battle; dragging is not clicking away"). While something is pinned, hovering
  // another fight does not steal the card — only a click moves the pin.
  let hoverBattle = $state(null)
  let pinnedBattle = $state(null)
  const openBattle = $derived(pinnedBattle ?? hoverBattle)
  let vw = $state(880)             // the frame, in CSS px — measured, and square
  let vh = $state(880)
  let view = $state({ x: 0, y: 0, z: OPEN_Z })
  // $state: the template reads it for the grab cursor.
  let drag = $state(null)          // {px, py, moved} while a drag is live
  let pinch = null                 // {d, z}
  const pointers = new Map()

  $effect(() => {
    const id = runId
    route = null
    if (!id) return
    loading = true
    fetchRunRoute(id)
      .then((r) => {
        route = r
        // Per game: FireRed's 3:0 is Pallet Town and Emerald's is somewhere
        // else. A route with no game gets no atlas and draws on the lattice.
        const g = r?.game ?? null
        return Promise.all([loadAtlas(g), loadTrainers(g)])
      })
      .then((pair) => { if (pair) { atlas = pair[0]; trainers = pair[1] } })
      .finally(() => { loading = false })
  })

  // Real artwork laid over the synthetic lattice, per MAP: a partly-rendered
  // game draws finished maps as pictures and the rest as grids, in one frame.
  const effective = $derived(mergeAtlas(atlas, latticeAtlas(route)))
  const TPX = $derived(tilePxOf(effective, route))
  const world = $derived(worldLayout(route, effective))
  const layout = $derived(inside ? clusterLayout(inside.building, effective) : world)
  const markers = $derived(!inside && layout && route && effective ? markersFor(layout, route, effective) : [])
  const exits = $derived(inside && layout ? exitsFor(layout, effective) : [])
  const floors = $derived(inside && layout ? floorsFor(layout, effective) : [])
  const drawnOnLattice = $derived(!!layout && Object.values(layout.at).every((p) => isLattice(p.m)))
  // Fights on the maps this canvas draws. One inside a building is drawn in
  // that building's popup instead, where its tile actually is.
  const battles = $derived(layout && route ? battlesFor(layout, route) : [])

  /** Tile → viewport pixel; the same function the canvas and the markers use. */
  function place(g, m, x, y) {
    const p = layout?.at[`${g}:${m}`]
    if (!p) return null
    // minus the drawn window: an interior whose first column is a flat strip is
    // drawn from column 1, so its tile 1 sits at pixel 0.
    return [(p.x + x - p.win.x + 0.5) * TPX * view.z + view.x,
            (p.y + y - p.win.y + 0.5) * TPX * view.z + view.y]
  }
  /** A world tile's top-left corner in the viewport — for the overlay buttons. */
  const screenAt = (tx, ty) => [tx * TPX * view.z + view.x, ty * TPX * view.z + view.y]
  const onScreen = (sx, sy, pad = 40) => sx > -pad && sx < vw + pad && sy > -pad && sy < vh + pad

  // Open on the first tile of the run, zoomed in. Fit is one click away.
  function centreOn(tile, z = view.z) {
    view = { z, x: vw / 2 - (tile[0] + 0.5) * TPX * z, y: vh / 2 - (tile[1] + 0.5) * TPX * z }
  }
  function fit() {
    if (!layout) return
    // The whole route, edge to edge — the button's only claim.
    const z = Math.max(MIN_Z, Math.min(MAX_Z, vw / (layout.w * TPX), vh / (layout.h * TPX)))
    view = { z, x: (vw - layout.w * TPX * z) / 2, y: (vh - layout.h * TPX * z) / 2 }
  }
  function zoomAt(cx, cy, factor) {
    const z = Math.max(MIN_Z, Math.min(MAX_Z, view.z * factor))
    const k = z / view.z
    view = { z, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k }
  }

  /** Walk into a cluster: keep the world's view, then fit the new one. */
  function enter(m) {
    back = { ...view }
    inside = { building: m.building, label: m.name }
    hover = null
    hoverBattle = pinnedBattle = null
    fitNext = true
  }
  /** Walk back out, to exactly the view the world was left at. */
  function leave() {
    inside = null
    hover = null
    hoverBattle = pinnedBattle = null
    if (back) { view = back; back = null; fitNext = false } else fitNext = true
  }

  // A layout change (in or out) fits the new one on the next paint. Not done in
  // `enter` because the cluster layout does not exist until `inside` has been
  // read back through the derivation.
  let fitNext = $state(false)
  $effect(() => {
    const L = layout
    if (!L || !fitNext) return
    fitNext = false
    fit()
  })

  let placed = false
  $effect(() => {
    const L = layout, r = route
    if (!L || !r?.visits?.length || placed || inside) return
    placed = true
    // The FIRST PLACEABLE visit, not simply the first. Nearly every run opens
    // in the player's bedroom, and an interior has no place on the world frame
    // (it lives in its building's popup) — so `visits[0]` has no position here
    // and centring on it opened the map on the empty gap beside the world.
    const v = r.visits.find((x) => L.at[`${x[2]}:${x[3]}`])
    if (!v) { fit(); return }
    const p = L.at[`${v[2]}:${v[3]}`]
    centreOn([p.x + v[4], p.y + v[5]], OPEN_Z)
  })
  $effect(() => {
    runId                                     // a new run re-centres
    placed = false
  })

  $effect(() => {
    if (!frame) return
    const ro = new ResizeObserver(([e]) => {
      vw = Math.round(e.contentRect.width)
      vh = Math.round(e.contentRect.height)
    })
    ro.observe(frame)
    return () => ro.disconnect()
  })

  // The still layer — artwork and cables — is baked to an offscreen canvas and
  // blitted; only the chevrons are redrawn each frame. Re-stroking 1,400 cables
  // sixty times a second is not free, and this map can hold 1,400.
  let still = null
  let last = 0

  function bake() {
    const L = layout, r = route, el = canvas
    if (!el || !L || !r?.visits?.length) { still = null; return }
    const dpr = Math.min(2, (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) || 1)
    el.width = Math.ceil(vw * dpr)
    el.height = Math.ceil(vh * dpr)
    el.style.width = `${vw}px`
    el.style.height = `${vh}px`
    const off = document.createElement('canvas')
    off.width = el.width
    off.height = el.height
    const c = off.getContext('2d')
    c.setTransform(dpr, 0, 0, dpr, 0, 0)
    c.imageSmoothingEnabled = false
    const s = TPX * view.z

    // Pass 1 — the FRINGE, for the sides lib/borders.js names (measured by
    // scripts/analyse_border_edges.py, hand-overridable there). Drawn
    // first, so a neighbouring map's real ground always wins where the two
    // overlap, and a road out is never painted over at all.
    for (const [key, p] of Object.entries(L.at)) {
      if (isLattice(p.m) || !hasBorder(r.game, key, p.m)) continue
      const bimg = decoded.get(`${r.game}/${p.m.border.file}`)
      if (!bimg) continue
      const bleed = bleedOf(p.m, borderRows(r.game, key))
      const wT = p.win.w + 2 * bleed, hT = p.win.h + 2 * bleed
      const [bx, by] = screenAt(p.x - bleed, p.y - bleed)
      if (bx > vw || by > vh || bx + wT * s < 0 || by + hT * s < 0) continue
      const baked = borderPatch(bimg, r.game, key, p.m, p.win)
      if (!baked) continue
      c.drawImage(baked, bx, by, wT * s, hT * s)
    }

    // Pass 2 — the maps themselves.
    for (const p of Object.values(L.at)) {
      const win = p.win
      const [dw, dh] = [win.w, win.h]
      const [sx, sy] = screenAt(p.x, p.y)
      // Off-screen maps are not drawn at all — at 8× on Viridian Forest that is
      // most of the atlas.
      if (sx > vw || sy > vh || sx + dw * s < 0 || sy + dh * s < 0) continue
      // A map with no artwork yet draws as its coordinate lattice, in the same
      // pass and the same frame, so a partly-rendered game is not all-or-nothing.
      if (isLattice(p.m)) { drawLattice(c, { width: dw, height: dh }, sx, sy, s); continue }
      const img = decoded.get(`${r.game}/${p.m.file}`)
      if (!img) continue
      // The source rect is in the PNG's own frame. `pngOrigin` is [0, 0] for
      // every atlas whose artwork already starts at the route's origin, and the
      // tile the image starts at for a DS map, which ships map-local — see the
      // note on `pngOrigin`.
      const [pox, poy] = pngOrigin(p.m)
      c.drawImage(img, (win.x - pox) * TPX, (win.y - poy) * TPX, dw * TPX, dh * TPX,
                  sx, sy, dw * s, dh * s)
    }
    still = { off, dpr, s, lines: drawRoute(c, r, place, { scale: s }), tiles: r.visits.length }
  }

  function drawFrame(now) {
    const el = canvas
    if (!el || !still) return
    const c = el.getContext('2d')
    c.setTransform(1, 0, 0, 1, 0, 0)
    c.clearRect(0, 0, el.width, el.height)
    c.drawImage(still.off, 0, 0)
    c.setTransform(still.dpr, 0, 0, still.dpr, 0, 0)
    c.imageSmoothingEnabled = false
    drawArrows(c, still.lines, { scale: still.s, tiles: still.tiles, now })
  }

  // `loadMapImage` hands back a PROMISE, always — the shared cache is keyed on
  // one per file. A bake has to be synchronous (it runs on every pan), so the
  // images are resolved into a plain map here and `ready` is bumped when one
  // lands, which re-bakes. Reading the promise cache from inside the bake is
  // how the canvas came out blank the first time.
  const decoded = new Map()
  // One faded border field per map, baked once and reused every frame. Baking
  // matters: the fade is four gradients and a composite pass, and doing that
  // per map per frame at 60 fps while dragging is the one thing in this
  // component that would be felt.
  const borderPatches = new Map()
  function borderPatch(bimg, game, key, m, win) {
    const sides = borderSides(game, key, m)
    if (!sides.size) return null
    const rows = borderRows(game, key)
    const bleed = bleedOf(m, rows)
    const wT = win.w + 2 * bleed, hT = win.h + 2 * bleed
    const id = `${game}/${key}:${wT}x${hT}:${rows}:${TPX}`
    if (borderPatches.has(id)) return borderPatches.get(id)
    const cv = document.createElement('canvas')
    cv.width = wT * TPX
    cv.height = hT * TPX
    const g = cv.getContext('2d')
    if (!g) return null
    g.imageSmoothingEnabled = false
    const pat = g.createPattern(bimg, 'repeat')
    if (!pat) return null
    const B = bleed * TPX
    // Only the bands for the sides that were named.
    g.fillStyle = pat
    if (sides.has('up')) g.fillRect(0, 0, cv.width, B)
    if (sides.has('down')) g.fillRect(0, cv.height - B, cv.width, B)
    if (sides.has('left')) g.fillRect(0, 0, B, cv.height)
    if (sides.has('right')) g.fillRect(cv.width - B, 0, B, cv.height)

    g.globalCompositeOperation = 'destination-out'
    // A road out is never painted over, whatever this map is listed for. The
    // spans come from pret's own `connections` (scripts/render_gamemaps.py
    // open_edges) and are in the map's tiles, so they shift by the drawn
    // window exactly as everything else does.
    for (const o of m.open ?? []) {
      const a = B + (o.from - (o.side === 'up' || o.side === 'down' ? win.x : win.y)) * TPX
      const len = (o.to - o.from) * TPX
      if (o.side === 'up') g.fillRect(a, 0, len, B)
      else if (o.side === 'down') g.fillRect(a, cv.height - B, len, B)
      else if (o.side === 'left') g.fillRect(0, a, B, len)
      else if (o.side === 'right') g.fillRect(cv.width - B, a, B, len)
    }
    // Everything past the innermost row fades out. At one row that is nothing,
    // and the trees end where trees end.
    const F = Math.max(0, bleed - Math.max(1, m?.border?.w ?? 2, m?.border?.h ?? 2)) * TPX
    if (F > 0) {
      const ramp = (x0, y0, x1, y1) => {
        const gr = g.createLinearGradient(x0, y0, x1, y1)
        gr.addColorStop(0, 'rgba(0,0,0,1)')
        gr.addColorStop(1, 'rgba(0,0,0,0)')
        return gr
      }
      g.fillStyle = ramp(0, 0, F, 0); g.fillRect(0, 0, F, cv.height)
      g.fillStyle = ramp(cv.width, 0, cv.width - F, 0); g.fillRect(cv.width - F, 0, F, cv.height)
      g.fillStyle = ramp(0, 0, 0, F); g.fillRect(0, 0, cv.width, F)
      g.fillStyle = ramp(0, cv.height, 0, cv.height - F); g.fillRect(0, cv.height - F, cv.width, F)
    }
    g.globalCompositeOperation = 'source-over'
    borderPatches.set(id, cv)
    return cv
  }

  $effect(() => {
    const L = layout
    if (!L) return
    const g = route?.game
    const want = (file) => {
      if (!file) return
      const k = `${g}/${file}`
      if (decoded.has(k)) return
      decoded.set(k, null)
      loadMapImage(g, file).then((img) => { decoded.set(k, img); ready += 1 })
    }
    for (const [key, p] of Object.entries(L.at)) {
      if (isLattice(p.m)) continue
      want(p.m.file)
      // Only a border that lib/borders.js actually asks for — a map whose
      // edges did not score must not pay a request for a picture nothing draws.
      if (hasBorder(g, key, p.m)) want(p.m.border.file)
    }
  })

  $effect(() => {
    ready; vw; vh; view; layout; route; canvas
    bake()
    drawFrame(last)
  })

  // One clock for the whole map. It stops itself when the map scrolls out of
  // view, and under `prefers-reduced-motion` it never starts — the viewer gets
  // the chevrons as a still picture instead of a slower one.
  $effect(() => {
    const el = canvas
    if (!el) return
    return motionClock(el, (now) => { last = now; drawFrame(now) })
  })

  function toLocal(e) {
    const rect = canvas.getBoundingClientRect()
    return [e.clientX - rect.left, e.clientY - rect.top]
  }
  function onpointerdown(e) {
    canvas.setPointerCapture(e.pointerId)
    pointers.set(e.pointerId, toLocal(e))
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()]
      pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1 }
      drag = null
      return
    }
    const [x, y] = toLocal(e)
    drag = { px: x - view.x, py: y - view.y, moved: false }
  }
  function onpointermove(e) {
    const [x, y] = toLocal(e)
    if (pointers.has(e.pointerId)) pointers.set(e.pointerId, [x, y])
    if (pinch && pointers.size === 2) {
      const [a, b] = [...pointers.values()]
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]) || 1
      zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, d / pinch.d)
      pinch.d = d
      return
    }
    if (drag) {
      if (Math.abs(x - view.x - drag.px) > 3 || Math.abs(y - view.y - drag.py) > 3) drag.moved = true
      view = { ...view, x: x - drag.px, y: y - drag.py }
      hover = null
      return
    }
    const v = visitAt(route, place, x, y, TPX * view.z * 0.7)
    // Flip the readout near an edge so it cannot hang off the panel (2026-09-15).
    hover = v ? { cx: e.clientX, cy: e.clientY, turn: v[0], key: `${v[2]}:${v[3]}`,
                  tile: `${v[4]},${v[5]}`, battle: !!v[6],
                  flipX: e.clientX > innerWidth - 300, flipY: e.clientY < 48 } : null
  }
  function onpointerup(e) {
    pointers.delete(e.pointerId)
    if (pointers.size < 2) pinch = null
    // A click (a pointer-up that never moved) on the map itself unpins the open
    // battle card; a DRAG leaves it alone, which is the whole point of the flag.
    if (drag && !drag.moved) {
      if (pinnedBattle) pinnedBattle = null
      else if (onturn && hover) onturn(hover.turn)
    }
    drag = null
  }
  function onwheel(e) {
    e.preventDefault()
    const [x, y] = toLocal(e)
    zoomAt(x, y, Math.exp(-e.deltaY * 0.0016))
  }
  function ondblclick(e) {
    const [x, y] = toLocal(e)
    zoomAt(x, y, e.shiftKey ? 1 / 1.8 : 1.8)
  }
  function onkeydown(e) {
    const step = e.shiftKey ? 120 : 40
    const move = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key]
    if (move) { view = { ...view, x: view.x + move[0], y: view.y + move[1] }; e.preventDefault(); return }
    if (e.key === '+' || e.key === '=') { zoomAt(vw / 2, vh / 2, 1.4); e.preventDefault() }
    if (e.key === '-') { zoomAt(vw / 2, vh / 2, 1 / 1.4); e.preventDefault() }
    if (e.key === '0') { fit(); e.preventDefault() }
    if (e.key === 'Escape' && inside) { leave(); e.preventDefault() }
  }

  const mapName = (key) => atlas?.maps?.[key]?.name ?? key
</script>

{#if loading}
  <p class="faint small">Drawing the route…</p>
{:else if route?.visits?.length && layout}
  <figure class="routemap">
    <div class="frame" bind:this={frame}>
      <canvas bind:this={canvas} class:clickable={!!onturn} class:dragging={!!drag}
        tabindex="0" role="application"
        onpointerdown={onpointerdown} onpointermove={onpointermove}
        onpointerup={onpointerup} onpointercancel={onpointerup}
        onpointerleave={() => (hover = null)}
        onwheel={onwheel} ondblclick={ondblclick} onkeydown={onkeydown}
        aria-label={`Every tile this run stood on, on ${drawnOnLattice ? 'a coordinate grid' : "the game's own map"}. Drag to pan, arrow keys to move, + and - to zoom, 0 to fit.`}
      ></canvas>

      {#each markers as m (m.key)}
        {@const p = screenAt(m.tile.x + 0.5, m.tile.y + 0.5)}
        {#if onScreen(p[0], p[1])}
          <button class="marker" class:entered={m.entered}
            style={`left:${p[0]}px;top:${p[1]}px`}
            title={`${m.name}${m.entered ? '' : ' — never went in'}`}
            onpointerdown={(e) => e.stopPropagation()}
            onclick={(e) => { e.stopPropagation(); enter(m) }}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2 15 8h-2v6H3V8H1z" /></svg>
            <span class="sr">{m.name}</span>
          </button>
        {/if}
      {/each}

      {#each battles as b (b.id)}
        {@const p = screenAt(b.tile.x + 0.5, b.tile.y + 0.5)}
        {#if onScreen(p[0], p[1])}
          <button class="fight" class:trainer={b.kind === 'trainer'} class:unknown={!kindIsKnown(b)} class:pinned={pinnedBattle?.id === b.id}
            style={`left:${p[0]}px;top:${p[1]}px`}
            onpointerdown={(e) => e.stopPropagation()}
            onmouseenter={() => (hoverBattle = b)} onfocus={() => (hoverBattle = b)}
            onmouseleave={() => (hoverBattle = null)} onblur={() => (hoverBattle = null)}
            onclick={(e) => { e.stopPropagation(); pinnedBattle = pinnedBattle?.id === b.id ? null : b }}>
            <span class="sr">{battleAria(b)}</span>
          </button>
          {#if openBattle?.id === b.id}
            <!-- The viewport clips, so a fight near the top gets its card BELOW
                 the dot rather than off the frame. -->
            <div class="bcard" class:below={p[1] < 170}
              style={`left:${p[0]}px;top:${p[1] + (p[1] < 170 ? 14 : -8)}px`}>
              <BattleCard battle={b} {trainers} game={route?.game ?? null} />
            </div>
          {/if}
        {/if}
      {/each}

      {#each floors as f (f.key)}
        {@const p = screenAt(f.tile.x, f.tile.y)}
        {#if onScreen(p[0], p[1], 120)}
          <!-- clamped into the frame: the label is a fixed pixel height while the
               margin above the floor shrinks with the zoom, so at `fit` on a tall
               cluster it went off the top edge -->
          <span class="floorlab" style={`left:${Math.max(4, p[0])}px;top:${Math.max(13, p[1])}px`}>{f.label}</span>
        {/if}
      {/each}

      {#each exits as e (e.key)}
        {@const p = screenAt(e.tile.x + 0.5, e.tile.y + 0.5)}
        {#if onScreen(p[0], p[1])}
          <button class="exit" style={`left:${p[0]}px;top:${p[1]}px`}
            title={`Back out to ${e.name.replace(/([a-z])([A-Z0-9])/g, '$1 $2')}`}
            onpointerdown={(ev) => ev.stopPropagation()}
            onclick={(ev) => { ev.stopPropagation(); leave() }}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9.6 3.4 5 8l4.6 4.6" /></svg>
            <span class="sr">Back out to {e.name}</span>
          </button>
        {/if}
      {/each}

      {#if inside}
        <button class="backchip" onclick={leave}>
          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M9.6 3.4 5 8l4.6 4.6" /></svg>
          <span>{buildingLabel(inside.building)}</span>
        </button>
      {/if}

      <div class="zoombar">
        <button onclick={() => zoomAt(vw / 2, vh / 2, 1.4)} title="Zoom in">+</button>
        <button onclick={() => zoomAt(vw / 2, vh / 2, 1 / 1.4)} title="Zoom out">−</button>
        <button class="fitb" onclick={fit} title="Fit the whole route">fit</button>
        <span class="zlabel">{view.z < 1 ? view.z.toFixed(2) : view.z.toFixed(1)}×</span>
      </div>
    </div>

    {#if hover}
      <div class="tip" class:flip-x={hover.flipX} class:flip-y={hover.flipY}
        style={`left:${hover.cx}px;top:${hover.cy}px`}>
        <b>Turn {hover.turn}</b>
        <span>{mapName(hover.key)} ({hover.tile})</span>
        {#if hover.battle}<span class="bad">in a battle</span>{/if}
        {#if onturn}<span class="faint">click for the trace</span>{/if}
      </div>
    {/if}

  </figure>
{/if}

<style>
  .routemap { margin: 0; }
  .frame {
    position: relative;
    /* Square, and as tall as the screen allows. `min()` rather than a media
       query: on a narrow column the width wins, on a tall screen 88vh does, and
       `aspect-ratio` keeps the other side equal either way. */
    width: min(100%, 88vh);
    aspect-ratio: 1;
    margin: 0 auto;
    overflow: hidden;              /* a viewport, not a scroll box */
    background: var(--dark, #14161a);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    touch-action: none;            /* the canvas handles pan and pinch itself */
  }
  canvas { image-rendering: pixelated; display: block; cursor: grab; outline: none; }
  canvas:focus-visible { box-shadow: inset 0 0 0 2px var(--accent, #3b6ef5); }
  canvas.dragging { cursor: grabbing; }
  .zoombar {
    position: absolute; right: 8px; bottom: 8px; z-index: 4;
    display: flex; align-items: center; gap: 1px;
    background: rgba(16, 18, 22, .88); border: 1px solid rgba(255, 255, 255, .13);
    border-radius: 7px; padding: 3px; backdrop-filter: blur(6px);
  }
  .zoombar button {
    width: 24px; height: 24px; padding: 0; border: 0; border-radius: 5px;
    background: transparent; color: #e8ebf1; font: inherit; font-size: 15px; line-height: 1;
    cursor: pointer; display: grid; place-items: center;
  }
  .zoombar button:hover { background: rgba(255, 255, 255, .14); }
  .zoombar .fitb { width: auto; padding: 0 7px; font-size: 11px; letter-spacing: .04em; }
  .zoombar .zlabel {
    font-size: 10.5px; color: #98a0b0; padding: 0 6px 0 4px;
    font-variant-numeric: tabular-nums; min-width: 34px; text-align: right;
  }
  .backchip {
    position: absolute; left: 8px; top: 8px; z-index: 4;
    display: flex; align-items: center; gap: 4px;
    background: rgba(16, 18, 22, .88); border: 1px solid rgba(255, 255, 255, .13);
    border-radius: 7px; padding: 4px 10px 4px 5px; cursor: pointer;
    color: #e8ebf1; font: inherit; font-size: 11.5px; font-weight: 650;
    backdrop-filter: blur(6px);
  }
  .backchip:hover { background: rgba(28, 32, 40, .95); }
  .backchip svg { width: 13px; height: 13px; fill: none; stroke: currentColor; stroke-width: 2; }
  /* The way out, on the door itself. A cluster's exits are the doors you would
     have walked through, so they point back the way you came in. */
  .exit {
    position: absolute; transform: translate(-50%, -50%);
    width: 20px; height: 20px; padding: 0;
    display: grid; place-items: center;
    border-radius: 5px; border: 1.5px solid rgba(15, 18, 22, .75);
    background: rgba(236, 238, 244, .92); color: #23262e;
    cursor: pointer; box-shadow: 0 1px 4px rgba(0, 0, 0, .45);
  }
  .exit:hover, .exit:focus-visible { background: #fff; outline: 2px solid #fff; outline-offset: 1px; }
  .exit svg { width: 12px; height: 12px; fill: none; stroke: currentColor; stroke-width: 2.2; }
  .floorlab {
    position: absolute; transform: translate(0, -130%);
    pointer-events: none; white-space: nowrap;
    font-size: 10.5px; font-weight: 650; letter-spacing: .02em;
    color: #dfe4ec; text-shadow: 0 1px 3px rgba(0, 0, 0, .9), 0 0 6px rgba(0, 0, 0, .7);
  }
  .marker {
    position: absolute;
    transform: translate(-50%, -60%);
    width: 20px; height: 20px; padding: 0;
    display: grid; place-items: center;
    border-radius: 5px;
    border: 1.5px solid rgba(15, 18, 22, .75);
    background: var(--accent, #3b6ef5);
    color: #fff;
    cursor: pointer;
    box-shadow: 0 1px 4px rgba(0, 0, 0, .45);
  }
  .marker:not(.entered) { background: rgba(230, 232, 238, .82); color: #2a2d34; }
  .marker:hover { outline: 2px solid #fff; outline-offset: 1px; }
  .marker svg { width: 12px; height: 12px; fill: currentColor; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .fight {
    position: absolute; transform: translate(-50%, -50%);
    width: 13px; height: 13px; padding: 0; border-radius: 50%;
    border: 2px solid #12151a; background: rgba(255, 255, 255, .9);
    cursor: pointer; box-shadow: 0 1px 3px rgba(0, 0, 0, .5);
  }
  .fight.unknown { background: rgba(154, 162, 178, .55); border-style: dashed; border-color: #e6e9ef; }
  .fight.trainer { background: var(--red, #dc3214); border-color: #12151a; }
  .fight:hover, .fight:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }
  /* A pinned fight keeps the ring so it is obvious which card is held open. */
  .fight.pinned { outline: 2px solid #fff; outline-offset: 2px; }
  .bcard {
    position: absolute; transform: translate(-50%, -100%);
    max-width: 280px;
    z-index: 3; pointer-events: none;
    background: rgba(16, 18, 22, .96); color: #eef1f6;
    border: 1px solid rgba(255, 255, 255, .14);
    border-radius: 7px; padding: 8px 9px; font-size: 11px; line-height: 1.5;
    box-shadow: 0 6px 20px rgba(0, 0, 0, .5);
  }
  .bcard.below { transform: translate(-50%, 0); }
  .tip {
    position: fixed; z-index: 40; transform: translate(12px, -130%);
    pointer-events: none; white-space: nowrap;
    background: rgba(18, 20, 24, .93); color: #f2f4f8;
    border-radius: 5px; padding: 4px 7px; font-size: 11px; line-height: 1.5;
    display: flex; gap: 7px; align-items: baseline;
  }
  .tip.flip-x { transform: translate(calc(-100% - 12px), -130%); }
  .tip.flip-y { transform: translate(12px, 30%); }
  .tip.flip-x.flip-y { transform: translate(calc(-100% - 12px), 30%); }
  .tip b { font-weight: 700; }
  .tip .faint { color: #98a0b0; }
  .tip .bad { color: #ff8b7a; }
  .small { font-size: 12px; }
</style>
