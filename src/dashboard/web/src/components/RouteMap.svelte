<script>
  // Every tile a run stood on, drawn on the real FireRed artwork
  // (artifacts/game-map-render/plan.md; the plain-rectangle version it replaces
  // was on the page for one day, 2026-09-15).
  //
  // The maps are static PNGs rendered offline from pret's own tilesets and
  // shipped in the bundle, so a run publishes nothing but its route.json and a
  // viewer downloads only the maps that run entered. Tiles are drawn at the
  // game's own 16 px, in a panel that scrolls (M9): no zoom, no fit-to-width.
  //
  // Interiors are NOT on the world map. Every building that is a place rather
  // than a corridor gets a marker on its door tile, and the marker opens the
  // building — all its floors — over the map (M10-M12).
  import { fetchRunRoute } from '../lib/api.js'
  import { TILE, loadAtlas, loadTrainers, loadMapImage, worldLayout, markersFor, battlesFor, drawRoute, drawSize, rampCss, visitAt } from '../lib/mapatlas.js'
  import InteriorPopup from './InteriorPopup.svelte'
  import BattleCard from './BattleCard.svelte'

  // `onturn` is the local report link: the run detail passes a handler that
  // opens that turn's trace, and the published site passes nothing, so the map
  // is a picture there and a way into the transcript here.
  let { runId = null, height = 560, onturn = null } = $props()

  let route = $state(null)
  let atlas = $state(null)
  let trainers = $state(null)
  let loading = $state(false)
  let canvas = $state(null)
  let ready = $state(0)            // bumped when the images for this route are in
  let hover = $state(null)         // {x, y, turn, map, tile}
  let openBuilding = $state(null)  // a marker, while its popup is up

  $effect(() => {
    const id = runId
    route = null
    if (!id) return
    loading = true
    Promise.all([fetchRunRoute(id), loadAtlas(), loadTrainers()])
      .then(([r, a, t]) => { route = r; atlas = a; trainers = t })
      .finally(() => { loading = false })
  })

  const layout = $derived(worldLayout(route, atlas))
  const markers = $derived(layout && route && atlas ? markersFor(layout, route, atlas) : [])
  // Fights on the maps this canvas draws. One inside a building is drawn in
  // that building's popup instead, where its tile actually is.
  const battles = $derived(layout && route ? battlesFor(layout, route) : [])
  let openBattle = $state(null)
  const px = $derived(layout ? layout.w * TILE : 0)
  const py = $derived(layout ? layout.h * TILE : 0)

  /** Tile → canvas pixel centre; null for a map this canvas does not draw. */
  function place(g, m, x, y) {
    const p = layout?.at[`${g}:${m}`]
    return p ? [(p.x + x + 0.5) * TILE, (p.y + y + 0.5) * TILE] : null
  }

  // Load every image this route needs, then let the draw effect run. Without
  // the gate the canvas paints once against an empty cache and stays blank.
  $effect(() => {
    const L = layout
    if (!L) return
    let live = true
    Promise.all(Object.values(L.at).map((p) => loadMapImage(p.m.file)))
      .then(() => { if (live) ready += 1 })
    return () => { live = false }
  })

  $effect(() => {
    const L = layout, r = route, el = canvas
    ready                                  // redraw once the images are decoded
    if (!el || !L || !r?.visits?.length) return
    const dpr = Math.min(2, (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) || 1)
    el.width = Math.ceil(px * dpr)
    el.height = Math.ceil(py * dpr)
    el.style.width = `${px}px`
    el.style.height = `${py}px`
    const c = el.getContext('2d')
    c.setTransform(dpr, 0, 0, dpr, 0, 0)
    c.imageSmoothingEnabled = false
    c.clearRect(0, 0, px, py)
    for (const p of Object.values(L.at)) {
      loadMapImage(p.m.file).then((img) => {
        if (!img || canvas !== el) return
        const [dw, dh] = drawSize(p.m)
        c.drawImage(img, 0, 0, dw * TILE, dh * TILE, p.x * TILE, p.y * TILE, dw * TILE, dh * TILE)
        // The route is drawn after every image lands, so a slow map cannot
        // paint over the line that crosses it.
        drawRoute(c, r, place)
      })
    }
    drawRoute(c, r, place)
  })

  function onmove(e) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left, y = e.clientY - rect.top
    const v = visitAt(route, place, x, y, TILE)
    // Flip the readout near an edge: the frame scrolls, so a tooltip hanging
    // past the canvas grows the scroll area and puts a scrollbar across the
    // panel (2026-09-15).
    hover = v ? { cx: e.clientX, cy: e.clientY, turn: v[0], key: `${v[2]}:${v[3]}`,
                  tile: `${v[4]},${v[5]}`, battle: !!v[6],
                  flipX: e.clientX > innerWidth - 300, flipY: e.clientY < 48 } : null
  }

  function onclick() {
    if (onturn && hover) onturn(hover.turn)
  }

  const mapName = (key) => atlas?.maps?.[key]?.name ?? key
  const tr = $derived(route?.transitions ?? {})
  const blackouts = $derived(tr.teleport ?? 0)
</script>

