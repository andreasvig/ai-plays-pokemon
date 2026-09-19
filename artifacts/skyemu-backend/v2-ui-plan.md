# v2 UI — a queue that plays every game

> Source: conversation 2026-09-19, after the cross-game work landed. Andreas,
> verbatim: *"imagine when we start a run (lets jsut start with causal, bounded
> by eiteh rturns or budget) we shoudl now be able to choose from all games. i
> also woudl want all spectate interfaces both simpel and advanced to work with
> all games. for now i woudl liek all of tehse to uns to stay qued such taht we
> only have oen run running at a tiem, but tehy shodul be abel to excnahge as
> they wwant such that i coudl sepctate finishe a gen 3 run and teh next run
> would be gen 4 or 5 run. aditinlly i owuld want the same logging for all
> models and teh agnet cli should also be able to start these runs add add tehm
> to teh que."*

Branch `skyemu-backend`.
Companion to [cross-game-plan.md](cross-game-plan.md) (the memory-address work,
P-A…P-F) and [p-a-results.md](p-a-results.md) (what was actually measured).

---

## 0. Headline

**Four of the five asks are already built and one is not.**

The control center was designed for mixed-game queues before the cross-game work
started: `QueuedRun.rom` / `.start` / `.max_turns` / `.max_spend_usd` exist
(`src/app/models.py:317-408`), the new-run dialog already has a game picker
(`AddRunDialog.svelte:103`), the executor already reconciles the cartridge before
every dispatch (`executor.py:486`), and `pokemon queue add --rom … --start …
--max-turns … --max-spend …` already posts to the same API the dialog does
(`src/cli/queue.py:151`). None of that has to be built.

What is missing is in three places, in descending size:

1. **The supervisor holds one emulator, chosen once, and it is mGBA**
   (`src/cli/app.py:308-323`, `src/app/supervisor.py`). mGBA cannot run an NDS
   ROM at all, so "spectate a gen 3 run finish and have the next one be gen 4"
   is not a ROM switch — it is a **backend** switch, which nothing in the app can
   do. This is the whole of ask (c) and it is the only structural change.
2. **The per-game memory contract does not exist** — `TRACE_SPEC`
   (`src/referee/trace.py:56-62`) is FireRed's addresses, wired unconditionally
   at `runner.py:626,713` and `launch.py:80,141`. Run it on Emerald and it does
   not fail; it records **junk that looks like data** and feeds
   `overworld_steps`, `walls_hit` and `movement_efficiency`. This is ask (d).
3. **Registry + packaging** — `configs/roms.yaml` has two entries and the seven
   start states are raw `.state` files rather than savepoint dirs. Mechanical.

Everything else is small: one hardcoded CSS aspect ratio, one missing index
field, six `or "Pokemon FireRed"` fallbacks.

---

## 1. What already works, verified

Claimed here only where I read the code, with the line.

