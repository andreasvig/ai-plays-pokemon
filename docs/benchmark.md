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
| Config | `config-3.13.yaml` (frozen) | you choose |
| Benchmark | you choose (easy / first-badge / full) | n/a |
| Goal | the benchmark's goal (overrides the config's) | the config's |
| Model | you choose | you choose |
| Max turns | none — gate deadlines bound it | you choose |
| Gate ladder | the benchmark's, enforced (`enforce: true`) | none |
| Leaderboard | eligible (if completed/terminated), per benchmark | never |
| Continues | n/a | always casual |

**Official** is the comparable benchmark: same frozen config, same start save
(`configs/saves/pokebench-v1`), for every model — the only variable is the
model. You pick *which benchmark* the run plays (see below); that selects the
gate ladder **and** the goal. A cancelled official run is **voided** (it never
reaches the leaderboard). **Casual** runs are for experimentation — pick any
config and turn budget; they run free of gates and never score.

---

## The benchmarks

A **benchmark** bundles an *overall goal* (the meta-goal the agent plays toward,
shown in the UI) with its own *gate ladder*. The registry is
`configs/benchmarks.yaml`; three ship today, all on FireRed:

| id | Goal (overall) | Ladder file | Final gate |
|---|---|---|---|
| `pokebench-easy` | Reach Viridian City | `checkpoints-firered-easy.yaml` | `viridian_reached` (7 rungs) |
| `pokebench-first-badge` | Earn the Boulder Badge (beat Brock) | `checkpoints-firered-firstbadge.yaml` | `brock_defeated` (12 rungs) |
| `pokebench-full` *(default)* | First three badges → Thunder Badge | `checkpoints-firered-v1.yaml` | `thunder_badge` (20 rungs) |

The easy / first-badge ladders are **self-contained prefixes** of the full
ladder, kept as **separate files** so each benchmark's gates can be edited
independently. Reaching a benchmark's *final* rung **wins** the run
(`status: completed`); missing any rung's deadline terminates it.

**Goal override.** When an official run is queued, the executor loads the chosen
benchmark and (a) injects its ladder as the run's enforced `referee` block, and
(b) overwrites the frozen config's `task.goal` with the benchmark's `goal` — so
the same `config-3.13` plays toward a different objective per benchmark. The
benchmark id is stamped onto the run and drives the **per-benchmark leaderboard**
(rankings aren't comparable across benchmarks, since the ladders differ).

The main page filters to **one benchmark at a time** (tabs above the
leaderboard); the new-run dialog picks the benchmark from the same registry.
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
| 8 | `parcel_delivered` — Delivered Oak's Parcel | var | T250 |
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

**Multigate (#17):** Nugget Bridge opens the moment you reach Cerulean, so the
agent may beat Misty first *or* fetch Bill's S.S. Ticket first. The rung holds
two gates with a progressive deadline list `[800, 900]`: the first of the two by
T800, both by T900.

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

- **primary:** `gates_reached` (count of stamped gates) — higher is better
- **tiebreak:** `turns` (total game turns) — fewer is better

The leaderboard (`src/app/derivations.py`) keeps **only leaderboard-eligible runs**
(official, status completed or terminated), takes the **best run per model**
(max gates, then min turns), and sorts winners by gates descending, then turns
ascending. So reaching gate 12 in 500 turns beats reaching gate 12 in 600, and
both beat reaching gate 10.

Each run's flat index row (`RunSummary`) carries `furthest_gate`,
`furthest_gate_turn`, `gates_reached`, `total_gates`, `turns`, `duration_s`,
`total_cost_usd`, and `termination_reason` — the fields the History and Report
views render.

---

## The agent: TaskMaster + Player

Official runs (config-3.13) use a **two-level agent loop**:

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
Report view buckets turns under their task. config-3.13 is the first frozen
official config with TaskMaster enabled.

See also: [Control center](control-center.md) · [CLI reference](cli.md).
