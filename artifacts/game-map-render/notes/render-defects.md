# DS 3D map render — three reported defects, one cause

2026-09-21, branch `polish/render`. Touched: `scripts/ds3d/nsbmd.py`,
`scripts/ds3d/scene.py`, `tests/test_dsmaps.py`, `tests/test_gen5maps.py`, and
every PNG + sheet for `platinum-us`, `soulsilver-us`, `black-us`, `black2-us`.

Andreas: *"both teh gen 4 and 5 views are actually really bad … they are missing
tns of etxtures, liek houses, and grass. and soem of the trees look liek teh
folow tigether."*

## The cause, in one sentence

`ds3d/nsbmd.py` read every material struct **eight bytes late**, so the
renderer could not read a material's texture parameters and took the wrap mode
from the NSBTX instead — where the repeat bits are always clear. Every UV past
the first tile therefore **clamped to an edge texel**.

## Where the eight bytes came from

Two independent four-byte errors in the same function (`nsbmd.py`,
`_material_at` and the constants above it):

1. The struct was anchored at the material **name dictionary** (`mat_off + 4`)
   rather than at the material **section** (`mat_off`). The dict's own data
   offsets are section-relative.
2. `dummy` and `size` were read as two `u32`s; the format has two `u16`s.

Consequences, none of which raises:

| field | was read as | actually is |
|---|---|---|
| `diffuse_ambient` (+0x04) | `polygon_attr` (+0x0C) | the flat colour an untextured face draws |
| `texture_params` (+0x14) | `palette_base`/`flags` (+0x1C) | the repeat/flip bits |
| `polygon_attr` (+0x0C) | `polygon_attr_mask` (+0x10) | the material's own alpha |

### The oracle that settles the base — not a reference image

A material states `orig_width`/`orig_height`, the size of the texture it binds.
The NSBTX states the same size independently, in a different file, in different
bits. Over every model the two gen-4 games draw:

* base = `mat_off + rel`: **514 of 514 agree**
* base = `mat_off + 4 + rel` (the old code): **0 of 514 agree**

Same census on the wrap bits: **495 of 514 materials ask for repeat in both
directions**, 6 for S only, 3 for T only, 10 genuinely want the clamp. The
NSBTX says clamp for all 514.

`tests/test_dsmaps.py::test_a_material_struct_starts_where_its_own_texture_size_says_it_does`.

## Defect 1 — the horizontal dark-green bands. REAL, fixed.

**The band is not in the cartridge.** Ground truth: emulator frame
`local/runs/2026-09-20_00-17-45_config-v2-platinum__gemini-3-8-flash-minimal/screenshots/00124_turn_124.png`,
the player standing in ROUTE_201 at (153, 849). The real route is packed blocks
of conifers and tall grass, with no ribbons.

**It is not props.** `add_chunk` reports **0 placements** on both of Route
201's matrix cells, so every pixel on that map is terrain. (This kills the
"each tree prop draws a stretched shadow quad" hypothesis outright — there are
no tree props on Route 201.)

**It is the `conttree_b` / `conttree_t` tree-row material.** Rendering each of
the 13 terrain materials of `map03_26c` alone shows `conttree_b` drawing
edge-to-edge dark ribbons with canopies strung on them, and `tshadow` covering
62% of the map in one flat green. Both textures are 32–64 texels wide, tiled
across a 512-unit chunk. Clamped, the whole strip becomes the darkest texel of
the row.

The size correlation Andreas spotted is the same thing seen twice: a 64-tile
map gives the clamp 64 tiles to smear across, a 32-tile map only 32.

**Fix** — `scene.py:add_model`: `p = texset.tex_params(tname) | model.material_texparams(mi)`.
The NSBTX still owns offset/size/format/colour-0; the material owns repeat and
flip.

**Mutation control.** Reverting that one expression puts the bands back and
fails `test_route_201_is_tree_rows_and_not_ribbons` and
`test_the_wrap_mode_comes_from_the_material_and_not_from_the_texture`.

**Before / after**, rows of the PNG that are more than half a single RGB value:

| map | before | after |
|---|---|---|
| platinum `342:0` ROUTE_201 (64x32) | 262 / 512 | **51 / 512** |
| soulsilver `33:0` (96x32) | 199 / 512 | **26 / 512** |
| platinum `411:0` TWINLEAF (32x32) | 34 / 512 | **15 / 512** |

