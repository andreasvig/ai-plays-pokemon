# Does the map we ship match the game? — the per-turn alignment check

`scripts/verify_map_alignment.py`. One command, one run, one sheet: the map
artwork we ship, cropped to the camera window the player was standing in on a
given turn, beside the emulator frame the model was actually shown on that turn.

    ./venv/bin/python scripts/verify_map_alignment.py local/runs/<run_id>

This page is written in two halves on purpose. **Everything under
"Pre-registration" was written before a single alignment score was read**, so
the band cannot have been drawn around the numbers it judges. The measured
half comes after it.

---

## Why this is a new script and not `stitch_maps.py --verify`

They answer different questions and share only a tolerance.

| | compares | direction | granularity |
|---|---|---|---|
| `stitch_maps.py --verify` | the atlas **the stitcher built out of frames** against the shipped render | map vs map | one number per map |
| `verify_gamemap_render.py` | the shipped render against one run's frames | frame vs map | one number per turn, FireRed only |
| `verify_map_alignment.py` (this) | the shipped render against one run's frames | frame vs map | one number per turn, **every game with a screen model**, with controls and a sheet |

`--verify` cannot answer Andreas's question at all: it never looks at a
screenshot, it looks at a *stitch* of screenshots. A stitch is built with the
same `window_origin()` the crop would use, so a constant camera offset cancels
out of it — the stitch and the render would agree happily while every crop was
four tiles wrong. The pairing that has to be checked is frame-to-crop, and the
nearest existing code for that is `verify_gamemap_render.py`, which does the
pairing right and nothing else right: FireRed constants at module scope, no
`png_origin`, no camera declaration, no sheet, no controls, and it is not a
file this branch owns. So: a new script, reusing `src/app/stitch.py`'s
`ScreenSpec` / `run_spec` / `to_native` / `keep_mask` for the reading half and
`render_dsmaps.py`'s real-size-panel sheet idiom for the drawing half.

---

## Pre-registration

*(written before the first score was read)*

### The metric

`match` = the share of **comparable** pixels where the rendered crop and the
emulator frame agree within ±1 per channel.

Comparable means: inside the map PNG, not the player's own sprite box, above
the textbox rows, and at the console's native resolution (the saved frame is
decimated NEAREST, which is exact because the harness upscaled it NEAREST).
A per-colour lookup is fitted and used **only if it lowers the residual**, and
it is fitted identically for the correct arm and for every control, so it
cannot flatter one of them; the `via` column says which arm used it.

### The controls, reported with every number, never without

| control | what it is | what a working instrument does |
|---|---|---|
| `self` | the rendered crop pushed back out through the *screenshot* reader — upscaled to the run's own screenshot size, decimated, un-gridded — and compared to itself | 1.0000 exactly |
| `wrong crop` | the same map, the crop taken 4 tiles right and 4 tiles down | clearly below `match` |
| `wrong map` | the same frame against a *different* map of the same atlas | below `wrong crop` |
| `margin` | `match` minus the best of the eight one-tile-neighbour crops | positive |

`self` is the control that would have caught the 2026-09-20 `grid_overlay`
bug on sight: a reader that un-composites an overlay that was never applied
mangles the two pixels straddling every tile boundary, so a crop that has been
through that reader no longer equals itself. It is not a tautology — it is the
reader checking itself, and `tests/test_verify_map_alignment.py` proves it
bites by forcing `grid_overlay=True` on a run recorded with it off.

`margin` is the one number with room on both sides of the right answer. It goes
negative exactly under the classic silent failure — the atlas off by a constant
— because then a neighbouring crop fits better than the declared one.

### The band

- **`self` must be ≥ 0.9999.** Below that the reader is lossy and no other
  number on the page may be quoted at all.
- **ALIGNED** — median `match` ≥ 0.90, and median `match` − median `wrong crop`
  ≥ 0.20, and median `match` − median `wrong map` ≥ 0.20, and median `margin`
  ≥ 0.05.
