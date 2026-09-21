# Gen 5 — six failing tests, and a third off the artwork

2026-09-21, branch `polish/gen5fix`. Touched: `scripts/ds3d/pngout.py` (new),
`scripts/render_gen5maps.py`, `tests/test_gen5maps.py`, `tests/test_pngout.py`
(new), and every PNG + `index.json` for `black-us` and `black2-us`.
`scripts/render_dsmaps.py` deliberately untouched — gen 4 adopts the new writer
separately.

Three things landed on the Gen 5 renderer at once: the pitched field camera,
the observed-transition doors, and a much longer Black 2 run that walked five
maps nobody had rendered (437, 438, 439, 443, 446). Six tests failed. Three
were tests written against inputs that have legitimately changed; three were
findings. None of the six was fixed by loosening an assertion.

**The failures only reproduce after merging the rebuilt observed graphs**
(`skyemu-backend`, 2adb79e). Before that merge this branch re-derives the OLD
door set and all 29 tests pass, which looks like nothing to do.

## The six

### 1. `test_every_entry_matches_the_image_actually_on_disk` — RE-POINTED

Asserted `im.size == (width * tile_px, height * tile_px)`. That was only ever
right by coincidence: under a pitch the two axes have different pixel scales
and the picture carries a margin for the roofs and canopies the camera lifts
off their own tiles. Both numbers are published, so the size is

    round(width * matrix[0]) + png_pad[0] + png_pad[2]
    round(height * matrix[3]) + png_pad[1] + png_pad[3]

Black's `317:0` is 64x96 tiles at `[16.0, 13.72208]` with pad `[32, 89, 32, 21]`
= 1088x1427, which is what is on disk. Computed with the VIEWER's arithmetic
(`mapatlas.js` reads `matrix[0]`/`matrix[3]`, never `tile_px` twice), not the
renderer's, so it checks the number something actually loads.

### 2. `test_the_png_frame_is_declared_and_outdoor_maps_carry_their_origin` — RE-POINTED

Asserted `render in ("3d-ortho", "collision")`. Adding `"3d-pitched"` to the
tuple is the lazy fix and it defeats the assertion's purpose: an allow-list
answers *is this a kind somebody once thought of*, when the question is *is
this the kind this renderer would emit for the camera the atlas declares*.
Those came apart the day the camera was pitched — a stale atlas still saying
`3d-ortho` over straight-down artwork would pass a widened tuple forever.

Now keyed on the pair, against the renderer's own `RENDER_KIND_FOR` table, so
a `render` that disagrees with `camera.kind` cannot pass; and the published
`camera` block must equal what `ds3d_camera.atlas_camera(camera_for(...))`
produces, so a matrix the viewer would mis-place tiles with is caught too.

Also removed `render_gen5maps.RENDER_KIND`, a dead lookup superseded by
`RENDER_KIND_FOR` that still claimed `3d -> 3d-ortho`. A stale table beside a
live one is how a re-pointed test gets pointed at the wrong thing.

### 3. `test_every_placement_resolves_to_a_prop_model` — FINDING, renderer now explains it

Real and new: `black2-us: 2 placements name no model`, arriving with the five
newly-walked maps. They are **map 437, chunk cell (1,21), prop ids 442 and
443**, both at chunk-local (0, -16, 0).

Not an arithmetic error. 437's zone header value 214 gives prop area 53, and
maps 439 and 446 carry the same value, resolve to the same area and resolve
every placement they have. The two ids belong to **area 52 — map 427's set** —
and they are `c12_cloud01` and `c12_sky`: the backdrop of the c12 city next
door. A chunk in 437 references the area next to it.

Three measurements decided what to do:

* **A prop id is global.** Across Black 2's archives, 446 outdoor ids appear in
  2070 repeat listings and 222 indoor ids in 3593, and in **zero** cases do two
  areas give one id a different model. So the models can be found.
