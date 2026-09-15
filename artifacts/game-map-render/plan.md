# Drawing the route on the real FireRed map

> Andreas, 2026-09-15: "lets plan how to make a much better map using real game maps instead of this".
> Today's map draws each map as a plain rectangle and the route on top (`RouteMap.svelte`,
> `artifacts/route-fidelity/plan.md` R4). Terrain, buildings and paths are invisible, so a viewer
> cannot see that the run walked *around* a fence or *into* a forest maze.

## 0. What we have already

- `local/pret-cache/` — the pret/pokefirered mirror the walk graph is built from, pinned at
  `c75f352304d529f6ba92d4f74b9cf8b5c3810788`. It holds `data/layouts/<Layout>/map.bin` (the grid) and
  `data/tilesets/*/*/metatile_attributes.bin` (passability). **512 KB.**
- `data/firered-walkgraph.json` v2 — 32 maps, outdoor ones carrying `world: [x, y]` in one shared
  frame with Pallet Town at (0,0), `tile_px: 16`.
- `data/runs/<id>/route.json` — every tile the run stood on, already published and already drawn.

So the geometry is solved. What is missing is **pixels**.

## 1. What pret has that we never fetched

Confirmed live against the pinned SHA. Every tileset directory also contains:

| file | what it is | size |
|---|---|---|
| `tiles.png` | the 8×8 tile sheet, 4bpp indexed | 0.9–8.7 KB |
| `metatiles.bin` | 16 bytes per metatile: 8 × u16 tile refs | 0.7–10 KB |
| `palettes/00.pal … 15.pal` | JASC-PAL, 16 colours each | tiny |

The first-badge region needs **15 tilesets** across 13 primary+secondary pairs, over **32 maps**
totalling **18,698 tiles (4.79 Mpx at 16 px/tile)**. The whole source payload is well under a
megabyte; it is a one-time fetch into the same cache, offline thereafter.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| M1 | Render **offline into one PNG per map**, committed under `src/dashboard/web/public/maps/` and published as a shared static asset — not per run. | 32 maps are the same for every run and for all time at a pinned SHA. The browser caches them; a run that only reached Pallet Town fetches one ~15 KB image. `publish.sync_site` already syncs `public/` folders (that is how `logos/` ships). |
| M2 | Render at the game's native **16 px per tile**, the walk graph's `tile_px`. | Tile coordinates × 16 = pixels, with no scale factor anywhere. The component downscales with CSS; upscaling a smaller render would blur. |
| M3 | `maps/index.json` beside the PNGs: map key → `{name, width, height, world, file}` plus the **pret SHA and the walk-graph version**. | The map images and the walk graph must describe the same world. A mismatch has to be detectable rather than silently drawn one tile off. |
| M4 | Terrain only: **no NPCs, items, or cut trees.** | They are object events, not layout, and they move. The walk graph already treats a cut tree as a barrier, so a route that stops at one will look unexplained — §5 says how we mark it. |
| M5 | Animated tiles (water, flowers, the Pokémon Center door) render **frame 0**. | A still map. Say so in the caption rather than let someone wonder why the sea does not move. |
| M6 | A new script `scripts/render_gamemaps.py`, `--offline` from the cache and pinned to the same SHA as `build_walkgraph.py`. | Same shape as the walk-graph builder, same cache, same pin. A regenerate is one command. |
| M7 | The route keeps its current styling and draws **on top** of the image: blue→red by turn, doors as circles, blackouts marked. | The map is a backdrop; the run is the subject. Nothing about the overlay changes. |

## 3. The rendering algorithm

Per map, from its layout:

1. **`map.bin`** — one u16 per tile, little-endian. `metatile = v & 0x03FF`; the high bits are
   collision and elevation, which we ignore here (the walk graph owns passability).
2. **Which tileset** — `metatile < 0x280` → the primary tileset; otherwise the secondary, at
   `metatile - 0x280`.
3. **`metatiles.bin`** — 16 bytes per metatile: eight u16s, the first four the bottom layer (2×2
   tiles) and the last four the top layer.
4. **Each u16** — `tile = v & 0x03FF`, `xflip = v>>10 & 1`, `yflip = v>>11 & 1`, `palette = v>>12 & 0xF`.
5. **`tiles.png`** — indexed, 128 px wide, 8×8 tiles in row-major order.
6. **Palettes** — the primary tileset supplies palettes 0–6, the secondary 7–12
   (FireRed `NUM_PALS_IN_PRIMARY = 7`). **Verify this constant against the pinned tree; it differs
   from Emerald's, and getting it wrong recolours half the world.**
7. **Composite** — blit the bottom layer, then the top layer with colour index 0 transparent.

## 4. The risky part, and how to de-risk it

Steps 5 and 6 are where this goes wrong: index order in `tiles.png`, the palette split, and which
layer treats index 0 as transparent. All three fail *silently* — you get a picture, just the wrong
one.

**Spike first: render Pallet Town alone and check it pixel-for-pixel.** Every run folder holds
`screenshots/*.png` at 1440×960, exactly 6× the GBA's 240×160. Downscale one to 240×160, take the
player's tile from the same turn's `referee_position`, crop the equivalent 15×10-tile window out of
the rendered map, and diff. The game draws the player and NPCs over it, so expect differences at the
sprites and nowhere else. **If the terrain does not match, the render is wrong — not the screenshot.**

Do not render the other 31 maps until Pallet Town matches.

## 5. Open questions for Andreas

- **Cut trees and boulders.** The walk graph treats them as walls, so a route that stops dead at an
  open-looking tile will read as a bug. Draw a marker where the graph says impassable but the map
  looks walkable, or leave it and explain in the caption?
- **The unvisited world.** Draw the whole first-badge region greyed, with the visited maps in
  colour — so you can see how little of it a short run touched — or only what the run saw, as now?
- **Weight.** All 32 maps is roughly 250–700 KB of PNG. Fine as a one-time cached fetch; worth
  knowing before it ships.

## 6. Estimate

- Fetch the three missing files per tileset into the cache: **~20 min** (the fetch function exists).
- The renderer up to a pixel-correct Pallet Town: **1–2 hours**, nearly all of it on §4's three traps.
- The remaining 31 maps once one is right: **~15 min** — same code, and the diff harness re-runs.
- `RouteMap.svelte` drawing the image under the route, plus `index.json` plumbing and the publish
  path: **~45 min.**
- Tests and a live check: **~30 min.**

**Half a day**, and the risk is concentrated in one place: if Pallet Town does not match a real
screenshot within the first two hours, the format assumptions in §3 are wrong and I should come back
with what the diff showed rather than keep guessing.

## 7. Not in this step

- Object events (NPCs, items, trainers' lines of sight).
- Animated tiles.
- Maps outside the first-badge ladder — the script takes the map list from the walk graph, so
  extending the ladder extends the render for free.