- **SUSPECT** — median `match` in [0.70, 0.90), or `match` ≥ 0.90 with any of
  the three spread conditions unmet.
- **MISALIGNED** — median `match` < 0.70, or median `margin` ≤ 0.
- **NO INSTRUMENT** — the game has no flat-tile screen model. No number is
  printed for it, only the reason.

### The saturation guard

If every game's median `match` is ≥ 0.995 *and* every game's `wrong crop`
control is also ≥ 0.90, the band is void: that would be a measurement of the
ceiling rather than of the alignment, and the verdicts must not be quoted.

### Which games the instrument is valid for

Valid for the three 2D games — `firered-us`, `emerald-us`, `crystal-us`. Their
screen is an axis-aligned window of a tile grid, the artwork is the same tile
grid, and a pixel in one is a pixel in the other.

**Not valid for the four DS games** — `platinum-us`, `soulsilver-us`,
`black-us`, `black2-us`. Their atlases declare `render: 3d-ortho`: the artwork
is the cartridge's own 3D field rendered *straight down and orthographic*. The
emulator draws that same field through the game's **tilted perspective** field
camera, so a tree in the frame leans and occludes the ground behind it while
the same tree in the artwork is a flat footprint. The two images are of the
same world and are not the same projection, and a per-pixel difference between
them is not a measurement of anything. The script refuses them by name rather
than printing a number, and says this.

---

## Amendments to the instrument, made after the first read

Two, both recorded here rather than folded in quietly. **Neither moves a band
threshold** — the thresholds above are exactly the ones the script applies,
and `tests/test_verify_map_alignment.py::test_the_notes_quote_the_band_the_script_actually_applies`
fails if the page and `BAND` ever drift apart.

### 1. Compare on the console's colour, not on the PNG's bytes

*Found by looking at the first numbers and refusing to believe them.* The
first Emerald run reported `match 0.9854` with a **raw** agreement of
`0.0012`: the whole signal was coming from a per-colour lookup fitted on the
data. A metric whose entire signal is a lookup fitted from the frame it is
scoring is not a measurement.

The cause is an encoding, not an alignment. A GBA and a GBC hold 5 bits per
channel; a PNG has 8, so whoever writes the PNG chooses how to fill the other
three, and the two writers here chose differently — the renders expand `v` to
`(v << 3) | (v >> 2)` (14 → 115), SkyEmu emits `v << 3` (14 → 112). That is a
constant 0-7 per channel on every pixel of every map, which is 3-6 past a ±1
tolerance and says nothing about whether the map lines up.

Both sides are now reduced to the console's own 32 levels before comparison
(`on_ladder`), and the raw-byte agreement is still reported as `raw8` so the
mismatch stays visible. It is not uniform across the games, which is itself
worth someone's attention:

| game | `raw8` (raw bytes agree) |
|---|---|
| firered-us | 0.0000 |
| emerald-us | 0.0015 |
| crystal-us | 0.9896 |

Crystal's PNGs already agree with SkyEmu byte for byte; FireRed's and
Emerald's do not. **This is settled and is not a defect** — see "The 5-bit
expansion question is settled" below. Both spellings are the same 5-bit
value; `gen2_pal` has its own measured reason to match SkyEmu exactly, and
where the gen-3 path differs from SkyEmu ours is the truer colour.

### 2. Refuse a colour lookup that collapses the palette

With the encoding gone the fit had little left to do — and what it was still
doing was wrong. Unguarded, `fit_colour_map` maps every render colour onto
the screen colour it most often sits under, so on a frame that is **not the
map** it collapses the palette and scores a perfect match on a picture of
nothing. Measured:

| frame | palette in → out | unguarded score |
|---|---|---|
| Crystal turn 169, a fade | 4 → 1 | **1.0000** (fabricated) |
| FireRed turn 230, a menu | 39 → 6 | 0.5040 |
| FireRed Viridian Forest, `WEATHER_SHADE` | 11 → 11, 35 → 35 | 0.9972 (correct) |