* **They are not furniture.** `c12_sky` is 1000 world units across sitting 29
  to 370 units up; `c12_cloud01` is a 919-unit two-triangle billboard at 108 to
  309. The tallest thing 437 actually draws is 176 and stands on the ground.
  Across both games, all 229 other placements are ground furniture — houses,
  signs, rocks, chairs, PCs. Not one sky or cloud prop is drawn anywhere.
* **They are invisible.** Resolving them through the real renderer adds 52
  triangles, moves the pad not at all, and produces a **byte-identical PNG**.
  They project clear of the map's own rectangle.

So the drawing path is unchanged and a **global fallback was deliberately NOT
added**: because ids are global, a renderer that searched every area would
resolve most placements no matter how wrong `prop_area` was, and would take
this whole check down with it — that is the louder half of the failure the test
exists for. Instead `Gen5Rom.prop_elsewhere` is diagnosis only, and
`check_props` now returns `unresolved` naming each placement, so `--check`
prints:

    unresolved prop: map 437 cell [1, 21] id 442 — this map's prop area is 53;
      model 'c12_cloud01', which lives in area 52

The test pins that exact set instead of zero, and two new tests keep the
exception honest: every area with a gap must be an area some other map empties
completely, and resolving the gap must change no pixel.

### 4. `test_two_chunks_of_one_map_meet_without_a_step_in_tone` — RE-POINTED, false positive

Reported a step of 23.45 against a threshold of 12, red only (`[66.1, 130.4,
93.2]` vs `[42.7, 126.0, 91.7]`, green 4.3 and blue 1.5). Not a lighting seam:
the patches were hard-coded at x = 470..555, written for the straight-down
render. Under the pitch the seam moved from x = 512 to **x = 544** (32px left
pad) and the vertical scale went 16 -> 13.72, so both 40x40 patches had slid
onto different content — one on a brown path, one on grass, which is exactly
what a red-only step means.

At the real seam: step **4.09**, ranking **16th of 47** adjacent-column steps
in its own neighbourhood (median 2.87, p90 13.81, max 23.36), with 277 and 411
distinct colours either side. No seam.

Mean colour was the wrong instrument, not just wrongly located. Chunk
boundaries are lines map authors put things on: `black2-us 446:0` has a cliff
down its x = 32 seam and the means differ by 45.65 with nothing wrong. The
property the clamp bug destroyed is that the two chunks draw **the same
grass**, so the test now compares palettes — the share of each side's pixel
mass drawn in a colour the other side also uses. Measured over all ten seams in
both atlases: healthy **0.595 to 0.998**; the same ten with one side repainted
in its own mean colour (the clamp bug reproduced) score **0.000, all ten**. The
floor is pre-registered at 0.40, below every healthy seam and far above every
clamped one. 446's cliff scores 0.910/0.889 and passes, correctly.

### 5. `test_the_shipped_doors_are_exactly_the_ones_we_derived` — RE-POINTED

Black 2's graph grew from 4 maps and 10 warps to 9 and 26. `black-us` did NOT
change — its observed graph is not in 2adb79e and its pinned doors re-derived
identically, which is a useful control on the re-pin. Black 2 gained 437, 439
and 443, and 438 turned out to be a gate joining 427 and 437.

### 6. `test_an_outdoor_door_tile_is_a_tile_the_collision_grid_calls_a_wall` — RE-POINTED, and its own prediction came true

The note on this test said the `427 -> 435` door had no blocked collision
candidate and that the count would drop "the day a run walks back out". **That
day has arrived.** One of the new warps leaves 435, so the derivation could
read the door off where that run LANDED — (48, 739), which the collision grid
does call a wall — instead of off the walked tile (49, 741), which it does not.
427's door to 435 moved one tile and flipped `walked` -> `landed`; 435's own
exit flipped the other way, `landed` -> `walked`.

