# Black 2's measurement fixtures

Two extra save states on **Aspertia City (outdoors)**, made so the address
search has a SECOND map to intersect against. The canonical start state,
`configs/saves/skyemu/black2/emulator.state`, is inside the player's house —
one map, one tile — and a single map cannot tell a coordinate apart from the
several dozen other bytes that happen to move with it.

| file | where the player is standing |
|---|---|
| `probe.state` / `probe.png` | Outdoors, on the paved plaza south of the player's house: **one tile west and three tiles south of the front-door tile**. The red mailbox is up-left, the little pond is three tiles east, a low fence and treetops one tile south. |
| `probe2.state` / `probe2.png` | Same map, **seven tiles west and one south** of `probe.state`: the last paved tile at the south-west corner of the plaza, treetops immediately south and west, the two-storey building to the north. This is the *discriminator* state — two states on one map must agree, which is what `find_map_id.py --state MAP=… --state MAP=…` uses to kill false positives. |
| `probe3.state` / `probe3.png` | A **third map**: the ground floor of the neighbour's two-storey house at the west end of the plaza (an old woman and a bald man by the table, stairs up, a bed and a red rug on the right). Standing on the room's bottom row, two tiles east of the doormat column, so no probe can step on the exit warp. |

The player is **ACE** (read off Hugh's line "Hi, ACE!", not assumed). It is
night in both states.

## How they were made

Driven button-by-button from the canonical start state with a throwaway script
(`make_emulator` + `load_state` + `press_button_list` + `wait_for_stable_screen`
+ `capture_screenshot` + `save_state`), reading the screenshot after every
batch. The route, in order:

    D,D,D  D,D,D  L,L,L        # out of the green-carpet bedroom, west
    D,D    L,L,L  L,L,L        # into Mom, who starts talking
    A x5, B, D, L,L,L,L        # Mom's conversation…
    A x15, B                   # …which is long…
    A x20                      # …and ends by itself
    L,L,L  L,L,L  D            # west across the living room to the doormat
    R  D  D                    # onto the mat and out the front door
    D,D    L,L,L               # south off the doorstep, then west  == tile T
    R,R                        # -> probe.state
    from T:  D  L,L,L  L,L                            -> probe2.state
    from T:  U,U  L,L,L,L  L,L,L  R  U,U  D  U,U  U,U  R,R   -> probe3.state

Two things this route does that a route-finder would not have guessed:

* **The house is ONE map.** Black 2's Aspertia house is not a bedroom upstairs
  and a living room downstairs — it is a single floor, so nothing inside it
  crosses a map boundary. The only map transition near the start is the front
  door.
* **Walking north from the plaza fires Hugh's scripted scene** ("Hugh: Hey!
  You get a Pokémon yet?") about four tiles north of the doorstep, and a
  wandering NPC greets the player ("Hi, ACE!") a little before that. Both
  probe tiles are deliberately kept *south* of that line, so the four
  three-press probes `find_addresses.py` and `find_map_id.py` fire cannot
  wander into a cutscene.

## Clearance at `probe.state`

Measured, not assumed — each direction run as its own three-press probe from a
fresh `/load`:

    up    2 tiles, then the house's front wall (the door warp is one tile east,
          so U,U,U does NOT leave the map — the tile three south of the door does)
    down  1 tile, then the low fence
    left  3 tiles, clear pavement
    right 3 tiles, then the pond

The tile directly below the front door is unusable for this: `U,U,U` from there
steps onto the door warp and the probe ends up back inside the house.

Regenerate rather than edit: the route above is the source of truth.

## What these produced

`find_addresses.py --stage xy` from `probe.state`, intersected with the same
scan from the canonical house state, leaves 6 x and 11 y addresses
(`local/addr-hunt/black2-probe-xy.json` ∩ `local/addr-hunt/black2-xy.json` =
`local/addr-hunt/black2-intersect-xy.json`), and they pair up +8 apart — the
same spacing Platinum has. The copy whose struct also holds the player's name
in UTF-16 ("ACE", at +0x20) is the player's. The whole contract is one
16-byte block:

    0x0223b444  u32   map id          428 house / 427 Aspertia City / 431 neighbour
    0x0223b448  s32   x               16.16 fixed point, tile = value >> 16
    0x0223b44c  s32   height          0 indoors, 1 on the Aspertia pavement
    0x0223b450  s32   y  (the engine's z)     tile = value >> 16

    state         map      x      y   h
    house         428     14      3   0
    city/probe    427     46    764   1
    city/probe2   427     39    765   1
    nbr/probe3    431      8      9   0

Standing still always leaves 0x8000 in the low half — the player sits at the
centre of the tile, so `>> 16` is the tile and there is no rounding question.
Outdoor Unova coordinates are GLOBAL (y = 764 in a city thirty tiles across);
interior coordinates are local to the room.

**Gen-5 raw addresses survive a map load.** Walked out of the front door and
back in again in ONE session, no reload: 0x0223b448 read (5.5, 10.5) inside,
(47.5, 762.5) one step later outside, and (5.5, 10.5) again on re-entry, and
the map id went 428 -> 427 -> 428. No save-block pointer is needed, same as
Gen 4.

The map id was NOT found by `find_map_id.py`'s ranking. Its prior — the id sits
next to the coordinates — half-holds here: the id is 4 bytes before x, but
`--min-anchor-dist 4` drops exactly that byte, and the rows the ranking does
promote are all the HEIGHT component of the player's and the NPCs' position
vectors. 0x0223b444 was found instead by a strict three-map pass
(`local/addr-hunt/black2-mapid-3way.*`: constant under four walk probes, equal
at two tiles of one map, pairwise distinct across three maps, u16 < 1024) and
then confirmed by the house -> city -> house round trip above.

A bonus from the same pass: **0x0223b4f4 is a step counter** (u32, +1 per tile
walked, reset by a map load). It is the value that looks most like a map id
until you walk.
