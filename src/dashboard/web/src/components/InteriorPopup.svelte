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
  import { TILE, loadMapImage, drawRoute, visitAt, battlesFor } from '../lib/mapatlas.js'
  import BattleCard from './BattleCard.svelte'

  let { building, route, atlas, trainers = null, onturn = null, onclose = () => {} } = $props()

  // The rival at Oak's lab and Brock in his gym are indoor fights, so their
  // icons belong here rather than on the world map (M15).
  const battlesOn = (key) => battlesFor(null, route, { onlyMap: key })
  let openBattle = $state(null)

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
  const anyFights = $derived(floors.some((f) => battlesOn(f.key).length))
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
        {@const fights = battlesOn(f.key)}
        <figure class:unvisited={!visited(f.key)}>
          <canvas use:mount={f.key} onmousemove={(e) => onmove(e, f.key)}
            onmouseleave={() => (hover = null)}
            onclick={() => onturn && hover?.key === f.key && onturn(hover.turn)}
            class:clickable={!!onturn}
            aria-label={`${building.name} ${floorLabel(f.m.name)}`}></canvas>
          {#each fights as b (b.id)}
            <button class="fight" class:trainer={b.kind === 'trainer'}
              style={`left:${(b.tile.x + 0.5) * TILE}px;top:${(b.tile.y + 0.5) * TILE}px`}
              onmouseenter={() => (openBattle = b)} onfocus={() => (openBattle = b)}
              onmouseleave={() => (openBattle = null)} onblur={() => (openBattle = null)}
              onclick={() => onturn && onturn(b.opened_turn)}>
              <span class="sr">{b.kind} battle on turn {b.opened_turn}</span>
            </button>
          {/each}
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
    <!-- A hovered fight reports BELOW the floors: the popup scrolls, so a card
         floating over a canvas is clipped the moment the icon is near its top.
         The slot is ALWAYS here, at a FIXED height, when the building saw a
         fight. Showing it on hover resized the popup, which is centred in the
         scrim, so the whole dialog rose and the dot slid out from under the
         cursor — the browser then fired mouseleave and the card vanished again.
         A reserved slot that does not change size is what stops that. -->
    {#if anyFights}
      <div class="bcard" class:empty={!openBattle}>
        {#if openBattle}
          <BattleCard battle={openBattle} {trainers} />
        {:else}
          Hover a battle dot for who it was against and how long it took.
        {/if}
      </div>
    {/if}
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
    position: absolute; transform: translate(10px, 10px); pointer-events: none; z-index: 2;
    background: rgba(18, 20, 24, .95); border-radius: 5px; padding: 4px 7px;
    font-size: 11px; display: flex; gap: 6px; white-space: nowrap;
  }
  .tip .faint { color: #98a0b0; }
  .sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
  .fight {
    position: absolute; transform: translate(-50%, -50%);
    width: 13px; height: 13px; padding: 0; border-radius: 50%;
    border: 2px solid #12151a; background: rgba(255, 255, 255, .9);
    cursor: pointer; box-shadow: 0 1px 3px rgba(0, 0, 0, .5);
  }
  .fight.trainer { background: #dc3214; }
  .fight:hover, .fight:focus-visible { outline: 2px solid #fff; outline-offset: 1px; }
  .bcard {
    margin-top: 12px; padding: 9px 10px; font-size: 11px; line-height: 1.5;
    background: rgba(255, 255, 255, .05); border: 1px solid rgba(255, 255, 255, .14);
    border-radius: 7px; box-sizing: border-box;
    /* Fixed in BOTH directions: the card's content is wider than the prompt it
       replaces, and a popup that grows sideways slides the dot away just as
       surely as one that grows taller. */
    width: 272px; height: 156px; overflow: auto;
  }
  .bcard.empty { color: #8b93a3; display: flex; align-items: center; }
</style>
