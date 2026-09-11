# PokeBench — the benchmark

PokeBench measures how far an LLM can play Pokémon FireRed **from the screen
alone**, judged by a deterministic referee against a fixed ladder of story
gates. It's the thing the control center's leaderboard ranks.

Two ingredients:

1. a **gate ladder** — an ordered list of story checkpoints with turn deadlines, and
2. a **referee** — an out-of-band, read-only memory watcher that stamps each gate
   the instant it's truly reached.

The agent never sees the referee. The referee never helps the agent. It only
watches and scores.

---

## Official vs casual runs

| | **Official (a benchmark)** | **Casual** |
|---|---|---|
| Config | `config-5.1.yaml` (frozen; was `config-5.0` until 2026-09-09) | you choose (`config-5.1` by default) |
| Benchmark | you choose (easy / **first-badge**, the default / full) | n/a |
| Goal | the benchmark's goal (overrides the config's) | the config's |
| Model | you choose | you choose |
| Max turns | none — gate deadlines bound it | you choose |
| Stop at an event | n/a — ends at its own ladder | optional ([below](#stopping-at-a-story-event)) |
| Gate ladder | the benchmark's, enforced (`enforce: true`) | none, unless you stop at an event (then observe-only) |
| Leaderboard | eligible (if completed/terminated **and config-5.x**), per benchmark | never |
| Continues | yes — stays official on the SAME benchmark | always casual |

**Official** is the comparable benchmark: same frozen config, same start save
(`configs/saves/pokebench-v1`), for every model — the only variable is the
model. You pick *which benchmark* the run plays (see below); that selects the
gate ladder **and** the goal. A cancelled official run is **voided** (it never
reaches the leaderboard), but it can be **continued** and the continuation scores
(see *Pausing & resuming* below). **Casual** runs are for experimentation — pick
any config and turn budget; they run free of gates and never score. A casual run
may also name a story event to stop at (*Stopping at a story event*, below), so
"play 100 turns" and "play until Viridian Forest" are both expressible.

---

## Pausing & resuming an official run

An official run **may be paused and resumed any number of times** — stop it at
night, hit Continue in the morning, and it finishes as the same benchmark. A full
multi-day game benchmark is run this way. This is sound because **the score is
turn-based, not clock-based**, and the checkpoint is turn-exact:

- **What's scored is checkpoint-exact.** The score is `gates_reached` + `turns`
  (below), both turn-based; gate deadlines are in *turns*, never wall-clock. A
  savepoint is an **atomic turn-N bundle** — emulator state + the referee gate
  latch + the TaskMaster task tree + the turn counter, all captured at the same
  turn — and a continue restores all of them to turn N, discarding anything the
  source recorded *after* N. So resuming is indistinguishable from never stopping.
- **Wall-clock is active-compute time, and isn't ranked.** `duration_s` sums the
  active segments and **excludes the idle pause** — an overnight gap is invisible
  to it, and it's display-only (never part of the ranking).
- **Hard-kill safe.** A cooperative Stop is near-instant: it **cancels the
  in-flight turn** and checkpoints the *last settled* turn (`on_crash`). Because
  a turn's buttons are pressed only after the model returns, the cancelled turn
  left the game untouched — so the continuation simply **re-runs the interrupted
  turn from that clean boundary**. That re-run is not a second scored attempt: it
  changed no game state and leaked no context (memory updates apply only on a
  completed turn), and the turn counter is restored to the bundle turn, so it is
  not double-counted. A hard death (OOM/power) falls back to the last bundle; the
  official config checkpoints every 10 turns **and** at every TaskMaster handoff,
  so at most a few turns replay — and the referee latch is capped to the bundle
  turn, so a replayed turn never double-credits a gate or double-counts.
- **Tamper-evident.** Each bundle carries a `checkpoint.sha256` over its
  score-bearing parts (emulator state + gate latch + task tree). An official
  continue **refuses to resume a checkpoint whose seal doesn't match** — a paused
  benchmark can't be hand-edited overnight and still score.

The intermediate stopped segment is itself voided (status `cancelled`, never on
the leaderboard), but it keeps `kind=official` + its benchmark id so the chain
stays resumable; the segment that finally reaches the last gate (or misses a
deadline) is the one that posts the verdict.

---

## The benchmarks

A **benchmark** is a single self-contained ladder file: a top-level `benchmark:`
block (`id`, `name`, `goal`, `default`) — the *overall goal* is the meta-goal the
agent plays toward, shown in the UI — followed by its own *gate ladder*. One YAML
= one whole benchmark. `configs/benchmarks.yaml` is just a **manifest**: the
ordered list of ladder files (it sets display order, nothing else). Three ship
today, all on FireRed:

| id | Goal (overall) | Ladder file | Final gate |
|---|---|---|---|
| `pokebench-easy` | Reach Viridian City | `checkpoints-firered-easy.yaml` | `viridian_reached` (7 rungs) |
| `pokebench-first-badge` | Earn the Boulder Badge (beat Brock) | `checkpoints-firered-firstbadge.yaml` | `brock_defeated` (12 rungs) |
| `pokebench-full` *(default)* | First three badges → Thunder Badge | `checkpoints-firered-v1.yaml` | `thunder_badge` (20 rungs) |

The easy / first-badge ladders are **self-contained prefixes** of the full
ladder, kept as **separate files** so each benchmark's goal *and* gates can be
edited independently. Reaching a benchmark's *final* rung **wins** the run
(`status: completed`); missing any rung's deadline terminates it.

**Goal override.** When an official run is queued, the executor loads the chosen
benchmark and (a) injects its ladder as the run's enforced `referee` block, and
(b) overwrites the frozen config's `task.goal` with the benchmark's `goal` — so
the same frozen official config (`config-5.1`) plays toward a different objective per benchmark. The
benchmark id is stamped onto the run and drives the **per-benchmark leaderboard**
(rankings aren't comparable across benchmarks, since the ladders differ).

The main page filters to **one benchmark at a time** (tabs above the
leaderboard); the new-run dialog picks the benchmark from the same manifest.
Legacy official runs (recorded before the split) map to `pokebench-full` — they
were scored on the full ladder.

---

## The gate ladder

The ladder is fully data-driven — `configs/checkpoints-firered-v1.yaml`, an
ordered list of gates. Each gate has a detector **type** and a **signature**:

- `map` — `{map_group, map_num}`: stamped when the player first enters that map
- `flag` — `{flag_id}`: stamped when a story-flag bit is set
- `var` — `{var_id, min_value}`: stamped when a game variable reaches a value
- `party` — `{min_count}`: stamped when party size reaches a count

`deadline_turn` is the turn by which the gate must be stamped. In an **official**
run (`enforce: true`) a missed deadline **terminates** the run
(`termination_reason: missed_gate:<id>`). A `null` deadline is observe-only
(scored, never fatal). An optional `cross_check` signature is logged for
diagnostics only — it never decides.

### FireRed v1 ladder (Bedroom → Thunder Badge)

| # | Gate | Type | Deadline |
|---|---|---|---|
| 1 | `left_bedroom` — Left the bedroom | map | T25 |
| 2 | `left_house` — Stepped outside in Pallet Town | map | T50 |
| 3 | `oaks_lab_entered` — Entered Oak's Lab | map | T75 |
| 4 | `starter_chosen` — Chose a starter | flag | T100 |
| 5 | `rival1_done` — First rival battle done | flag | T125 |
| 6 | `route1_reached` — Reached Route 1 | map | T150 |
| 7 | `viridian_reached` — Reached Viridian City | map | T200 |
| 8 | `parcel_delivered` — Received Oak's Parcel at Viridian Mart | var | T250 |
| 9 | `pokedex_received` — Received the Pokédex | flag | T300 |
| 10 | `viridian_forest_reached` — Entered Viridian Forest | map | T350 |
| 11 | `pewter_reached` — Reached Pewter City | map | T400 |
| 12 | `brock_defeated` — Defeated Brock (Boulder Badge) | flag | T500 |
| 13 | `route3_reached` — Reached Route 3 | map | T550 |
| 14 | `mt_moon_entered` — Entered Mt. Moon | map | T600 |
| 15 | `mt_moon_cleared` — Cleared Mt. Moon (Route 4) | map | T700 |
| 16 | `cerulean_reached` — Reached Cerulean City | map | T700 |
| 17 | `cascade_badge` + `bills_errand_reached` — Misty / Bill's errand (**any order**) | flag / map | T800, T900 |
| 18 | `vermilion_reached` — Reached Vermilion City | map | T1000 |
| 19 | `ss_anne_boarded` — Boarded the S.S. Anne | map | T1100 |
| 20 | `thunder_badge` — Defeated Lt. Surge (Thunder Badge) | flag | T1200 |

The first-badge benchmark carries **no cumulative deadlines** since 2026-09-10:
every gate is bounded by its per-leg cap alone (below), and a gate with a cap
and no `deadline_turn` is enforced. v1 ran cumulative deadlines 20/30/40/50/65/
75/100/120/150/200/300/400, and v1.1's first cut (2026-09-09) 30/40/50/65/85/
100/150/180/220/300/500/600; runs stamped `pokebench-v1` stay on the board
because a run that met the tighter deadlines meets the looser ones. The full
benchmark above keeps its cumulative deadlines, Boulder Badge at T500.

**Per-leg turn caps (v1.1, 2026-09-09).** Each first-badge gate also carries
`leg_cap_turns`, the most turns a run may spend on the leg into that gate,
counted from the previous gate's stamp: 30 / 30 / 30 / 30 / 30 / 30 / 100 / 50 /
100 / 150 / 300 / 100, sum 980 (2026-09-10; no leg under 30. The 2026-09-09
first cut was 30 / 20 / 20 / 20 / 30 / 20 / 60 / 30 / 60 / 100 / 200 / 100
alongside cumulative deadlines, and ended two gemini-3.5-flash-lite runs on the
20-turn lab leg and the 30-turn rival leg). Spending the cap ends the run with
`termination_reason = "leg_cap:<id>"`. Where a ladder carries both a cap and a
cumulative deadline they are independent and the first to fire ends the run.
The motivation is the Viridian Forest maze: under cumulative deadlines a run
that reached the Forest at turn 180 had 320 turns to reach Pewter, and a fast
opening banked even more. Calibration: the first full clear
(gemini-3.8-flash(medium)) spent 1/3/2/3/8/3/13/14/17/39/112/22 turns per leg.
The scorecard's gate entries carry `leg_cap_turns` and `leg_turns`.

The parcel checkpoint retains the legacy ID `parcel_delivered` for saved-run
compatibility. Its detector now checks the Mart scene variable for ≥1 (pickup),
rather than ≥2 (the end of Oak's Pokédex scene). Historical stamps were recorded
under the old detector and are not retroactively changed.

**Multigate (#17):** Nugget Bridge opens the moment you reach Cerulean, so the
agent may beat Misty first *or* fetch Bill's S.S. Ticket first. The rung holds
two gates with a progressive deadline list `[800, 900]`: the first of the two by
T800, both by T900.

### Stopping at a story event

A **casual** run can borrow the ladder as a finish line instead of a scorecard:

```bash
pokemon run --model "claude-opus-5(medium)" --turns 400 --stop-at viridian_forest_reached
pokemon queue add --kind casual --stop-at pewter_reached "claude-opus-5(medium)"
pokemon queue events                 # the id list
```

…or the **Stop at** picker in the new-run dialog. The run ends the moment the
referee stamps that gate — so "100 turns" and "until Viridian Forest" are both
expressible, and setting both means whichever lands first.

Three things worth knowing:

- **The event ids are the gate ids above** — the same RAM signatures the
  benchmark uses, so detection is exact rather than inferred from the screen.
  Every gate is selectable individually, including the two multigate members.
- **The ladder rides along observe-only** (`enforce: false`). Deadlines are
  *not* armed, so no pace gate can kill a casual run on its way to the event.
  The only two ends are the event and the turn cap.
- **It is still a casual run.** It shows gate progress in the HUD and History
  (a side effect of carrying the ladder), but leaderboard eligibility keys on
  `kind == official`, so it can never post a score. Official runs ignore
  `stop_at` entirely — a benchmark ends at its own ladder.

---

## The referee

The referee (`src/referee/`) polls the emulator's memory out-of-band on a read
that's invisible to the agent. Each poll it dereferences SaveBlock1 (with a tear
guard for DMA shuffling) and snapshots: current map, the story-flag bitfield, the
vars array, and party count. For every not-yet-stamped gate it runs the detector
against that snapshot; a first match records a **first-seen stamp** with the turn
number. Stamps persist to `referee_state.json`, so they survive a `--continue`.

**Termination paths:**

- **Completed** — the final rung is reached → the run stops, `status: completed`.
- **Terminated** — a gate with an integer deadline goes unstamped past its turn,
  and `enforce: true` → `status: terminated`, `termination_reason: missed_gate:<id>`.
- **Observe-only** — with `enforce: false`, missed deadlines are still stamped and
  scored but never end the run.

---

## Scoring & the leaderboard

A run's score is **"farthest, fastest"**:

- **primary:** `progress` = `gates_reached` + the fraction of the **current leg**
  walked — higher is better. The fraction is `1 − d_min / D` where `D` is the
  number of walking steps from where the leg opened (the tile the previous
  gate was stamped on) to the next gate's tiles, and `d_min` is the steps from
  the *closest* position the run ever polled during that leg. Steps are path
  distance on the FireRed walk graph (`data/firered-walkgraph.json`, built from
  pret's collision, ledge, warp and connection data by
  `scripts/build_walkgraph.py`), so trees, water and the Viridian Forest maze
  count as the detours they are, ledges are one-way, and doors join maps.
  Only positions polled *after* the previous gate stamped count; a lab visit at
  turn 30 earns nothing on the Pokédex leg that opens at turn 110. The first
  leg opens on a read taken *before* turn 1's action, so its length is the
  walk from the canonical start tile, the same for every run.
  An **unfinished** leg is capped at 0.95 (`OPEN_LEG_FRACTION_CAP`,
  2026-09-11): standing on the target's tile without the stamp is not the gate,
  so a run that died at Brock's feet reads 11.95, not 12.0, and never ties a
  run that beat him. Only a stamp closes the leg and counts the whole gate.
- **tiebreak:** `turns` (total game turns) — fewer is better

Why the fraction exists: two official runs terminated at the same gate carry
the *same* turn count (that gate's deadline), so before 2026-09-09 they could
not be told apart. A run that recorded no positions (every run before the
tracker shipped, or one without a walk graph) has `progress = null` and ranks
on its gate count (`RunSummary.rank_score`).

The leaderboard (`src/app/derivations.py`) keeps **only leaderboard-eligible runs**
(official, status completed or terminated), takes the **best run per model**
(max progress, then min turns), and sorts winners by progress descending, then
turns ascending. So reaching gate 12 in 500 turns beats reaching gate 12 in
600; both beat reaching gate 10; and dying 60% of the way from gate 3 to gate 4
beats dying 20% of the way there.

Each run's flat index row (`RunSummary`) carries `furthest_gate`,
`furthest_gate_turn`, `gates_reached`, `total_gates`, `progress`, `leg_gate`,
`leg_fraction`, `leg_distance_min`, `leg_distance_open`, `turns`, `duration_s`,
`total_cost_usd`, and `termination_reason` — the fields the History and Report
views render. The referee's per-leg detail (steps walked as a lower bound,
efficiency = optimal ÷ walked, distinct tiles seen) lives in
`run_summary.json["referee"]["progress"]` and on the report page. The agent
never sees any of it — the referee stays out-of-band.

---

## The agent

Official runs use **config-5.1, the append-and-compact single agent** (config-5.0 until 2026-09-09; 5.1 drops the forced previous-turn verdict from the gameplay output) — one
self-directed agent on one append-only conversation, with periodic self-written
handovers instead of a sliding window. See
[Append-and-compact agent](append-agent.md) for how it works, what it records,
and its provider contracts. It has no TaskMaster: `append_compact` is
self-directed and `_validate_append_config` refuses a config that enables one.

### Legacy: TaskMaster + Player (config-3.13 / 3.x)

config-3.13 was the frozen official config until 2026-09-07 and is still
runnable as a casual config. It uses a **two-level agent loop**:

- **TaskMaster** (`src/agent/task_master.py`) — a strategic meta-agent. It doesn't
  press buttons. Each handoff it (a) **rates the previous task** — cross-checking
  the Player's self-assessment against the start/end screens (status: succeeded /
  failed / partial / other) — and (b) **issues the next task** (title,
  description, success criteria), informed by the run goal, the Player's memory, a
  rolling window of past tasks, and a web-research tool (Perplexity Sonar).
- **Player** (`src/agent/agent.py`) — the reasoning LLM that actually plays. Each
  turn it sees the screenshot + OCR text + its memory dictionary + the current
  task, then emits button inputs, reasoning, and memory updates. It runs until it
  hands back to the TaskMaster or hits the per-task turn budget.

In the run log this shows up as `task_started{N}`, `task_master_trace{N}`, and
`task_completed{N}` events; every Player turn carries a `task_index` so the
Report view buckets turns under their task. config-3.13 was the first frozen
official config with TaskMaster enabled — and the last: config-5.0 replaced it
as official on 2026-09-07, and TaskMaster does not exist on that harness.

See also: [Control center](control-center.md) · [CLI reference](cli.md).
