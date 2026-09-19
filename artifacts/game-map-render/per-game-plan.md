# The detailed map, for the other six games

> Source: conversation 2026-09-19 (Andreas + Marvin), branch `skyemu-backend`.
> Andreas, verbatim: *"okay please help research and plan this, how do we get back
> the detailed map for each of the games?"*
> Status: **research + plan. Nothing here is built.**
> Companion to [plan.md](plan.md) (the FireRed map, shipped 2026-09-15) and to
> `artifacts/skyemu-backend/v2-ui-plan.md` §8 (the capability tiers).

## Current truth, revision 3 (2026-09-19, evening): published assets, not stitching

Andreas, after seeing the stitched atlas: *"okay this is soo bad, not worth it
going down this range. if we ignore the future roms. could you gather enough
publicly available assets to make maps for all games either in decomps or with
finding maps, and syncing coordinates?"* — then, on the research below:
*"i think i have seen online fly-overs of regions so i think people have
rendered even the 3d maps."* He is right, and it decides this.

**Option C is built, measured, and rejected as ARTWORK.** It works exactly as
designed — 15 maps, 96.3%-99.5% identical to pret's render (4.1) — and the
output is still a fog-of-war smear, because a stitch can only ever hold ground
somebody walked. Rejecting it was the right call on the picture; none of the
measurements were wrong.

**What survives from C, and it is the half that matters here:**

- `scripts/stitch_maps.py --verify` compares ANY atlas against real frames, per
  pixel, through a fitted colour map. That is the acceptance test for every
  option below.
- `--calibrate` derives the camera from motion with no reference map, which is
  how an asset with no declared origin gets pinned to the tile grid: one frame
  at a known (map, x, y), cross-correlated. **"Syncing coordinates" is the part
  that is already solved.**

### Where every game's map can come from, checked 2026-09-19

| game | source | what it is | still needed |
|---|---|---|---|
| FireRed | `pret/pokefirered` | shipped | — |
| Emerald | `pret/pokeemerald` | 2D tiles, same file shapes as FireRed | one constant (7 palettes -> 6) |
| Crystal | `pret/pokecrystal` | `maps/*.blk` + tilesets, gen 2 blocks | a second extractor |
| Platinum | `pret/pokeplatinum` `res/field/` | **667 committed `map_data_*.bin`** (model + 32x32 collision + BDHC), `texture_sets/`, and `matrices/*.json` for placement | a 3D render |
| SoulSilver | The Models Resource, **102 "Locations"** (Route 29, Violet City, Goldenrod...) — or our own ROM | ripped map models | registration + a 3D render |
| Black 2 | The Models Resource, **110 "Maps"** (Aspertia, Castelia, Route 01...) | ripped map models | registration + a 3D render |
| Black | The Models Resource has **1** location — this one needs our own ROM | NARC `a/0/0/8` | extraction + a 3D render |

So the DS games do not need a NARC extractor written first: Platinum's data is
in a decomp, and SoulSilver's and Black 2's have already been ripped and
published. Black (BW1) is the single gap, and it is the one game of the seven
nobody has asked to watch.

**Found map IMAGES are not a shortcut.** Checked, because it is the obvious
idea: the Spriters Resource "Full World Map + Objects" for HGSS is 784x1256 —
about 49 tiles wide for Johto *and* Kanto, so it is a stylised region
illustration, not the overworld. Bulbagarden's per-location maps (Azalea Town
932x508, Goldenrod 1330x885, plus day/evening/night variants) are closer to
usable and are worth one registration test each, but nothing about them
declares a tile origin or a scale. The MODELS are the real asset; an image rip
is a picture of one.

**The render, once, for all four DS games.** apicula converts NSBMD to glTF;
`trimesh` + `pyrender` render each map headless with an orthographic camera at a
chosen px/tile (no Blender dependency). Registration is exact by construction
because we choose the camera. And since we choose it, we do not have to look
straight down — set it to the game's own angle and the result looks like
Pokemon rather than like a roof survey.