{#if loading}
  <p class="faint small">Drawing the route…</p>
{:else if route?.visits?.length && layout}
  <figure class="routemap" style={`--h:${height}px`}>
    <div class="frame">
      <div class="stage" style={`width:${px}px;height:${py}px`}>
        <canvas bind:this={canvas} class:clickable={!!onturn}
          onmousemove={onmove} onmouseleave={() => (hover = null)} onclick={onclick}
          aria-label="Every tile this run stood on, on the FireRed map"></canvas>
        {#each markers as m (m.key)}
          <button class="marker" class:entered={m.entered}
            style={`left:${(m.tile.x + 0.5) * TILE}px;top:${(m.tile.y + 0.5) * TILE}px`}
            title={`${m.name}${m.entered ? '' : ' — never went in'}`}
            onclick={(e) => { e.stopPropagation(); openBuilding = m }}>
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2 15 8h-2v6H3V8H1z" /></svg>
            <span class="sr">{m.name}</span>
          </button>
        {/each}
        {#each battles as b (b.id)}
          <button class="fight" class:trainer={b.kind === 'trainer'}
            style={`left:${(b.tile.x + 0.5) * TILE}px;top:${(b.tile.y + 0.5) * TILE}px`}
            onmouseenter={() => (openBattle = b)} onfocus={() => (openBattle = b)}
            onmouseleave={() => (openBattle = null)} onblur={() => (openBattle = null)}
            onclick={(e) => { e.stopPropagation(); if (onturn) onturn(b.opened_turn) }}>
            <span class="sr">{b.kind} battle on turn {b.opened_turn}</span>
          </button>
          {#if openBattle?.id === b.id}
            <!-- The panel scrolls and clips, so a fight near the top of the map
                 gets its card BELOW the dot rather than off the frame. -->
            <div class="bcard" class:below={b.tile.y * TILE < 170}
              style={`left:${(b.tile.x + 0.5) * TILE}px;top:${(b.tile.y + (b.tile.y * TILE < 170 ? 1 : -0.5)) * TILE}px`}>
              <BattleCard battle={b} {trainers} />
            </div>
          {/if}
        {/each}
        {#if hover}
          <div class="tip" class:flip-x={hover.flipX} class:flip-y={hover.flipY}
            style={`left:${hover.cx}px;top:${hover.cy}px`}>
            <b>Turn {hover.turn}</b>
            <span>{mapName(hover.key)} ({hover.tile})</span>
            {#if hover.battle}<span class="bad">in a battle</span>{/if}
            {#if onturn}<span class="faint">click for the trace</span>{/if}
          </div>
        {/if}
      </div>
    </div>
    <figcaption>
      <span class="ramp" aria-hidden="true" style={`background:${rampCss()}`}></span>
      <span class="faint">first turn → last</span>
      <span class="dot">·</span>
      <b>{route.visits.length.toLocaleString()}</b> tiles stood on across <b>{layout.outdoor + layout.insets + layout.interiors.length}</b> maps
      {#if tr.step}<span class="dot">·</span><b>{tr.step.toLocaleString()}</b> steps{/if}
      {#if tr.warp}<span class="dot">·</span><b>{tr.warp}</b> door{tr.warp === 1 ? '' : 's'}{/if}
      {#if tr.jump}<span class="dot">·</span><b>{tr.jump}</b> ledge{tr.jump === 1 ? '' : 's'}{/if}
      {#if blackouts}<span class="dot">·</span><b class="bad">{blackouts}</b> blackout{blackouts === 1 ? '' : 's'}{/if}
      {#if tr.break}<span class="dot">·</span><b class="bad">{tr.break}</b> unexplained jump{tr.break === 1 ? '' : 's'}{/if}
      {#if route.battles?.length}<span class="dot">·</span><b>{route.battles.length}</b> battle{route.battles.length === 1 ? '' : 's'}{/if}
    </figcaption>
  </figure>
  <p class="note faint">
      The artwork is the game's own, drawn from pret's tilesets at 16 px a tile. It is terrain only:
      NPCs, items and cuttable trees are object events that move, so a route that dead-ends at a tree
      ends at what looks like open ground, and animated tiles — water, the flower beds — are drawn at
      their first frame. Viridian Forest is shown in daylight; the game tints it.
      Where the run walked the same way more than once the passes are laid side by side, one thin
      cable each, so four crossings of a corridor read as four lines rather than one painted over
      three times.
      {#if markers.length}A house marks a building you can open; the pale ones this run never entered.{/if}
      {#if battles.length}A dot marks a battle where it started — filled for a trainer, hollow for a
      wild one.{/if}
  </p>
  {#if openBuilding}
    <InteriorPopup building={openBuilding} {route} {atlas} {trainers} {onturn} onclose={() => (openBuilding = null)} />
  {/if}
{/if}

<style>
  .routemap { margin: 0; }
  .frame {
    /* The journey Pallet → Pewter is a portrait strip at native size: cap the
       height and scroll inside it rather than shrink the artwork. */
    max-height: var(--h);
    overflow: auto;
    background: var(--dark, #14161a);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 6px;
    display: flex;
    justify-content: center;
  }
  .stage { position: relative; flex: none; }
  canvas { image-rendering: pixelated; display: block; }
  canvas.clickable { cursor: crosshair; }
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
  .fight.trainer { background: var(--red, #dc3214); border-color: #12151a; }
  .fight:hover, .fight:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }
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
  figcaption { margin-top: 6px; font-size: 11.5px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  figcaption b { color: var(--text); font-weight: 650; }
  figcaption b.bad { color: var(--red); }
  .dot { color: var(--faint); }
  .note { font-size: 11px; margin: 6px 0 0; line-height: 1.5; max-width: 78ch; }
  .ramp { width: 52px; height: 7px; border-radius: 4px; display: inline-block; }
  .small { font-size: 12px; }
</style>