## Defect 2 — buildings as flat saturated slabs. PART DEFECT, PART NOT.

Split into two claims, because they have different answers.

**"The texture never bound" — refuted.** Every material of every prop on
SANDGEM_TOWN resolves both its texture and its palette from the area's prop
texture set; `unbound=[]` for all ten placements. The colours are right too:
`pcloof` decodes as the orange Pokémon Center roof with its P.C sign, and the
turn-63 emulator frame of Sandgem shows the same blue roof our render now draws
on the centre building (the **pre-fix** render drew it brown, so the wrap fix
corrected it).

**"Flat slab" — real, and it was the clamp again.** The roofs were painted with
one smeared texel. After the fix Sandgem's houses show roof ridges, skylights
and the Poké Ball on the Center's roof; Twinleaf's show tile ridges and
windows.

**A second real defect underneath it: the black slabs.** A gen-4 building's
drop shadow (`h_kage`) is an **opaque** texture drawn at polygon alpha **9 of
31**, and `is_translucent` only reads texels — so it went through the opaque
pass as a solid black rectangle beside every house. Census over the two gen-4
games: 134 of 630 materials are translucent by polygon alpha (alpha 6, 9, 12,
22, 27) and **none of them is translucent in its texture**.

**Fix** — `nsbmd.material_alpha` + `scene.add_model` folds the material alpha
into the texel alpha, so the existing two-pass opaque/blended split picks it up
with no second mechanism.

**Mutation control.** Disabling the fold fails
`test_a_translucent_material_is_blended_even_when_its_texture_is_not`; the same
test asserts the other direction (most materials must stay opaque) so
"everything is translucent now" cannot pass it.

**Before / after**, opaque near-black pixels (alpha > 200, max channel < 40):

| map | before | after |
|---|---|---|
| platinum `411:0` TWINLEAF | 1368 | **0** |
| soulsilver `33:0` | 1257 | **0** |

## Defect 3 — Twinleaf's teal roofs. NOT A DEFECT.

This is what a roof looks like from directly above. Evidence:

* The spike's own two images are the control pair. `twinleaf_town-gamecam.png`
  and `twinleaf_town-ortho.png` are the **same scene, same assets, same
  code** — only the camera differs. The gamecam shows walls and windows
  because it can see the walls. Straight down there are no walls to see.
* `sandgem_town-gamecam.png` has exactly the same cyan box, orange box and blue
  lozenge the ortho sheet was criticised for. Those are the roofs, and the
  emulator frames agree with their colours.
* Nothing is unbound: the Twinleaf house prop resolves every material.
* After the wrap fix the roofs are no longer flat — they carry tile ridges and
  skylight windows (see the crop in the report).

**What remains, and it is a lighting choice not a bug.** `scene._one` gives any
face with `|n·up| > 0.8` a flat shade of 1.0, so all four slopes of a hip roof
come out identical and the ridge is invisible. A directional light would
separate them. I did **not** do it: it changes every pixel of every map for
every game, and a sibling agent is building the angled camera that solves the
same problem properly. The stale spike images in `artifacts/ds-3d-spike/` were
not regenerated — they predate this fix and no longer match what ships.

## Gen 5 — the same cause, two more symptoms

The gen-5 script shares `ds3d/`, so both were fixed by the same two lines.

* **Chunk tone seams on Black `317:0` and `319:0`.** Measured before: two 40x40
  patches of open grass either side of the x=512 chunk boundary were **exactly
  one colour each** and different from each other. The grass texture is 64x64
  with sixteen greens at one texel per world unit; clamped, each chunk showed
  one corner texel. After: 41 and 90 distinct colours, means within 6.
  `test_two_chunks_of_one_map_meet_without_a_step_in_tone`.
* **"Whole chunks render as opaque black."** Not a defect in the PNG — those
  regions are **alpha 0** (317 has 516,608 fully transparent pixels, 319 has
  258,976). They read as black because the proof sheet composites onto a
  `(10,11,14)` backdrop. The viewer will show the page background there, which
  is what a matrix cell no zone claims should look like.