**Order:** Emerald (one constant, proves "per game" is a descriptor) ->
**Platinum** (its data needs no extraction, so it proves the whole 3D pipeline
on the cheapest possible input) -> SoulSilver and Black 2 (the same pipeline,
fed by published rips) -> Crystal -> Black.

---

## Decision, 2026-09-19 (SUPERSEDED by revision 3 above): option C, done properly, is the path

Andreas, verbatim: *"i think what we should do is then just to become really good
and flawless at C dont you agree?"*, after asking whether C would also be the
answer for ROM hacks (§3.2). **Agreed** — with one amendment and one boundary.
This supersedes the recommendation in §3.1, which is left in place as the
reasoning that led here.

- **Why C wins outright.** One implementation covers all seven games, every game
  added later, and every ROM hack — and it is the only one of the five options
  whose output is the view the model actually played.
- **Amendment: keep option A for Emerald, as an ORACLE rather than a
  deliverable.** FireRed's pret atlas is what makes the stitcher checkable, and
  one reference is n=1. Emerald is ~a day through the already-shipped pipeline
  (one palette constant) and buys a second ground truth in a different region
  with a different tileset, on a game the stitcher was not tuned against. If it
  runs over a day, drop it — it is test infrastructure, not a feature.
- **Boundary — AMENDED 2026-09-19, and the amendment is the interesting part.**
  I first wrote that C cannot produce collision or warps, so the movement numbers
  would stay decomp work per game. Andreas: *"cant we simply measure when an
  input doesnt change the location + a text box / battle is not open?"* He is
  right, and §3.4 works it out: the collision comes out of the same observation
  stream, with one new address per game and one asymmetry to respect. What
  remains true is narrower: an observed graph knows only ground somebody has
  walked, so its distances are **upper bounds**, never fictional ones.
- **Unchanged: P comes first.** No position, no stitching. SoulSilver, Black and
  Black 2 have no position at all today.

## 0. "The detailed map" is three independent things, and the picture is the cheap one

What is on the report today for a FireRed run is one picture made of three layers,
each from a different source, each with a different cost per game:

| # | Layer | What it is | Produced by | Consumed by |
|---|---|---|---|---|
| **P** position | map id + x/y polled every turn | the per-game memory contract (L1 in the v2-ui-plan) | `route.json`, every route point |
| **G** geometry | passable tiles, warps, and the world frame that places one map beside another | `scripts/build_walkgraph.py` -> `data/firered-walkgraph.json` | progress distances, `world: [x,y]` in the atlas index |
| **A** artwork | one PNG per map at 16 px/tile | `scripts/render_gamemaps.py` -> `public/maps/*.png` + `index.json` | `RouteMap.svelte` / `mapatlas.js` |

**The dependency order is P -> G -> A, and it is strict.**

- With no **P** there is nothing to draw at all: no route, no tiles visited, no
  panel. This is the whole reason the map is missing for six games — not the
  artwork.
- **A** depends on **G** for placement, not only for passability:
  `render_gamemaps.py:433` copies `world` out of the walk graph into
  `index.json`, and `mapatlas.worldLayout:154` splits maps into the world frame
  and a column of insets on exactly that field. Artwork without geometry is a
  pile of rectangles in no particular arrangement.

So "get the detailed map back" is mostly the position work already priced in
`v2-ui-plan.md` §8, plus one new question that document never answers: **where the
pixels come from for a game whose overworld is 3D.** That is §3 here.

## 1. Where the seven games stand (measured, 2026-09-19)

P is from `p-a-results.md` (measured on real ROMs). G and A are verified today by
reading the upstream trees, and one of them corrects §8.3 of the v2-ui-plan.

