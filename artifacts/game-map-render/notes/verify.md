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
Emerald's do not. One of the two renderers is writing the other expansion.

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

---

## What it measured

`./venv/bin/python scripts/verify_map_alignment.py local/runs/*config-v2-<game>__* --turns 14`

Medians over the sampled turns, spread across every v2 run of each game.
Sheets under `artifacts/game-map-render/verify/`.

| game | verdict | turns | match | no fit | wrong crop | wrong map | self | margin | raw8 |
|---|---|---|---|---|---|---|---|---|---|
| firered-us | **ALIGNED** | 13 of 14 | 0.9955 | 0.9929 | 0.1019 | 0.0276 | 1.0000 | +0.4392 | 0.0000 |
| emerald-us | **ALIGNED** | 11 of 14 | 0.9818 | 0.9818 | 0.2191 | 0.0722 | 1.0000 | +0.3738 | 0.0015 |
| crystal-us | **ALIGNED** | 13 of 14 | 0.9900 | 0.9896 | 0.4279 | 0.4589 | 1.0000 | +0.1637 | 0.9896 |
| platinum-us | NO INSTRUMENT | — | — | — | — | — | — | — | — |
| soulsilver-us | NO INSTRUMENT | — | — | — | — | — | — | — | — |
| black-us | NO INSTRUMENT | — | — | — | — | — | — | — | — |
| black2-us | NO INSTRUMENT | — | — | — | — | — | — | — | — |

Every clause of the band cleared for all three 2D games. The saturation guard
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
- **`--turns` is a sample.** 14 turns spread over a run is a smoke test, not a
  proof; the 60-turn sweep above is what the "no negative margins" claim
  rests on.