* Side effect, measured by the existing oracle: Black's prop-pixels-on-a-wall
  went **87.3% → 90.6%**, Black 2 → 90.3% (control, z as stored: 75.0% / 49.2%).

## Why no existing counter saw any of this

`add_chunk`'s loss counters were **clean before and after**:

| game | maps | chunks | terrain tris | props drawn |
|---|---|---|---|---|
| platinum-us | 11 | 12 | 7310 / 7310 | 113 / 113 |
| soulsilver-us | 6 | 8 | 3689 / 3689 | 65 / 65 |
| black-us | 10 | — | — | 113 / 113 |
| black2-us | 4 | — | — | 67 / 67 |

Every triangle was decoded, every prop resolved, every declared quad drawn, and
`check_artwork` reported 1879/1879 and 1175/1175 route tiles on opaque
geometry. Nothing was **missing**; what was wrong was what each triangle was
**painted with**, and no count can see a colour.

## Known and not fixed

* **Back-face culling.** All 630 gen-4 materials set "render front only" and
  the renderer draws both sides. The depth buffer hides the consequences from
  straight down; it would matter for an angled camera.
* **Texture-coordinate transform mode.** 32 of 630 materials set `tcmode = 1`;
  three of them (a SoulSilver prop in New Bark Town) carry a 52-byte material
  struct with the extra scale/rotate/translate fields. The other 29 are
  identity. Not implemented.
* **Vertex colour and normals.** `dlist.decode` ignores opcodes 0x20 (colour)
  and 0x21 (normal) and `scene._one` substitutes a flat shade by face normal.
* **Matrix commands in display lists** (0x14 and friends) are still ignored, so
  a multi-bone prop draws every piece at bone 0.

---

# 2026-09-21, later: Lake Verity arrives and breaks two checks

A Platinum run reached Lake Verity, so the atlas grew `311:0
LakeVerityLowWater` (96x64) and `334:0 VerityLakefront` (64x64) — two maps
nothing had ever rendered. Both `tests/test_dsmaps.py` failures they caused are
in the CHECKS. Neither is a renderer defect, and neither was fixed by widening a
threshold.

## 1. `2 of 605 materials disagree` — the struct oracle

**The two are `s_snow02`, in `m_dun2701_00_01c` and `m_dun2701_01_01c`** —
Lake Verity's two content chunks. Each declares `orig_width/height` = 32x16
where the NSBTX ships a 16x16 `s_snow02`.

**They are copies of the `s_snow` material sitting beside them in the same
model**, re-pointed at a different texture and never re-declared:

| model | material | declared | NSBTX | u span |
|---|---|---|---|---|
| `m_dun2701_00_01c` | `s_snow` | 32x16 | 32x16 | 0..32 |
| `m_dun2701_00_01c` | **`s_snow02`** | **32x16** | **16x16** | **0..32** |
| `map03_26c` (Route 201) | `s_snow02` | 16x16 | 16x16 | 0..16 |

Route 201's own `s_snow02` is what a material authored for that texture looks
like: 16x16 declared, 0..16 span. Lake Verity's carries `s_snow`'s numbers
verbatim.

**Nothing reads the field, so the picture is right.** `magW`/`magH` are 1.0 and
the texture-transform mode is 0, so `orig_width` is never spent; with repeat on,
the 16x16 simply tiles twice across the same quad. The proof that a UV span past
the declared size is ordinary is one material along: `s_snow04` in the same model
declares 16x16 (correctly) and spans 0..32.

**The test now lists the two**, `STALE_ORIG_SIZE`, keyed `(model, texture) ->
(declared, actual)`, and asserts:
* the disagreements are exactly the listed ones — a third fails;
* a listed entry whose model is still in the atlas must still disagree — a stale
  exception fails;
* **and, in the same pass, that ZERO of the 605 agree eight bytes late.** That
  second arm is the evidence for the 8-byte correction, measured here rather
  than recalled, and it makes the test a comparison instead of a threshold so
  the growing corpus cannot drift it.

Mutations run: restore the 8-byte-late base → fails; drop one listed exception
→ `an unlisted material disagrees`; add a bogus third → `listed but no longer
stale`.

## 2. `334:0 (64, 211, 0, 54)` — read the tuple before theorising

