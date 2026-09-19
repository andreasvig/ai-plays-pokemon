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

**Correction (audit, 2026-09-19): four values is not the whole per-game contract —
there is a fifth.** `TRACE_SPEC` (`src/referee/trace.py:56-62`) is a module constant
built from FireRed's SaveBlock1 pointer, its `gMain` in-battle byte and
`gameStats[7]`, and it is wired into every run unconditionally at
`src/cli/runner.py:626,713` and `src/cli/launch.py:80,141` — **there is no config
path and no injection point.** Ship it unchanged to another game and the per-input
trace goes blind (`trace.py:211`): `overworld_steps` is `None` on every turn, and
the input census — the thing the whole comparison rests on — measures nothing. It
needs the same per-game treatment as the four addresses.

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
| 4 | **The ladder schema** | `configs/checkpoints-*.yaml`, signatures of type `flag` / `var` / `map` / `party` | `party` is portable as-is. `map` is portable through Gen 2–3 only — see §2.1. `flag` and `var` are Gen-3 structures and a shallow ladder does not need them. |

### 2.1 The one schema change, and where it bites

Revision 1 of this document claimed the shallow ladder needs no schema change at
all. **That is true for Gen 2 and Gen 3 and false for Gen 4/5**, and it was the
happiest claim here, so it is the one worth getting right.

`_REQUIRED_SIGNATURE_FIELDS["map"]` hard-requires **both** `map_group` and
`map_num` (`src/referee/checkpoints.py:29`), and `_satisfied` compares both
(`referee.py:695-699`). That pair is a Gen-2/Gen-3 shape. A Gen 4 map is a single
id. So Platinum's "reached the first city" gate cannot be *written* in today's
schema — `map_id` has to become an accepted alternative to the pair, in the
validator and in the detector.

Small — two places, a handful of lines — but it is a code change on the path to
the NDS games, not zero, and it should be counted as such.

*(The Gen-2-pair / Gen-4-single-id claim is from general knowledge, not from this
repo. P-A's first map-id probe on a real Gen 4 dump confirms or kills it in five
minutes, and it costs nothing to check then.)*

### 2.2 A wrong walk graph is worse than a missing one

`referee.py:98` loads `data/firered-walkgraph.json` unconditionally. `graph_path`
is a constructor argument **nobody passes** — the one production site
(`src/agent/turn.py:1021-1032`) omits it, and `src/app/route.py:64-67` has no such
parameter at all. Nodes are keyed `(map_group, map_num, x, y)` with **no game
field and no check at load**.

