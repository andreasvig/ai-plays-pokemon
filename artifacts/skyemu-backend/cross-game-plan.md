# A benchmark that runs on every game — the shallow-ladder route

> Source: conversation 2026-09-19 (Andreas + Marvin), on the `skyemu-backend` branch.
> Andreas's scope, verbatim: *"it should at some point, so getting simple checkpoint
> such as get pokmon or get to frist city is hour current goal."*
> Status: **plan only.** Nothing here is built.

## 0. Why the shallow scope changes the method, not just the size

FireRed's ladder reads the whole of SaveBlock1 — a `0x120`-byte flags bitfield, a
256-entry vars array, an encryption key out of SaveBlock2, and battle telemetry at
four more addresses (`referee.py:58-90`). Porting *that* to a new game is a
reverse-engineering project per game, and it is why "cross-game" has looked
expensive.

Two gates do not need any of it:

| gate | what it reads | addresses |
|---|---|---|
| **Got a Pokémon** | party count ≥ 1 | **1** |
| **Reached the first city** | map id | **2** (or 1 pointer + 2) |

Add the player's x/y — free, adjacent to the map id, and needed for the walkgraph
and the input census — and the **whole per-game contract is four values**.

That is small enough to find **empirically**, by controlled experiment on a running
machine, instead of from a disassembly that may not exist for the game. Which
matters most for Black/Black 2, where community data is thinnest.

## 1. The method: differential memory search

We have already done this once, successfully, and it is the template. `gRngValue`
was located by searching all 8,192 IWRAM words for one obeying the Gen-3 LCG across
consecutive frames — **exactly one hit** (`v2-experiments/load_divergence.py`). The
same shape works for all four values, and each comes with its own built-in oracle.

Every probe is: reach a state, `/save`, snapshot memory, change exactly one thing,
snapshot again, diff. The emulator being frozen between steps is what makes this
clean — nothing drifts underneath the comparison.

| value | the experiment | the control that proves it |
|---|---|---|
| **player x** | press `right` once | the candidate changed by exactly ±1; pressing `left` returns it; pressing `up` does **not** move it |
| **player y** | press `down` once | mirror of the above |
| **map id** | walk through one map transition | returning to the first map restores the original value, and a *different* transition gives a *different* value |
| **party count** | receive the starter | goes 0→1; catching or receiving a second goes 1→2 (the 0→1 test alone also matches any "has saved" flag) |

Each control is the point. A single diff over a map transition returns hundreds of
changed bytes; only a handful survive "and it goes back when you walk back". The
party-count control is the one most likely to be skipped and most likely to be
needed — plenty of bytes flip once when you get your first Pokémon.

**Expected failure mode, and it is already proven to happen:** Gen 3 DMA-shuffles
its save blocks, so a raw address is not stable — FireRed's read `0x0202552C`
pre-game and `0x02025594` in the bedroom on one boot (plan §3.4). So the per-game
contract must be able to say *"dereference this pointer, then add this offset"* as
well as *"read this address"*. FireRed already needs both. Assume every Gen 3+ game
does until shown otherwise.

## 2. What has to become data

Four things are Python or absent today.

| # | Thing | Today | Needs to become |
|---|---|---|---|
| 1 | **The memory map** | module constants in `src/referee/referee.py:58-90`, commented "FireRed BPRE-US v1.0 == v1.1" | a per-game block: direct addresses *and* pointer+offset forms. The referee's backend contract (`read_memory`) does not change. |
| 2 | **Input timing** | `emulator.button_hold_frames` etc. in `configs/config-*.yaml` — per **run**, not per **game** | a per-game home, most naturally `configs/roms.yaml`, which already keys everything off `game:` |
| 3 | **ROM identification** | `game_code`, the 4-byte GBA header at `0x080000AC` (`roms.yaml`, `src/app/roms.py`) | per-console: GB/GBC have a title+checksum in the cartridge header, NDS has its own game code at a different offset |
| 4 | **The ladder schema** | `configs/checkpoints-*.yaml`, signatures of type `flag` / `var` / `map` / `party` | `map` and `party` are portable as-is. `flag` and `var` are Gen-3 structures and a shallow ladder does not need them — so **the shallow ladder is expressible in today's schema**, which is the single luckiest fact in this document. |

## 3. Input timing is measurable, not guessable

