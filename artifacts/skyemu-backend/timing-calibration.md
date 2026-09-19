# P-C: how long a press has to be, measured

> Source: conversation 2026-09-19, on the `skyemu-backend` branch.
> Tool: `v2-experiments/calibrate_timing.py`.
> Andreas's question, verbatim: *"teh duration of button presses, too cirectly
> move aroudn and turn."*

## 0. FireRed's numbers

Direction `R`, from the benchmark start state, every reading taken from
`*0x03005008 + 0` rather than from the screen. Each point is an independent
`/load`, so nothing accumulates.

| what | measured | in the config today | headroom |
|---|---|---|---|
| **move hold** — smallest hold that walks a tile from a standing start *not* facing that way | **9 frames** | `button_hold_frames: 12` | 3 frames |
| **repeat hold** — smallest hold that walks when *already* facing | **1 frame** | — | — |
| **gap** — smallest `frames_between_inputs` at which two consecutive presses both land | **7 frames** | `frames_between_inputs: 24` | 17 frames |

All three curves are monotone — every hold above the threshold works and every
one below fails — which is the thing a sweep has to show before a single number
is worth quoting. The sweep scans upward rather than bisecting for exactly that
reason: a bisection turns a non-monotone curve into a confident wrong number
without saying so.

## 1. The turn/walk threshold only applies to the first press

**move hold is 9; repeat hold is 1.** Once the character faces the direction, a
one-frame press steps a tile. The whole turn-versus-walk question — the thing
`lua/socketserver-1.lua:32` calls "ensures walk, not just turn" — is about the
*first* press in a new direction and nothing else.

That matters for a game we have not measured. The failure mode is not "the hold
is too short"; it is "the hold is too short **for the first press of each new
direction**", which is exactly the press most likely to be a deliberate turn
toward an NPC or a sign. So a wrong number here does not slow a run down
uniformly — it corrupts the one bucket the census treats as free
(`src/referee/trace.py:34-36`).

## 2. A tension with the A3 result, and it is not resolved

A2 established that the drift from mGBA to SkyEmu is input timing, not OCR: 185
inputs against 240, because mGBA's `press_button_list` sleeps
`total_frames/60 + 0.5s` and SkyEmu steps exactly, so roughly 30 frames of
padding per sequence vanished.

But the numbers above say FireRed's overworld needs **9 + 7 = 16 frames** and the
config already spends **12 + 24 = 36**. Twice the measured minimum. If 36 frames
were enough, removing mGBA's extra 30 should have cost nothing — and it cost 55
inputs, with `blocked_by_actor` going 3 → 16.

So one of these is true and this sweep cannot tell which:

1. **The overworld is not where the time goes.** A and B have their own numbers
   (`ab_hold_frames: 50`, `ab_gap_frames: 30`) and dialogue, menus and battle
   text are not measured here at all.
2. **NPCs are.** `blocked_by_actor` quadrupling points at a walking NPC occupying
   the tile — a timing question about *the game's* actors, not the player's, and
   one this probe (an empty bedroom) is blind to by construction.

**The sweep as designed measures the wrong thing for the defect it was built
next to.** It is still the right number for `button_hold_frames`, and it is still
required before any cross-game run. It is not an explanation of the A3 gap, and
this document does not claim it is.

## 3. What to measure next, in order

1. **The A-press threshold through a text box.** The same sweep shape, with a
   dialogue-state address instead of a coordinate. The notice board upstairs in
   the FireRed start state opens one in three presses, so the fixture is free.
2. **The NPC-blocked case.** A tile an NPC walks across, and the smallest gap at
   which a press into it still lands.
3. **Then Emerald**, which is where the numbers are actually expected to differ.

## 4. Running it

```
PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
  v2-experiments/calibrate_timing.py --port 8191 --dir R \
  --x-ptr 0x03005008 --x-offset 0
```

`--x-addr` instead of `--x-ptr/--x-offset` for a game whose block does not move.
Both spellings come out of `find_addresses.py`. 38 seconds for all three sweeps.