`passable_doors` is now **0**, not 1. Every outdoor door in both Gen 5 atlases
is a tile the cartridge independently calls impassable. Asserted as zero rather
than "at most one": a passable outdoor door reappearing is now a finding.

## The artwork: five bits per channel

`scripts/ds3d/pngout.py`, new and shared — gen 4 is meant to call it too.

A DS colour is five bits per channel. An eight-bit value off that 32-level grid
is precision the hardware never had; it is our own blending, supersampling and
shading. Rounding back onto the grid throws away nothing the cartridge ever
contained.

|  | raw | shipped | saving | error |
|---|---|---|---|---|
| `black-us` | 1688.6 KB | **1137.0 KB** | 32.7% | max 4, mean 0.83 |
| `black2-us` | 2298.2 KB | **1524.4 KB** | 33.7% | max 4, mean 0.99 |

**`black2-us` goes 209.1 -> 139.8 bytes per tile**, against the 200 alarm in
`tests/test_gamemaps.py`. `black-us` is 88.8. Nothing else moved; the two gen-4
games are still unquantised at 91.4 and 80.4 and will drop by about a third
when they adopt the writer.

Three decisions worth recording:

* **Rounded, not truncated.** `x >> 3` was the measured variant (max error 7,
  and it can only ever darken). Rounding halves the worst case to 4 and is also
  **~2% smaller** — 1137.0 vs 1160.0 KB and 1524.4 vs 1554.8 KB. Better on both
  axes, which was not the expectation.
* **Widened with `(v << 3) | (v >> 2)`, not `v << 3`.** The cheap spelling is
  genuinely smaller — 512.4 KB against 576.7 KB on `446-0.png`, which is where
  the 524.9 KB figure in the brief came from — but it widens 31 to **248**, so
  every opaque pixel in every map becomes slightly transparent and the whole
  atlas composites 3% of the page background through it. 64 KB is the right
  price for that.
* **The collision tier opts out.** Its two tones are a UI palette this repo
  chose, not colour read off a cartridge, and three of their six channel values
  are off the grid. "The hardware never had this precision" is not an argument
  about a colour we invented.

The octree option (185.9 KB, max error 38, 19% of pixels off by more than 8)
stays refused.

### Can you see it?

No — not at 1:1, and not at 4x either. Crops in this directory:
`gen5-5bit-crop-1x.png` (real size) and `gen5-5bit-crop-4x.png`.

The crop is `black2-us 437-0.png` at (288,144), 240x150, chosen by scanning
every shipped map for the largest smooth ramp — the region where banding would
show. It scored 172 on "range achieved by small steps" against 39 for the next
map, a range of 126.7 built from a local gradient of 0.23. In that crop the
quantiser changes 96.3% of pixels and cuts 2103 distinct colours to 533, max
error 4. The water gradient bottom-right is the banding candidate and it is
unchanged to the eye.

A test pins the property rather than a file size:
`test_no_shipped_pixel_is_a_colour_the_console_could_not_have_shown` asserts no
channel of any shipped artwork PNG holds a value the DS cannot express. A byte
count moves when a map is added or zlib changes its mind and says nothing about
what was done to the pixels.

## Mutations

16 mutations, all applied to a clean tree and reverted byte-exact, each run
against the single test named. **16/16 killed.**

