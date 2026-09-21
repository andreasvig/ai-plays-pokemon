# The DS map camera: the atlas schema, and the numbers that chose it

> 2026-09-21. `scripts/ds3d/camera.py`, `scripts/compare_cameras.py`,
> `src/dashboard/web/src/lib/mapatlas.js`, `src/dashboard/web/src/components/RouteMap.svelte`.
> Pictures: `artifacts/game-map-render/camera/`.

Andreas, having looked at the straight-down DS atlases:

> "for gen 4 we shoudl defenlty not haev top down view, pelase make it teh real game like view."
>
> "both teh gen 4 and 5 views are actually really bad , bith are top down whcih dsont feel right"

`scripts/ds3d/scene.py` and `v2-experiments/render_sandgem.py` both argue in
writing FOR straight down, because an angled camera z-buffers and a building
therefore hides the route behind it. That argument lost. The occlusion it
names is real and is **measured** below rather than denied.

---

## 1. Which angled camera — pitched orthographic, not the game's own

The field camera (`CAMERA_TYPE_DEFAULT`, `src/overlay005/field_camera.c`) is
**perspective**: half-FOV 8.0914°, distance 666.92, pitch 59.0515°. "Nearly
orthographic at 8 degrees" was the going-in assumption. It is wrong at map
scale, and this is the number that decided the projection:

