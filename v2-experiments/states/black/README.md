# Pokemon Black's measurement fixtures

Six states on three maps, made 2026-09-20 to find Black's player x, y and map id.
Written by `v2-experiments/make_black_probe_states.py`, which drives the emulator
from a state, presses a route, screenshots, and optionally saves. Every route
below starts from the file in the row above it, so the chain is replayable; the
screenshots next to each `.state` are what the route actually produced.

`configs/saves/skyemu/black/emulator.state` (the registered start) is **not**
usable for the axis search. Its screenshot is the reason: the player stands in
the middle of a row of three, **Bianca on his left and Cheren on his right**, with
the gift-box table below. Left and right are both bodies, so x cannot move and
`find_addresses.py --stage xy` returns "player x: NO CANDIDATES" — which is what
the first run got. The fix is `--prelude U,U`: two tiles up onto the empty top of
the rug, where left, right, up and down are all free. That prelude produced 10 x
candidates and 10 y candidates on the first try.

## The chain

| # | file | where the player is | route from the row above |
|---|---|---|---|
| 0 | `configs/saves/skyemu/black/emulator.state` | bedroom, between Bianca and Cheren, tile (5,6) | — |
| 1 | `bedroom.state` | same bedroom, after BOTH rival battles; room is trashed, nobody else in it, tile (4,6) | `D` then 220 × `A` — `D,A×5` opens the gift box and takes Tepig (the default cursor), `A×35` reaches the Bianca battle, `A×60` wins it, `A×120` wins Cheren's and clears the aftermath |
| 2 | `bedroom2.state` | same bedroom, tile (7,6) | `R,R,R` |
| 3 | `probe.state` | living room downstairs, open floor, tile (5,5) | from `bedroom.state`: `U,U,R,R,R,R,U,U,R` (the stair tile is reached from (8,2) pressing R) then `A×70` for Mom's speech, then `D,D,D,R,R` |
| 4 | `probe2.state` | same living room, tile (3,8) | from the same post-speech point: `D,D,D,D,D,D` |
| 5 | `town.state` | outside in Nuvema Town, night, tile (782,749) | from `probe.state`: `D,D,D,D,D,D` out the front door, then `A,A,A,A` — without those four the player is still inside a script and cannot walk |
| 6 | `town2.state` | Nuvema Town, tile (779,749) | `L,L,L` |

`probe.state` is the one `find_addresses.py --stage xy` should use for a second
map: (5,5) is free in all four directions and needs no prelude.

Checkpoints along the chain (`starter`, `battle1`, `postbianca`, `downstairs`,
`town_free`, `reentry`) are in `local/addr-hunt/ck/` — scratch, not fixtures.

## What these produced

    player actor      0x0224F90C          (also spelled *0x02258284, *0x022582C0,
                                           *0x022584F0, *0x02330464 — all four hold
                                           0x0224F90C in every state below)
      +0x04  s32     0x0224F910   x, 16.16 fixed point, = (tile_x + 0.5) << 16
      +0x08  s32     0x0224F914   height; 0.0 on every flat tile measured
      +0x0C  s32     0x0224F918   y, 16.16 fixed point, = (tile_y + 0.5) << 16
      +0x18  u32     0x0224F924   facing, in turns: 0=up 0x4000=left
                                  0x8000=down 0xC000=right

    player x  =  u16 at 0x0224F912   (the high half of the fx32 — the tile index)
    player y  =  u16 at 0x0224F91A

Read back in every state, against the tile the screenshot shows:

| state | x | y | facing |
|---|---|---|---|
| canonical start | 5 | 6 | down |
| `bedroom.state` | 4 | 6 | right |
| `bedroom2.state` | 7 | 6 | right |
| `probe.state` | 5 | 5 | right |
| `probe2.state` | 3 | 8 | down |
| `town.state` | 782 | 749 | down |
| `town2.state` | 779 | 749 | left |

**Raw addresses survive a map load in gen 5.** The same two addresses read the
right tile after four transitions — bedroom → living room → Nuvema Town → living
room → bedroom — with no pointer chase. That was the open question for gen 5 and
the answer is yes, *for the player actor*. It is emphatically **not** true of
map-scoped memory: of the 99,766 bytes that are constant across a three-tile walk
and differ between the living room and the town, **zero** hold their living-room
value when the living room is entered again, and some duplicate copies of the
coordinates move outright (a second x copy sits at 0x0225241A in the bedroom and
0x0225251A downstairs).

## The map id is NOT found

`find_map_id.py` does not converge on Black, and both of its priors are false here:

* *"the map id sits next to the coordinates"* — on gen 2/3/4 the coordinates are
  in a save block that also holds the id. In Black they are in the overworld
  **actor**, and the ranked-by-proximity list is the actor's own fields, sprite
  ids and heap bookkeeping. Its top hit, 0x0224F90C = 391/390/389, is the actor
  struct's first word and counts down one per map nesting level.
* *"the id is constant while you walk inside a map"* — gen 5 **streams** map data
  as the camera moves, so the bytes that behave map-wise are not walk-constant.
  297,710 bytes take one value on both visits to the living room and another on
  both visits to the town; **none** of them is constant across a three-tile walk.

A three-state design is also confounded with *story progression*, because the
states are necessarily made in sequence: 0x0214678F reads 9, 23, 27, 28 in
canonical-bedroom, post-battle-bedroom, living room and town — monotone, never
returning. Re-visiting each map is what breaks that, and re-visits are also how
we learned that entering a house from outdoors leaves the outdoor zone resident,
which is what the 99,766 bytes above actually are.

With four revisit samples as oracles (`local/addr-hunt/reentry*.npy`,
`canonical-bedroom.npy`) 1,651 candidates remain, all of them allocator
bookkeeping in 0x0224FFE0–0x02251000. That is a shortlist, not an answer.