| mutation | caught by |
|---|---|
| vertical scale from `tile_px` (the old square-PNG assumption) | `test_every_entry_matches_the_image_actually_on_disk` |
| drop `png_pad` from the expected size | `test_every_entry_matches_the_image_actually_on_disk` |
| pretend no map declares a pad | `test_the_png_size_check_would_notice_a_map_rendered_at_the_wrong_scale` |
| atlas says `3d-ortho` under a pitched camera (stale atlas) | `test_the_png_frame_is_declared_and_outdoor_maps_carry_their_origin` |
| published matrix is not the camera's | `test_the_png_frame_is_declared_and_outdoor_maps_carry_their_origin` |
| the two known prop gaps silently start resolving | `test_every_placement_resolves_to_a_prop_model` |
| `prop_area` off by one | `test_every_placement_resolves_to_a_prop_model` |
| `prop_area` off by one, against the shared-area guard | `test_a_map_that_shares_an_areas_props_resolves_all_of_them` |
| the skipped placement is a visible model, not backdrop | `test_the_unresolved_props_would_not_have_been_visible_anyway` |
| raise the shared-mass floor above what real seams score | `test_two_chunks_of_one_map_meet_without_a_step_in_tone` |
| stop flattening the side (control goes vacuous) | `test_the_seam_check_would_see_a_chunk_painted_one_flat_colour` |
| move one door tile by 1 | `test_the_shipped_doors_are_exactly_the_ones_we_derived` |
| flip one exit `walked` -> `landed` | `test_the_shipped_doors_are_exactly_the_ones_we_derived` |
| assert the old count of one passable door | `test_an_outdoor_door_tile_is_a_tile_the_collision_grid_calls_a_wall` |
| nudge one channel of one shipped pixel off the grid | `test_no_shipped_pixel_is_a_colour_the_console_could_not_have_shown` |
| drop the render kind from the refusal message | `test_the_ds_games_are_refused_with_the_projection_reason` |

**The one survivor was the harness, not the test.** `assert passable_doors ==
0` -> `== 1` is a same-length edit, and CPython invalidates a `.pyc` on
(mtime-seconds, size) — so the mutant re-ran the cached bytecode and "passed".
Re-run with a per-mutation `PYTHONPYCACHEPREFIX` it fails with `assert 0 == 1`.
Any mutation harness that edits in place needs this; a same-length mutation is
the one that silently survives.

## Known and not done

* Gen 4 does not use `pngout` yet. `render_dsmaps.py` has its own `to_png` and
  another agent is in that file.
* The two backdrop placements on 437 are still skipped, by choice. If a future
  map ever references a neighbouring area's prop that IS inside its rectangle,
  `test_the_unresolved_props_would_not_have_been_visible_anyway` fails rather
  than letting artwork silently drop.
* `black2-us 446:0`'s x = 32 seam scores 45.65 on the old mean-colour rule and
  is the single largest step in either atlas. It is a cliff on the chunk line,
  confirmed by the two sides sharing 0.91/0.89 of their pixel mass and their
  top grass colours being the identical RGB values. Worth remembering if a
  future seam check reintroduces a brightness comparison.


## Two things outside the six

### `test_the_ds_games_are_refused_with_the_projection_reason` — fixed, same anti-pattern

`scripts/verify_map_alignment.py` refuses to measure a DS game because the
atlas is orthographic and the emulator is perspective. The refusal is correct
and already pitch-aware. The TEST asserted the message contained the literal
`"3d-ortho"` — the only thing a DS atlas could say when it was written.

It was already red for `platinum-us` and `soulsilver-us` at baseline; making
Gen 5 pitched dragged `black-us` and `black2-us` in with them. Re-pointed to
`atlas["render"] in str(exc.value)`, which is the actual claim: the refusal
names the kind the atlas declares. All four games green.

This is the same defect as failure 2 in a second place. A render kind written
as a literal inside an assertion goes red on a change to the ARTWORK while the
thing it tests is still correct — and goes quiet in the other direction, still
passing if the message stops naming the kind at all.

### `test_camera_topdown_reproduces_the_shipped_*_pngs_byte_for_byte` — NOT fixed, and not mine

Both halves were already failing at baseline and both still fail. Left alone
deliberately; the fix has to move gen 4 too and belongs with the camera work.

* `platinum-us`: shipped `3d-pitched` from before this branch started, so a
  TOPDOWN re-render has not matched it for some time. Nothing to do with Gen 5.
* `black2-us`: failed at baseline with `FileNotFoundError` on `437-0.png` —
  the rebuilt graph named five maps the committed atlas had never rendered.
  Now that they exist it fails on content instead, because the shipped PNGs
  are pitched and the test renders topdown.

