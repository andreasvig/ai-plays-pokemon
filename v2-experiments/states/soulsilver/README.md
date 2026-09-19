# SoulSilver's measurement fixtures

Four states, hand-driven from the registered casual start
(`configs/saves/skyemu/soulsilver/emulator.state`, the player's bedroom), which
is the fifth. Regenerate by replaying the route below; the screenshots are what
was actually read back after every batch of presses, not a reconstruction.

| file | where the player is standing | what it is for |
|---|---|---|
| `probe.state` | **New Bark Town**, the open dirt plaza between the player's house (NE) and Elm's lab (S), at **x=693 y=400** | `find_addresses.py --stage xy` — three tiles free in all four directions and no door within three tiles, which the bedroom start is not |
| `probe2.state` | **New Bark Town**, six tiles west and three south, between two houses, at **x=687 y=403** | the same-map discriminator: two states that must AGREE |
| `house1f.state` | **the player's house, ground floor**, at the foot of the stairs, **x=3 y=3**, after Mom's greeting | third map |
| `neighbour.state` | **the neighbour's house** in New Bark Town (old man at the table), **x=5 y=6** | fourth map — the one that made the map-id search converge |

The bedroom start (`configs/saves/skyemu/soulsilver/emulator.state`, x=6 y=6) is
the fifth state and the second map.

## How they were driven

Load state → press a few buttons → save state + PNG → **read the PNG** → repeat.
The route, from the registered start:

    L,L,L,U,U,L          bedroom (6,6) -> the stair tile at (2,4) -> house 1F (3,3)
    D, A x12             Mom's "Hi, AA! You're finally awake"
    A x15                the rest of it, back to free control   [house1f.state]
    D x9                 out through the 1F door mat -> New Bark Town (695,397)
    D, A x15             the forced Lyra/Ethan greeting outside the front door
    D,D,D,L,L            -> probe.state   (693,400)
    L,L,L,L,L,L,D,D,D    -> probe2.state  (687,403)
    D,D,R,R,R,U          -> the neighbour's house (3,10); U,U,U,U,R,R -> neighbour.state (5,6)

and the return leg, used only as a control (below):

    from probe.state: R,R,U,U,U,U (back inside) ... U,U,U,L -> bedroom (3,4)

Three things had to be found by experiment and none is guessable:

* **The bedroom stairs are entered by walking LEFT onto (2,4), not UP into
  them.** Pressing UP from below the staircase graphic at x=1 and at x=2 does
  nothing at all. The same is true in reverse: the up-stairs on the ground floor
  is entered by walking LEFT from (3,3).
* **Two cutscenes fire on the way out and both freeze the player.** Mom
  intercepts at the foot of the stairs; Lyra/Ethan runs up the moment the player
  steps outside the front door. A state saved before either measures nothing,
  because every button is swallowed — the first attempt at an outdoor probe
  state produced five identical readings in a row.
* **A state saved right after a warp can be mid-fade.** `wait_for_stable_screen`
  returns happily on a black screen, so the screenshot is black and the
  coordinates are the destination's but nothing else is settled. Walk a tile
  after arriving before saving.

The player is **AA** — whatever name the registered start state already carried;
read off Mom's first line, not assumed.

## What these produced

SoulSilver's position contract, found without a disassembly. One block, raw
addresses, **no save-block pointer** — the addresses are identical in all five
states across four maps, which is the Platinum result replicated:

    0x0227D448  map id            <I
    0x0227D44C  unknown           <I   (0xffffffff on the untouched start, 0/1 after)
    0x0227D450  player x          <I
    0x0227D454  player y          <I
    0x0227D458  elevation / z     <I
    0x0227D45C  PREVIOUS map id   <I
    0x0227D464  previous x        <I
    0x0227D468  previous y        <I

Measured map ids: **New Bark Town 60, player's house 1F 63, player's bedroom
64, neighbour's house 66** — the interiors of one town numbered just above it.

The previous-map/previous-x/previous-y triple is what turns this from a ranked
guess into a reading: in `house1f.state` it says "came from map 64 at (3,4)",
which is exactly the bedroom tile the stairs were entered from, and in
`neighbour.state` it says "came from map 60 at (690,407)", the door tile
outside. Nothing was told to look for that; it fell out of the layout.

`x` and `y` were each found twice — `find_addresses.py --stage xy` from the
bedroom (`local/addr-hunt/soulsilver-xy.json`, 14 x) and from `probe.state`
(`local/addr-hunt/soulsilver-probe-xy.json`, 14 x) — and the intersection over
two different maps is 7 x and 6 y (`local/addr-hunt/soulsilver-xy-intersect.json`),
the same narrowing and the same counts Platinum got. Three of the 7 and five of
the 6 hold the exact tile coordinate in all eleven states measured; the rest are
camera or sub-tile values that merely track the axis.

The map id did **not** come out of `find_map_id.py` on two states: with the
bedroom's (6,6) against New Bark Town's (693,400), every position-derived field
in RAM differs, and 86,193 bytes passed the plausible-id test. It took two more
conditions — a **third and fourth map**, all pairwise different, and a
**revisit** (walk back to a map by another route; a real map id reads the same,
history-dependent leftovers do not) — to get to 16 candidates, of which exactly
one sits inside a coordinate block. Full ladder in
`local/addr-hunt/soulsilver-mapid*.json` and `soulsilver-contract.json`.