| Ask | Where it already lives |
|---|---|
| Pick a game when starting a casual run | `AddRunDialog.svelte:103` (`rom`), fed by `GET /api/roms` (`server.py:1680` → `roms.list_roms`) |
| Pick an opening within that game | `AddRunDialog.svelte:100` (`start`), `GET /api/starts` (`server.py:1704`), registry `configs/starts.yaml` |
| Bound a run by turns **or** budget | `QueuedRun.max_turns` + `.max_spend_usd` (`models.py:332,344`), applied at `executor.py:565,583` — "whichever lands first ends the run" |
| Bound a run by a story event | `QueuedRun.stop_at`, catalog at `catalog.py:192` |
| One run at a time | `RunExecutor.drain_once` is a no-op while `supervisor.status().busy` (locked decision #1) |
| A freely reorderable queue that switches cartridge between items | `_ensure_rom_loaded` (`executor.py:486`) calls `switch_rom(…, force=True)` once per dispatch. Deliberately at dispatch, not enqueue, "so the queue stays freely editable" |
| Benchmark-capability derived, never flagged | `roms.benchmark_games()` — a ROM can score iff some ladder declares its `game:`. Authoring a ladder is the only step |
| CLI enqueues into the same queue | `pokemon queue add --kind casual --rom … --start … --max-turns … --max-spend … --stop-at … --record …` (`src/cli/queue.py:151-239`), same `POST /api/queue` |
| CLI can name everything a flag asks for | `pokemon ls models\|roms\|configs\|events\|benchmarks\|starts` |
| The frame pipeline knows three consoles | `src/emulator/backends/frame.py` — GB / GBA / NDS measured from the capture's shape, NDS stacked with a seam, `touch_top_row` in the meta |
| The backend refuses a stylus on a cartridge | `skyemu.py:_probe_system` — the console is **measured**, not asked |

So **ask (e), the agent CLI, needs no work at all.** It inherits every new ROM
the registry gains, for free.

---

## 2. The one structural problem: the supervisor is welded to mGBA

`pokemon app` builds its config with `prepare_config(None, …)` — "the LATEST
config" (`app.py:316`), which resolves to `config-5.1.yaml`, which is
`emulator.type: mgba`. That config is handed to one `AppSupervisor` at
`app.py:415` and lives for the process's lifetime.

The supervisor is mGBA-shaped throughout:

- `_process_up()` (`supervisor.py:331`) reads `handle["mgba_proc"]`. A SkyEmu
  handle carries `skyemu_proc` (`runner.py:727`), so a supervised SkyEmu would
  report the fake-handle branch — "treat presence of the handle as up" — and
  never notice a dead emulator.
- `_swap_rom_in_place()` (`supervisor.py:231`) drives mGBA's File → Recent over
  AppleScript and verifies through the Lua socket. There is no SkyEmu path.
- `verify_loaded_rom()` (`supervisor.py:274`) reads the 4-byte code at
  `0x080000AC` — **a GBA cartridge-header fact**. Crystal (GBC) and the DS games
  keep their identity somewhere else entirely.

And the load-bearing fact: **mGBA cannot run an NDS ROM.** So the queue Andreas
described — gen 3 finishes, gen 4 starts — cannot be served by switching
cartridges. It has to switch emulators.

### 2.1 The good news

`run_prepare_phase` **already forks on `emulator.type`** (`runner.py:600-615`),
and the SkyEmu branch is one `Popen` plus a `/ping` loop with **no window, no
Accessibility permission, no Lua script for a human to load**
(`_run_prepare_phase_skyemu`, `runner.py:686`). A backend switch is therefore
*cheaper* than the mGBA cartridge switch the app already supports — the expensive
half of `switch_rom` is exactly the half SkyEmu does not have.

### 2.2 Decision D1 — one backend, or two?

**Recommendation: the control center runs SkyEmu only, on this branch.**

| | SkyEmu for everything | mGBA for GBA, SkyEmu for GB/NDS |
|---|---|---|
| Switch matrix | one path | four combinations, each its own failure mode |
| Human in the loop | never | every relaunch into mGBA needs the Lua script re-loaded |
| Start states | one dialect (SkyEmu refuses mGBA states) | two dialects per game, and nothing on a savepoint says which |
| Timing | one calibration per game | two per game |
| Cost | re-bases the FireRed casual arm onto a different core | keeps v1 FireRed bit-for-bit |

The cost is real but already paid: `plan.md` §5 established that a SkyEmu run is
**a different arm, not a continuation of the v1 board**, and the FireRed SkyEmu
start state already exists (`configs/saves/skyemu/firered-pokebench-v2`).
`CANONICAL_SAVE` is a constructor argument on `RunExecutor`, so pointing the app
at the SkyEmu save is configuration, not a code change.

If Andreas wants v1 FireRed preserved live, the fallback is two control centers
on different ports — which the branch already supports (`dashboard.port: 3430`,
and SkyEmu deliberately does not take slot 1's 8888).

### 2.3 The change, if D1 = SkyEmu-only

Contained, because `_ensure_rom_loaded` is the single call site.

- `Rom` gains `console` (`GB` / `GBA` / `NDS`) and the registry's `game_code`
  becomes console-scoped — or, better, **drops out of the supervisor entirely**:
  SkyEmu already measures the console from the capture (`_probe_system`), and
  what we actually want to verify is "the right ROM is loaded", which `/status`
  answers by name.
- `AppSupervisor` learns the backend from its handle rather than assuming:
  `_process_up` reads whichever of `mgba_proc` / `skyemu_proc` is present;
  `switch_rom` becomes `switch_target(rom)` comparing `(backend, rom_path)`, and
  takes the shutdown→start path whenever the backend differs.
- `app.py` grows `--config` (it has `--rom` already, `app.py:343`) so the
  supervisor can be pointed at `config-v2-firered.yaml`, or
  `_build_supervisor_config` learns to prefer a v2 config.

**A control on this is cheap and mandatory**: enqueue Emerald then Platinum, and
assert the second run's `/status` names the Platinum ROM and its capture measures
`NDS`. Nothing about a backend switch is observable from the run's own logs
otherwise — it would silently play the wrong cartridge and look fine.

---

## 3. "The same logging for all games"

**Read as: a run on any game produces the same event and summary shape.** (If
Andreas meant the same logging across LLM *models*, that is already true — the
event pipeline never branches on the model — and this section is instead the
answer to a question he did not ask. Worth one line of confirmation.)

### 3.1 What is already game-independent

Everything that is not derived from emulator memory. `events.jsonl`,
`run_summary.json`, per-turn timings, cost and token accounting, the screenshots,
the conversation and its compactions, the recording — none of it branches on the
game. A Black 2 run today would produce all of it correctly.

### 3.2 What is FireRed's addresses wearing a general name

| Module | What it hardcodes |
|---|---|
| `referee/trace.py:56-62` | `GSAVEBLOCK1_PTR = 0x03005008`, `gMain` in-battle byte, `gameStats[7]` |
| `referee/battles.py` | `GMAIN_IN_BATTLE_BYTE`, `SB1_GAME_STATS`, the XOR key layout |
| `referee/referee.py` | the poll addresses behind `referee_position` |
| `referee/walkgraph.py:29` | `data/firered-walkgraph.json` |
| `app/catalog.py:189`, `app/projection.py:33`, `app/replay.py:51`, `app/executor.py:58` | four fallbacks to a FireRed ladder path |

The trace one is the dangerous member. It is wired **unconditionally** — there is
no config path to it — so on Emerald `*0x03005008` dereferences something that is
not SaveBlock1 and the harness records a plausible-looking tile stream. That
stream then becomes `overworld_steps`, `walls_hit`, `charged_steps` and
`movement_efficiency` on the run's index row. **A wrong number is worse than a
missing one**, and every one of these fields already has a documented "None →
omit the row, do not draw zero" contract (`models.py:198-210`) that would handle
absence correctly if absence were what it got.

### 3.3 The fix, and the precedent for it

The walk-graph game gate shipped this session (`7aacb6a`) is the shape: the graph
carries a `game:`, `_load_default_graph` returns `None` on mismatch, and the
referee runs without it rather than with the wrong one. **Do the same for the
trace spec and the referee poll addresses: absent means "this game records no
trace", never "use FireRed's".**

That means one module — call it `src/referee/contracts.py` — holding, per
`game:` key, the address block P-A measured, and `TRACE_SPEC` becoming
`trace_spec_for(game)` returning `None` for a game with no entry. `runner.py:626`
and `:713` then set `emu.trace_spec` only when there is one.

### 3.4 What each game can honestly log today

From [p-a-results.md](p-a-results.md). This is the *measured* state, not a plan.

| Game | Position | Map id | Battles | Walk graph | ⇒ logging tier |
|---|---|---|---|---|---|
| FireRed | ✅ `*0x03005008` +0/+2 | ✅ +4/+5 | ✅ | ✅ | **full** — gates, route, movement, battles |
| Emerald | ✅ `*0x03005d8c` +0/+2 | ⚠️ +4/+5, **group inferred, not measured** | ✗ | ✗ | position + steps; needs the group control |
| Crystal | ✅ `0xdcb7`/`0xdcb8` raw | ✅ `0xdcb5`/`0xdcb6` | ✗ | ✗ | position + steps |
| Platinum | ✅ +0/+8 (32-bit) | ✗ **not found** | ✗ | ✗ | position only — no tile identity across maps |
| SoulSilver | ✗ | ✗ | ✗ | ✗ | universal layer only |
| Black | ✗ | ✗ | ✗ | ✗ | universal layer only |
| Black 2 | ✗ | ✗ | ✗ | ✗ | universal layer only |

So "the same logging for all games" is achievable **as a declared tier**, not as
a uniform number set — and the tier belongs on the run record, so a report can
say *"this game records no movement"* rather than showing a blank where FireRed
shows a figure. That is the honest version of the ask and I would build it that
way unless told otherwise.

### 3.5 `RunSummary` cannot say which game a run played

`models.py:117-274` has no `game` or `rom` field. `config.json` carries both
(`game_name` and `emulator.rom_path`, stamped by `apply_rom`, `roms.py:290`), so
the projection can derive it — and the re-projection mechanism already exists
(`projection_version`, `models.py:242`, which rebuilds stored rows when the
projection learns a field). One field, one bump, no migration.

Without it History and the run list are game-blind, and a mixed queue makes every
row ambiguous the moment it finishes.

### 3.6 The six FireRed fallbacks

`turn.py:674`, `agent.py:343`, `task_master.py:178,423`, `append_agent.py:629`,
`runner.py:837`, `tools/ask_perplexity.py:45` all read
`config.get("game_name") or "Pokemon FireRed"`.

With one game that was harmless. With seven it means any path reaching an agent
without `apply_rom` **tells the model it is playing FireRed**, and the model will
act on it. These should raise or resolve to `None`, not to a game name — a run
that cannot say what it is playing should fail loudly at turn 0, not play Black 2
under a FireRed system prompt.

---

## 4. Spectate on every game

Both views, checked against the code.

**Simple view** — already aspect-agnostic. `SimpleView.measureShot`
(`SimpleView.svelte:521-540`) measures the rendered picture from
`naturalWidth`/`naturalHeight` and publishes `--shotw`; the recorder does the
same measurement from the page (`recorder.py:726`). A stacked NDS frame will be
*measured* correctly. The open question is only whether a 2:3 portrait frame
inside a `1/1` stage (`SimpleView.svelte:725`) reads well next to the turn box —
**that is a look-at-it question, not a defect**, and the answer is a screenshot.

**Advanced (Spectate)** — one real defect: `Spectate.svelte:776` hardcodes
`.gba { aspect-ratio: 240/160 }`. A 256×384 stacked DS frame letterboxes into a
sliver of that box. Fix by driving the aspect from the frame's natural size, the
way SimpleView already does.

**Not defects, worth naming so nobody "fixes" them:**

- The gate HUD reads the ladder from `/runs/{id}/api/config` and hides itself
  when there is none (`Spectate.svelte:574`, comment: *"real, never hardcoded"*).
  A casual Black 2 run correctly shows no gates.
- `lib/gates.js` is the hardcoded Kanto ladder, imported by ten modules — but
  those are **board, report and history** surfaces, not spectate. They matter for
  the leaderboard, which is out of scope here (casual runs never reach it).
- `RouteMap` / `mapatlas.js` draw on FireRed artwork and are fed by `route.json`,
  which is fed by `referee_position`. On a game with no contract there is no
  route, and the panel must be **absent**, not empty — same rule as
  `charged_steps` and `walls_hit`.

---

## 5. Registry and packaging

Mechanical, and the prerequisite for everything above being visible.

1. **Five ROM entries** in `configs/roms.yaml`: crystal, platinum, soulsilver,
   black, black2 — each with `game`, `game_name`, `sha1`, `start_save`. The
   `game_code` field needs the console decision from §2.3 first (it is a GBA
   header offset and means nothing for the other four).
2. **Package the seven start states as savepoint dirs.** Today they are bare
   `v2-experiments/states/<game>/start.state`. A savepoint dir is three files
   (`starts.py:38`: `emulator.state`, `state.json`, `tasks.json`) — FireRed's
   SkyEmu one is the template (`configs/saves/skyemu/firered-pokebench-v2/`,
   whose `state.json` is literally `{}`). Rename, add two small files, commit
   under `configs/saves/skyemu/<game>/`.
3. **Emerald's state needs choosing**: the registry currently points casual
   Emerald at `configs/saves/emerald-truck`, which is an **mGBA** state. Under
   D1 = SkyEmu-only it must point at the SkyEmu truck state instead.
4. **`configs/starts.yaml`** gains an entry per game so the picker preselects
   something with a name and a description rather than falling through to
   `start_save`.
5. The picker already filters on `on_disk` (`AddRunDialog.svelte:32`), so a game
   whose dump is not on this machine disappears by itself. No new gating needed.

Two incidental defects found in the earlier audit, still open, both one-liners:

- `configs/roms.yaml:15-16` documents a `rom_sha1` check that **does not exist**
  anywhere in `src/`.
- `scripts/build_walkgraph.py:89` fetches `/master/` while
  `scripts/render_gamemaps.py:112` honours the pinned SHA.

---

## 6. Order of work

Each step is independently shippable and each ends in something observable.

**S1 — make the games pickable (no new capability).**
Registry entries + packaged savepoint dirs + starts.yaml. Ends when
`pokemon ls roms` shows seven and the dialog's picker shows the ones whose dumps
are present. Nothing runs yet on gen 4/5.

**S2 — `RunSummary.game`.**
One projection field + `projection_version` bump. Ends when History shows the
game on every row, including the old ones. Do this *before* S3 so a mixed queue's
output is legible the moment it exists.

**S3 — the backend switch (D1).**
Supervisor learns its backend from the handle; `switch_target(rom)` compares
`(backend, rom_path)`; `app.py --config`. Ends with **the control**: enqueue
Emerald then Platinum, watch the first finish, assert the second comes up on NDS.
This is the one Andreas actually described and the only structural change.

**S4 — the per-game contract, absent-by-default.**
`contracts.py`, `trace_spec_for(game)`, `runner.py:626,713` conditional, the six
FireRed fallbacks made loud. Ends when an Emerald run records **no** trace rather
than a wrong one — verified by the absence, which is the harder assertion and the
one that matters.

**S5 — spectate geometry.**
`Spectate.svelte:776` driven from the frame; look at a DS run in both views and
decide whether the simple view's square stage needs anything.

**S6 — fill the contract in, per game.**
Emerald first (the group control that P-A left inferred), then Crystal. Platinum
needs its map-id route, which is open work from the cross-game plan. This is the
long tail and it is additive: each game that gains a contract gains its movement
numbers without anything else changing.

S1 + S2 + S5 are a day's work. S3 is the real one. S4 is the one that decides
whether the numbers on the board can be trusted.

### Progress (2026-09-19)

**S1 ✅ `528e51d`** — all seven games registered, states packaged as savepoint
dirs under `configs/saves/skyemu/<game>/`, `console` added to the registry and
`game_code` made optional (a GB/GBC cartridge has none). `pokemon ls roms` shows
seven; the picker shows seven.

**S2 ✅ `02f6853`** — `RunSummary.game` / `game_name` / `console`, derived at
projection time, `PROJECTION_VERSION` 13 → 14 so boot re-projects. Five of the
eight tests are the negative control: the projection must NOT fall back to
FireRed the way six sites in `src/` still do.

**S3 ✅ `faaa34e`** — and the finding is that the plan overestimated it. **A
mixed queue needs no backend switch**, because SkyEmu holds every console the
registry declares; Emerald → Platinum changes cartridge, not emulator. What it
needed was for the supervisor to stop being mGBA-shaped by assumption (liveness
read `mgba_proc`, so a dead SkyEmu reported healthy forever) and a hard refusal
for the case that fails *silently*: mGBA given a `.nds` comes up with no
cartridge, the Lua connector still dials in, and the run plays a black screen for
its whole turn budget with every health check green.

Verified live rather than against fakes — cartridges changed across three
consoles, each console **measured from the rendered frame**, not read back from
the config just written:

| | declared | measured | time |
|---|---|---|---|
| start (emerald) | GBA | GBA | — |
| → platinum | NDS | NDS | 0.8 s |
| → crystal | GB | GB | 0.3 s |
| → black2 | NDS | NDS | 1.5 s (536 MB, incl. staging) |

No human step in any of them. `pokemon app --config configs/config-v2-firered.yaml
--rom black2` boots the whole control center on a DS game and reports
`backend: skyemu, console: NDS, awaiting_lua: false`.

**S4, S5, S6 — not started.**

---

## 7. Decided (2026-09-19, Andreas)

All three answered in conversation. These are settled; the sections above keep
their reasoning but the recommendations are no longer open.

**D1 — SkyEmu only.** Verbatim: *"for number 1 i woudl exect us to stop using
mGBA in v2 so gba games shoudl work on skyemu"* and *"yes sky emulator only. yes
we dont have towrroy about backwards combatbvilty and small behavieur chanegs.
once we go full v2, tehn teh benchamrk itself changes majorly. so its no real
problem for small changes"*.

So §2.3 is the plan, and the caveat in §2.2 about re-basing the FireRed arm is
**explicitly waived** — v2 changes the benchmark anyway. Consequences that follow
automatically and should not be re-litigated later:

- mGBA is not removed from `src/` (v1 still runs from `pokemon run`), but the
  control center never selects it. `_build_supervisor_config` points at a v2
  config.
- `configs/saves/pokebench-v1` (an mGBA state) stops being the app's
  `CANONICAL_SAVE`; `configs/saves/skyemu/firered-pokebench-v2` takes over. It is
  a constructor argument on `RunExecutor`, so this is configuration.
- `configs/saves/emerald-truck` is an mGBA state and must be replaced with the
  SkyEmu truck state from `v2-experiments/states/emerald/truck.state`.
- `supervisor.verify_loaded_rom` reads a **GBA** header offset. Under one backend
  the better check is the one SkyEmu already does — `/status` names the ROM and
  `_probe_system` measures the console — so `Rom.game_code` should stop being the
  verification currency rather than being extended per console.

**D2 — show every game, label what it cannot record.** Option (b). The
completeness question he asked alongside it — *"what woudl it take to make tehse
experimnets complete?"* — is §8.

**D3 — all games.** Verbatim: *"yes all games"*. §3 is the right reading; the
per-game contract is the work.

**And the shape of the fix for §3**, confirmed: *"so for this we shoudl fix teh
runner to have differnt game profiles?"* — yes. One profile per `game:` key (the
same join key `roms.yaml` and the ladders already use), holding the memory
contract. The runner asks the profile for a trace spec and wires nothing when
there is none. **`RunSummary.game` is confirmed too** (*"this shoudl be added tehn
rigt"*) — §3.5.

---

## 8. What "complete" costs, per game

The answer to D2's follow-up. Nothing here is a plan yet; it is the price list.

### 8.1 The contract is five layers, not one

Each unlocks specific fields and each has its own method and cost.

| Layer | Unlocks | How it is got |
|---|---|---|
| **L1** position + map id | `referee_position`, `route.json`, route points/coverage, gate distance, tile-change step counting, `overworld_steps`, `map`-type checkpoints | `v2-experiments/find_addresses.py` — already built, ~hours per game |
| **L2** in-battle flag | battle/overworld separation in the trace | differential search with a battle/no-battle control — a **new probe mode**; the finder does xy/map/party only |
| **L3** battle counters + party | `wild_battles`, `trainer_battles`, `battle_turn_share`, `party`-type checkpoints | same method; the finder's party stage is its weakest (935 → 322 → 81 candidates) and needs work |
| **L4** walk graph | `walls_hit`, `wall_rate`, `movement_efficiency`, `shortest_steps`, `movement_legs` | full map + collision + warp data. **This is where the games diverge sharply** — §8.3 |
| **L5** checkpoint ladder | gates, `stop_at`, benchmark capability | authoring, once L1 (+ flags/vars) exists |

**L2 is not optional and is easy to underrate.** Without the in-battle bit the
trace cannot tell a battle press from an overworld one, so every press inside a
battle is misclassified — `walls_hit` and `wall_rate` come out *wrong*, not
missing. A game with L1 but not L2 must record no wall accounting at all.

### 8.2 Where each game stands (measured, from p-a-results.md)

| Game | L1 pos | L1 map | L2 | L3 | L4 | L5 |
|---|---|---|---|---|---|---|
| FireRed | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Emerald | ✅ | ⚠️ group **inferred, not measured** | ✗ | ✗ | ✗ | ✗ |
| Crystal | ✅ | ✅ | ✗ | ✗ | ✗ | ✗ |
| Platinum | ✅ | ✗ **not found** | ✗ | ✗ | ✗ | ✗ |
| SoulSilver | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Black | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| Black 2 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |

### 8.3 L4 is the one that is not a matter of hours

`scripts/build_walkgraph.py` builds FireRed's 8,473-node graph from **pret's own
files, fetched from GitHub** — `data/layouts/<Layout>/map.bin` (one u16 per tile:
collision in bits 10-11, elevation in 12-15), `metatile_attributes.bin`,
`data/maps/<Map>/map.json` for warps and connections, `map_groups.json` for the
(group, num) pair the referee reads out of SaveBlock1.

Checked against the pret org today:

| Game | Decomp | Ships map data in-repo? | What L4 would take |
|---|---|---|---|
| Crystal | `pret/pokecrystal` | **yes** — `maps/` and `data/` are in the tree | a second builder. Gen 2 collision is per-*block*, not gen 3's per-tile u16, so it is a new extractor, not a parameterisation |
| Platinum | `pret/pokeplatinum` | **no** — the repo is `src`/`include`/`res` and builds against a `platinum.us` baserom | the decomp gives the format and the NARC indices; the data comes out of **our own ROM**, so it needs a NARC extractor first |
| SoulSilver | `pret/pokeheartgold` | **no** — same shape (`heartgold.us` / `soulsilver.us` baserom dirs) | as Platinum |
| Black / Black 2 | **none exists** | — | no decomp, but the format is implemented in working open source — §8.3.1 |

So L4 is: *tractable* for Crystal, *a NARC pipeline* for Platinum and SoulSilver,
and *a NARC pipeline plus a format port* for the gen 5 pair.

#### 8.3.1 Gen 5 maps are gettable — correcting "research"

Checked properly (2026-09-19, after the first pass called this open-ended).
**A decomp is not what a walk graph needs; a format is.** For gen 5 the format
exists in working, maintained code.

[`Trifindo/Pokemon-DS-Map-Studio`](https://github.com/Trifindo/Pokemon-DS-Map-Studio)
(Java, 269 stars, last commit 2026-08-30) carries a dedicated
`src/main/java/formats/collisions/bw/` package — `CollisionsBW3D.java`,
`CollisionHandlerBW.java`, `CParam2D.java`, plus `CollisionsColorsBW.txt`. It
reads and writes gen 5 `.per` collision files, one per map.

The format, read off that code: **per tile, a collision type plus an index into a
table of four corner heights** (a ~1000-entry float table in `CollisionsBW3D`).
The four corners are gen 5's overworld being genuinely 3D — tiles have slopes,
which gen 3 tiles do not. A walk graph needs the passability and the warps, not
the rendering, so most of that table is irrelevant to us; the type byte is the
part that matters.

The pipeline is therefore: NARC `a/0/0/8` → extract (Tinke, or ~100 lines of
Python — NARC is a simple container) → parse `.per` with our own reader → the
same graph builder shape as FireRed's.

**Licensing, stated plainly: the repo carries no license.** So this is *read the
format, write our own parser* — a file format is not copyrightable, an
implementation is. No code gets copied.

That makes gen 5 the most expensive of the seven, but **not open-ended**. What it
is NOT is the thing standing between us and a playable Black run: that is L1+L2,
which needs none of this.

#### 8.3.2 There is no published gen 5 RAM map, and that is fine

Data Crystal has RAM-map pages for Red/Blue, Crystal and FireRed. It has **none
for Black/White** (404). So nothing external will hand us the addresses.

This is not a blocker, because **the finder never used a decomp as input.**
`FIRERED_TRUTH` is touched only by `--stage verify` — a check on the *tool*, not
a dependency for a new game. Platinum's x/y came out of a 4 MB scan in ~2 minutes
with zero external data.

One gen-5-specific risk worth a control before we trust a result: gen 5's
overworld is 3D (see the corner-height table above), so position may be stored as
fixed-point sub-tile rather than a plain tile integer. `MAX_TILE_DELTA = 4` in
`find_addresses.py` would then **reject the right answer**. Gen 4 turned out to
store plain tile ints in 32-bit fields (Platinum x at +0, y at +8), so the risk
may not materialise — but the ablation already measured that *magnitude* does
most of the filtering (x: 4 → 24 candidates without it), which is exactly the
condition that would misfire. The cheap control: add fixed-point forms to `FORMS`,
relax magnitude, and **re-run Platinum, where the answer is already known**,
before pointing it at Black.

### 8.4 One schema change blocks every DS ladder

`_REQUIRED_SIGNATURE_FIELDS["map"]` (`src/referee/checkpoints.py:29`) hard-requires
a `map_group` + `map_num` pair. **A gen 4/5 map is a single id.** No DS ladder can
be authored until that is a per-game shape rather than a gen-3 fact — which is the
same lesson as the trace spec, in the checkpoint schema.

### 8.5 The cheap half, and what I would actually do first

Days, not weeks, with the tool that already exists:

- **Emerald's map group** — the one inferred value in the whole table. Both maps
  reachable from the truck are group 1, so it needs a route that crosses a group
  boundary and a re-run. ~1 hour, and it closes the only ⚠️.
- **L2 for FireRed's peers** — one new probe mode in `find_addresses.py`, then
  Emerald and Crystal.
- **Platinum's map id** — the open item from the cross-game plan. The adjacency
  prior (`NEAR_ANCHOR = 8`) that found it on three games failed here, so this
  needs a wider search and probably a longer cross-map route.
- **L1 for SoulSilver, Black, Black 2** — the method is proven on NDS (Platinum's
  x/y took ~2 minutes), so this is route authoring more than searching.

**But before any of it**: nothing has played these games. The start states were
verified by measuring that the four directions respond, not by playing. **One
casual 100-turn run per game, once S1-S3 land, is worth more than any of the
above** — it says whether a model can get out of the room at all, and a game it
cannot leave does not need a walk graph yet.

---

## Related

- [cross-game-plan.md](cross-game-plan.md) — P-A…P-F, the address work
- [p-a-results.md](p-a-results.md) — what was measured per game
- [plan.md](plan.md) — the SkyEmu backend port, §5 on arms
- `v2-experiments/states/README.md` — the seven start states