The test's premise — "`--camera topdown` is a switch BACK, proved byte for
byte against the shipped atlas" — stopped holding for BOTH games the moment
the shipped atlas became pitched. The control that still means something is
the pitched one, and it passes: re-rendering `black2-us 437-0.png` through
`Field3D` at `camera_for("pitched", 16)` and quantising reproduces the shipped
file **byte for byte**, which is how the crop above was made.

## A note on measuring in this worktree

`local/` and `roms/` are symlinks into a sibling checkout, so the environment
moves under a measurement. Eight `test_dsmaps.py` tests went from red to green
during this work with nothing here touching them: they were failing on
`SystemExit: --offline but res/field/maps/data/map_data_004.bin is not cached`
and another agent populated the pret cache. None of that is this branch.

Also: the first baseline here was captured with `pytest -q | tail -25`, which
silently cut the first two `FAILED` lines off a 26-failure list — both of them
the camera tests above, which made them look new when they were not. A
tail-capped log is not the list.

## Follow-up: the footprint clip, and why black 2 would not render

Merged `skyemu-backend` (e1446b5), which added `camera.in_footprint`. Black 2
then failed `run_checks` on `bare_route` — `route tiles on real geometry
517/518`, bare tile `(438, 6, 1)` — and the renderer refuses to write on that
gate, so the game shipped nothing, names included.

### The clip is NOT pure loss in gen 5 — the argument dies

The proposal was that gen 5 renders the whole cell block and then CROPS to the
window, so out-of-cell geometry is already gone and the clip can only take
something useful. Two measurements kill it.

**Gen 5 keeps a pad, exactly as gen 4 does.** `Field3D.pixels` crops with
`frame.crop(...)`, which preserves `pad`, and `frame.cut` goes through
`crop_box`, which ADDS it back. Every shipped Gen 5 map has a non-zero top pad
(18 to 128 px). The band the gen-4 slivers appeared in is present here too.

**And the crop provably cannot do the clip's job, because the pitch lifts
out-of-cell geometry back INSIDE the ground rectangle.** Measured per map,
clip on versus off with the pad pinned so only the triangle set varies:

    TOTAL 70,918 opaque px removed — 60,153 in pad bands,
                                     10,765 INSIDE the ground rectangle

    black2 427   19,414  (8,998 pad, 10,416 inside)   black2 437   33,195 (all pad)
    black2 435    5,798  (all pad)                    black2 443    5,798 (all pad)
    black-us 398  5,798  (all pad)                    black2 446      566 (all pad)
    black-us 319    349  (all inside)

The 10,765 pixels inside the ground rect are unreachable by any crop. They are
the mechanism gen 4's own test already names: a neighbour's ground south of the
cell, "lifted 8.7 px by its own altitude back over the boundary and into the
last 9 rows of the map's own rectangle". 427's is a full-width band of
treetops along its southern edge. The clip earns its place in gen 5.

### Tile (6, 1) of map 438: the triangle

Two triangles, one quad, and they are not a sliver:

    x [8.0, 152.0]   y [-64.0, 72.0]   z [0.0, 0.0]

A VERTICAL face standing in the plane `z = 0` — map 438's reception counter,
against the north wall of Aspertia Gate. Its footprint on the z axis is a
LINE, not a box, so `v[:, 2].max() <= origin[1]` reads `0.0 <= 0.0` and drops
it. It left a black hole through the counter and a walked tile standing on
nothing. One tile out of 518 because a room has exactly one north wall.

### The fix is in `in_footprint`, NOT a call-site opt-out

A gen-5 opt-out would have been wrong twice over: it would have thrown away
70,918 px of correct clipping in gen 5, and it would have left gen 4 holding
the same latent bug for the first wall it ever puts on a cell boundary.