| Game | P position | G source | A source | Format family |
|---|---|---|---|---|
| FireRed | yes | done | done, 32 maps / 760 KB | gen 3 2D tiles |
| Emerald | yes (map **group inferred**, not measured) | `pret/pokeemerald` `data/layouts` (443 entries) | same tree: `graphics/tilesets`, same file shapes as FireRed | gen 3 2D tiles |
| Crystal | yes | `pret/pokecrystal` `maps/*.blk` (643 entries) + `gfx/tilesets` (90) | same tree | gen 2 2D blocks |
| Platinum | x/y only, **no map id** | `pret/pokeplatinum` `res/field/maps/data/*.bin` — **667 files, committed in-repo** | same files (each carries its own model) | gen 4 3D |
| SoulSilver | none | `pret/pokeheartgold` commits `mapmatrix` + `build_model` NARCs but **not** land data | our own ROM: `fielddata/land_data/land_data_release.narc` | gen 4 3D |
| Black / Black 2 | none | no decomp exists (checked the whole `pret` org today: gen 4 is the newest) | our own ROM, NARC `a/0/0/8`, format from Pokemon-DS-Map-Studio | gen 5 3D |

### 1.1 Correction: Platinum's map data IS in the decomp

`v2-ui-plan.md` §8.3 says Platinum "ships map data in-repo? **no** ... the data
comes out of our own ROM, so it needs a NARC extractor first." That is wrong, and
it is the single biggest cost change in this document.

Verified by fetching one file rather than by reading a directory listing:

```
res/field/maps/data/map_data_001.bin   9790 bytes
  00: 00 08 00 00   permissions  2048 = 32 x 32 tiles x 2 bytes
  04: 00 00 00 00   (second section, empty here)
  08: ec 1d 00 00   model        7660 -> starts with "BMD0"/"MDL0" (NSBMD)
  0c: 42 00 00 00   BDHC           66
  16 + 2048 + 0 + 7660 + 66 = 9790   <- the arithmetic closes exactly
```

So for Platinum, **G needs no ROM and no NARC reader**: the collision grid is a
committed 2048-byte block per map, and `res/field/matrices/map_matrix_*.json` is
committed as readable JSON — that is the world frame, the thing gen 3 gets from
map connections. The 3D model sits in the same file, which also means **A's raw
material is in-repo for Platinum** — as geometry, not as pixels.

SoulSilver is *not* in that position: pokeheartgold commits the matrices and the
building models but not land data, so its equivalent has to be lifted out of our
own ROM. Same format family, so the reader is shared; the extractor is the extra
step (`ndspy`, a maintained Python NARC library, makes it ~100 lines).

## 2. What is already in place and does not need building

- **The referee refuses a foreign walk graph.** `referee._load_default_graph`
  takes the ladder's `game` and collapses to `None` on a mismatch, with the
  reasoning in its docstring. The "Emerald map (3,0) hits FireRed's Pallet Town"
  hazard from `cross-game-plan.md` §2.2 is closed.
- **A missing graph and a missing route already degrade.** `progress` stays
  `None`, the board falls back to gate count, and the map panel is absent rather
  than empty.
- **The atlas is a static shared asset** synced by `publish.sync_site`, so adding
  a game adds files, not a publishing path.

## 3. The decision: where the pixels come from for a 3D overworld

Gen 3 and gen 2 render themselves: their maps *are* tile grids plus a tile sheet,
which is what `render_gamemaps.py` already does. Gen 4 and gen 5 do not — each map
is a textured 3D mesh. Four ways to get a top-down picture, and one way to decide
not to.

**Option A — parameterise the pret pipeline (gen 1-3 only).**
Turn `render_gamemaps.py` and `build_walkgraph.py` into game descriptors (repo,
pinned SHA, seed maps, `NUM_PALS_IN_PRIMARY`). Emerald is nearly free: the
docstring already names the one constant that differs (7 for FireRed, 6 for
Emerald, "an Emerald value of 6 here would recolour half the world"). Crystal is a
second extractor — gen 2 is blocks of 4x4 tiles, collision per block, and its map
headers are `.asm` rather than JSON, so it needs a small parser.
*Reaches:* Emerald, Crystal. *Not:* any DS game. *Cost:* Emerald 1-2 days,
Crystal 2-3.