A runtime recolour is a permutation of the palette; a non-map frame collapses
it. The lookup is now used only when it keeps at least three quarters of the
palette distinct (`LOOKUP_KEEPS`), with the nearest legitimate case at 23 → 22.
This tightened the **controls** far more than the headline: FireRed's wrong-map
control fell from 0.33 to 0.03 once the fit stopped rescuing it.

### 3. "Not a map frame" is a third answer, not a low score

The two lowest Emerald scores in the first run were both Mudkip fighting a
Zigzagoon. Calling that "the map is misaligned" is wrong twice over.

The nine candidate crops give a free oracle for the distinction, because the
two cases are mutually exclusive:

- **misaligned** — the declared crop scores badly and a NEIGHBOUR scores well
  (`margin` clearly negative);
- **not a map frame** — nothing fits anywhere; all nine score the same low
  number (`margin ≈ 0`).

A turn whose best of nine is under `MAP_FRAME_FLOOR = 0.70` is now reported
and excluded rather than averaged in.

**And the reason those frames were there at all is a finding of its own.** Two
v2 runs — `2026-09-19_22-48-56_config-v2-emerald` (386 traces) and
`…_config-v2-crystal` (383 traces) — report `in_battle` on **zero** samples,
while later runs of the same games report it on dozens. Their battle probe
never fired, so every battle frame they hold reaches this tool as a map frame
— **and reaches `stitch_maps.py`'s atlas the same way**, which is the exact
failure that made Pewter Gym come out 56% battle screen. `src/app/stitch.py`'s
`battle_turns` and `has_trace` both treat "has a trace" as "can filter
battles"; on these two runs it cannot. Not fixed here (that file belongs to
the stitcher); the tool warns by name.

### 4. Geometry and colour are two claims, and ALIGNED was making only one of them

*Pre-registered below BEFORE the clause was implemented or run, on the same
terms as the original band. Nothing above this heading has been edited.*

**The gap.** The Crystal atlas was rendered `nite` and checked against night
runs, which is consistent — and then the atlas moved to `day` (correctly: the
100-turn runs are daytime and the shipped map was drawing Johto in indigo
under screenshots of green grass). Run against a night run, the `day` atlas
reports:

    verdict  turns   match  no fit  wrong crop  wrong map    self   margin    raw8
    ALIGNED     10  0.9923  0.0000      0.0000     0.0000  1.0000  +0.1590  0.0000

`no fit 0.0000` and `raw8 0.0000` say the two pictures share **no colours at
all**, and the fitted lookup is supplying the entire 0.9923. Day and night are
a whole-palette permutation with the same number of distinct colours, so the
three-quarters-distinct guard from amendment 2 passes it — honestly, because
it *is* a permutation, which is the case that guard exists to allow. And the
verdict reads **ALIGNED**, for a purple map under a green game.

The verdict is not wrong about geometry: margin +0.1590, every tile where it
should be. It is wrong because it was answering one question with one word.

**The rule.** Two verdicts from here on, both always printed.

*Geometry* is unchanged — ALIGNED / SUSPECT / MISALIGNED, on the band above.
A wrong palette must NOT become MISALIGNED; the tiles do line up.

*Colour* is new. A sampled map frame is **recoloured** when the fitted lookup
did the work: its pre-lookup agreement (`no fit`) is below
`RECOLOURED_BELOW = 0.70` while its fitted `match` clears
`aligned_match = 0.90`. The colour verdict is then taken over the **distinct
maps** sampled, not over turns:

| colour verdict | condition | reading | gate |
|---|---|---|---|
| `COLOURS MATCH` | no sampled map is recoloured | the artwork is the right palette | pass |
| `COLOURS DIFFER ON SOME MAPS` | ≥ 2 maps sampled, some recoloured, some not | a per-map runtime recolour — weather, a cave, a dark room — which the artwork is supposed to absorb and the lookup is supposed to see through | pass, maps named |
| `COLOURS DIFFER EVERYWHERE` | ≥ 2 maps sampled and **every** one recoloured | the atlas is the wrong palette variant for these runs | **fail** |
| `COLOURS DIFFER, CAUSE AMBIGUOUS` | exactly 1 map sampled and it is recoloured | cannot be told apart from this sample | **fail** |