The rule is now per axis: a footprint **with extent** must overlap the cell, a
**degenerate** one need only lie within it.

    def outside(lo_v, hi_v, lo, hi):
        if lo_v == hi_v:                 # a line, not a box: edge-on face
            return lo_v < lo or lo_v > hi
        return hi_v <= lo or lo_v >= hi

Both halves are load-bearing and the existing tests already pinned the other
one: `quad(17.0, 200, 232, 512, 528)` has `z.min() == 512 == gz`, real extent,
no area inside, and must still be dropped — relaxing the whole comparison to
`<`/`>` keeps it and puts 178 px of Route 201's neighbour back on the map.
Equality is exact, not toleranced: the case is a face whose vertices share a
coordinate literally.

### Controls

* **Gen 4 unchanged, measured.** All 13 placeable Platinum maps render
  byte-identically under the old rule and the new one. Twinleaf's right-pad
  sliver stays at **0** opaque px outside the ground rect under both.
  (SoulSilver could not be measured — `map_data_048.bin` is not in the local
  pret cache.)
* **Gen 5 changes one map.** Of the 19 maps, only 438 differs (1,710 px, the
  counter). Five maps keep a handful of extra degenerate triangles and render
  byte-identically — they are edge-on to the camera.
* **`tests/test_camera.py` 42/42**, including every `in_footprint` fixture.

### Mutations

| mutation | caught by |
|---|---|
| drop the degenerate branch (the original rule) | `test_a_face_standing_in_the_boundary_plane_is_inside_the_cell` |
| " | `test_every_route_tile_lands_on_real_geometry` — reproduces `black2-us: [(438, 6, 1)]` exactly |
| relax to `<`/`>` for every triangle | `test_a_face_standing_in_the_boundary_plane_is_inside_the_cell` |
| " | `test_geometry_wholly_outside_the_cell_is_dropped_rather_than_padded_for` |
| drop the `quantise` call in the gen-5 reproduce test | `test_the_gen5_renderer_reproduces_its_own_shipped_atlas` — 9 of 9 |

### Two more things this turned up

**`test_the_gen5_renderer_reproduces_its_own_shipped_atlas` compared the wrong
two things.** It put `render_map`'s raw output against the shipped PNG, but the
shipped PNG went through `to_png` and is 5-bit quantised, so it asked the
renderer to reproduce something it never wrote. Fixed by comparing through
`pngout.quantise` for the artwork tier. **The gen-4 twin
(`test_rerendering_the_shipped_atlas_under_its_own_camera_reproduces_it`) has
the identical seam and passes today only because gen-4 PNGs are not yet
quantised — it needs the same line the moment `render_dsmaps` adopts the
writer.**

**The names were not wired.** `ds3d/mapnames.py` says it is "the one lookup
both renderers call" and nothing called it; every Gen 5 entry still shipped
`name: null`. Wired at the single `"name":` site in `build_atlas`, and the
comment there — "no Gen 5 decomp means no header constant, the location names
are in an archive nothing here decodes" — was true when written and is not any
more, so it is replaced rather than left to mislead.

### After

    black-us  : 10 maps (3d-pitched), 87.7 bytes of artwork per map tile
                1667.6 KB -> 1123.0 KB (32.7% smaller), error max 4 mean 0.88
    black2-us :  9 maps (3d-pitched), 134.1 bytes of artwork per map tile
                2198.5 KB -> 1461.3 KB (33.5% smaller), error max 4 mean 1.01

Both under the 200 alarm. Black 2 is lower than the 139.8 measured before this
merge because `measure_pad` now forces the horizontal pad to zero, so every
map is 32 to 64 px narrower.

All nine Black 2 maps named: Aspertia City (427, 428, 429, 435), Route 19
(437), Aspertia Gate (438), Floccesy Town (439, 443), Route 20 (446). 438
being a GATE is independent corroboration of the door derivation, which had
already worked out that 438 is the building joining 427 and 437.
