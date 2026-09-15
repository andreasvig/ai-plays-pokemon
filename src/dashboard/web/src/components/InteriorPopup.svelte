<script>
  // One building, opened from its door marker on the world map (M10-M12).
  //
  // Andreas, 2026-09-15: "when you click on them then a pop-up map of the
  // interior pops up with the center on where it was on the map with a small
  // black background. for multi level houses such as pokecenter or reds house I
  // would expect all floors to be present on the popup."
  //
  // Every floor is drawn at the game's own 16 px a tile with the run's route on
  // it — the same `drawRoute` the world map uses, given a tile → pixel function
  // that only places THIS floor, so a route that leaves the building simply
  // stops at the door.
  import { TILE, loadMapImage, drawRoute, visitAt } from '../lib/mapatlas.js'

  let { building, route, atlas, onturn = null, onclose = () => {} } = $props()

  const floors = $derived((building?.floors ?? [])
    .map((key) => ({ key, m: atlas?.maps?.[key] }))
    .filter((f) => f.m))

  /** '…_2F' → '2F'; a single-floor building gets no label at all. */
  const floorLabel = (name) => (/_(B?\d+F)$/.exec(name || '')?.[1] ?? '')

  let hover = $state(null)

  function mount(node, key) {
    // A Svelte action so each floor's canvas draws itself once it exists.
    const m = atlas.maps[key]
    const w = m.width * TILE, h = m.height * TILE
    const dpr = Math.min(2, (typeof devicePixelRatio === 'number' ? devicePixelRatio : 1) || 1)
    node.width = Math.ceil(w * dpr)
    node.height = Math.ceil(h * dpr)
    node.style.width = `${w}px`
    node.style.height = `${h}px`
    const c = node.getContext('2d')
    c.setTransform(dpr, 0, 0, dpr, 0, 0)
    c.imageSmoothingEnabled = false
    const place = (g, n, x, y) => (`${g}:${n}` === key ? [(x + 0.5) * TILE, (y + 0.5) * TILE] : null)
    loadMapImage(m.file).then((img) => {
      if (img) c.drawImage(img, 0, 0, w, h)
      drawRoute(c, route, place)
    })
    return {}
  }

  function onmove(e, key) {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left, y = e.clientY - rect.top
    const place = (g, n, tx, ty) => (`${g}:${n}` === key ? [(tx + 0.5) * TILE, (ty + 0.5) * TILE] : null)
    const v = visitAt(route, place, x, y, TILE)
    hover = v ? { key, x, y, turn: v[0], tile: `${v[4]},${v[5]}` } : null
  }

  const visited = (key) => !!route?.maps?.[key]
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && onclose()} />

<div class="scrim">
  <button class="backdrop" onclick={onclose} aria-label="Close"></button>
  <div class="popup" role="dialog" tabindex="-1" aria-label={building.name}>
    <header>
      <h4>{building.name}</h4>
      {#if !building.entered}<span class="never">the run never went in</span>{/if}
      <button class="x" onclick={onclose} aria-label="Close">×</button>
    </header>
    <div class="floors">
      {#each floors as f (f.key)}
        <figure class:unvisited={!visited(f.key)}>
          <canvas use:mount={f.key} onmousemove={(e) => onmove(e, f.key)}
            onmouseleave={() => (hover = null)}
            onclick={() => onturn && hover?.key === f.key && onturn(hover.turn)}
            class:clickable={!!onturn}
            aria-label={`${building.name} ${floorLabel(f.m.name)}`}></canvas>
          {#if hover?.key === f.key}
            <div class="tip" style={`left:${hover.x}px;top:${hover.y}px`}>
              <b>Turn {hover.turn}</b><span>({hover.tile})</span>
              {#if onturn}<span class="faint">click for the trace</span>{/if}
            </div>
          {/if}
          <figcaption>
            {floorLabel(f.m.name) || 'Ground floor'}
            {#if !visited(f.key)}<span class="faint"> · not entered</span>{/if}
          </figcaption>
        </figure>
      {/each}
    </div>
  </div>
</div>

<style>
  .scrim {
    position: fixed; inset: 0; z-index: 60;
    display: grid; place-items: center;
    padding: 24px;
  }
  /* The scrim itself is the close control, so dismissing by clicking away is a
     real button rather than a click handler on a decorative div. */
  .backdrop { position: absolute; inset: 0; border: 0; padding: 0; background: rgba(8, 9, 12, .72); cursor: default; }
  .popup {
    position: relative;
    background: #101216;
    border: 1px solid rgba(255, 255, 255, .14);
    border-radius: 10px;
    box-shadow: 0 18px 48px rgba(0, 0, 0, .55);
    max-width: min(96vw, 1100px);
    max-height: 90vh;
    overflow: auto;
    padding: 12px 14px 14px;
    color: #eef1f6;
  }
  header { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  h4 { margin: 0; font-size: 14px; font-weight: 750; }
  .never { font-size: 11px; color: #98a0b0; }
  .x { margin-left: auto; background: none; border: 0; color: #cfd5df; font-size: 20px; line-height: 1; cursor: pointer; padding: 0 4px; }
  .floors { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
  figure { margin: 0; position: relative; }
  figure.unvisited canvas { opacity: .55; }
  canvas { image-rendering: pixelated; display: block; border-radius: 4px; background: #000; }
  canvas.clickable { cursor: crosshair; }
  figcaption { margin-top: 5px; font-size: 11px; color: #aab2c0; font-weight: 650; }
  .tip {
    position: absolute; transform: translate(10px, -130%); pointer-events: none;
    background: rgba(18, 20, 24, .95); border-radius: 5px; padding: 4px 7px;
    font-size: 11px; display: flex; gap: 6px; white-space: nowrap;
  }
  .tip .faint { color: #98a0b0; }
</style>