The headline appends `, COLOURS DIFFER` to the geometry verdict for the last
two, so `ALIGNED, COLOURS DIFFER` is a sentence a reviewer who reads verdicts
and not columns cannot misread.

**Why the discriminator is scope and not pixels, said plainly.** Per turn,
"the game dimmed the lights on this one map" and "the whole atlas is the wrong
time of day" are *the same observation*: a palette permutation with a
near-zero raw agreement and a near-perfect fitted one. **They cannot be
separated from one frame's pixels, and this tool does not pretend to.** What
separates them is how far the recolour reaches. `WEATHER_SHADE` is a property
of Viridian Forest; a clock is a property of the world, so it recolours every
map the run walked. Hence a verdict over distinct maps, and hence the explicit
ambiguous row: with one map in the sample there is no scope to measure and the
tool says so instead of guessing.

`COLOURS DIFFER, CAUSE AMBIGUOUS` fails the gate rather than passing. A sample
too narrow to tell purple Johto from a dark cave is not a pass, and the remedy
is one flag (`--turns`), which the message says.

**The control this clause must survive.** FireRed's Viridian Forest (`1:0`)
under `WEATHER_SHADE` is a genuine permutation the map is supposed to absorb
(measured in amendment 2: raw 0.0000 → fitted 0.9972, palette 11 → 11 and
35 → 35). A FireRed sample that includes it must still come out **plain
ALIGNED / COLOURS DIFFER ON SOME MAPS**, never `COLOURS DIFFER`. If it does
not, this clause is worse than the gap it closes.

**A recommendation this measurement cannot make for itself.** No atlas records
which palette variant it was rendered from — `Game.time_of_day` lives in
`scripts/render_gamemaps.py` and stops there. If the atlas carried it, this
clause could name the mismatch outright ("atlas is `nite`, the run is not")
instead of inferring it from scope. Not changed here; that file is not this
branch's.

### 5. The colour clause failed its own control, in the direction that matters

*Amendment 4 is left exactly as pre-registered above. This records what
happened when it was run, which is the point of writing it down first.*

It passed the control it was written to survive — FireRed's Viridian Forest
came out `COLOURS DIFFER ON SOME MAPS`, 1 of 11, no failure, correct. And it
**missed the defect it was written to catch**: a daytime Crystal run against
the `nite` atlas came out `COLOURS DIFFER ON SOME MAPS`, 1 of 3, passing.
Two separate mistakes, both visible in the per-map numbers:

    map                      indoor   raw (no fit)   fitted
    24:3 ROUTE_29            False         0.0000    0.9862
    24:4 NEW_BARK_TOWN       False         0.0002    0.8965
    24:5 ELMS_LAB            True          0.9255    0.9299
    24:6 PLAYERS_HOUSE_1F    True          0.9806    0.9806

**Mistake one: the scope unit.** I counted every sampled map. But a clock is a
property of the world and only reaches maps that take their colours from the
world — **a gen-2 indoor map declares `PALETTE_DAY` and is the same picture at
midnight**, which `render_gamemaps.py`'s own docstring says and I did not
read carefully enough. The two clean rows above are not evidence of anything;
they are maps the cause cannot touch. Counted over all four maps this is "1 of
4" and reads as weather. Counted over the two the cause can reach, it is all
of them.

So the scope unit is now the **outdoor** maps, by the atlas's own `indoor`
flag. That generalises past gen 2 rather than special-casing it: a gen-5
season (`"season": "spring"` in the Black/Black 2 atlases) will reach exactly
the same set when those games get an instrument.

