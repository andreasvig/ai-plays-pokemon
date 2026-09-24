# v2 experiments — a task suite instead of one long run

> Started 2026-09-18. Design + scratch area for PokeBench v2. Not gitignored (`local/` is,
> this is not), so anything here shows up in `git status`.

## What v2 changes

v1 is **one long task**: every official run starts in the FireRed bedroom
(`configs/saves/pokebench-v1`) and walks a linear ladder — 7, 12 or 20 rungs — until it
misses a deadline. Three consequences:

- **Early failure hides everything else.** A model that can't menu never gets asked
  whether it can battle. 9 of 16 board entries scored 0–43%, which tells us almost
  nothing about what they *can* do.
- **Long and expensive.** 35m–1h25m and up to $9.37 per data point, for one number.
- **One game.** Emerald is registered and bootable but casual-only: benchmark capability
  is derived from a ladder declaring the same `game:`, and no Emerald ladder exists.

v2 is **10–30 short, independent tasks** across multiple games. Each starts from its own
purpose-built savestate, drops the agent into one situation, and scores one skill in a
small turn budget. Failure is local — it doesn't truncate the suite.

## Decisions (2026-09-18)

| Question | Decision |
|---|---|
| Suite shape | **Tasks are the unit and independently runnable**, plus a suite object that constitutes a whole evaluation once every task has a result |
| PKHax tier | Either; **must be driven from the CLI** |
| Games | Red, Crystal, FireRed, Emerald, other Gen 3, Platinum, HGSS, BW, B2W2 — see the blocker below |

## Game coverage — verified, and one hard blocker

The emulator is **mGBA 0.10.5** (`/opt/homebrew/bin/mgba`), launched and driven over a
Lua TCP socket. mGBA emulates **GB, GBC and GBA** — and nothing else.

| Game | System | Runs on the current harness? | Needs |
|---|---|---|---|
| Red | GB | ✅ | ROM dump; own referee address map (Gen 1 layout) |
| Crystal | GBC | ✅ | ROM dump; own referee address map (Gen 2 layout) |
| FireRed | GBA | ✅ **today** | — (fully mapped, `src/referee/`) |
| Emerald | GBA | ✅ boots today | first ladder; SaveBlock offsets differ from FRLG |
| Ruby/Sapphire/LeafGreen | GBA | ✅ | ROM dumps; offsets are near-FireRed |
| **Platinum, HGSS, BW, B2W2** | **NDS** | ❌ **mGBA cannot run these** | a second emulator backend |

> **SUPERSEDED 2026-09-18 — there is no fork.** A patched SkyEmu build runs GB, GBC, GBA
> *and* NDS behind one HTTP API, verified on Platinum, SoulSilver and Crystal, including
> `/write_byte` (so PKHax needs no emulator patching) and touch coordinates. See
> [`emulator-research.md`](emulator-research.md). The paragraphs below are kept as the
> reasoning that led to the experiment; the conclusion they reach is no longer the plan.

**The NDS half of the list is a real architectural fork, not a config entry.** It needs a
second backend behind the same interface the Lua bridge exposes today
(`CAP` / `PRESS` / `SEQ` / `SAVE` / `LOAD` / `READMEM`). Candidates:

- **melonDS** — most accurate; no built-in Lua scripting, so the bridge would be a
  patched build or the libretro core driven over RetroArch's network command interface.
- **DeSmuME** — has Lua scripting, closest in shape to what exists, but macOS builds
  ship it inconsistently and accuracy is weaker.
- **RetroArch + melonDS core** — no emulator patching; network command interface plus
  the memory-map API. Most operationally realistic on this machine.

Three things also change for NDS beyond the backend: **two screens**, **a touchscreen**,
and **a per-game memory map** for the referee. Two of the three turned out cheap:
`/screen` returns both screens stacked in one 256×384 PNG, so the vision pipeline keeps
its single-frame assumption at a new size; and the stylus is one new action carrying
(x, y), now that `/input` accepts `touch_x`/`touch_y`. **The per-game memory map is the
one that stays expensive** — it is the real per-game cost for every game past FireRed.

→ **Building the mGBA tier first (Red, Crystal, FireRed, Emerald, Gen 3) and treating
NDS as its own phase.** That's 5+ games and comfortably 10–30 tasks; nothing in the task
model is GBA-specific, so NDS slots in behind a backend rather than a redesign.

**Still the right build order** — but for a different reason now. It is not that NDS
can't run; it is that every game past FireRed needs its own referee address map, and
that work is identical whichever backend serves the frames.

ROMs: **all six ingested and SHA-1 verified** 2026-09-18 — Blue (not Red), Crystal,
Platinum, SoulSilver, Black, Black 2, alongside the FireRed and Emerald already in
`../roms/`. See [`roms/MANIFEST.md`](roms/MANIFEST.md).

## What the harness already gives us

| Need | Status |
|---|---|
| Multiple ROMs | `configs/roms.yaml` — registry; adding a game is a YAML edit |
| Per-task start savestate | `configs/starts.yaml` — keyed `(rom, label)`, dir of 3 files |
| Per-task success condition | Referee gates — `map` / `flag` / `var` / `party` detectors |
| Per-task goal text | `benchmark:` block overrides `task.goal` |
| Per-task turn budget | `deadline_turn` per gate |
| Manifest | `configs/benchmarks.yaml` lists ladder files |

