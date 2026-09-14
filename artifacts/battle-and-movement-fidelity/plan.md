# Battle and movement fidelity — plan

> Source: conversation 2026-09-14 (Andreas + Marvin). Probes run against the 25 published runs the same day.
> Status: built 2026-09-14. §3.1 referee battle state and §3.2 per-input trace verified LIVE on a 60-turn casual run (gemini-3.5-flash-lite low, 16:11): every turn traced, traced steps equal the polled displacement, bumps counted as lost inputs, a door warp as one step; battle counters/bit read every poll (no battle occurred before the cap — a second run toward Route 1 is queued for that). Two live defects fixed on the way: the Lua sampler referenced `tohex` before its definition (every sample empty → recorded as 0 steps; a blind trace now leaves the bound), and a casual run without a stop-at has no referee so nobody drained the trace (fetch moved before the referee guard; Lua buffer capped). §3.3 v7, §4.1, §4.2, §5 live. §4.3 video: pilot passed, batch of 25 running in an Opus sub-agent.

## 0. Decisions already taken (Andreas, 2026-09-14)

| # | Question | Decision |
|---|---|---|
| 1 | Which battles get projected for a run that lacks them | **1B with a floor**: any battle (wild category, or a trainer by id) is projected from the field only when it has been observed in **≥ 5 runs**. The Route 22 rival and most Viridian Forest bug catchers will not clear the floor; they are shown when fought, never projected. |
| 2 | A turn straddling battle and overworld | **2C**: record both attributions. The board uses **A** (whole turn to the state it started in) until enough traced runs exist to switch to **B** (split by input). |
| 3 | Backfill for existing runs | **3C**: cheap backfill (savepoints, screenshot classifier, OCR) **and** video frame analysis for steps. |
| 4 | Movement efficiency | **4A**: directness only — shortest path from the leg's opening tile ÷ overworld steps, map-typed gates only. A heal detour counts as inefficiency. |
| 5 | A battle that starts and ends inside one turn | **(a)** costs 0 turns. Rule A stays exact and simple. |
| 6 | Losses and repeats (counter − flags) | **Attempts, aggregated per trainer**: a lost Brock fight plus the rematch is one figure, "how long you battled him". `won` is a per-trainer boolean, turns are summed over attempts. |
| 7 | Video backfill order | **All 24 runs**, but pilot on ONE medium-sized run first to learn the method, then the rest. Video work runs in Opus-level sub-agents to save tokens. |
| 8 | Trainer identity for lost fights (evening) | **Option A: read `gTrainerBattleOpponent_A` (EWRAM 0x020386AE)** at every poll and from every savepoint; names the fight at the counter step, won or lost. Chosen over coordinate estimation (B) and flag-only attribution (C). OCR identity hints stay only as a fallback for pre-opponent records. |
| 9 | Trainer projection (evening) | Pace-based like legs: own turns ÷ field mean over fought trainers, × field mean for EVERY unfought trainer (finished runs included, one fight each, no encounter-rate weighting); threshold 4 fights per column; a fight against a trainer <4 runs met is shown but not counted; a flag with no counted battle is not a fight. |
| 10 | Board scope (evening) | Every card but Performance shows only runs that cleared Route 1. Battle share card removed. Movement efficiency counts the open leg, credited with the ground gained. |

The live probe for the in-battle address is **not needed**: §2.3 found and verified it offline from savepoints.

## 1. Metrics this plan delivers

Efficiency section, new cards (design: [[artifacts/online-leaderboard/plan.md]] §11 conventions — picker, dotted = measured on a partial run, hatched = projected).

- **Movement efficiency** — Σ shortest path over closed map legs ÷ Σ overworld steps in those legs (4A). Exact for traced runs; for backfilled runs the source is stated (§4.4).
- **Wild battles** — count, turns spent (rule A: turns that *started* inside a wild battle), turns per wild battle. One category, no per-species split.
- **Trainer battles** — by trainer id: fought or not, turns spent, outcome (counter − flags = losses or rematches). Mandatory two (Oak's Lab rival, Brock) projected when missing; optional ones (Route 22 rival, five Bug Catchers, Camper Liam) shown when fought, projected only past the ≥ 5 floor.
- **Battle share** — fraction of the run's turns that started inside any battle.
- **Inputs lost to interruptions** (traced runs only) — direction inputs that moved nothing because a battle or dialogue took them. This is the "planned sequence cut off by a wild battle" case made visible.

Projection of a missing battle follows the leg rule already on the Estimation methods page: field median turns for that battle id (or the wild category's median turns per battle × the field's median wild encounters on the legs the run never played), added to the projected total, hatched.

## 2. What the probes established (2026-09-14)

### 2.1 Battle counters live in SaveBlock1, one read away
FireRed keeps `gameStats[64]` (u32) at **SaveBlock1 + 0x1200**, XOR-encrypted with `encryptionKey` at **SaveBlock2 + 0xF20**. Indices: 7 total battles, 8 wild battles, 9 trainer battles (pret `include/constants/game_stat.h`, `include/global.h`). The referee reads SB1 up to exactly 0x1200 today (`_SB1_READ_LEN`), so the counters begin one byte past the current read.

Verified on the Fable medium run's savepoints: 37 total / 30 wild / 7 trainer battles at turn 276, monotone across all 28 savepoints, and the wild count tracks the OCR "appeared" turns in shape while OCR overcounts (43 vs 30 — dialogue re-captures).

### 2.2 Trainer identity is in the flag block the referee already reads
Trainer-defeated flags are ids **0x500–0x7FF** (`TRAINER_FLAGS_START`), inside the 0x120-byte flag bitfield at SB1 + 0xEE0. Fable's final state: {102 Rick, 104 Sammy, 142 Liam, 327 rival Oak's Lab (Bulbasaur), 414 Brock, 531 Anthony}. Seven trainer battles vs six flags → one battle was a loss or a repeat: **counter − flags = attempts that did not set the flag**. First-badge roster (pret map data): Oak's Lab rival 326–328, Route 22 rival 329–331, Bug Catchers 102/103/104/531/532, Camper Liam 142, Brock 414.