**Mistake two: a second gate wearing the first one's clothes.** The recoloured
test was `no fit < 0.70 AND fitted >= 0.90`. NEW_BARK_TOWN's fitted 0.8965 is
NPCs; its 0.8963 *gap* is the palette. The absolute level the lookup reached
carries sprites and animated tiles and has nothing to say about colour, and it
discarded the second-clearest recolour in the sample over four thousandths.
The test is now about the gap the lookup had to close:

    RECOLOURED_GAP = 0.3        how much of the disagreement the fitted colour
                                lookup had to close. Above this, the two
                                pictures do not share their colours and a
                                palette permutation is what explains it.

This started as two constants — a `direct < 0.70` floor as well as the gap —
and the mutation control killed the floor: changing it to 1.01 changed no
result, because `match <= 1` and `match >= direct` mean a gap of 0.30 already
implies a pre-lookup agreement at or under 0.70. A no-op mutant is a constant
that decides nothing, and two constants where one bites is one of them waiting
to drift, so the floor is gone.

**The corrected rule**, which is what the script applies:

| colour verdict | condition (over the sampled maps) | gate |
|---|---|---|
| `COLOURS MATCH` | none recoloured | pass |
| `COLOURS DIFFER ON SOME MAPS` | some but not all **outdoor** maps recoloured | pass, maps named |
| `COLOURS DIFFER EVERYWHERE` | ≥ 2 outdoor maps sampled and **every one** recoloured | **fail** |
| `COLOURS DIFFER, CAUSE AMBIGUOUS` | every outdoor map recoloured but fewer than 2 were sampled | **fail** |

The ambiguous row also had to be corrected: as pre-registered it asked whether
*every sampled map* was recoloured, and with one route and two clean houses
that is false, so the daytime run fell through to `SOME MAPS`. It now asks
about the outdoor maps, for the same reason as mistake one.

**Both controls now behave**, and the ambiguity is real rather than decorative
— the same run gives a different answer at a narrower sample, which is the
honest one:

| arm | sample | colour verdict | gate |
|---|---|---|---|
| FireRed, Viridian Forest among 11 maps (7 outdoor) | `--turns 20` | `COLOURS DIFFER ON SOME MAPS` (1:0 ViridianForest) | pass |
| Crystal night runs vs the `nite` atlas | `--turns 14` | `COLOURS MATCH` | pass |
| Crystal **day** run vs the `nite` atlas | `--turns 20` | `COLOURS DIFFER, CAUSE AMBIGUOUS` — only ROUTE_29 was world-lit | **fail** |
| Crystal **day** run vs the `nite` atlas | `--turns 40` | `COLOURS DIFFER EVERYWHERE` — ROUTE_29 and NEW_BARK_TOWN | **fail** |

and the headline for the last two reads `ALIGNED, COLOURS DIFFER`, with the
concrete pair printed underneath: *the map's commonest colour 120,112,192 is
176,248,80 on screen* — indigo against green, which needs no explaining.

`--turns` now defaults to 16 rather than 10, because the colour verdict needs
the sample to span two outdoor maps before it can say anything, and says so
when it cannot.

**Note on which atlas this branch measured.** This worktree still holds the
`nite` Crystal atlas; the change to `day` has not landed here. So the arms
above are the mirror of the coordinator's — a day run against a night atlas
rather than the reverse — and they are the same defect seen from the other
side. Once `day` lands, the night runs become the failing arm and the day runs
the passing one. `test_the_atlas_is_one_time_of_day_so_exactly_one_kind_of_run_must_differ`
asserts the XOR rather than a direction, so it survives the flip.

### 6. A map-entry frame is a third exclusion class, and it needs a non-pixel signal

*Written before the clause was implemented. Unlike amendments 4 and 5 the
thresholds here are NOT blind: I measured one frame's disagreement profile
first, because the question "can this be told from a real misalignment by
pixels alone?" can only be answered by looking. What that measurement decided
is recorded below, and the control that keeps it honest is stated with it.*