**Option B — render the 3D models offline.**
`apicula` (maintained, Rust) converts NSBMD to glTF/COLLADA; headless Blender
renders each map orthographically from straight above at 16 px/tile. For Platinum
the input is already in the decomp; for SoulSilver and the gen 5 pair it comes out
of our ROM first.
*Reaches:* every DS game, and the whole map whether or not anyone walked it.
*Cost:* a new toolchain (Rust binary + Blender in the build), and the output is a
roof-view render that does not look like what the player saw. Estimate: a week to
a first Platinum region, most of it in the render harness rather than the format.

**Option C — stitch the map out of our own emulator.**
We already hold the machine, and P gives us the player's tile on every turn. Walk
-> capture -> place the frame at the player's tile -> accumulate. One
implementation covers all seven games, GB through gen 5, with no decomp, no NARC
and no 3D.
*Reaches:* everything, but only ground that has been walked; the atlas grows with
every run, which for a benchmark is arguably a feature ("the map is drawn by the
players").
*Risks, each with a cheap control:* per-game px/tile and camera offset — measured
by pressing `right` once and diffing two frames, which also proves the shift is
uniform (if it is not, the camera is perspective and only the tiles nearest the
centre may be sampled); the player sprite and NPCs baked into the picture —
sampled from many visits and reduced by median, or masked at the player tile;
animated water and doors — same frame-0 caption the FireRed atlas already carries.
*Cost:* 2-3 days for the stitcher, plus the calibration probe per game.
**And it has a free acceptance test that none of the others has: run it on FireRed
and compare per tile against the pret render we already trust.**

**Option D — the in-game region map.**
Every game ships its own town map (Platinum's is committed at `res/town_map`).
Cheap, tiny, and honest at the scale of "how far across Sinnoh did this run get",
but it cannot show that a run walked around a fence, which was the whole point of
[plan.md](plan.md).

**Option E — no map for the DS games, stated.**
The panel stays absent, and the run page says the game records no position. This
is the current behaviour and it is not embarrassing; it costs nothing and is the
right answer for any game nothing has played yet.

### 3.1 Recommendation (SUPERSEDED by the decision block at the top — kept as the reasoning)

**A for Emerald now, C as the universal path, B only if C's FireRed control
fails.** Reasons, in order: Emerald is the one game where the finished pipeline
needs a constant changed rather than a pipeline built; C is the only route that
reaches Black and Black 2 at all, and the only one whose output looks like the
game the model actually played; B is the only route to a *complete* DS map, which
matters for progress geometry (G) rather than for the picture — and Platinum's G
comes free from the decomp either way.

### 3.2 C is the only option that survives a ROM hack

Andreas, 2026-09-19: *"would C also not be the perfect solution for Rom hacks?"*
Yes, and more strongly than for the official games — for a hack C is not the
cheaper option, it is the only one.

- **A dies completely.** New maps, new tilesets, new map ids, and no upstream
  tree to fetch any of it from.
- **The expensive layer is usually inherited.** Most FireRed/Emerald hacks keep
  the base engine's SaveBlock layout, so the position contract we already
  measured is likely to work unchanged. Where a decomp-based hack moved things,
  `find_addresses.py` re-measures in hours and needs no external data — it never
  took a decomp as input.
- **Bonus: C recovers the placement too.** Collision needs a decomp, but the
  world frame does not. Crossing a map connection scrolls the camera
  continuously, which gives the offset between two maps directly — the one part
  of the geometry layer the artwork needs, and otherwise unobtainable for a hack.
- **A hack is the only Pokémon content a model cannot have memorised.** If that
  ever becomes a benchmark direction, C is what makes it watchable, and it is the
  same build either way.

Two design consequences, both cheap now and expensive later:

1. **The atlas must be keyed by ROM identity, not by `game:`.** Radical Red's map
   `(3,0)` is not Pallet Town but answers to the same key. `public/maps/<game>/`
   (§5) becomes `public/maps/<rom sha1 or registry id>/`.
2. **The walk-graph gate needs the same.** `referee._load_default_graph` refuses a
   foreign graph by comparing the ladder's `game`; a hack registered as
   `game: firered-us` sails past it and loads FireRed's graph, which is exactly
   the confidently-wrong-distances failure that gate exists to stop. A hack needs
   its own `game` key even when it inherits every address.

### 3.3 What "flawless" has to mean, concretely

The stitcher's output is a claim that a pixel belongs to a tile. Each way that
claim can be false needs a rule, and most of them are a **discard**, not a
correction — a dropped frame costs nothing when the map fills in over many runs.

| # | Failure | Rule |
|---|---|---|
| F1 | wrong px/tile or camera offset — the map drifts a pixel per screen and looks fine until it does not | calibrate per game with a one-press diff (press `right` once, diff two frames); assert the measured shift equals the declared tile size |
| F2 | the camera is mid-scroll when the frame is captured, so the whole frame is offset | require the player's tile to sit at the calibrated screen position; otherwise **discard the frame** |
| F3 | the player sprite and NPCs baked into the artwork | mask the player tile always; take the median across visits for everything else, so a walker is outvoted |
| F4 | animated water, doors, grass | same frame-0 honesty the FireRed atlas already carries, said in the caption; the median handles the rest |
| F5 | perspective in gen 4/5 — tiles far from the camera centre are foreshortened | the F1 probe also measures whether the shift is uniform across the frame; if it is not, sample only the tiles nearest the centre |
| F6 | occlusion — a building or cliff hides tiles behind it that a walk-past never reveals | accepted: those tiles fill in when someone walks there, and unfilled cells are drawn as unknown rather than guessed |
| F7 | day/night palettes (Crystal, HGSS) — the same tile sampled at different in-game times | tag samples with the time of day, or accept one palette per cell and say which |
| F8 | a tile from a different map landing in the same cell | every sample tagged `(map id, x, y)`; this is also what makes it safe for hacks |
| F9 | the atlas is not reproducible | re-running over the same runs must produce byte-identical output |

**Acceptance bar, and it is free:** run the stitcher over the 29 published FireRed
runs and compare per tile against the pret render we already trust. A tile that
disagrees is either a bug or an honest difference (an NPC, an animation frame)
that has to be named. Emerald, through option A, is the second oracle — the game
the stitcher was not tuned on.

### 3.4 The collision can be observed too — invert the call the trace already makes

Andreas, 2026-09-19: *"cant we simply measure when an input doesnt change the
location + a text box / battle is not open?"* Yes, and the machinery is almost
entirely built — pointing the other way.

`trace.derive` already classifies every single input (`trace.py:150-230`). It
tracks the player's facing by inference, isolates the quiet (no-battle) state,
and then asks the walk graph one question:

```python
open_ = passable(tile, key)      # trace.py:192 — the graph as oracle
if open_ is False:   walls += 1              # a wall
elif open_ is True:  blocked += 1            # "a textbox, an NPC or a script ate it"
else:                unknown += 1
```

**Today the graph tells the trace what a wall is. His proposal is to let the
trace tell the graph.** Everything on the left of that call is already per-input,
already per-game, and already recorded.

What the observation stream yields, per `(map, x, y, direction)`:

| Observation | What it proves |
|---|---|
| the tile changed by one | a **passable edge**, permanently |
| the map changed | a **warp edge** — the thing gen 3 gets from `map.json` |
| the tile changed by two in one press | a **ledge**: one-way, and the reverse press proves it when it is refused |
| crossing a map boundary | the **world offset** between two maps (§3.2) |
| already facing that way, in control, nothing moved | **evidence** of a wall |

**The asymmetry is the whole design, and it is what makes this safe.**
Passability is *monotone*: one successful traversal proves an edge forever, and
no later observation can take it away. A block is only ever evidence — an NPC
standing in a doorway, a script, a textbox all look identical to a wall from the
outside. (FireRed's own builder excludes NPCs as barriers deliberately; an
observed graph would record them as walls unless told not to.) So:

- an edge is written on **one** positive observation;
- a wall is written only after **k** independent blocks with no positive
  observation ever, and one positive observation deletes the wall for good.

**What it needs that we do not have: the in-control signal.** Two flags, and the
trace's own docstring already names the gap — *"model error and bad luck are
indistinguishable without a textbox flag in the spec"*:

1. **in-battle** — L2 in `v2-ui-plan.md` §8.1, already priced, one new probe mode.
2. **textbox / script-active** — new, and findable by the same differential
   method: press A at a sign, snapshot; close the box, snapshot; controls are a
   menu, a battle and a cutscene. **This one pays for itself on FireRed alone**,
   where `blocked_by_actor` is measured but uncharged precisely because it cannot
   be attributed.

A weaker fallback that needs no new address, for a game where the flag resists:
credit a block only when some other direction pressed from the same tile in the
same turn window *did* move. That proves the game was accepting input, without
knowing why the first press failed.

**What it still cannot do:** know about ground nobody has walked. A shortest path
computed on an observed graph is an **upper bound** on the true one — it can
claim a detour where an unwalked shortcut exists. That is the opposite failure
from the wrong-graph hazard (`cross-game-plan.md` §2.2): incomplete, never
fictional, and it converges as runs accumulate. Any distance derived this way has
to be labelled as a bound, not a number.

**And for a ROM hack it is the only collision there will ever be** (§3.2).

### 3.5 How the method lands on gen 5 (Black / Black 2)

Andreas, 2026-09-19: *"okay how will this method work on gen 5?"* The shape is
unchanged — same observation stream, same monotone merge. Five things differ,
and one of them breaks a rule §3.4 states as universal.

**1. Nothing runs until position is measured, and gen 5 is where the finder is
most likely to miss it.** `find_addresses.py` has `FORMS = [u8, s8, u16, s16]`
(line 179) and `MAX_TILE_DELTA = 4` (line 183), which requires the per-press
delta to be 1..4. Platinum's 32-bit fields were caught anyway because a small
32-bit value's low u16 moves like a u16. A gen 5 coordinate stored fixed-point
— tile << 4, a plausible shape for a 3D overworld — steps by 16 and is
**rejected by that bound**. Fix before the first gen 5 run: make the magnitude
test scale-aware (delta in {1..4} x unit, unit in {1, 16}) and let the unit be an
OUTPUT of the search. Control, per v2-ui-plan §8.3.2: re-run **Platinum**, where
the answer is known, before pointing it at Black.

*A bonus if it is fixed-point:* the fractional part is a free "player is at rest"
signal. It settles the mid-animation sampling problem for the trace and F2 (the
camera-settled discard) for the stitcher, with no second address.

**2. Are the coordinates global to Unova, or map-local?** Gen 4/5 lay the world
out as a matrix of chunks, so x/y may be region-global rather than reset per map.
Cheap test, and it should be the first thing the probe does after finding them:
walk across a zone boundary. A jump means map-local (key the graph
`(map, x, y)`, as gen 3); continuity means global (one coordinate space, and the
world-frame stitching of §3.2 comes free). This decides the node key, so it is
worth knowing before anything is accumulated.

**3. The third axis, and the one way this method can invent an edge.**
Platinum's x is at +0 and its "y" at +8, 32-bit — which leaves **+4** unused
between them. The DS convention for a position vector is (x, height, z), so the
referee's "y" is the depth axis and the **height is probably already sitting at
+4, unread.** Control: stand at the foot of stairs or a slope, snapshot, climb,
snapshot; +4 moves while the other two do not.

This matters more in gen 5 than anywhere else because Unova is full of bridges
(Skyarrow, Driftveil drawbridge, Village Bridge). With a `(x, y)` node key, the
bridge deck and the ground beneath it are **one node**, and the observed graph
will happily join them — a **fictional** edge, which §3.4 claims this method
never produces. The claim holds only with height in the key. Gen 3's graph
already models elevation, so the data model does not change.

**4. Seasons break "one traversal proves an edge forever".** Unova cycles
spring/summer/autumn/winter by month, and in winter snow piles **open passages
that do not exist in other seasons** — Route 8, Icirrus City, Moor of Icirrus,
Twist Mountain, Dragonspiral Tower. So a gen 5 edge is not permanent; it is
permanent *for a season*. Edges need a season tag (a mask, since most edges hold
in all four).

**This is bigger than the map.** Season is an **uncontrolled variable in the run
configuration today**: two Black 2 runs started in different real-world months
are not playing the same geography, and nothing in `roms.yaml` or the start state
pins it. That is a benchmark-comparability defect independent of any of this, and
worth closing whether or not the map work happens.

**5. The stitcher meets a camera that moves.** Gen 5's overworld camera is
perspective and in places cinematic (Castelia, the bridges, Nimbasa). A single
per-game calibration (F1) is therefore wrong in those places. Fix: derive the
calibration **per frame** from the observed one-tile motion, and discard frames
whose measured px/tile disagrees with the standing calibration — so a swinging
camera drops out of the atlas instead of corrupting it. Same discard-not-correct
rule as the rest of §3.3.

**6. Gen 5 can still be validated, without a decomp.** There is no pret repo, but
the ROM's own collision is in NARC `a/0/0/8` as `.per` files, a format already
implemented in open source (§8.3.1 of the v2-ui-plan). Extracted with `ndspy`,
that is an **oracle for verification only** — exactly the role FireRed's pret
graph plays for the observed graph. It is not on the critical path, and it is
what lets us say the gen 5 map is right rather than hope so.

## 4. Order of work

C-first, after the decision above. P is not the map work — it is
`find_addresses.py` work already priced in `v2-ui-plan.md` §8.5 — but nothing
here draws a pixel without it.

1. **P1 — position for SoulSilver, Black, Black 2** (hours per game, tool exists,
   proven on Platinum) and **Platinum's map id** (the one that resisted the
   adjacency prior).
   - Gen 5 control first: add fixed-point forms to `FORMS` and re-run *Platinum*,
     where the answer is already known, before pointing the finder at Black
     (v2-ui-plan §8.3.2).
2. **C1 — the calibration probe**, one game at a time: press `right` once, diff
   two frames, read off px/tile, the camera offset, and whether the shift is
   uniform across the frame (F1, F2, F5). It is a few lines against a backend we
   already drive, and it is what tells us whether gen 5 needs centre-only
   sampling before any stitcher exists.
3. **C2 — the stitcher, against FireRed.** Build it, run it over the 29 published
   FireRed runs, accept it only on per-tile agreement with the pret atlas. Every
   rule in §3.3 gets its control here, where a ground truth exists.
4. **C3 — Emerald as the second oracle** (option A, one palette constant, one
   day, dropped if it runs longer). A game the stitcher was not tuned on.
5. **C4 — the first DS game**, whichever of SoulSilver / Black 2 P1 finishes
   first. This is where F5-F7 stop being hypothetical.
6. **G1 — the textbox/script flag** (§3.4) on FireRed first, where it settles an
   ambiguity we already measure, then per game alongside L2.
7. **G2 — the observed walk graph**: accumulate `(map, x, y, dir)` observations
   across runs with the monotone merge rule, and label its distances as bounds.
   Validated the same way as the stitcher: build it from the 29 published FireRed
   runs and compare against the pret graph — every edge it has must be in the
   pret graph, and the gap is exactly the ground nobody walked.
8. **G3 — Platinum's walk graph from the committed permissions** (§1.1), if a
   complete graph ever beats an observed one for a game that has one.

Crystal's extractor and the gen 4/5 offline render (option B) sit after all of
that, and neither blocks anything.

## 4.1 Built 2026-09-19: C2, the stitcher, against FireRed

`src/app/stitch.py` + `scripts/stitch_maps.py` + `tests/test_stitch.py`.
**Result: 15 maps stitched out of real runs, every one of them 96.3%-99.5%
identical to pret's render** (within 1 per channel, which is the GBA's 5-bit
colour rounding). Viridian Forest reads 99.5% through a fitted colour map, which
is its runtime `WEATHER_SHADE` and not a defect. Run it with:

    venv/bin/python scripts/stitch_maps.py <runs>/ --verify --calibrate