Andreas: *"teh duration of button presses, too cirectly move aroudn and turn."*
A short D-pad press turns the character in place; a longer one moves a tile; the
threshold differs by game. Today one number serves every game, so on a game that
wants more frames, half the movement inputs silently become turns-in-place.

This does not need research — it needs a sweep, and the oracle is already in the
census (`turns_to_face` vs `overworld_steps`):

> From a known tile, for hold = 1, 2, 3, … frames: press `right`, read x, reload.
> The smallest hold where x changes is the game's move threshold. Repeat facing a
> wall to get the *turn* threshold, which is the number that must stay **below** it.

Runs in a couple of minutes per game, gives a defensible number per game, and the
result is a table we can commit rather than a constant someone tuned by eye.

**Do this before any scored cross-game run.** A wrong hold time does not fail
loudly — it produces a run that plays badly, which looks like a bad model.

## 4. Order of work

**P-A. The address finder.** One script, `v2-experiments/find_addresses.py`, that
takes a running game and a scripted route and reports candidates for x, y, map id
and party count, each with its control applied. Build it against **FireRed, where we
already know all four answers** — if it does not rediscover FireRed's known
addresses, it is not ready to point at a game we cannot check.

*This is the gate for everything else, and it is the one piece that could fail.*

**P-B. Start states.** One per game, via `v2-experiments/make_start_state.py`, which
already replays FireRed cold boot → bedroom in 8,598 frames. Each game needs its own
scripted opening. Known traps: no `.sav` beside the ROM, START on an empty name field
is the "accept default" verb, and Platinum is still in an unskippable cinematic at
3,000 frames (PRESS START at ~7,200).

**P-C. Timing calibration.** §3's sweep, per game. Cheap, and blocks a fair run.

**P-D. The per-game contract.** Move FireRed's constants into the new data form
**with no behaviour change** — the existing referee tests are the oracle — then add
one game beside it.

**P-E. Two gates, one game.** A shallow ladder for the cheapest second game, latched
in a real run. First city, then starter.

**P-F. The rest of the games.** Repeat P-B through P-E. Tedious, not hard, *if* P-A
worked.

## 5. Which second game

Deliberately not decided here — an audit of the cross-game assumptions is running and
should answer it with file:line evidence rather than my impression. The candidates and
what each would cost:

- **Emerald (GBA)** — already in `configs/roms.yaml` and on disk. Same console, same
  generation, the same SaveBlock+DMA shape as FireRed, and the registry says a ROM
  becomes benchmark-capable the moment a ladder declares its game. Almost certainly
  the cheapest, and the one that tests the *plumbing* rather than the emulator.
- **Crystal (GBC)** — furthest from FireRed's structures, but Gen 2 has no DMA shuffle,
  so its addresses are static. Tests whether the contract's *simple* form suffices.
- **Platinum / SoulSilver (NDS)** — the ones the branch exists for, and the only ones
  that exercise the stylus and the stacked frame. Also the least-documented.

A defensible order is Emerald to prove the machinery, then Crystal to prove the
contract generalises across consoles, then NDS.

## 6. What is genuinely unknown

Separated from what is merely typing, because these are the only things that can
sink the plan.

1. **Whether the empirical finder works on a game nobody has mapped.** FireRed is a
   rehearsal with the answers in the back of the book. Black 2 is the real test.
2. **Whether "first city" is even a clean signal in every game.** FireRed's map ids are
   a `(group, num)` pair. Gen 2 and Gen 4 index maps differently, and a "city" may not
   be one map.
3. **Whether party count is one byte everywhere.** Assumed, not checked, outside Gen 3.
4. **Whether the models can play the NDS games at all well enough to reach a gate.**
   The 10-turn experiments showed menus and a stylus working; nothing has played to a
   checkpoint. A ladder nobody reaches measures nothing.
5. **NDS cold boot carries ~10 bytes of host-dependent state** at six fixed addresses
   (plan §6.2). Harmless so far; unexplained, and it sits in the same memory the
   finder will be searching.

## 7. Explicitly out of scope

Andreas, 2026-09-19: *"okay jsut full ignore OCR for now. we will reimplemnt it
smarter soon."* No text channel on any game. v2 is vision-only until that is
redesigned, and nothing here should be built to accommodate the old OCR service.