**The frame.** Emerald turn 49, `0:9 LittlerootTown` tile (14, 8), match
0.5090, **margin −0.3432**. A negative margin is the one signal built to mean
"a neighbouring crop fits better than the declared one" — the constant-offset
failure — so it reads as the tool catching a real defect. It is not one. The
frame carries the **"LITTLEROOT TOWN" area-name banner** across the top-left,
the overlay the game paints for a second or two on entering a new area.

**Can it be told from a misalignment by its pixels? No.** Measured, per tile
row, the share of comparable pixels that disagree:

    at the DECLARED crop     0.80  0.74  0.30  0.47  0.53  0.42  0.17
    at the best neighbour    0.43  0.45  0.10  0.00  0.00  0.02  0.00   (0, +1)

At the declared crop the disagreement is spread over the whole frame, not
concentrated at the top — because on an entry frame **the camera is also
still mid-scroll from the warp**, so the recorded tile really is one tile off
from where the camera was. Outside a 9×3-tile top-left block: 0.398 bad at the
declared crop, 0.004 at the neighbour one tile down.

That is the same observation a genuine one-tile atlas offset produces. **So
the honest answer is that the pixels alone cannot separate them, and the
map-change signal is required** rather than corroborating.

**The rule.** A sampled turn is a MAP-ENTRY frame when **both** hold:

1. **the run's own record** — the position this frame is paired with is on a
   different map from the position before it, so this is the first frame after
   a warp. Non-pixel, independent, and required; without it nothing is
   excluded on banner grounds.
2. **a banner-shaped residual** — at the best-fitting of the nine candidate
   crops there is a block of disagreement anchored at the frame's top-left
   corner, at least `BANNER_TILES_W = 5` wide and `BANNER_TILES_H = 2` tall,
   at least `BANNER_INSIDE = 0.3` of it disagreeing, while **outside** that
   block the frame agrees at `BANNER_OUTSIDE = 0.9` or better.

Clause 2 is tested at the best of the nine offsets, not at the declared one,
precisely because the camera may not have settled; and it is a statement about
the SHAPE of the residual, never about the turn's score. A genuine
misalignment has a neighbour that is clean *everywhere including the top* and
so leaves no block to find.

Turn 49 against those numbers: block 9×3, inside 0.478 disagreeing, outside
0.996 agreeing. Turn 50 and 51, ordinary frames on the same map, have no such
block at their declared crop (bad 0.00–0.03 on every row).

Excluded turns are **listed by name** in their own class, beside the
battle/menu one, so nobody has to wonder whether an inconvenient score was
quietly dropped.

**The controls this clause must survive**, because an exclusion that hides the
failure mode is worse than the noise it removes:

- with a **4-tile offset** applied to the atlas, the banner rule must NOT
  fire on the banner frame — nothing matches at any of the nine offsets, so
  there is no clean outside and no block to find. The exclusion must not be
  what hides a broken atlas.
- with a 4-tile offset applied, the game must still come out **not ALIGNED**.
- the wrong-crop and wrong-map controls on the frames that are KEPT must not
  move materially when the clause is switched on. If excluding transitional
  frames flatters the spread, the spread was measuring the transitions.

**Scope, stated so it cannot grow.** The clause can only ever remove frames
the run's own positions call the first on a new map. On the flagship Emerald
run that is a handful of the 82 placeable turns; every other turn is still
evidence.

### The 5-bit expansion question is settled (not by me)

`read_pal` in `scripts/render_gamemaps.py` now carries the reasoning: pret's
`.pal` files use the full-range spelling `(v << 3) | (v >> 2)` and SkyEmu
writes `v << 3`; they are the same 5-bit value 0-7 apart per channel, and
where they differ ours is the truer colour. Crystal agrees byte for byte only
because `gen2_pal` has its own measured reason to match SkyEmu exactly
(a frame's commonest colour is 120,112,192 and nite grey is RGB 15,14,24 —
15×8, 14×8, 24×8). **Neither renderer is broken**, the `on_ladder` reduction
in amendment 1 is the right comparison, and `raw8` stays visible. The wording
under "What it measured" that treated this as an open question has been
corrected.