**The calibration passes its own control.** Derived from motion alone, with no
reference map: tile 16 px (11 of 11 unambiguous one-tile moves agree), player
column 7, tile top row 71 +/- 2 against the spec's 72. That is the measurement
a new game gets, and on FireRed it recovers what the render already proved.

Three things building it found that the plan above did not predict:

**1. The harness's grid overlay had to be UNDONE, not masked.** Every saved
screenshot carries `mgba._draw_grid_overlay`'s red tile grid. Masking it looks
safe and is not: it is drawn in screen space, and a frame is only captured with
the camera at rest, which is tile-aligned — so the same 2 px of every tile are
covered in every frame forever and accumulation never fills them. The first
stitch came out as artwork behind a black grid. The overlay is a constant alpha
composite, so it inverts exactly, and snapping the result to the console's
5-bit colour ladder takes the overlaid pixels from **1.6% matching the render
before, to 84.0% inverted, to 98.1% inverted and snapped** — the same rate as
pixels it never touched. Recovering them is +51% of the atlas.

**2. A frame's agreement with the settled map is bimodal with an empty band.**
Per map, the share of a frame that matches what the votes settled on falls in
[0, 0.3] or [0.7, 1.0] and never between. A frame either shows the map or it
does not, so the threshold is picked from a gap in the data rather than by
taste. 

