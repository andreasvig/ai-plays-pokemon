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
| M4 | Terrain only: **no NPCs, items, or cut trees**, and **no marker where the graph says impassable but the ground looks open**. The caption says so instead. | They are object events, not layout, and they move. Andreas 2026-09-15 picked the caption over a marker: the artwork stays faithful to what the game draws. A route that dead-ends at a cut tree is explained in words, not in ink. |
| M8 | Draw **only the maps the run entered**, as today — not the whole region greyed. | Andreas 2026-09-15. A short run's map is honestly small. The cost is that two runs' maps are different shapes and do not compare side by side; accepted. |
| M9 | Keep the **fixed panel with a scrollbar** the map already has. No fit-to-width, no zoom-and-pan. | Andreas 2026-09-15. Native detail, no new interaction to build or maintain. |
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

## 5. Settled (Andreas, 2026-09-15)

All three open questions came back as the simpler option, so the visible behaviour of the map does
not change at all — only what is drawn underneath the route:

- Obstacles: **not marked**, explained in the caption (M4).
- Unvisited world: **not drawn**, only what the run entered (M8).
- Presentation: **the scrolling panel stays** (M9).

The only thing left to decide is weight: all 32 maps is roughly 250–700 KB of PNG, fetched once and
browser-cached, and a run only pulls the maps it entered.

## 6. Estimate

- Fetch the three missing files per tileset into the cache: **~20 min** (the fetch function exists).
- The renderer up to a pixel-correct Pallet Town: **1–2 hours**, nearly all of it on §4's three traps.
- The remaining 31 maps once one is right: **~15 min** — same code, and the diff harness re-runs.
- `RouteMap.svelte` drawing the image under the route, plus `index.json` plumbing and the publish
  path: **~30 min** — down from 45, because M8 and M9 leave the layout and the interaction alone.
- Tests and a live check: **~30 min.**

**Around half a day**, and the risk is concentrated in one place: if Pallet Town does not match a
real screenshot within the first two hours, the format assumptions in §3 are wrong and I should come
back with what the diff showed rather than keep guessing. Nothing else in the list can fail quietly.

## 7. Not in this step

