# Wasted inputs — what a press bought, and charging the walls

> Plan for the 2026-09-15 step. Andreas: "do we detect inputs which just walk into a wall for 3 turns
> as 3 inefficient inputs?" … "do we distinguish between movement eaten by turning, walking into a
> wall, interrupted by a battle, eaten in a dialogue box? and is it just movement or also a/b presses?
> my ideal thing is to make the presses eaten by walking into walls be part of the efficiency
> calculation" … "more granularity for the wasted inputs, do A and C, and keep the full breakdown in
> the full report somewhere."
> Builds on `artifacts/route-fidelity/plan.md` (R8: the run folder is raw, every number re-derived at
> projection time).

## 0. Where it stood

`trace.derive()` counted `inputs_lost`: a direction press, outside battle, after which the tile was
unchanged. Three defects:

1. It lumped **wall bumps, turn-to-face and dialogue-eaten presses** into one number. Only battle was
   genuinely separated (`battle_inputs`, and the `quiet` guard excludes a press that starts one).
2. **A/B presses were never counted** — `inputs_lost` tests the input name against `DIRECTIONS`.
3. It **went nowhere**: logged in `turn_input_trace`, read by nothing. No published number used it.
   A wall press adds 0 to `steps_walked`, so it was free. The mark run threw away ~580 presses at no
   cost to its 0.292 efficiency.

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| W1 | Classify every input into one of eight buckets at derive time: moved, battle started / in battle / battle ended, wall hit, turn-to-face, blocked-by-actor, blocked-unknown, idle A/B. | Andreas asked for all four distinctions by name. Every one is derivable from data already in `events.jsonl`; no new memory reads, no new runs. |
| W2 | A press is a WALL only when the player was **already facing that direction** and the walk graph gives the target tile no edge. | Andreas picked the strictest option of four. In FireRed a direction press while facing elsewhere only turns the player — a legitimate, necessary input (facing a sign or an NPC). The first press in a direction is therefore never charged. |
| W3 | Never charge a tile whose graph node has a **cross-map edge** (a warp). | Control 2026-09-15: the naive rule charged 9 presses at door tiles — PalletTown (16,13), Oak's lab exit, both Viridian Forest entrances, PewterCity (15,16) — where the player DID move in that direction moments later. A door's target is on another map, so it is absent from the same-map neighbour set and reads as a wall. With the rule: 0 contradictions over both traced runs. |
| W4 | A wall press costs **one step**, charged in a new per-leg `walls_hit`; `steps_walked` keeps holding only real steps. `efficiency = d_open / max(steps_walked + walls_hit, d_open)`. | Andreas picked one-step over frame-accurate. Keeping the charge in its own field is the raw-vs-derived rule of R8: the pure-path number stays recoverable and a future reweighting is a projection change, not a migration. |
| W5 | Blocked-by-actor (tile open, press eaten by a textbox / NPC / script) is measured and shown but **not charged**. | It cannot be attributed. Pressing a direction into an open textbox is model error; an NPC stepping into the path is not, and the trace has no textbox state to tell them apart. |
| W6 | Idle A/B is measured and shown but **not charged**, and labelled as unattributable. | The trace samples x/y/map, the battle bit and the battle counter. An A that advanced a dialogue box and an A mashed at nothing are identical in it. Separating them needs a script-context flag in `TRACE_SPEC` and would only apply to future runs. |
| W7 | The breakdown lands in `referee.inputs` (run level) and per-leg `walls_hit`; the report gets a section, the board a wasted-input column. The column renders **only for runs with a per-input trace**, and the legend says how many. | Andreas picked report + board. The trace went live 2026-09-14: 2 of 25 published runs have one. A column that renders 0 for a run that never measured it would be a ghost bar. |

## 2. Measured before building (mark run, gemini-3.8-flash(high), 310 turns, 4158 inputs)

```
2031  48.8%  moved
 687  16.5%  pressed during a battle
 577  13.9%  WALL, already facing it            ← charged
 381   9.2%  tile open — eaten by dialogue/NPC/script
 248   6.0%  A/B, nothing moved
 107   2.6%  battle started on this press
  58   1.4%  wall, first press (turn-to-face)
  46   1.1%  battle ended on this press
   8   0.2%  moved on an A/B (scripted walk)
```

Worst turn: 22 — 20 inputs, 18 lost, 15 consecutive `U` into the Route 1 ledge at (14,17), a node with
neighbours left, right and down and nothing up. Efficiency 0.292 → ~0.23 once the walls are charged.

## 3. Not in this step

- A textbox / script-context flag in `TRACE_SPEC` to split useful A/B from mashed A/B (future runs only).
- Sampling the player's facing byte instead of inferring it from the previous direction press
  (the inference is conservative: it can only under-charge).
- Charging blocked-by-actor. Needs the flag above to be attributable.

## 4. Found while building (additive, not a redefinition)

| # | Defect | Fix |
|---|---|---|
| D1 | The naive wall test charged 9 presses at door tiles where the player demonstrably walked off in that direction moments later: a warp's target is on another map, so it is absent from the same-map neighbour set. | `passable_in` returns None — "cannot say" — for any node with a cross-map edge. Nothing is charged there. |
| D2 | It also called 4 of 1467 observed moves walls, every one a ledge: a ledge edge targets the tile TWO away, so an adjacent-only test missed it. | Any edge along the axis counts, not just the 4-adjacent tile. Control now: 0 of 1467. |
| D3 | The report page reads `summary.json` and the board reads the row, but only the row went through `replay`. They had disagreed since the replay shipped that morning — the mark run's published summary carried 2858 leg steps (596 phantom blackout warps) against the row's 2262. | `public_summary_text` replays the referee block, and `refresh_rows` rewrites `summary.json` alongside the row and `route.json`. |
| D4 | The first census mixed units: the row labelled "moved" showed `overworld_steps` (TILES — one scripted press can move eight) while every other bucket counted presses, and the press a battle ends on was in no bucket at all. The card stated a total that did not add up. | The buckets are now a PARTITION of the turn's inputs (`moved_inputs`, `battle_edge`, `unclassified` added), asserted by `tests/test_trace.py::test_the_buckets_partition_every_input`. On the mark run: 4158 = 4158. |

## 5. Where it landed

- `trace.derive` classifies; `WalkGraph.passable_in` decides wall vs open vs unknown.
- `_Leg.walls_hit` accumulates, `_Leg.charged_steps()` = `steps_walked + walls_hit`, and only
  `efficiency()` divides by the charged figure — `steps_walked` still holds tiles walked.
- `battle_stats.movement` returns `steps` (walked) and `charged` separately; `walls`/`charged` are
  **None**, not 0, when no leg carried the field.
- Row fields: `walls_hit`, `charged_steps`, `wall_rate`, `input_breakdown`. `PROJECTION_VERSION` 13.
- Report: a "Where the inputs went" card, public. Board: a "Presses into a wall*" bar card that
  omits untraced runs and says how many in its note.

Mark run, before → after: efficiency **0.292 → 0.233**, steps walked unchanged at 2258, 576 charged.