**3. Voting cannot substitute for knowing whether the game was in the
overworld.** Pewter Gym stitched to 56% — two thirds of it a *battle screen*,
Bulbasaur and all — because one run fought Brock for 91 turns from one tile and
six of the nine runs that reached the gym predate the per-input trace
(2026-09-14) that flags a battle. Counting runs instead of frames does not fix
it either: the parts of a battle screen that are identical between runs (the
white ground, the HP box) agree with each other as happily as terrain does. The
fix is the honest one — **a run with no in-battle flag is refused entirely**,
and the gym then reads 98.4%. The cost is coverage: 6 of 29 runs qualify.

**Finding 3 is the same requirement as 3.4, arriving from the other side.** The
observed walk graph needs an in-control signal to tell a wall from a textbox;
the stitcher needs one to tell a map from a battle. One flag, two consumers —
which moves G1 (the textbox/battle flag per game) ahead of C3 and C4 in the
order above, because on a DS game *nothing* will be stitchable without it.

## 5. Schema changes this forces (small, but they gate the DS games)

- **`index.json` keys and the atlas directory.** Map keys are `"<group>:<num>"`
  and files `<group>-<num>.png`; a gen 4/5 map is a single id. The atlas needs a
  `game` field and a per-game directory (`public/maps/<game>/`), and the key shape
  has to come from the game descriptor.