## The three real gaps

1. **A benchmark can't declare its own start state.** `RunExecutor._resolve_start`
   ignores `--start` for official runs by design — a benchmark always begins at
   `executor.CANONICAL_SAVE`, because two scores from two openings aren't comparable.
   Right *within* a task, wrong *across* tasks. v2 needs `benchmark.start:` in the
   ladder file, still frozen per task.
2. **There is no suite object.** `benchmarks.yaml` is a display-ordered list and the
   leaderboard ranks one benchmark at a time. v2 needs suite = ordered task set,
   evaluation = one pass over it, score = per-task results + an aggregate that only
   counts as complete when every task has a result.
3. **Nothing can author a savestate except playing the game.** 20 tasks need 20
   savestates, and "right before the Elite Four with a full box" is not 10 minutes of
   play. This is the load-bearing gap — see below.

## PKHax — authoring the savestates

**Verified today, so the plan rests on facts:**

- `roms/*.sav` is **131072 bytes of 0xFF — a completely blank battery save**. Every
  sector footer fails the `0x08012025` signature check. The harness has never written
  one; it lives entirely in savestates. There is no existing `.sav` to edit.
- `configs/saves/*/emulator.state` are **PNG-wrapped mGBA savestates** (all three begin
  `89 50 4E 47`). Offline patching means decoding PNG chunks, not seeking an offset.
- The Lua bridge has **`READMEM:<addr>:<len>` but no write command**
  (`lua/socketserver-1.lua:188`). mGBA's Lua API exposes `emu:write8/16/32`.
- **The FireRed address map is already verified in-repo** — `src/referee/referee.py:59-80`
  and `src/referee/battles.py:52-79`: `gSaveBlock1Ptr` @ `0x03005008`, `gSaveBlock2Ptr` @
  `0x0300500C`, player x/y @ SB1 +0x0000/+0x0002, map_group/map_num @ +0x0004/+0x0005,
  **story-flag bitfield @ +0x0EE0 (0x120 bytes)**, vars @ +0x1000, party count @
  `0x02024029`. `local/pret-cache/` holds the decomp data naming the flags and maps.
- No `dotnet`, no `mono`, no PKHeX on this machine — the C# route needs a toolchain first.
- mGBA also accepts `-c/--cheats FILE` (Action Replay / GameShark), a useful lever for
  Gen 1/2 before those address maps exist.

### Tier A — live RAM injection (building this)

> **2026-09-18:** on the SkyEmu backend this needs no bridge work at all — `/write_byte`
> is already there and was verified round-tripping against Platinum's ARM9 RAM. The
> `WRITEMEM:` addition below applies to the *mGBA* bridge, which is still what serves
> FireRed today. Whichever backend, the authoring module above it is the same code.

Add `WRITEMEM:` to the Lua bridge, then a Python authoring module that writes
party/box/flag/position structs into EWRAM of a running emulator and captures a
savestate through the existing `SAVE:` path — the same provenance every other savepoint
has. Fits the repo: addresses already mapped, socket already there, and it composes with
`AppSupervisor(...).start()` → `load_state` → `save_state`.

To build:
- gen-3 `BoxPokemon` writer — 100 bytes: PID, OT id, nickname, the 4-substructure
  shuffle keyed on `PID % 24`, XOR encryption by `PID ^ OTID`, checksum. ~200 lines,
  fully specified by pret.
- flag/var setter over the +0x0EE0 bitfield (badges, story gates, map access).
- a readable roster format in the task file, not a binary blob.

### Tier B — PKHeX proper

Install .NET, drive `PKHeX.Core` from a thin C# console app over JSON. Buys legality
checking and Showdown import; costs a toolchain and a second language, and still needs
Tier A's boot-and-snapshot step (PKHeX writes `.sav`, the harness loads savestates).
Kept as a possible *producer* feeding Tier A, not a replacement for it.

### CLI surface (the stated constraint)

```
pokemon savestate new   <id> --rom firered --from <start|savepoint>
pokemon savestate set   <id> --flag <name|0x..>=1 --var <id>=<n> --at <map>:<x>,<y>
pokemon savestate party <id> --from teams/e4-box.txt          # readable roster file
pokemon savestate box   <id> --box 1 --from teams/e4-box.txt
pokemon savestate show  <id>                                   # decode + screenshot
pokemon savestate commit <id>                                  # → configs/saves/<id>/
```

Every mutation lands in a live emulator and is re-snapshotted, so the artifact is always
a real savestate the executor can resume unchanged.

### The Elite Four example, concretely

1. Set the 8 badge flags + the story flags opening Victory Road / Indigo Plateau.
2. Write map_group/map_num/x/y to the Indigo Plateau entrance.
3. Fill a PC box with a written roster — species, level, moves, EVs/IVs, held items.
4. Snapshot through `SAVE:` into `configs/saves/firered-e4/`.
5. **Verify by looking at the frame, not the wiring** — load it, screenshot, read it.
   (The same discipline the `girl` start was built under.)

Task then becomes *"pick a team from the box and beat Lorelei"* — a battle-only test no
v1 run ever reaches.

## Open questions

Tracked in chat, not here.