### 2.3 The in-battle bit, found and verified offline
`gMain` sits at **IWRAM 0x030030F0** (callback pointers 0x08056549/0x080565c9 in the overworld, 0x080123f9/0x08011115 in battle). `gMain.inBattle` is **bit 1 of the byte at 0x03003529** (pret `include/main.h`: `/*0x439*/ u8 inBattle:1` second bit). Read from every Fable savepoint and compared with the screenshot of the *next* turn: **27 of 27 agree** (1 at turns 31, 141, 151, 171, 201, 241, 271 — all battle screens; 0 everywhere else). The byte at 0x02022B4C is not `gBattleTypeFlags` (constant 0x4) — not needed anyway.

### 2.4 Screenshot t is the START of turn t; savepoint t is AFTER turn t's inputs
Event order inside a turn: `turn_start` → `screenshot` → LLM → `turn_explanation` → inputs → `referee_position` → `savepoint_saved` → next `turn_start` (Fable turn 30: position and savepoint both at 16:14:23). So "turn started inside a battle" for existing runs is a property of screenshot t, and the savepoint at t labels screenshot t+1.

### 2.5 A trivial screenshot classifier is 97.7 % on the labelled set
Feature: share of dark-navy pixels in the bottom 28 % of the frame (the battle text box). Threshold 0.15 → **596 of 610** savepoint-labelled screenshots across the 25 runs (the first cut, 0.08, was 580: Oak's Lab floor reads 0.09). The 14 misses are battle boundaries: a black transition frame at a battle start, the Pokédex page after a catch, the old-man tutorial. Fable: 80 of 276 turns (29 %) started inside a battle. Screenshots are 1440×960, one per turn, every run. Built as `scripts/backfill_states.py` (calibration reported per run).

### 2.6 Savepoints cover every run at 10-turn resolution
All 25 runs have `savepoints/turn_*/emulator.state` every 10 turns plus the final turn. The state is an mGBA PNG savestate: the `gbAs` chunk zlib-decompresses to 0x61000 bytes with **IWRAM at +0x19000 and EWRAM at +0x21000**. Continued runs chain to their source run dir via `run_summary.continued_from`; all sources are present locally.

### 2.7 Input replay on the walk graph cannot backfill steps
Simulating each turn's direction inputs from the polled start tile reproduces the polled end tile on **56 % (Fable), 58 % (astra medium), 60–71 % (gemini 3.8 high)** of moving turns. On the turns it reproduces, true steps are only 5–10 % above the tracker's shortest-path bound. The misses are the interrupted turns — unknowable from inputs plus end tile. Per-turn screenshots have the same blind spot, so LLM sub-agents reading them would be guessing. **Not pursued.**

### 2.8 Video is viable and cheap to decode
24 of 25 runs have `recording.mp4` locally (Fable medium recorded none). 1920×1080 at 30 fps, the dashboard composite: the game screen occupies a fixed region (≈ x 112–1008, y 165–760, 3.7× GBA scale) and the **TURN counter is in frame** (top-left box), so frame→turn mapping is free. Decoding 60 s of footage to 480-px grey frames takes ≈ 1 s wall (4.4 s CPU); the ~25 h of footage decodes in ≈ 25–30 min. Three older runs also have a 1080×1080 game-only `recording-simple.mp4`.

## 3. Live instrumentation (new runs) — exact data

### 3.1 Referee (`src/referee/referee.py`)
- Extend `_SB1_READ_LEN` to 0x1300 (covers the 64 stats), read the SB2 key (pointer at 0x0300500C, +0xF20), read the byte at 0x03003529. Two extra READMEMs per poll.
- Decode per poll: `in_battle` (bit 1), `battles_total/wild/trainer` (XOR key), `trainers_defeated` (flag ids 0x500–0x7FF set). Emit `referee_battle_state` with the deltas; the tracker persists the series in `referee_state.json` like positions.
- Turn attribution A: `state_at_start[t+1] = in_battle after turn t`; turn 1 starts in the overworld.
- Tests: fixtures built from the Fable savepoints (golden: 30 wild / 7 trainer / flags above); a decode test for the XOR path; a torn-read test for the longer block.

### 3.2 Emulator trace (`lua/socketserver-1.lua`, `src/emulator/emulator.py`)
- In the SEQ queue loop, when a key finishes (`queue_state` → idle) read map/x/y from SB1 via the pointer, the in-battle byte, and `gameStats[7]`; append `{i, key, map_group, map_num, x, y, in_battle, battles}` to a trace table. Lua reads memory directly (no round trip).
- Return the trace with `SEQUENCE_DONE:` as one JSON line, or via a new `TRACE` command the Python side calls after the settle check (the DONE line is currently skipped by whichever reader sees it next — do not rely on it). Emit `turn_input_trace`.
- Derived per turn: overworld steps (tile changed while `in_battle == 0`), inputs lost to interruptions, per-input attribution B, battle start index. WAIT pseudo-inputs record no sample.
- The tracker's `steps_walked` switches to the traced count when present, keeps the bound otherwise, and records which (`steps_source`).

### 3.3 Projection (`src/app/projection.py`, version 7)
New `RunSummary` fields, all `None` when absent so the board leaves the run off: `wild_battles`, `wild_battle_turns`, `trainer_battles` (list of {id, name, turns, won}), `battle_turn_share`, `overworld_steps`, `movement_efficiency`, `inputs_lost`, `fidelity` ({steps: trace|video|bound, battles: live|savepoint, states: live|classifier}). Re-project published rows with `publish --site-only --refresh-rows`.

## 4. Backfill of the 25 existing runs (3C)

### 4.1 Savepoint decode — `scripts/backfill_battles.py`
Walk each run and its `continued_from` chain, decode every `emulator.state` (§2.6, §2.1–2.3), write `battle_backfill.json` in the run dir: cumulative counters, trainer flags and `in_battle` per savepoint turn. Exact, 10-turn resolution. Half a day.

### 4.2 Screenshot classifier — `scripts/backfill_states.py`
Navy-band feature (§2.5) on every screenshot → `state_at_start[t]` for every turn. Calibrate per run against that run's savepoint labels and record the agreement in the output; flag any run under 95 % for a look. Combine with the counters: a battle segment opens at the first battle-state turn after a counter increment and closes at the next overworld turn; wild vs trainer from which counter moved between the bracketing savepoints, else from OCR text. Gives turns per wild battle and per trainer battle at turn resolution. Half a day.

### 4.3 Video steps — `scripts/backfill_steps.py` (24 runs)
Crop the game region, downscale, grey. Detect one-tile scrolls by phase correlation between consecutive frames (a walking step scrolls the background one tile over ~16 game frames ≈ 8 video frames; no running shoes before the first badge, so walking speed is constant). Gate by the classifier (overworld only) and ignore frames inside door/warp fades. Map frames to turns by OCR of the TURN box or by aligning event timestamps to the recording start. Output per-turn overworld steps. Validation: (a) a turn's net displacement on the graph ≤ detected steps for every turn, (b) once one instrumented run exists, run it with recording on and require ≥ 95 % agreement between video steps and the trace. Risks: NPC motion causing false scrolls (mitigate: require whole-frame shift, not local), map transitions, the Fable medium run has no video (stays on the bound). Decode ≈ 30 min; development 1.5–2 days including the validation run.

### 4.4 Provenance
Every backfilled value carries its source in the projection (`fidelity`, §3.3) and the card's tooltip says it: "steps from video", "battles from savepoints", "state from screenshot". Rows built on the bound stay dotted. Nothing backfilled is presented as measured live.

## 5. Board
- Efficiency: **Movement efficiency**, **Turns per wild battle**, **Battle share**, **Trainer battles** (stacked by id, projected part hatched). Existing Turns per task and Inputs per turn unchanged.
- Estimation methods page: a "Battles" paragraph — the ≥ 5 floor, the median rule, which trainers are mandatory.
- Tooltips carry fidelity per row.

## 6. Order and estimates

| Step | Work | Estimate |
|---|---|---|
| A | Referee battle state + tests (§3.1) | 0.5 day |
| B | Savepoint backfill (§4.1) | 0.5 day |
| C | Screenshot classifier + battle segments (§4.2) | 0.5 day |
| D | Emulator input trace (§3.2) | 1 day |
| E | Projection v7 + board cards + methods text (§3.3, §5) | 1 day |
| F | Video steps + validation run (§4.3) | 1.5–2 days |

A→C need no emulator; D needs the queue idle for a restart of the control center; F last, after D, so the validation run has a trace to compare against. Total ≈ 5–5.5 days of build.

## 7. Open questions

None open — all seven decisions are in §0 (2026-09-14).
