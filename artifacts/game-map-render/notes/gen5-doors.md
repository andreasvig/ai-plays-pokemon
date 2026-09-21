# Gen 5 doors — making Black and Black 2's interiors clickable

2026-09-21, branch `polish/doors`. Touched: `scripts/render_gen5maps.py`,
`tests/test_gen5maps.py`, `src/dashboard/web/public/maps/black-us/index.json`,
`src/dashboard/web/public/maps/black2-us/index.json`. **No PNG changed** — the
renderer was re-run in full and every one of the 28 images came out
byte-identical to the artwork fixed earlier the same day.

## The gap

Every Gen 5 interior was rendered, shipped as a PNG and listed in the atlas,
and none of them could be opened. The atlas said why, in its own words:

> `"doors": "none: Gen 5 event data is not decoded, so an interior renders but
> the viewer has no door to open it from"`

Marker counts before: `black` 0, `black2` 0. Gen 4 reads its doors out of the
decomp's `warp_events`; there is no Gen 5 decomp.

## Where the doors came from instead

Not from the event archive — nothing here decodes it, and nothing needed to.
`src/app/observed.py` already mints a **warp** whenever two consecutive samples
of a run are not adjacent, and
`artifacts/game-map-render/observed/<game>-observed.json` ships them under
`warps`. Black has 30, Black 2 has 10. The same evidence SoulSilver's atlas has
used since it was written (`render_dsmaps.observed_doors`), so the atlas now
carries the same token SoulSilver does: `"doors": "observed-transitions"`.

`render_gen5maps.observed_doors` is the whole derivation. Three rules:

1. **Both ends on the same map is not a door.** A staircase, a warp pad, a
   ledge the field code resolves by teleporting — Black 2's
   `427|36|715 -> 427|36|718` is one of these. 15 of Black's 30 warps and 5 of
   Black 2's 10 are same-map.
2. **Two outdoor maps is not a door either.** SoulSilver's rule is "different
   map matrix"; every Gen 5 outdoor field map is on `matrix_000`, so the same
   sentence is spelled "at least one end is an interior". The seam between two
   outdoor maps is already an `open` span. This drops 4 more of Black's warps
   (`317 <-> 397` both ways, `389 -> 317`) and 0 of Black 2's.
3. **Direction comes from the atlas's own `indoor` flag**, never from the size
   of the coordinate. A door on an outdoor map is written in GLOBAL tiles, a
   door on an interior in LOCAL ones, because that is the frame the run's own
   steps are in and `mapatlas.drawWindow` subtracts the map's `origin` from
   both alike.

### The magnitude heuristic does not work, and is not used

The brief suggested a 20x gap between interior coordinates (max 19) and global
ones (min 393). It holds on y — the lowest global y in any DS atlas is 384 —
and **fails on x**: Black 2's world map `427` has `origin [32, 704]`, so its
global x runs 32–63 and the lowest one a run stood on is 36, while its
interiors' rectangles reach x = 17. Nineteen tiles of daylight on that axis,
not a 20x gap. What is used as the
check instead is rectangle containment — every emitted tile must lie inside
`[origin, origin + size)` of the map it is written ON, which `build_atlas`
raises on and `test_a_door_is_written_in_the_frame_of_the_map_it_sits_on`
asserts against the shipped file.

### Half a door, and which tile it goes on

A warp is directed, and the direction that matters most was never walked:
**every run starts inside the player's house**, so Black's `390` and Black 2's
`428` were left and never entered. Dropping one-sided doors would leave the
room every Black 2 run begins in unreachable — which is exactly the reported
gap — so the missing direction is read off the tile the player LANDED on coming
the other way, and each door carries `"via": "walked"` or `"via": "landed"`.

When a pair has both candidates, the **cartridge's collision grid** breaks the
tie, not the observed/derived distinction. An outdoor door tile is impassable —
you never walk onto a door, the field code warps you off it, which is the
exception `check_walkable` already has to make — so a blocked candidate wins.
Measured over both games' cross-map warps:

| candidate (distinct tiles, both games) | blocked in the collision grid |
|---|---|
| outdoor-side **landing** tile | 7 of 7 |
| outdoor-side **walked** tile | 4 of 6 |
| interior-side tile, either end | 1 of 16 (Black's `390(2,2)`, a staircase) |

The two walked-but-passable ones are a sample taken a step short of the door.
Only one of them has a landing tile to fall back on (Black's `397 -> 398`,
where the walked tile `(795,658)` is one diagonal step off the real door at
`(796,657)`); the collision tie-break moves it, and that is the single door
this rule changes.

### Floors

A cartridge ships map ids and no names, so the name-prefix rule that groups
Platinum's floors has nothing to work on. Floors are grouped by the SHAPE of
the doors instead — an interior whose door leads to another interior is a floor
of that building — and the building is named after its lowest floor. Black's
`391` opens onto `390` and nothing else, so `Map390` is one house with two
floors and one marker. Nothing else grouped.

**Names are deliberately not touched.** Interiors still say `"name": null` and
`"building": "Map428"`; a separate pass is extracting the real ones.

## The doors, all of them

`walked` = a run left this tile for that map. `landed` = nobody walked it in
that direction; the tile is where a run arrived coming back.

### black-us — 6 markers, was 0

| on | tile (global) | opens | via | arrival tile inside |
|---|---|---|---|---|
| `319:0` | 761, 649 | `320:0` gate house | landed | — |
| `389:0` | 776, 757 | `392:0` | walked | 6, 8 |
| `389:0` | 777, 740 | `396:0` | walked | 4, 11 |
| `389:0` | 782, 748 | `390:0` player's house (+`391:0` upstairs) | landed | — |
| `397:0` | 768, 649 | `320:0` gate house | walked | 15, 6 |
| `397:0` | 796, 657 | `398:0` | landed | — |

Exits: `320:0` (1,6)→`319:0` walked, (15,6)→`397:0` landed · `390:0` (2,2)→`391:0`
landed, (5,10) and (6,10)→`389:0` walked · `391:0` (8,2)→`390:0` walked ·
`392:0` (6,10)→`389:0` walked · `396:0` (3,11)→`389:0` walked · `398:0`
(8,19)→`397:0` walked.

### black2-us — 3 markers, was 0

| on | tile (global) | opens | via | arrival tile inside |
|---|---|---|---|---|
| `427:0` | 43, 750 | `429:0` | walked | 6, 11 |
| `427:0` | 47, 761 | `428:0` player's house | landed | — |
| `427:0` | 49, 741 | `435:0` | walked | 7, 19 |

Exits: `428:0` (5,10) and (6,10)→`427:0` walked · `429:0` (6,11)→`427:0` walked ·
`435:0` (7,19)→`427:0` landed.

**What was rejected**, and **two-sided vs one-sided** counted per unordered
pair of maps:

| game | warps | rejected: same-map | rejected: outdoor↔outdoor | kept | map pairs | two-sided | one-sided |
|---|---|---|---|---|---|---|---|
| black | 30 | 15 | 4 | 11 | 7 | 3 | 4 |
| black2 | 10 | 5 | 0 | 5 | 3 | 1 | 2 |

Two-sided: `389 <-> 392`, `389 <-> 396`, `397 <-> 398`, `427 <-> 429`.
One-sided: `319 <-> 320`, `320 <-> 397`, `389 <-> 390`, `390 <-> 391`,
`427 <-> 428`, `427 <-> 435` — every one of them filled in from the other
direction, so no interior was lost to a missing half. Emitted: black 6 doors +
9 exits, black2 3 doors + 4 exits (more entries than pairs because Black's
`390` and Black 2's `428` were each left by two different mat tiles).

### The one door that is visibly off

`427(49,741) -> 435:0` is the only outdoor door whose tile the collision grid
does NOT call a wall, and it has no landing tile to fall back on because
nobody ever walked back out of `435`. The nearest blocked row is two tiles
north, so the marker sits about two tiles into the street south of the
building instead of on its door. It is correct evidence — that is where the
run was standing — and it is pinned by
`test_an_outdoor_door_tile_is_a_tile_the_collision_grid_calls_a_wall`
(`passable_doors == 1`), so the day a run walks out of `435` the assertion
fails and the marker snaps onto the door.

## Would this add doors SoulSilver is missing? No.

Run against `soulsilver-us-observed.json`, the same derivation produces the
three markers the atlas already ships (`60:0` → `61:0`, `63:0`, `66:0`) and
**not one more**. SoulSilver's "3 markers for 6 maps" is complete, not a gap:
the six maps are two outdoor and four interiors, and `64:0` is a second floor
of `63:0`'s building, reachable through the one marker as the viewer intends.

The only thing it would add is an in-popup exit, `63:0 (3,3) -> 64:0` — the
staircase back up, derived from the tile a run landed on descending. It adds
no marker, changes nothing on the world map, and SoulSilver was left alone.
(For the record, the same run against `platinum-us` would ADD four markers
Platinum's decomp-sourced `warp_events` does not have and would MOVE three
interior exits by one tile. That is a question about Platinum's warp list, not
about this change, and Platinum was not touched.)

## How it was verified in the viewer, not in the JSON

The control center on 3420 serves the other worktree, so this branch's
`src/dashboard/web` was built with vite into a scratch dir and served on
:8792 behind a small stub that answers `/api/runs/<id>` and
`/api/runs/<id>/route` from `src/app/route.load_route` over the two Black runs
in `local/runs`. Playwright then drove the page the way
`scripts/shoot_walkmap.mjs` does — open `/history/<run>`, expand "Where it
walked", click **fit**, count `button.marker`:

```
black2 run: 3 button.marker  [Map 429, Map 428, Map 435]  all .entered
black  run: 6 button.marker  [Map 392, Map 396, Map 390, Map 320, Map 398, Map 320]  all .entered
```

Every one of the nine was clicked: all nine put the viewer inside the building
(`button.backchip` appears) with the run's route drawn on the interior and the
exit chips sitting on the door mat. Black 2's `428` — the bedroom every Black 2
run starts in and the whole reason the `landed` fallback exists — draws the
route from the upstairs room out through both mat tiles.

## Tests, each with its mutation

All in `tests/test_gen5maps.py`, section 8. The seven atlas mutations below
were applied to `black2-us/index.json` and each was caught by the test named:

| mutation | caught by |
|---|---|
| move one door tile by 1 | `test_the_shipped_doors_are_exactly_the_ones_we_derived` |
| add a `427:0 -> 427:0` self-door | `test_a_warp_inside_one_map_is_never_a_door` |
| write an interior exit in global tiles | `test_a_door_is_written_in_the_frame_of_the_map_it_sits_on` |
| delete the door into `428:0` | `test_every_interior_is_reachable_from_a_door_on_another_map` |
| relabel a `landed` door `walked` | `test_a_direction_nobody_walked_says_so` |
| restore the old "no door to open it from" note | `test_the_atlas_says_its_doors_came_from_our_runs_and_not_from_events` |
| merge `428:0` and `429:0` into one building | `test_two_rooms_are_one_building_only_when_a_door_joins_them` |

Two of the tests are themselves mutation controls for the tests beside them:
`test_dropping_the_same_map_rule_would_put_self_doors_on_the_page` proves the
corpus actually contains same-map warps (otherwise "no door leads to itself" is
vacuous), and `test_the_frame_check_rejects_a_door_written_on_the_wrong_side`
proves a frame-crossing door written on the far map would fall out of bounds.
The last is scoped to doors that cross the two frames on purpose: two interiors
are both local and their rectangles overlap, so no bounds check can catch a
stairway written on the wrong floor, and claiming otherwise would be a control
that passes for the wrong reason.
