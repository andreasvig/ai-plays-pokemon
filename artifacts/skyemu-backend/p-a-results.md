# P-A: the address finder works on FireRed

> Source: conversation 2026-09-19, on the `skyemu-backend` branch.
> Tool: `v2-experiments/find_addresses.py`. Commits `a69058e`, `236b6b5`.
> Status: **P-A passes.** It is the gate in `cross-game-plan.md` §4, and it is open.

## 0. The result

All four values of the shallow-ladder contract are found on FireRed **without the
finder ever reading the answer key** — `FIRERED_TRUTH` is touched only by
`--stage verify`, after the search is over.

| value | what the finder returns | the known answer | inside? |
|---|---|---|---|
| player x | **4** distinct addresses | `gSaveBlock1 + 0x0000` | yes |
| player y | **3** distinct addresses | `gSaveBlock1 + 0x0002` | yes |
| map id | **27** in-block runs; the answer is the 9th line | `+0x0004` / `+0x0005` | yes |
| block pointer | chosen from 9 spellings | `*0x03005008 + 0` | yes, chosen |
| party count | **81** addresses | `0x02024029` | yes |

The x/y/map run takes **16 seconds** end to end, ten probes included. The party
run takes 41 seconds because it loads twelve save states.

The map line reads, verbatim from the tool:

```
*0x03005008+0x0003   x3  here=['.', 4, 1] -> ['.', 4, 0] / ['.', 3, 0]
```

— the map group holding at 4 while the map number goes 1 → 0 downstairs, and the
group dropping to 3 outdoors. That is FireRed's `(map_group, map_num)` pair,
rediscovered.

## 1. What the method actually is

Each probe is independent: `/load` the same state, walk a scripted few presses,
snapshot all 288 KB of EWRAM and IWRAM. No route accumulates, so a wall that
blocks one direction cannot displace the probes after it. `/load` is exact
enough for this — `reloaded[k]` is byte-identical to `live[k+1]` everywhere, and
the one frame of slippage is the RNG, not a coordinate.

Presses come in **threes** because on many games the first press of a direction
turns the character and only the second moves it. Three presses works either
way, and every condition is an inequality rather than an exact delta for the
same reason.

## 2. Three things this got wrong first

Each of these was found by a control, not by reading the code again.

**The verdict could not fail.** An early revision returned **28,813** candidates
for the map id and printed `P-A PASSES`, because a list that large contains the
right answer by construction. The fix is a width cap in `verify()`: inclusion is
necessary, and a candidate list wider than 64 is not an answer however right it
is. That is the whole reason this document can be believed.

**The block-pointer chooser preferred the wrong block.** FireRed's SaveBlock1 and
SaveBlock2 move together, so `gSaveBlock2Ptr + 0xFA4` reaches the player's x
exactly as `gSaveBlock1Ptr + 0` does — and SaveBlock2 scored **higher** on
content agreement (0.982 against 0.865) *precisely because its contents did not
change*, which is the opposite of what identifies the block the coordinates live
in. Agreement is now a floor; the ranking is proximity to the anchor.

**Both of my claims about which condition does the work were wrong.** The file
first said the null probes — the ones expected to change nothing — were "the
whole discriminating power". `--control` ablates each condition in turn:

|  | all | without magnitude | without reverses | without nulls |
|---|---|---|---|---|
| player x | 4 | 24 | 4 | 5 |
| player y | 3 | 20 | 5 | 3 |

It is the **magnitude bound** — the delta is between one and four, the size of a
step in tiles. The nulls are worth one candidate on x and none on y; the
reversal is worth none on x and two on y. Both stay, because they cost one
comparison and because they, not the magnitude bound, are what stays true on a
game that moves two tiles per press. But the comment that named them was a
guess, and the table replaced it.

## 3. The DMA shuffle, seen rather than assumed