| over one 32×32 chunk | perspective (the game's own) | pitched orthographic |
|---|---|---|
| one tile, far edge → near edge | **13.26 px → 19.52 px (1.47×)** | 16.00 px everywhere |
| departure from its own best-fit affine | median 14.97 px, p95 40.8, max 62.3 | 0 by construction |
| same, on 64×32 Route 201 | median 22.69 px, p95 74.1, max 115.0 | 0 |
| canvas for Sandgem's 512×512-unit chunk | 900×800 (2.75× the pixels, 44% empty) | 520×470 |

A perspective render of a whole map is a keystoned trapezoid on a mostly-empty
canvas, its tiles change size by half again from top to bottom, and it frames
one chunk — so a multi-chunk map and a multi-map world frame do not line up at
their seams under one camera. Pitched orthographic keeps the world-to-screen
map **affine**, which is a 2×3 matrix the browser can carry and invert.

The pictures are side by side at real size with a real run's route on both:
`artifacts/game-map-render/camera/camera-comparison.png`
(Platinum 418 Sandgem, 342 Route 201 — the 64×32 case, 391 Route 219 — the
cliff-and-water case, SoulSilver 33 — the 96×32 case, Black 2 427 Aspertia).

The projection, once:

```
px = (X - x0) * s + pad_left
py = (Z - z0) * s * sin(pitch) - Y * s * cos(pitch) + pad_top
```

`s = tile_px / 16` pixels per world unit. `pitch = 90` is straight down
(`sin` 1, `cos` 0) and is the old projection exactly — see §6.

---

## 2. The atlas schema

### Atlas level — `camera` stops being a string

It was `"camera": "topdown"`, a name. A name is not enough to place a tile, so
it is now the projection:

```json
"camera": {
  "kind": "pitched",
  "pitch_deg": 59.051513671875,
  "matrix": [16.0, 0.0, 0.0, 13.72208, 0.0, 0.0],
  "height_px": 0.514267,
  "height_unit": "world"
}
```

- **`kind`** — `"topdown"` or `"pitched"`. What a consumer switches on.
- **`matrix`** — `[a, b, c, d, e, f]`, canvas `setTransform` order:
  `px = a·tx + c·ty + e`, `py = b·tx + d·ty + f`, for a **map-local tile
  corner** (tile centres add 0.5 to each, exactly as `RouteMap.place` already
  did). `e`/`f` are 0; the per-map offset is `png_pad`.
  **This is the authority for both pixel scales.** `tile_px` is not read twice
  — `matrix[0]` is the horizontal scale and `matrix[3]` the vertical one, and
  a viewer that used `tile_px` for the vertical axis would put the route 14%
  too low by the bottom of the map.
- **`height_px`** — PNG pixels a tile is lifted UP the picture per world unit
  of altitude. It is a third column the matrix cannot hold, because a tile's
  altitude comes from a different field. `0.0` under a topdown camera.
- **`height_unit`** — `"world"`: 16 world units is one tile.

`render` becomes `"3d-pitched"` where it was `"3d-ortho"`. The tier says what
the pixels *mean*; the camera says where a tile is *in* them, and a consumer
that read `3d-ortho` and got a pitched picture would place every route tile
wrong.

**A reader must still accept the bare string.** The atlases on disk are
re-rendered one game at a time, and a viewer that only understood the new
shape would draw the old ones at scale 0. `mapatlas.cameraOf` normalises both;
`verify_map_alignment.projection_for` reads the kind out of either.

### Map level — `png_pad` and `heights`

```json
"png_pad": [4, 31, 4, 0],
"heights": [1024, 16]
```

- **`png_pad`** — `[left, top, right, bottom]` in PNG pixels: where the map's
  own **ground rectangle** sits inside the picture. Straight down the two are
  the same rectangle and the field is absent. Pitched, a roof rises out of the
  top of the ground rect, so the picture must be taller than the map.
  A viewer anchors the image by it: the PNG pixel holding the map's ground
  corner is `png_pad` (plus the window offset), and that pixel lands where the
  layout says the map starts.

  **Left and right are always zero, and that is a proof, not a measurement.**
  The projection has no yaw — `px = tile_px · tx`, no `ty` term, no divide —
  so nothing whose x lies inside the map can project outside it. See §3a for
  what a horizontal pad was actually holding.

  **Top and bottom are measured, over vertices inside the map's own cell.**
  Top from tall geometry near the map's northern edge; bottom from geometry
  BELOW the reference plane, which projects down — Twinleaf's pond bed at −16
  world units is 11 px. Capped at `camera.MAX_PAD_TILES = (2, 8, 2, 2)` tiles
  so one runaway placement cannot size the canvas (uncapped, a single stray
  prop asks for 4,630 px of empty picture above a 439 px map).
- **`heights`** — the world altitude of the ground under each tile of the
  window, **run-length encoded row-major**: `[count, value, count, value, …]`
  over `width × height`, in DS world units, whole numbers. Indexed from the
  map's `origin`, because gen 4/5 route coordinates are global.

### What a viewer does with it

```js
const cam = cameraOf(atlas, route)              // string or object, normalised
const h   = heightAt(mapEntry, x, y)            // world units, 0 with no grid
const [px, py] = projectTile(cam, tx, ty, h)    // layout pixels at zoom 1
```

and for the image, `drawImage(img, sx - (padL + …) * z, sy - (padT + …) * z,
img.width * z, img.height * z)` — the whole picture, anchored, rather than a
source rect, because under a pitch there is no source rect that is the map.
`unprojectTile` is the exact inverse on any altitude plane.

Three consequences the viewer has to carry, and does:

1. **The layout must reserve the pad** or `fit` slices the roofs off the top
   row of buildings. `padTiles(atlas, cam)` is the widest pad in the atlas
   converted to tiles and rounded up, added to `MARGIN` on every side of
   `worldLayout` and `clusterLayout`.
2. **The maps are drawn north to south.** Pitched, a building on a southern
   map is drawn above its own ground and over the map behind it — a painter's
   algorithm. Straight down nothing overlaps and the order was free.
3. **Every overlay carries its tile's altitude.** `markersFor`, `battlesFor`,
   `exitsFor` and `floorsFor` return `h`, so a door on a raised terrace is
   drawn on the terrace and not on the plane below.

---

## 3. Height: measured, then shipped

Two questions, one measurement each, over the eleven Platinum maps our runs
have entered. Ground sampled by a ray straight down through the **terrain**
triangles (`camera.ground_heights` + `terrain_only`).

**Does the ground plane matter?** Yes, and it is the biggest single error:

- A gen-4 **outdoor** map's walkable ground is **16 world units**, not 0.
- A gen-4 **interior** floor is **−2**.
- Drawing the route at altitude 0 therefore puts it **8.2 px — 0.51 tiles —
  below the path it walked**, on every outdoor map, and 1.0 px out indoors.

**Does a per-TILE grid buy anything over a per-map constant?** A little, and
enough on the maps it matters on:

| map | walkable ground levels (world units) | spread | if flattened to one plane |
|---|---|---|---|
| 418 Sandgem Town | 16 | 0 | 0.0 px |
| 342 Route 201 | 16, 17 | 1 | 0.5 px |
| 343 Route 202 | 16, 17 | 1 | 0.5 px |
| **391 Route 219** (cliffs, water) | 8, 12, 16 | 8 | **4.1 px** |
| **411 Twinleaf Town** (beach) | 8, 16, 17 | 9 | **4.6 px (0.29 tiles)** |
| interiors (412–415, 420, 422) | −2, 0 | 2 | 1.0 px |

So: **ship the grid.** It costs almost nothing — Sandgem's 1,024 tiles are
`[1024, 16]`, two integers; Route 219, the worst case, is 142 integers; the
largest gen-5 map is 350.

Two traps the sampler exists to avoid, both of which produce a plausible number:

- **A ray down hits the ROOF.** Gen 4 models a lot of foliage in the terrain
  mesh itself. Sandgem's tiles report four heights of which 52.7 and 24.6 are
  the same clump of trees seen from above; its *walkable* tiles report exactly
  one, 16.0. So the grid is taken from walkable tiles and the rest is filled
  with the map's median (`walkable_plane`) — which also collapses the RLE.
- **Terrain is not a prefix of the triangle list.** `field.add_chunk` appends
  terrain then props **per chunk**, so a two-chunk map's terrain is two runs.
  `terrain_only(scene, spans)` slices per chunk; taking the first N reads a
  second chunk's terrain as props and its props as terrain.

### 3a. A gen-4 terrain model is not bounded by its own cell

Found by a two-tile sliver of forest hanging off Twinleaf Town's bottom-right
corner in the shipped pitched atlas, outside the map, attached to nothing.

The first guess was that a neighbouring matrix cell had been caught in the
chunk. **It had not.** The offending triangles are the map's OWN terrain
model: Twinleaf's land block reaches x = 548 and z = 576 against a cell of
512 × 512, i.e. **2.25 tiles past its own south-east corner**, and 16 units
past it to the north-west; **20 of its 2,398 triangles lie wholly outside**.
Route 201's reach one tile past (2 triangles). Sandgem, Route 202, Route 219
and every Platinum interior have none. SoulSilver's MAP_60 has 34.

In the game the adjacent cell's model is drawn over them and nobody sees it.
In a map-local render there is no adjacent cell. Straight down it still cost
nothing — a triangle outside the cell projects outside the canvas and is
clipped for free — so this could only appear once the camera was angled and a
pad opened a band around the picture for it to sit in.

Two changes, both in `camera.py`:

- **`in_footprint`** drops triangles with no part of their x/z footprint
  inside the map's own cell, before rendering and before measuring. Dropped,
  not clipped to the boundary, so a triangle that STRADDLES the edge — a tree
  half in the map — is still drawn and still cut off by the frame, exactly as
  the straight-down tier cuts it.
- **`measure_pad`** forces the horizontal pads to zero and takes the vertical
  bound over vertices inside the cell, so a quad reaching one tile south
  cannot buy a band to show the next cell in.

Measured, before and after, as opaque pixels strictly outside the ground rect:

| map | pad before | outside L/T/R/B before | pad after | outside after |
|---|---|---|---|---|
| 411 Twinleaf | `[4, 31, 32, 32]` | 476 / 7 868 / **2 268** / **2 497** | `[0, 28, 0, 11]` | 0 / 7 710 / 0 / 1 443 |
| 418 Sandgem | `[4, 31, 4, 0]` | 387 / 10 740 / 476 / 0 | `[0, 28, 0, 0]` | 0 / 10 660 / 0 / 0 |
| 342 Route 201 | `[4, 31, 4, 5]` | 30 / 23 872 / 59 / 65 | `[0, 9, 0, 0]` | 0 / 9 216 / 0 / 0 |
| 343 Route 202 | `[4, 31, 4, 0]` | 476 / 10 304 / 297 / 0 | `[0, 28, 0, 0]` | 0 / 10 226 / 0 / 0 |
| 391 Route 219 | `[0, 31, 0, 0]` | 0 / 10 642 / 0 / 0 | `[0, 9, 0, 0]` | 0 / 4 608 / 0 / 0 |
| SS 33 | `[0, 49, 0, 0]` | 0 / 43 122 / 0 / 0 | `[0, 35, 0, 0]` | 0 / 42 464 / 0 / 0 |
| SS 60 | `[32, 54, 32, 32]` | 134 / 14 112 / 0 / 0 | `[0, 54, 0, 0]` | 0 / 14 112 / 0 / 0 |

Every left and right band is now empty because it does not exist. Twinleaf's
remaining 11 px bottom band is its pond bed, which is the map's own and does
project down.

**What it costs inside the map: 178 pixels, on one map.** Rendering every map
with and without the clip and comparing the ground rectangle alone: identical
on 16 of 17, and Route 201 loses a 29 × 9 px strip of grass and one flower
cluster — a tile of Route 202's ground at y = 17, lifted 8.7 px by its own
altitude back over the boundary and into Route 201's last nine rows. The map
now ends where the map ends.

**The byte-identity control is intact and did not need re-establishing.**
Straight down, a dropped triangle projects outside the canvas and the
rasteriser already clipped it, so the clip is a no-op: checked against the
pre-change committed atlas, `--camera topdown` still reproduced all 11
Platinum and all 14 gen-5 3D PNGs byte for byte.

The control's REFERENCE changed, though, and for a different reason: the
shipped DS atlas is pitched from 2026-09-21, so "topdown reproduces the
shipped PNGs" is false by design and would have rotted into a skip or a lie.
It is now two assertions that survive a re-render:

- `--camera topdown` is **bit-for-bit `field.ortho_pixels`**, the
  straight-down rasteriser that predates `camera.py` and that nothing here
  touched. It is also the control on the clip, because `ortho_pixels` does no
  clipping at all.
- **re-rendering the shipped atlas under its own declared camera reproduces
  it**, for gen 4 and gen 5 separately. That one caught this change: it
  failed on 5 of 11 Platinum maps until the atlas was re-rendered, which is
  exactly what it is for.

---

## 4. Occlusion: the verdict, with the number

The browser draws the route over the PNG, so a building never hides it — the
route appears **in front of** a house it walks behind. Measured exactly, with
the renderer's own z-buffer: project each walked tile's ground point under the
pitched camera and ask whether anything is in front of it.

| map | walked tiles hidden |
|---|---|
| 342 Route 201 | 3 / 380 (0.8%) |
| 343 Route 202 | 4 / 652 (0.6%) |
| 418 Sandgem Town | 14 / 531 (2.6%) |
| 411 Twinleaf Town | 6 / 160 (3.8%) |
| **outdoors, total** | **27 / 1,724 = 1.6%** |
| 420 Pokécentre 1F | 4 / 33 (12.1%) |
| 413 Rival's house 2F | 6 / 46 (13.0%) |
| 415 Player's house 2F | 1 / 7 (14.3%) |
| 412 Rival's house 1F | 5 / 24 (20.8%) |
| **414 Player's house 1F** | **8 / 27 (29.6%)** |
| **all maps** | **52 / 1,879 = 2.8%** |

**Outdoors it is a non-issue** — 1.6% of tiles, a cable behind the corner of a
roof for a tile or two, and you can see it is behind because the cable is
continuous either side. `camera/probe-411-pitched-route.png` and
`probe-418-pitched-route.png` are those maps with the route on them at real
size; nothing in either reads as hidden.

**Indoors it is up to 30%**, because a room is small and its furniture is tall
relative to it. Looked at rather than counted
(`camera/probe-414-pitched-route.png`, the worst case at 29.6%), the route is
still legible: it is drawn over the TV stand and the rug, the way it is drawn
over them straight down. **No mitigation shipped.** If it is judged
unacceptable the cheapest fix is a per-map camera — interiors straight down,
the world pitched — which the schema already allows, because `camera` is read
per atlas and `worldLayout`/`clusterLayout` never mix an interior with the
world.

---

## 5. Three things checked because the pitch could have made them visible

- **Back-face culling is not implemented** (all 630 gen-4 materials declare
  front-face only and nothing enforces it). Straight down it cannot matter.
  Pitched, measured: gen 4 has **0–3% of triangles facing away and culling
  changes 0.02% of pixels at most** (41 px on Sandgem, max channel 31) — it is
  not worth implementing. Gen 5 is different: 9–18% of triangles face away and
  culling changes up to **14.6% of pixels** on Black's map 319 — but the median
  change on those pixels is 23/255 and the two renders are indistinguishable
  side by side (`camera/g5-319-nocull.png` vs `g5-319-cull.png`): gen 5 draws
  its foliage as two-sided quads, so culling removes a duplicate rather than a
  wrong face. **Not implemented, and nothing in the artwork asks for it.**
- **Multi-bone props.** Display-list matrix commands are ignored, so a prop
  with several bones draws every piece at bone 0. Of the props placed on the
  four Platinum maps rendered here, none needs it: the pieces are per-MATERIAL,
  not per-bone, and no placed model showed a mis-posed sub-mesh under the
  pitch. Worth re-checking on a city map before this is called settled.
- **Flat roofs.** `scene._one` shades a face by its ANGLE from vertical, so a
  hip roof's four slopes come out one colour and the ridge is whatever the
  texture shows. `camera.directional_shade()` is a Lambert lamp from the
  north-west, normalised so flat ground stays exactly 1.0 (a lighting model
  that darkened the ground by 3% would be a recolour of every map wearing a
  roof fix). **Off by default, behind `--light` on both renderers**, because it
  repaints every DS map and that is Andreas's call with the picture in front of
  him: `artifacts/game-map-render/camera/camera-lighting.png`.

  **The pitched render is NOT flatter than the spike's gamecam reference**,
  which was the worry: `camera/reference-vs-pitched.png` is
  `ds-3d-spike/twinleaf_town-gamecam.png` (re-rendered over the texture fix)
  beside the pitched render unlit and lit. The reference's roofs are just as
  flat — it goes through the same `scene._one`, so the only difference between
  the two is the projection. The lamp is an improvement over BOTH, and a small
  one.

---

## 6. The control: straight down still renders the atlas that shipped

Every DS render now goes through `ds3d/camera.py`, the straight-down one
included. "Looks the same" would hide a rounding rule, so:

> `--camera topdown` reproduces all 11 shipped Platinum PNGs and all 4 Black 2
> PNGs **byte for byte** (`tests/test_camera.py`).

Two things had to be pinned for that to be true, and each was found by the
control failing:

- **`cos(radians(90))` is 6.1e-17, not 0.** `Camera.cos_p` returns an exact
  0.0 at pitch 90.
- **The depth base is in the pixels.** `raster.draw_tri` interpolates `1/w`,
  which is right for a perspective divide and wrong for an orthographic one,
  and the error goes as `(spread/base)²`. `scene.ortho_projector` has always
  used 1000; rendering Sandgem at 1e5 instead changed **6.6% of its pixels, max
  channel 138**. So topdown keeps 1000 and pitched takes 1e5 — because pitched
  spreads over the scene's DEPTH, 527 world units on a 64-tile map, and a base
  of 1000 would leave `w` as low as 210 and negative on anything longer.

---

## 7. What this does NOT unlock: `verify_map_alignment.py`

The alignment verifier refuses the four DS games, and the reason it gives is
half this work: *"their atlases are rendered straight down while the emulator
draws that field through a tilted camera."* That half is now gone. **The other
half is not, and it has a number:**

The atlas is **orthographic**; the DS is **perspective** at a half-FOV of
8.09°, recentred on the player every frame. Across one 256×192 frame that
perspective scales the ground by about **19% top to bottom** (the same effect
that is 1.47× across a whole chunk), so a fixed-size rectangular crop of the
atlas is the right picture at the wrong magnification — **wrong by ~9% at the
frame edge**. A per-pixel score against it would still be measuring the
projection, not the alignment.

So `screen_for` keeps refusing, with a **different sentence**: the old one
named `render == "3d-ortho"` exactly and a `3d-pitched` atlas would have fallen
through to *"no ScreenSpec — measure it with `stitch_maps.py --calibrate`"*,
which is an instruction that cannot be carried out for a DS game. Both
refusals now name their own reason.

Doing it properly is **reachable for the first time**, and the atlas already
carries what it needs: `camera.matrix`, `camera.height_px`, and each map's
`png_pad` and `heights`. The missing piece is a crop warped by the emulator's
own camera rather than a rectangle. Not built.

---

## 8. Two viewport changes that came with it

- **The panel opens at `fit`, not at 2× on the first placeable visit.** The 2×
  default had a written reason (`RouteMap.svelte`'s header: at 1× four cables
  in a corridor are 3.5 px apart) and it lost to a worse first impression —
  on Platinum and on Black the opening view was the panel's black background,
  because 2× on a 96-tile world shows a tenth of it and the first placeable
  visit is wherever the run started. The zoom controls are one gesture away.
- **The panel takes the world's own aspect ratio**, clamped to `[0.62, 2.4]`
  and still capped at 88vh. It was a square whatever the world was, and a
  square spends two thirds of itself on background for a world that is not
  one: Crystal's is 84×22 tiles and fitted into the middle quarter of a square
  frame. The clamp is doing work — Crystal's true 3.8 : 1 would be a letterbox
  slit with the zoom controls on top of the map, so it gets 2.4 and keeps a
  band of background above and below. Measured through the projection, because
  an angled camera changes a world's aspect ratio (a square world is 1.17 : 1
  under this pitch).

Shots of all seven games through the live viewer, at `fit`:
`artifacts/game-map-render/camera/ui/`.

---

## 9. Files touched outside the camera's own

Flagged because two of them were fenced off:

- **`scripts/ds3d/scene.py`** — `render()` and `_one()` gained an optional
  `light=` keyword and the old slope shade moved into `_slope_shade()`.
  Additive, 15 lines, no behaviour change when `light` is None. Needed because
  a lighting model is per-triangle and the alternative was a second copy of the
  rasteriser.
- **`scripts/verify_map_alignment.py`** — two refusals, not a projection.
  `projection_for` reads the camera KIND out of either shape of the field
  (otherwise every re-rendered atlas, straight-down ones included, refuses),
  and `screen_for` gained a `3d-pitched` branch so a DS game is not told to run
  `stitch_maps.py --calibrate`, which cannot be done for it. §7.
- **`scripts/compare_cameras.py`** — new, the sheet. A sibling of
  `compare_upscale_runs.py`; kept out of `render_dsmaps.py` to keep that file's
  diff small.
