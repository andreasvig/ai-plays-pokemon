# Emerald's measurement fixture

Written by `v2-experiments/make_emerald_state.py` from a cold boot in 34 seconds.
Regenerate rather than edit; the script is the source of truth and it verifies
its own output.

`configs/saves/emerald-truck/emulator.state` is an **mGBA** savestate and SkyEmu
answers `/load -> failed`, the same refusal v1's FireRed start gets. So these
are not ported, they are replayed.

The script writes two states. Only one of them is still here:

| file | where it is | what it is for |
|---|---|---|
| `probe.state` | downstairs in the house in Littleroot, after Mom's greeting, no text box open | `find_addresses.py`, which needs a tile free in all four directions — the truck is five tiles wide with boxes on three sides |
| ~~`truck.state`~~ | inside the moving van, before the game has asked anything | **moved 2026-09-19** to `configs/saves/skyemu/emerald/emulator.state`, where it is Emerald's registered casual start |

The player is **TERRY**: the name field is confirmed empty with START and Emerald
substitutes its own default. Read back off Mom's first line, not assumed.

## What these produced

Emerald's whole shallow-ladder contract, found without consulting a
disassembly (`artifacts/skyemu-backend/p-a-results.md` §8):

    gSaveBlock1Ptr   *0x03005d8c        (FireRed's is 0x03005008)
    player x         +0x0000  s16
    player y         +0x0002  s16
    map number       +0x0005  u8
    map group        +0x0004  u8   — INFERRED, see below

The map group is the one value not measured. Both maps reachable from
`probe.state` are group 1 (the house's two floors), so a round trip between them
cannot move the byte and the scan is blind to it by construction. +0x0004 is
where FireRed keeps it and where the layout says it should be; that is a
symmetry argument, not evidence, and it needs a second transition out of the
house to become one.

Getting out needs more scripted play than this: Emerald gates the front door
until the player has been upstairs and set the clock.