---

## What it measured

`./venv/bin/python scripts/verify_map_alignment.py local/runs/*config-v2-<game>__* --turns 14`

Medians over the sampled turns, spread across every v2 run of each game.
Sheets under `artifacts/game-map-render/verify/`.

| game | geometry | colour | turns | match | no fit | wrong crop | wrong map | self | margin | raw8 |
|---|---|---|---|---|---|---|---|---|---|---|
| firered-us | **ALIGNED** | DIFFER ON SOME MAPS (1 of 11 — `1:0` ViridianForest) | 20 of 20 | 0.9883 | 0.9846 | 0.1454 | 0.0265 | 1.0000 | +0.3998 | 0.0006 |
| emerald-us *(flagship run, `--turns 60`)* | **ALIGNED** | COLOURS MATCH (0 of 9) | 47 of 60 | 0.9873 | 0.9873 | 0.2278 | 0.0000 | 1.0000 | +0.3737 | 0.0000 |
| crystal-us | **ALIGNED** | COLOURS MATCH (0 of 6) | 19 of 20 | 0.9929 | 0.9913 | 0.5595 | 0.4592 | 1.0000 | +0.1567 | 0.9913 |
| crystal-us, *daytime run vs the `nite` atlas* | **ALIGNED** | **COLOURS DIFFER EVERYWHERE** (2 of 2 outdoor) | 37 of 40 | 0.9796 | **0.0000** | 0.3111 | 0.0003 | 1.0000 | +0.1562 | **0.0000** |
| platinum-us | NO INSTRUMENT | — | — | — | — | — | — | — | — | — |
| soulsilver-us | NO INSTRUMENT | — | — | — | — | — | — | — | — | — |
| black-us | NO INSTRUMENT | — | — | — | — | — | — | — | — | — |
| black2-us | NO INSTRUMENT | — | — | — | — | — | — | — | — | — |

The fourth row is the point of amendment 5, kept as a committed
counter-example (`crystal-us-daytime-run.json`): identical geometry — margin
+0.1562, every tile where it should be — and a raw agreement of exactly zero,
because the atlas in this worktree is `nite` and that run was played in
daylight. Before the colour clause it read simply `ALIGNED`.

Every clause of the GEOMETRY band cleared for all three 2D games. The saturation guard
did **not** trip: the wrong-crop controls sit at 0.10 / 0.22 / 0.43, nowhere
near the 0.90 that would say the ceiling had been measured. Crystal is the
closest to saturation — a GBC map has four colours per tile, so a four-tile
offset lands on a plausible colour far more often, which is why its controls
are the highest and its margin the smallest of the three.

The DS games are refused with the projection reason and with their own counts,
so the refusal is visibly about the projection and not about missing plumbing:
374 placeable Platinum turns, 189 SoulSilver, 203 Black, 185 Black 2 — every
one of them naming a map the atlas ships, i.e. the run reader, the `"342"` →
`"342:0"` spelling and the `png_origin` subtraction all work, and only the
pixel comparison is refused.

## The map-entry exclusion, measured

On the flagship Emerald run (`2026-09-20_20-56-15`, all 82 placeable turns),
**seven** turns carry the run's own map-change record and exactly **one** of
them also carries the banner:

    turns the run says are the first on a new map   3, 30, 37, 42, 48, 49, 77
    ...of those, with a banner found                49
    ...entered but no banner, and still scored      3, 30, 37, 42, 48, 77

Turn 49 is excluded and printed by name, with the block that identified it:
a 5×2-tile block at the top-left disagreeing on 95% of its pixels while the
rest of the frame agrees on 96%. The other six warp frames are kept, which is
the point of requiring both signals — dropping every first-frame-on-a-new-map
would throw away six good measurements, and on a broken atlas six chances to
catch it.