So an Emerald run would not degrade — it would *succeed wrongly*. Emerald's map
`(3,0)` hits FireRed's `"3:0"` = Pallet Town, and the referee reports `on_graph:
true` with confident nonsense distances. A **missing** graph is safe and designed
for (`src/app/projection.py:457-482` leaves `progress` at `None` and the board
falls back to the gate count); a wrong one is silent corruption of the headline
score.

**Gate the graph load on the game before the first cross-game run, not after.**
Five lines.

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

**The sweep needs two numbers per game, not one.** Revision 1 measured only the
hold threshold. `frames_between_inputs` is the second, and it is independently
calibrated to FireRed: `lua/socketserver-1.lua:33` says *"400ms gap — walk
animation is ~16 frames (267ms)"*, and `:253-256` says the gap exists **so the walk
animation finishes before the trace samples the tile**. Set it shorter than the
game's animation and the trace reads the *old* tile: `overworld_steps`
under-counts and the press is filed under `blocked_by_actor` or `walls_hit`. So:
hold threshold, turn threshold, and animation length.

**And the metric itself changes meaning, which is the part that does not announce
itself.** `turns_to_face` is a **free** bucket (`trace.py:34-36,185-198`) whose
justification is a stated FireRed fact — *"In FireRed that only turns the player,
and turning to face a sign or an NPC is a necessary input, so it is never
charged."* Two ways that goes wrong on another game, neither loud:

- Game needs **more** than 12 frames to move → every first press becomes a turn,
  the run burns double the inputs, and the census records them as **free**. The
  score drops and the instrument reports nothing wrong.
- Game has **no** turn-in-place → a press that moved nothing while facing
  elsewhere is booked as a free turn when it was actually a wall. `walls_hit` —
  the only *charged* bucket (`trace.py:242`) — silently under-counts, and movement
  efficiency is flattered.

One piece of good news: facing is inferred in software in exactly one place
(`trace.py:152,204`). The backends' own `facing` attribute is vestigial — its only
reader (`turn.py:1818`) sets it to `None` and nothing reads the value — so
changing the facing model means changing one function.

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

## 5. Which second game — Emerald. Decided.

The audit answered this with file:line evidence, and the margin is larger than I
expected: **Emerald is the only candidate that needs no new emulator backend, no
new start-state authoring, no console work, and no schema change.**

Already done, not merely plausible: registered (`configs/roms.yaml:35-48`), on
disk, `game_code: BPEE` verified against the actual file by
`tests/test_rom_registry.py:70-82`, a committed start save
(`configs/saves/emerald-truck`), and `roms.yaml:47-48` states in the repo's own
words that authoring a ladder is the *whole* of what is missing.

What it still needs, exactly — and nothing here is a surprise:

| Need | Size |
|---|---|
| A per-game block for the 4+1 values. `GSAVEBLOCK1_PTR` differs from FireRed's `0x03005008`; `PLAYER_PARTY_COUNT` differs; the SB1 field offsets probably match Gen 3 but must be checked | P-A applied once. Emerald is the only candidate with a decomp (`pret/pokeemerald`) to check the finder's answer against |
| A `game: emerald-us` ladder YAML, 2–4 gates, listed in `configs/benchmarks.yaml:12-15` | Half an hour of typing, **no code change** |
| A timing sweep (§3) | Minutes, once the sweep exists |
| The walk-graph gate (§2.2) | **Skip the graph itself for the first run** — the degradation is designed for. Just don't let FireRed's answer it |
| A `rom_sha1` check that actually runs | ~10 lines — see §8 |

**Crystal is the more informative second game and still comes third-of-three-firsts.**
Emerald proves the *plumbing*; Crystal proves the *abstraction* — no DMA shuffle,
static addresses, a second console, and the first game where `GAME_CODE_ADDR`
(`src/app/roms.py:38`) returns garbage, because GB/GBC carts have no game code at
all (`v2-experiments/roms/MANIFEST.md` lists `—` for both, a title string and a
header checksum instead). Then NDS.

One thing the audit found that removes a whole worry: **"which console" is already
solved.** The SkyEmu backend identifies it by measuring the frame —
`src/emulator/backends/frame.py:40` `GEOMETRY = {(240,160):"GBA", (160,144):"GB",
(256,384):"NDS"}`, used at `skyemu.py:347`, populated on every run at `:188`. It is
"which *cartridge*" that has no per-console answer yet.

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

## 8. Two defects found on the way, worth fixing regardless

Neither is cross-game work; both were turned up by the audit and both are the kind
that stay invisible until they have already cost something.

1. **`configs/roms.yaml:15-16` documents a `rom_sha1` check that does not exist.**
   It says the hash is "Checked against the ladder's `rom_sha1` before a benchmark
   arms". `Rom.sha1` is declared (`roms.yaml:32,41`) and the ladder's `rom_sha1` is
   loaded and shape-validated (`checkpoints.py:135,437-439`) — and compared to
   nothing. The only ROM `hashlib.sha1` in the tree is in
   `tests/test_rom_registry.py:80`. With one game that is rhetorical; with two
   scorable games, "which dump produced this score" stops being.

2. **The walk-graph fetcher ignores its own pin.** `scripts/build_walkgraph.py:89`
   builds its URL with a literal `/master/`, while `scripts/render_gamemaps.py:112`
   correctly interpolates the pinned `{ref}`. So the committed walk graph and the
   committed map atlas can be built from different revisions of `pret/pokefirered`
   with nothing noticing.