The tuple is `(bare_walkable, walkable, bare_route, route)`. **`bare_route` is
0**: not one of the 54 walked tiles is on a hole. What failed is
`bare_walkable == 0` — 64 of 211 tiles the collision grid calls passable.

**The 64 are exactly the `TILE_BEHAVIOR_PUDDLE` tiles**, an 8x8 block at the
shared corner of the map's four matrix cells, at mean coverage 36.6 of 255.

**The cartridge puts nothing under it.** Measured by deletion: drop the
`puddle` and `puddlep` materials from all four terrain models and 30 of 16,384
pixels survive over that block, all of them edge fringe. Three of the four
chunks contain nothing but `puddle`, `puddlep`, `tshadow`, `conttree_b`,
`conttree_t` — no ground material at all. Gen 4's water sheet is alpha-36
texels and needs a bed to read as water; Twinleaf has `lake` under its pond and
Route 219 has `beach`/`searock` under its sea. Verity Lakefront has neither.

**The game never draws that square either.** The block is walled off: a flood
fill over the map's walkable tiles reaches 0 of the 64 from anywhere else, and
the nearest standable tile is 21 tiles away while the DS field camera frames
15x10. It is scenery at the corner of four filler chunks that no camera
position can reach.

**`in_footprint` is NOT implicated.** Measured both ways, both cameras:

| camera | clip | bare | see-through |
|---|---|---|---|
| topdown | on | 0 | 64 |
| topdown | off | 0 | 64 |
| pitched | on | 0 | 56 |
| pitched | off | 0 | 56 |

Identical. The clip drops triangles outside the map's own cell and this puddle
is at the centre of the window, four cells in.

### What changed: `check_artwork` counts three outcomes, not two

`covered` (opaque) / **`sheer`** (drawn, see-through) / `bare` (nothing drawn at
all, the threshold being `raster.draw_tri`'s own alpha test). A hole is `bare`.
`sheer_behaviour` names the cartridge's behaviour for every see-through tile, so
the caller says WHICH tiles it will see through rather than how many.

The relaxation is fenced by a second test,
`test_water_over_a_bed_is_opaque_and_water_over_nothing_is_not`, over 512 tiles
that fail for two different reasons: Twinleaf's 40 `WATER_RIVER` are opaque
**only because the renderer blends** the sheet onto its bed, and Route 219's 472
`WATER_SEA` are opaque in their own texels and so say that "water" is not a
licence.

Mutations run:
* turn off the two-pass blend → Twinleaf's 40 river tiles go see-through, and
  **both** tests fail (`Extra items in the left set: 'TILE_BEHAVIOR_WATER_RIVER'`);
* make one of Route 201's two chunks fail to load → 452 route tiles and 407
  walkable tiles go `bare`, and the hole check fails, so the split did not cost
  the original guard;
* widen `SEE_THROUGH_BEHAVIOURS` to cover the real waters → the control's corpus
  empties and it fails `only 0 bedded water tiles — the control is near-vacuous`.
  The exception list cannot be widened quietly.

### The one thing still visible

`334:0` ships with an 8x8 see-through square in the middle of a forest; the
viewer will show the page background through it. That is the cartridge's own
content and the game never frames it, so nothing was invented to fill it. If it
should be filled anyway, the honest way is to composite the map's own
translucent-over-nothing geometry onto the field's 3D clear colour — which is
not decoded here, so it would be a chosen colour, not a read one.

### Not mine, but failing at `6d09091`

Six gen-5 tests fail with none of this work involved (no gen-5 test imports
`render_dsmaps` or reads the Platinum atlas): a Black 2 run has walked five maps
— `437:0 438:0 439:0 443:0 446:0` — that the shipped gen-5 atlas predates, and
two of their placements resolve to no prop model. **`black2-us` needs a
re-render, and those two placements need the same kind of look this note gives
Lake Verity.**

## 3. `311:0 world [0, 0]` — the origin is right, the claim around it is not

**Refuted: it did not default.** `map_matrix_101` resolved six cells, and
`map_window` sets `ox, oy = (0, 0)` ON PURPOSE for a map with a private matrix
— its runs report map-local coordinates, and the run's own samples (x 46,
y 53-54) land inside the 96x64 window, which `check_windows` confirms
5342/5342. The zero is measured.