### The three controls

| control | result |
|---|---|
| with the atlas 4 tiles off, does the banner rule fire on turn 49? | **no** — nothing matches at any of the nine offsets, so there is no clean outside and no block. The frame is still excluded, but as "shows no map at all". |
| with the atlas 4 tiles off, is the game still not ALIGNED? | **yes** — MISALIGNED, exit 1, and `entry_frames` is empty. |
| does the exclusion flatter the spread it is judged on? | **no**. Clause on vs off, same run, same sample: match 0.98889 / 0.98886, wrong crop 0.23033 / 0.23282, wrong map 0.0 / 0.0, margin +0.41235 / +0.41017. One frame of 65, and every number moves by under 0.0025. |

## The worst thing it found

**Nothing that is a misalignment.** A deeper sweep (`--turns 60`, 164 scored
map frames across the three games) found **zero** map frames with a margin at
or below +0.05 — not one turn where a neighbouring crop fits as well as the
declared one. That is the strongest statement this instrument can make, and
it is the statement that would have been false if any atlas were off by a
constant.

The lowest-scoring real map frames, and what each one actually is:

| score | margin | where | what it is |
|---|---|---|---|
| 0.7015 | +0.1884 | firered-us turn 11, `4:3` Oak's Lab, tile (9, 5) | the Squirtle-in-a-window cutscene box over the middle of the screen, plus Oak, Green and the textbox. The bookshelves behind it line up pixel for pixel. |
| 0.7054 | +0.4499 | emerald-us turn 211, `0:17` Route 102, tile (8, 2) | animated tall grass and an NPC |
| 0.8705 | +0.4438 | emerald-us turn 76, `2:2` Oldale Pokecenter | Nurse Joy, two NPCs, and a YES/NO prompt box that sits ABOVE `textbox_top` and so is not masked |
| 0.5090 | **−0.3432** | emerald-us turn 49, `0:9` LittlerootTown, tile (14, 8) | **the one that looked like a real defect and was not.** The "LITTLEROOT TOWN" area-name banner across the top-left, over a camera still mid-scroll from the warp. Now excluded as a map-entry frame and listed by name — see amendment 6. |
| 0.9299 | +0.2139 | crystal-us turn 18, `24:5` Elm's Lab | Professor Elm and his aide |

Sheet to look at: `artifacts/game-map-render/verify/firered-us__8-runs.png`
(the 0.7015 turn is on it), and `emerald-us__3-runs.png`, `crystal-us__2-runs.png`.

Every one of these is frame content the artwork is not supposed to contain —
sprites, an open prompt, animated tiles — and in every one the margin is large
and positive, which is the instrument saying "this crop is unambiguously the
right one, the frame simply has things in it". The known limit that shows up
here is the prompt box: `textbox_top` masks the bottom three tile rows, and a
YES/NO box sits above them.

## Known limits

- **The prompt box.** A YES/NO box is drawn above `textbox_top` and is not
  masked, so any turn with one open loses a few percent.
- **A four-colour palette gives the controls more luck.** Crystal's wrong-crop
  control is 0.43 against FireRed's 0.10 for this reason, so Crystal's spread
  is the thinnest of the three and its margin the one to watch.
- **The colour lookup is still a fitted thing.** It is now guarded, offered to
  every arm equally, and reported in the `via` column — but a map that needs
  it (Viridian Forest) is being scored through a transform, and `no fit` is
  the number to read if that matters.
- **The JSON is written beside the sheet, under the sheet's own name.** It
  used to go to a fixed `<game>.json` while the sheet's name carried the runs,
  so two invocations left a current PNG next to a JSON from a different set of
  runs, with nothing in either saying so. Both now share one run-derived stem
  and the JSON carries `generated`, `runs` and the sheet it belongs to.
- **`--turns` is a sample.** 14 turns spread over a run is a smoke test, not a
  proof; the 60-turn sweep above is what the "no negative margins" claim
  rests on.
