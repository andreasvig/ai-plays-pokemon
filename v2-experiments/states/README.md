# Start states, one per game

Each game's "bedroom": the player standing in a room, with control, everything
still ahead of them. FireRed's upstairs room in Pallet Town is what v1 has always
started from; these are the equivalent for the rest.

Written by `v2-experiments/make_start_states.py` (and, for Emerald,
`make_emerald_state.py` — see below). **Regenerate rather than edit.** The
scripts are the source of truth and they verify their own output.

| game | console | where | cost |
|---|---|---|---|
| crystal | GBC | the bedroom upstairs in New Bark Town | 9,156 frames, 7 s |
| emerald | GBA | the moving van, and the house in Littleroot | 17,172 + 4,944 frames, 34 s |
| platinum | NDS | the bedroom upstairs in Twinleaf Town | 18,936 frames, 42 s |

FireRed's lives at `configs/saves/skyemu/firered-pokebench-v2/` because it is the
one a benchmark already runs from; `v2-experiments/make_start_state.py` writes it.

## Why these are replayed and not converted

Everything in `configs/saves/` is an **mGBA** savestate and SkyEmu answers
`/load -> failed` for all of them. There is no converter, so the opening is
played again from a fixed input sequence measured in frames. SkyEmu headless
starts paused and advances only via `/step`, so a slow machine and a fast one
replay the identical game.

## The check

There is no shared oracle here: the referee's addresses are known for FireRed and
Emerald and for nothing else, so "read the map id" does not generalise. What does
is **control** — from the saved state, all four d-pad directions change the
screen. A cutscene answers none. An open dialogue box answers none. A menu
answers some. Only a player standing in a room answers all four.

It is a weak check by itself, which is why every state also writes a
`preview.png` that a human looks at once. Emerald's van fails it on purpose —
five tiles wide with boxes on three sides — which is why `make_emerald_state.py`
also produces the Littleroot house state, and why that is the one the address
finder uses.

## What every opening had in common

- **START on an empty name field is the "accept the default" verb.** FireRed KAY,
  Emerald TERRY, Platinum TODD. Crystal is the exception: Gen 2 offers a preset
  *menu* rather than a keyboard, so the sequence picks CHRIS from it.
- **A professor's "would you like to know more?" menu traps an A-mash forever**,
  because the default option shows a page and returns to the menu. Answered by
  moving to the last option (`d2,w60,a1`). Two downs on a two-item YES/NO prompt
  wrap back to where they started, so the same macro is a no-op on the prompts it
  is not meant for.
- **Presses right after a warp are swallowed** by the transition — budget two.
