# Map names for the DS cartridges

`data/ds-map-names.json`, made by `scripts/extract_ds_mapnames.py`, decoded by
`scripts/ds3d/dstext.py`. Written 2026-09-21.

The three DS games with no decomp — SoulSilver, Black, Black 2 — rendered every
map as `Map427` because their atlases carry `name: null`. All three are now
named out of the cartridge. Twenty of twenty maps our runs entered have a name;
six of them were checked against a location plaque in a run recording, and the
whole method was re-run on Platinum, where the decomp already knows the answer.

## What the file is

```json
{ "soulsilver-us": { "33": "Route 29", "60": "New Bark Town", ... }, ... }
```

Keys are the plain integer, `"427"` — the spelling the observed graphs use.
The atlas spells the same map `"427:0"`, so whoever wires this in adds the
`:0`. Sizes: soulsilver-us 540, black-us 424, black2-us 612 named maps.

**Platinum is deliberately not in the file.** It is the control, and its names
here are *location labels* (`Twinleaf Town`), not the decomp's per-map constant
names (`TwinleafTown_RivalHouse_1F`) the Platinum atlas already ships. Merging
this over Platinum would lose information. Run the control with
`--control`; print the table with `--print platinum-us`.

## Interiors share their town's name, and that is the cartridge's answer