- Object events (NPCs, items, trainers' lines of sight).
- Animated tiles.
- Maps outside the first-badge ladder — the script takes the map list from the walk graph, so
  extending the ladder extends the render for free.

---

# Part 2 — house markers, interior popups, battle icons

> Andreas, 2026-09-15: rooms that are not transition rooms get a house marker on the map; clicking
> pops up the interior centred on where it sits, on a small black background, with every floor of a
> multi-storey building present. The map becomes an expandable section below the video, the gates and
> the census. And every battle gets a small icon you can hover for its turns and outcome — the
> species for a wild battle, the trainer's sprite and party with levels for a trainer.

## 7. House markers and interior popups

**A door tile is already derivable.** Every cross-map edge in the walk graph is a warp, so the
outdoor tile you step off to enter an interior is known for all 23 of them, with no new data:
Oak's lab is PalletTown (16,13), the Pewter gym PewterCity (15,16), Red's house PalletTown (6,7).
Those tiles are where the markers go.

**A transition room is one that joins two non-interior maps.** `map_type` alone does not separate
them — the forest gate houses and Red's living room are both `MAP_TYPE_INDOOR`. The rule that does:
count the distinct maps an interior's warps reach whose own type is not `MAP_TYPE_INDOOR`. The two
Viridian Forest entrances reach Route 2 **and** Viridian Forest, so they are transitions and get no
marker; Red's house reaches only Pallet Town (its 2F is itself indoor), so it gets one.

| # | Decision | Why |
|---|---|---|
| M10 | Non-transition interiors get a **marker on their door tile**; clicking opens the interior as a popup over the map, on a dark scrim, anchored to the marker. | Andreas. It keeps the world map uncluttered while every room stays one click away — and it retires the inset column, which never had a real position. |
| M11 | A multi-floor building shows **every floor in one popup**, side by side, ordered 1F → 2F. | Andreas. Floors are grouped by stripping a trailing `_<n>F`: `PewterCity_PokemonCenter_{1F,2F}`, `PalletTown_PlayersHouse_{1F,2F}`, `PewterCity_Museum_{1F,2F}`. |
| M12 | Viridian Forest keeps its **inset**, not a popup. | It is `MAP_TYPE_ROUTE` with no world frame — not a building, and too large and too important to hide behind a click. |

## 8. Where the sections sit

| # | Decision | Why |
|---|---|---|
| M13 | Order on the run detail: gates, video, **census**, then the **map as an expandable section, collapsed by default**. | Andreas. The map is the heaviest thing on the page — collapsing it also means its PNGs are not fetched until asked for. |

## 9. Battle icons — what we have, and what we do not

A hard split, and it decides how much of this can apply to the 26 runs already published.

**Already stored, every run** (`referee.battles.segments`, 47 of them on the mark run):
kind (wild or trainer), the turns the battle spanned, its turn count, and for a trainer the id, the
name and whether it was won. The **tile** is recoverable too — the trace samples `in_battle` per
button, so the tile the battle opened on is in `route.json` already.

**Not stored anywhere:**

| wanted | status |
|---|---|
| which Pokémon a wild battle was against | **not recorded** — no memory read for it |
| levels, either side | **not recorded** |
| wild outcome: won / ran / caught | **not recorded** — all three look identical |
| whiteout as a battle outcome | inferred from the route's teleports, not from the battle record |
| the trainer's party | **not recorded live — but static in pret** |

**The trainer half needs no new data.** `src/data/trainer_parties.h` (242 KB) and
`graphics/trainers/front_pics/` are in the pinned tree, and we already know the trainer id, so the
sprite, the party and their levels can be shown for **every run we have**, retroactively.

**The wild half needs new memory reads**, and those only ever apply to future runs:
`gBattleMons` for species and level on both sides, and `gBattleOutcome` for won / lost / ran /
caught / teleported. Both are standard FireRed symbols, but the addresses must be **probed live
against a mid-battle save state** the way `gTrainerBattleOpponent_A` was on 2026-09-14 — not taken
from memory or from a wiki.

**One cheap alternative for existing runs:** every run folder holds a screenshot per turn, and the
first turn of each battle shows the Pokémon, its level and the whole scene. Downscaled to the GBA's
own 240×160 a frame is **~14 KB**, so the mark run's 47 battles are about 660 KB. Showing the actual
frame is honest in a way a reconstruction is not — it is what the model saw.

## 10. Not in this part

- Reading the player's own party, moves or HP.
- Attributing a whiteout to the battle that caused it (the route already marks where it landed).

## 11. Settled (Andreas, 2026-09-15)

| # | Decision | Consequence |
|---|---|---|
| M14 | **Add the memory reads before building the wild-battle icon.** Probe `gBattleMons` (species + level, both sides) and `gBattleOutcome` (won / lost / ran / caught / teleported), ship them, and show a wild icon only on runs that carry the data. | The 26 published runs get **trainer icons only**. Wild battles appear from the next run onwards. Andreas chose this over showing the battle's own screenshot; the data is structured and queryable rather than a picture, and it does not grow the published payload. |
| M15 | Battle icons sit **on the map, at the tile the battle opened on**. | Already derivable: the trace flags `in_battle` per button, so the opening tile is in `route.json`. Ties the fight to the place — the Viridian Forest ambushes will cluster visibly. Not a timeline strip, so they are only visible with the map expanded. |
| M16 | Trainer hover card shows the **front sprite, the full party with levels, turns spent and won/lost**. | All static: `src/data/trainer_parties.h` plus `graphics/trainers/front_pics/`, keyed by the trainer id we already store. Retroactive to every run. It is the roster, not what was actually sent out — we do not know that, and M14's reads do not tell us either. |

## 12. How the probe is done

`gBattleMons` and `gBattleOutcome` are standard FireRed symbols, but **their addresses are not taken
from memory or a wiki** — the same discipline that fixed `gTrainerBattleOpponent_A` on 2026-09-14.
Run folders keep `savepoints/`, and mid-fight states already exist (battles.py verified the trainer
opponent against six of them). The probe loads one, reads the candidate address, and checks the
species and level against what the screenshot of that same turn shows on screen. A candidate that
disagrees with the picture is wrong, whatever the wiki says.

Only once a candidate matches on several states does it go into `TRACE_SPEC`.

## 13. Sequence and estimate

1. **Map render** (§1–6) — the Pallet Town spike and its screenshot diff, then the other 31. *~half a day*, nearly all risk in the three silent failures of §4.
2. **Memory-read probe** (§12) — candidate addresses checked against mid-fight savepoints and their screenshots. *1–2 hours*, and it either matches or I come back with what it read.
3. **Wiring the new reads** into `TRACE_SPEC`, `battles.py`, the events and the replay. *1–2 hours*.
4. **Trainer party + sprite extraction** from pret — a small parser for `trainer_parties.h`, sprites copied as static assets. *2–3 hours*.
5. **House markers, popups and the battle icons** on the map, plus the expandable section. *2–3 hours*.

**Roughly two days**, not the half day Part 1 alone was. Steps 1 and 2 are independent and both are
the kind that either work or fail loudly; 3–5 are routine once they do.