- **The same issue in the checkpoint schema** is already recorded:
  `_REQUIRED_SIGNATURE_FIELDS["map"]` hard-requires the gen-3 pair
  (`v2-ui-plan.md` §8.4).
- **Walk graph nodes carry no game field.** The loader now refuses on the
  ladder's `game`, which is enough; a second graph makes it worth writing the
  game into the file itself rather than relying on the caller.

## 6. Open questions for Andreas

1. ~~Which picture for the DS games~~ — **answered 2026-09-19: C.** See the
   decision block.
2. **Is Crystal worth its own extractor?** Its only remaining job is to be a
   third oracle, which two already cover. Default answer: no, unless a Crystal
   run turns out to need it.
3. **Publishing — still open.** The FireRed atlas is Nintendo artwork rendered
   from pret and already on the public site. A stitched atlas is the same
   artwork, out of our own dump. Same precedent; worth naming once rather than
   discovering later. A ROM-hack atlas (§3.2) is a third case and the author is
   a person who can be asked.

## Related

- [plan.md](plan.md) — the FireRed map render, M1-M16
- `artifacts/skyemu-backend/v2-ui-plan.md` §8 — the capability tiers and costs
- `artifacts/skyemu-backend/cross-game-plan.md` — the address work, §2.2 on a
  wrong walk graph being worse than a missing one
- `artifacts/granular-progress/plan.md` — where the walk graph came from
