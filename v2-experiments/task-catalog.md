# v2 task catalog — draft

> 2026-09-18. First pass at the 10–30 tasks. Nothing here is built yet; this is the
> shape argument. Turn budgets are guesses to be calibrated against one strong model.
>
> **Updated the same day, after the backend experiment** ([`emulator-research.md`](emulator-research.md)):
> a patched SkyEmu runs GB/GBC/GBA **and NDS** behind one HTTP API, and `/write_byte`
> works. Two things move as a result — the cost of the `pkhax` column, and the fact that
> Gen 4/5 tasks are now buildable. See the design notes at the bottom; the table itself
> is unchanged, because the five games in it are still the right place to start.

Each task is **one situation, one skill, a small budget**. A task file carries its own
`benchmark:` block (id, name, goal), its own `start:` savestate, and a 1–3 rung ladder.

**Authoring column**: `play` = reachable by playing from an existing start in minutes ·
`pkhax` = needs an authored savestate · `pkhax+` = needs authored progress flags *and* a
written roster.

## A. Menuing & interface (the floor)

| # | Game | Task | Budget | Detector | Authoring |
|---|---|---|---:|---|---|
| A1 | FireRed | Heal the party at a Pokémon Center | 15 | party HP restored | play |
| A2 | FireRed | Buy 5 Poké Balls with exactly enough money | 20 | item count + money var | pkhax |
| A3 | FireRed | Teach a TM to the right party member | 20 | move present on mon | pkhax |
| A4 | FireRed | Reorder the party so a named mon leads | 10 | party slot 0 species | pkhax |
| A5 | Emerald | Withdraw a specific Pokémon from the PC | 15 | party count + species | pkhax |

## B. Battle tactics (never reached in v1)

| # | Game | Task | Budget | Detector | Authoring |
|---|---|---|---:|---|---|
| B1 | FireRed | **Pick a team from a full box, beat Lorelei** | 60 | trainer flag | pkhax+ |
| B2 | FireRed | Beat Brock with a type-disadvantaged starter | 40 | `brock_defeated` | pkhax |
| B3 | FireRed | Win a battle where switching is required to survive | 30 | battle outcome | pkhax+ |
| B4 | FireRed | Win using a status move first (para/sleep) | 30 | outcome + turn count | pkhax+ |
| B5 | Emerald | Beat Roxanne | 40 | trainer flag | pkhax |
| B6 | Crystal | Beat Falkner | 40 | badge flag | pkhax |
| B7 | FireRed | Catch a wild Pokémon below 20% HP without fainting it | 25 | party count | pkhax |
| B8 | Red | Win a battle from a 1-mon party at low HP | 25 | battle outcome | pkhax |

## C. Navigation

| # | Game | Task | Budget | Detector | Authoring |
|---|---|---|---:|---|---|
| C1 | FireRed | Pallet → Viridian (the **v1 benchmark, demoted to one task**) | 60 | map | — (exists) |
| C2 | FireRed | Cross Viridian Forest exit-to-exit | 50 | map | pkhax |
| C3 | FireRed | Navigate Mt. Moon to the far exit | 60 | map | pkhax |
| C4 | Red | Rock Tunnel without Flash | 70 | map | pkhax |
| C5 | Crystal | Union Cave to the south exit | 50 | map | pkhax |
| C6 | Emerald | Petalburg Woods exit-to-exit | 40 | map | pkhax |
| C7 | FireRed | Find and reach a named building interior in a city | 30 | map | play |

## D. Puzzles

| # | Game | Task | Budget | Detector | Authoring |
|---|---|---|---:|---|---|
| D1 | FireRed | Team Rocket HQ teleport-tile maze → Giovanni's room | 70 | map | pkhax |
| D2 | FireRed | Seafoam Islands: push boulders to slow the current | 60 | var / map | pkhax |
| D3 | Crystal | Ice Path sliding puzzle | 60 | map | pkhax |
| D4 | Emerald | Granite Cave Strength boulder puzzle | 50 | map | pkhax |
| D5 | FireRed | Pokémon Tower: get past the possessed channelers | 50 | map | pkhax+ |

## E. Long-horizon / composite

| # | Game | Task | Budget | Detector | Authoring |
|---|---|---|---:|---|---|
| E1 | FireRed | Evolve a Pokémon one level short of its threshold | 40 | species change | pkhax |
| E2 | FireRed | Use Cut to clear a blocking tree and pass | 25 | map | pkhax |
| E3 | Emerald | Surf to an offshore location | 40 | map | pkhax |
| E4 | Red | Deliver Oak's Parcel | 50 | flag | play |

**26 tasks, 5 games.** Roughly: 5 menuing, 8 battle, 7 navigation, 5 puzzle, 4
composite — deliberately battle-and-puzzle heavy, since that's what v1 never measured.

## Design notes

- **C1 is the whole v1 benchmark as a single task.** That's the point: v1's entire
  result becomes one row of ~26, and the board stops being a referendum on whether a
  model can leave a bedroom.
- **Budgets are per-task deadlines on the final rung**, same mechanism as today, so no
  new termination machinery is needed.
- **Most tasks need PKHax.** 21 of 26. That confirms the savestate authoring tool is the
  critical path, not the suite runner — it should be built first.
- **PKHax got cheaper, not less necessary** (2026-09-18). On SkyEmu the memory-write
  primitive already exists and is verified — `/write_byte` round-trips against a running
  machine — so Tier A needs no emulator patching and no bridge work. What is left is the
  part that was always the real work: the gen-3 `BoxPokemon` writer (PID-keyed
  substructure shuffle, XOR encryption, checksum), the flag/var setter, and a readable
  roster format. 21 of 26 tasks still wait on that, and none of it is emulator code.
- **Gen 4/5 tasks are now buildable, and are deliberately still absent.** Platinum,
  SoulSilver and Black 2 boot and render cleanly. The blocker is no longer the emulator:
  it is that `src/referee/` is FireRed-only, and each generation needs its own address
  map before a task on it can be *scored*. Adding DS rows here before that map exists
  would be writing tasks nothing can grade. They belong in the catalog the day a Gen 4
  map does.
- **The DS touchscreen is one new action, not a new channel.** `/input` now takes
  `touch_x`/`touch_y`, so a stylus tap is `(x, y)` alongside the buttons. A Gen 4/5 task
  can assume the agent can tap a named on-screen target; what it cannot yet assume is
  that the agent knows *where* the target is, which is a vision problem, not a harness
  one.
- **Type coverage is deliberate**: every task has a detector the referee can already
  express (`map` / `flag` / `var` / `party`), except the HP- and outcome-based ones in
  A1/B3/B4/B7/B8, which need new detector types over data `src/referee/battles.py`
  already decodes (`gBattleMons`, `gBattleOutcome`). That's a small extension, not a
  new subsystem.
- **Red and Crystal need ROM dumps and their own address maps.** The dumps now exist —
  Crystal is verified and boots, and **Blue stands in for Red** (same engine and
  generation, different encounter tables; see [`roms/MANIFEST.md`](roms/MANIFEST.md)).
  Any task above that depends on Red *specifically* should be re-read as Blue. The
  address maps still do not exist, so the Gen 1/2 tasks remain the last phase.