**Refuted: it is not the multi-cell case.** `334:0` is 2x2 and `342:0` is 2x1;
both place correctly. The discriminator is the MATRIX, not the cell count:

| map | mapType | matrix | on the region frame | origin |
|---|---|---|---|---|
| `311:0` | **MAP_TYPE_CAVE** | **map_matrix_101** | **no** | [0, 0] |
| `334:0` | MAP_TYPE_OUTDOORS | map_matrix_000 | yes | [32, 800] |
| `342:0` | MAP_TYPE_OUTDOORS | map_matrix_000 | yes | [96, 832] |
| `411:0` | MAP_TYPE_TOWN_CITY | map_matrix_000 | yes | [96, 864] |
| `412:0`… | MAP_TYPE_INDOORS | private | no | [0, 0] |

**The defect is that `render_dsmaps.py` had two notions of "is this map on the
world frame" and they disagreed for exactly one map.** `map_window` decides by
the matrix (`shared = bool(matrix.get("headers"))`) — for the global
coordinates and the interior crop. `build_atlas` decided by `indoor`, i.e. by
`mapType in ("MAP_TYPE_INDOORS", "MAP_TYPE_POKECENTER")`. A cave is neither, so
Lake Verity was published as a world map at the world origin, 800 tiles north
of the band, and the viewer's `fit` fell to 0.10x. The entry even carried
`frame: map_matrix_101` beside a `world` measured in `map_matrix_000`'s frame —
it said so itself.

This is the same trap the atlas already recorded once: *"`worldLayout` stops
testing the string `MAP_TYPE_INDOOR`… the writer emits a normalised `indoor`
boolean"*. The normalised boolean was still the wrong question.

**The cartridge path already had it right.** `_MatrixMeta.__getitem__` derives
SoulSilver's indoor-ness as `not self.src.matrix(const).get("headers")`, with a
comment saying precisely why. Only the decomp path used the enum.

**Fix.** `Window.shared` carries the matrix answer, and `build_atlas` spends
that instead of `indoor` for the four things that follow from it — `world` +
`frame` + `open`, or `popup` + `building` + `floors` + `exits`; `doors` vs
`exits`; the name-prefix candidates; the floor union-find. `indoor` keeps its
own meaning (a claim about the map type) and is unchanged for all 13 maps, so
the two interior tests keep the population they were written for.

Result: `311:0` ships `popup: true`, no `world`, `exits` back to Verity
Lakefront, and `334:0` keeps the two `doors` that open it. Every other entry is
byte-identical.

Mutations run:
* put the split back on `indoor` → `maps placed on ['map_matrix_000',
  'map_matrix_101'] share one world`;
* let `311:0` keep a `world` while staying off the frame → the same failure, so
  the assertion pins the frame and not the map name.

`test_an_interior_is_reachable_from_the_map_its_door_is_on` now keys on
`popup ?? indoor`, the expression `mapatlas.js` itself keys on, so the new
cluster is covered rather than skipped — and it passes non-vacuously: `334:0`
holds the door into `311:0`'s single floor.

## The gen-5 failures are one thing, and it is not this

Six tests fail at `6d09091` with none of this work involved — no gen-5 test
imports `render_dsmaps` or reads the Platinum atlas. **A Black 2 run has walked
five maps the shipped gen-5 atlas predates** — `437:0 438:0 439:0 443:0 446:0`
— which is the same moving-target situation Lake Verity created for Platinum:

* `test_the_atlas_covers_exactly_the_maps_our_runs_stood_on` — the five extras;
* `test_the_gen5_renderer_reproduces_its_own_shipped_atlas` — opens
  `black2-us/437-0.png`, which was never rendered;
* `test_every_walked_tile_falls_inside_the_window_the_atlas_ships`,
  `test_the_window_check_rejects_a_map_moved_one_cell`,
  `test_every_route_tile_lands_on_real_geometry` — same five;
* `test_every_placement_resolves_to_a_prop_model` — **`black2-us: 2 placements
  name no model`**, which is a NEW fact about new assets, not a stale atlas.

`scripts/render_gen5maps.py --game black2-us` clears the first five. The two
unresolved placements want the same kind of look this note gives Lake Verity
and should not be shipped unexamined.