`cross-game-plan.md` §1 predicted that a raw address would not be stable. It is
not, and the finder measured it on the way past: `gSaveBlock1` sits at

    0x0202554c  in the bedroom
    0x02025564  downstairs
    0x02025570  back upstairs
    0x02025574  outdoors

So the raw scan for anything inside the block is meaningless across a
transition — an early revision "found" `map_group` at a raw address that held
the byte `4` for unrelated reasons. The map scan is therefore judged on the
**pointer spelling**, and the tool derives the pointer itself: of nine words
whose value sits within 0x4000 below the x candidate, two move with the block,
and the one whose base sits closest below it is `*0x03005008`.

**This is the pointer+offset form the per-game contract needs, produced as
output rather than assumed as input.**

## 4. Two ranking ideas that made the map answer readable

The filters leave two kinds of survivor and they are not equally likely to be an
id. A map id is a short isolated field. The rest is map *content* — object-event
templates, per-map script state — and content is bulk, and bulk is contiguous.

- **Group into contiguous runs, shortest first.** This moved the answer from
  "somewhere in 116 addresses" to the 9th of 27 lines.
- **Demote a run repeated at a constant stride.** Ten identical 4-byte runs
  0x14 apart survive every filter; no id is ever the 7th element of something.

Both are rankings, not filters: nothing is dropped, so a game that buries its map
id mid-struct ranks low but is still listed.

One bug worth naming because it looked like a weak method and was not: runs were
first computed across raw and pointer-relative addresses **together**. Both are
absolute EWRAM addresses, so they interleaved, and the answer's 3-byte run was
merged into a 70-byte one. Runs are per-spelling now.

## 5. The party count is the weak one, and here is why

81 candidates against x's 4. The narrowing, measured:

| candidates | condition added |
|---|---|
| 935 | +1 across the event, and unmoved by walking in both states |
| 322 | counts are **unsigned** — `0xFF → 0x00` is "+1" as s8 and is a wrap |
| 81 | a **negative control**: state pairs the party did *not* cross, across which the value must not move either |

It is weaker for a reason that is about the evidence, not the method: there is
only one event to observe. **No saved state in the archive has a 1 → 2
transition**, and that single control would likely do more than everything above.

There is a cross-run agreement condition in the code — two runs that both have
one Pokémon must read the same number — and it is **untested**. It changes
nothing here, and not because it is weak: the three v2 runs it was given are the
same model from the same start state and took the *same path*, reading map 4:3 at
(9,5) at turn 10 in all three. Three copies of one trajectory are one
observation. The comment says so at the line it guards.

## 6. What this does not yet show

1. **It has never run on a game we cannot check.** FireRed is the rehearsal with
   the answers in the back of the book. Emerald is next, and it is the last
   candidate that still has a decomp to check against.
2. **The probe tile has to be scouted.** The save state's own tile was not free
   in all four directions — walking up from the bedroom start is blocked, so the
   y search returned nothing until a one-press `--prelude` moved the probe to a
   tile with a clear square around it. On an unknown game that is an operator
   step, and the tool names it when the list comes back empty.
3. **The routes are hand-found.** The bedroom→downstairs warp and the walk out to
   Pallet Town were located by screenshot. The finder cannot invent a route out
   of a room it has never seen, and it says so rather than guessing.
4. **`TRACE_SPEC` is still not covered.** It is the fifth per-game value
   (`cross-game-plan.md` §0) and this tool does not produce it.

## 7. How to run it

```
PYTHONPATH=.:v2-experiments/harness ./venv/bin/python \
  v2-experiments/find_addresses.py --stage verify --port 8177 --prelude L \
  --out-route R,R,R,R,R,U,U,U,U,L --back-route R,R,R --walk-route D,D,L,L \
  --alt-route R,R,R,R,R,U,U,U,U,L,D,D,D,D,D,D,L,L,L,L,L,L,D,D,D
```

`--control` adds the ablation table. `--stage party` takes `--state` /
`--state-after` pairs and `--null-before` / `--null-after` for the negative
control.