A map header does not hold a name. It holds an **index into a table of place
names** — the table the game draws in the plaque — and an interior points at
the town it is in. So SoulSilver 60, 61, 63, 64 and 66 are all `New Bark Town`,
and Black 2's 427, 428, 429 and 435 are all `Aspertia City`. There is no string
anywhere in those ROMs for "Elm's Lab" or "the player's bedroom": pret invented
`MAP_HEADER_TWINLEAF_TOWN_RIVAL_HOUSE_2F`, the cartridge did not. If the walk
map needs interiors told apart, the distinction has to be added on top (the
Gen 5 zone header does carry a parent-zone field and an indoor/outdoor split;
Gen 4's carries a map type). Nothing here invents one.

## Where each game keeps it

| game | place-name strings | name index | how the table was located |
|---|---|---|---|
| soulsilver-us | `a/0/2/7` file 279, 235 strings | `u8` at +0x11 of the 24-byte arm9 map header | searched: every map id in the region matrix's header plane must read matrix 0 — 75 equations, one surviving address |
| platinum-us | `msgdata/pl_msg.narc` file 433, 126 strings | `u8` at +0x12 of the same 24-byte record | same search, 66 equations, one address |
| black-us | `a/0/0/2` file 89, 117 strings | `u8` at +0x1A of the 48-byte zone header in `a/0/1/2` | `a/0/1/2` is the table `render_gen5maps.py` already reads |
| black2-us | `a/0/0/2` file 109, 154 strings | same +0x1A | same |

Two things are worth knowing. Gen 4's two games **pack the header struct
differently** — SoulSilver's matrix id sits at +3 (unaligned), Platinum's at
+2 — so the extractor counts the name field from the matrix field the search
actually locates, not from the record start: SoulSilver +14, Platinum +16.
And the text files are found **by content**, never by index: `find_text_file`
takes the one subfile in the archive containing a named set of strings and
refuses if none or two do.

Gen 5's +0x1A was found by sweeping all 48 offsets at both widths against two
known zones. It is the only offset that satisfies Black and Black 2 together,
and `tests/test_ds_mapnames.py` re-refutes both neighbours.

## The Platinum control

`scripts/extract_ds_mapnames.py --control`: **PASS**.

588 of the 593 decomp-labelled Platinum map headers read the byte-identical
name out of the cartridge. The 5 that differ are all the same slot — pret spells
the symbol `LocationNames_Text_TeamGalacticEternaBuilding`, the cartridge prints
`T.G. Eterna Bldg` — and the control checks that separately as a bijection:
no symbol maps to two strings and no string to two symbols, so the two tables
agree about *which map gets which name* in all 593 cases. Independently, four
Platinum plaques read off a run recording (below) agree with the same table.

The control also proves the join it depends on: that the map id our runs report
is the 0-based line of `generated/map_headers.txt`. A wrong join could not
agree 593 times at a fixed 24-byte stride.

## The oracle: plaques, measured not assumed

A shifted index gives every map a real place name from the right game, so
"the strings decoded cleanly" proves nothing. Ground truth came from the
location plaque in the run recordings (`local/runs/<run>/recording.mp4` — the
per-turn screenshots miss it, the plaque is gone by the time the screen has
settled). Each frame is captioned with its TURN, and that turn's
`turn_input_trace` samples say which map the player was on.

| game | map | name | run | turn | frame |
|---|---|---|---|---|---|
| soulsilver-us | 60 | New Bark Town | 2026-09-20_22-12-03 | 7 | t=85.0s |
| black-us | 317 | Route 1 | 2026-09-20_23-09-22 | 94 | t=1097.5s |
| platinum-us | 418 | Sandgem Town | 2026-09-20_00-17-45 | 6 | t=78.0s |
| platinum-us | 342 | Route 201 | 2026-09-20_00-17-45 | 24 | t=216.5s |
| platinum-us | 343 | Route 202 | 2026-09-20_00-17-45 | 82 | t=801.5s |
| platinum-us | 411 | Twinleaf Town | 2026-09-20_00-17-45 | 107 | t=1290.0s |

**Six maps, not twenty, and the reason is a property of the game, not a gap in
the search.** The plaque is drawn only when the *location name* changes. Our
runs mostly walked between maps that share one name — every Black 2 map they
entered is Aspertia City, and five of six SoulSilver maps are New Bark Town —
so no plaque was ever drawn. A sweep of the whole Black 2 recording found none,
which is the expected result. The runs that did cross a name boundary
(SoulSilver into Route 29, Black into Accumula Town and Route 2) are the older
`config-v2` runs, which have no `recording.mp4`.

The unplaqued anchors: SoulSilver 33 = Route 29 and 60-66 = New Bark Town are
written into `src/referee/contracts.py`'s SoulSilver notes; Black starts in
Nuvema Town and Black 2 in Aspertia City. SoulSilver's Route 29 is corroborated
by the table's own shape — map ids 9..26 read Route 1..18 in order and 30..43
read Route 26..39 in order, and the run of consecutive ids pins the alignment
that the two anchors then fix absolutely.

## `scripts/ds3d/dstext.py` — for whoever comes next

The decoder is a separate module beside `nitrofs.py`/`nitro.py` because map
names are not its only customer. **Platinum's trainer and class names are in
reach**: `msgdata/pl_msg.narc` file 618 decodes today to `Tristan`, `Logan`,
`Natalie`, ... and is pinned in the tests. What the module gives you:

- `gen4_text(buf)` / `gen5_text(buf)` -> `list[str]` for one message file;
- `gen4_codes(buf)` -> the raw code units, if you want the table's numbers;
- `GEN4_CHARMAP` and `gen4_render(codes)`;
- `text_file(buf)` (sniffs the generation) and
  `find_text_file(subfiles, must_contain)`.

What it does **not** give you: any knowledge of which archive holds what. That
is per-cartridge research and it stays in the caller.

Three things a trainer-name job should know before starting.

1. **Gen 4 has a second, packed string form.** A message whose first unit is
   `0xF100` is bit-packed — nine-bit codes inside fifteen-bit containers, low
   bits first, ending at `0x1FF`. The trainer-name file is stored this way and
   is unreadable without it. `gen4_codes` unpacks it transparently.
2. **The charmap is recovered, not complete.** Digits, A-Z, a-z, `é`, the two
   gender symbols and thirteen punctuation marks are pinned, each off a
   sentence that can only be one thing. Anything else renders as `{1234}` —
   a visible gap rather than a plausible wrong letter. If trainer names turn
   up `{...}`, that is a missing table entry, not a broken cipher, and one
   more sentence will pin it.
3. **`0xFFFE` is a variable, not a character**, and it is followed by an id, an
   argument count and that many arguments. `gen4_render` consumes them and
   emits `{VAR:id}`. Colour changes ride the same mechanism (`0xFFFE 0xFF00 1
   1`).

Also worth carrying over: the sprite agent found pokeplatinum's
`res/trainers/data/*.json` a dead end because there is no id-to-file join.
Nothing here leans on the decomp for an id-indexed table — the Platinum control
uses `generated/map_headers.txt`, whose join it then proves 593 times over.

## The twenty maps

| game | map | name |
|---|---|---|
| soulsilver-us | 33 | Route 29 |
| soulsilver-us | 60 | New Bark Town |
| soulsilver-us | 61 | New Bark Town |
| soulsilver-us | 63 | New Bark Town |
| soulsilver-us | 64 | New Bark Town |
| soulsilver-us | 66 | New Bark Town |
| black-us | 317 | Route 1 |
| black-us | 319 | Route 2 |
| black-us | 320 | Accumula Gate |
| black-us | 389 | Nuvema Town |
| black-us | 390 | Nuvema Town |
| black-us | 391 | Nuvema Town |
| black-us | 392 | Nuvema Town |
| black-us | 396 | Nuvema Town |
| black-us | 397 | Accumula Town |
| black-us | 398 | Accumula Town |
| black2-us | 427 | Aspertia City |
| black2-us | 428 | Aspertia City |
| black2-us | 429 | Aspertia City |
| black2-us | 435 | Aspertia City |

Black's ten read as the real route: Nuvema Town and its four interiors, out to
Route 1, into Accumula Town and its interior, through Accumula Gate onto
Route 2 — which is the geography of the opening of Black, and the extractor
was told none of it.

## Extent of each table, and what is not covered

The Gen 4 table is cut where the header record's matrix field stops being a
valid matrix id — the same bound `render_dsmaps.py` already uses, so the two
agree exactly (SoulSilver: 540 headers). Gen 5 reads every zone in `a/0/1/2`
(Black 427, Black 2 615) and drops the rows whose name is the blank
full-width-dash entry.

Not cracked, and not attempted: per-interior names (they do not exist in the
ROM, see above), and Gen 5 map names for anything outside `a/0/1/2`'s zone
range. Nothing was left half-done.
