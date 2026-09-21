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
