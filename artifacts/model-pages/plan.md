# Model pages — plan (2026-09-14, evening)

> Status: **DECIDED 2026-09-14 evening — build next.** Requested by Andreas 2026-09-14:
> "start working on the per model side … inspired by Artificial Analysis, except all thinking
> levels share a side."

## 1. Goal

One page per model (all its thinking levels together) that replaces the run history:

1. Opens with the three headline cards — Performance, Time per task, Cost per task — over the
   whole field, with **this model's levels highlighted and every other model faded**. All of this
   model's levels are forced in, even the ones the home board collapses away.
2. Below: a **collapsible list of the model's thinking levels**, highest level first, each row
   carrying the old leaderboard's top-level stats. Levels the config knows but nobody has run are
   greyed "(not benchmarked yet)". Expanding a level shows the run: per-gate turns and wall time,
   per-leg movement efficiency, the recording — the History item, compacted.
3. This removes the Run history view from the public site.
4. Publishing gets a guard: a run for a model+level that is already on the board is refused until
   the old one is unpublished.

## 2. What exists today (verified in the repo, 2026-09-14)

- Routes (`src/dashboard/web/src/lib/router.svelte.js`): `/`, `/spectate`, `/history`,
  `/history/<run_id>` (Report), `/about`, `/methods`, `/changelog`. No model route.
- Home = `Leaderboard.svelte` (hero + `HeadlineCards`) + `Sections.svelte` (Price / Speed /
  Efficiency cards). Bars call `oninspect(row)` → the run's Report page.
- `board.js`: `baseModel(alias)` strips "(level)"; `collapseBest(rows)` keeps the best-ranked row
  per base model (the home board's collapsed view, toggle shows all levels). `BarCard.svelte` has
  no faded/highlight state; `entries` carry `{row, height, label, complete, partial, tip}`.
- `History.svelte` (318 lines): row per run with model, date, config, completion + furthest gate,
  turns, time, cost, and a recording player (`video_url` on published rows). `Report.svelte`
  (1080 lines): gate table (name, stamp turn, leg turns / cap), legs table (local only), video,
  trace feed.
- Published data: `data/leaderboard.json` (rows: gate_turns, progress.legs with d_open /
  steps_walked / distance_now, movement + battle fields, video_url, has_recording, …),
  `data/benchmarks.json`, `data/runs/<run_id>/summary.json` (session, referee.checkpoints, cost)
  and optional `trace.json`. **No model catalog is published**, so the site cannot list levels
  nobody ran.
- Thinking levels per model live in `configs/models.yaml` → `thinking_levels`, ordered HIGHEST
  FIRST (`src/config.py:model_thinking_levels`). `reasoning_type: none` models have no levels.
- Per-gate wall time is not on the row. `events.jsonl` has a timestamped `turn_start` per turn,
  so time-at-gate = timestamp(turn_start of the gate's stamp turn) − timestamp(turn 1); cost at
  gate likewise from `turn_usage`. Both are computable at publish / `--refresh-rows`.
- Publish (`src/app/publish.py` ~l.700-750) upserts the row by `run_id`; nothing checks the
  model+level. The board currently holds one duplicate: `gemini-3.5-flash-lite(minimal)` twice.

## 3. Page anatomy

```
/models/<base-model>                       e.g. /models/gemini-3.8-flash
┌ header: vendor mark · model name · openrouter id · n levels benchmarked / m known
├ Performance | Time per task | Cost per task      (3 BarCards, field-wide)
│   this model's rows: full colour, all levels forced in
│   every other model: faded (best level only, as the collapsed home board)  ← Q2
├ Thinking levels  (collapsible rows, highest level first)
│   ▸ high     ██████ 100%  369T   4.1 min/task  $0.21/task   62% moves  6.1 T/trainer  Sep 12 · config-5.1
│   ▸ medium   ██████  87%  512T   …                                                        ← Q5 columns
│   ▸ low      (not benchmarked yet)                      greyed, from configs/models.yaml
│   ▾ minimal  … expanded:
│       gate table: gate · turn · wall time at gate · leg turns/cap · leg movement efficiency
│       recording (video player, as History has today) · "open full report" link            ← Q6
└ methods footnote: same projection rules as the home board, link to /methods
```

Highlight heuristic (Q3): faded bars keep their name label and drop the value label; opacity ~0.3;
the model's own bars keep both. The same heuristic is reused wherever a level's run is placed in
a field-wide card.

## 4. Data changes (publish side)

1. **`data/models.json`** — exported from `configs/models.yaml` on every publish: `[{model,
   openrouter_id, vendor, thinking_levels (ordered), reasoning_type}]`. Lets the page list
   "(not benchmarked yet)" levels. Only models that have at least one published row are listed
   (the catalog also holds models never run — Q7).
2. **Row additions** (projection v8, computed from `events.jsonl` in `--refresh-rows` and on
   publish): `gate_times_s: {gate_id: seconds since turn 1}`, `gate_costs_usd: {gate_id: usd}`.
   Legs already carry `d_open / steps_walked / distance_now`; the per-leg movement efficiency is
   `battle_stats.movement(...)["legs"]` — publish it as `movement_legs` on the row so the page
   does not recompute.
3. **Duplicate guard** in `publish_run`: if `leaderboard.json` holds a row with the same `model`
   alias (model + level) and a different `run_id`, refuse with
   `already on the board: <run_id> — run "pokemon unpublish <run_id>" first`. `--dry-run` reports
   it too. One-off: resolve today's duplicate (Q8).

## 5. Frontend changes

- Router: `/models/<base>`; `App.svelte` view `model`; TopBar loses "History" on the static site
  (Q9 decides the local app). Home bars' `oninspect` → model page (Q1).
- `BarCard.svelte`: `highlight` prop (`(row) => boolean`); faded entries get class `.faded`.
- `board.js`: `modelPageSeries(rows, base)` = `headlineSeries` over `collapseBest(others) ∪
  allLevels(base)`, with `highlight` marking the base's rows; `levelRows(rows, catalog, base)` =
  the catalog's levels in order, joined with the published rows (missing → `{level, absent:
  true}`); level order from `models.json`.
- New `ModelPage.svelte` (header, 3 cards, `LevelList.svelte`), `LevelRow.svelte` (collapsed
  stats), `LevelDetail.svelte` (gate table with time/cost/leg efficiency, video). Gate table and
  video player are lifted out of `Report.svelte` / `History.svelte` into shared components rather
  than copied.
- Public site: `History.svelte` route removed; `/history/<id>` Report kept or dropped per Q6.

## 6. Tests and verification

- JS: `modelPageSeries` forces all levels of the base in and fades the rest; `levelRows` orders by
  catalog and marks absent levels; a `reasoning_type: none` model yields one level.
- Python: `gate_times_s` from a fixture `events.jsonl`; duplicate guard refuses the second
  publish and names the first run; `--dry-run` reports it; `unpublish` then publish succeeds.
- Live: publish, wait for the bundle, render `/models/gemini-3.8-flash` (4 benchmarked levels +
  none missing) and `/models/claude-opus-5` (1 of n levels, rest greyed); check the faded bars,
  the level order, an expanded level's gate table and video.

## 7. Decisions (Andreas, 2026-09-14 evening)

| # | Question | Decision |
|---|---|---|
| 1 | Clicking a bar on the home board | **A** — opens the model page `/models/<base>`. |
| 2 | The faded field behind the model's bars | **A** — other models at their best level only (as the collapsed home board) + every level of this model forced in. |
| 3 | What an expanded level shows | **A** — the run's gate table (turns, wall time at gate, leg turns / cap, leg movement efficiency) and its recording. **Below the level list** the page repeats every other view of the home page (Price, Speed, Efficiency cards) with this model's levels highlighted and the field faded; the cards keep their eligibility rules, so a level that has not reached far enough for a reliable score is still left off those cards. |
| 4 | Collapsed level-row columns | **B** — level, completion (bar + furthest gate), turns, time, cost. |
| 5 | The full run report page | **B** — removed from the public site together with History; the expanded level is the only run view, and the recording lives there. The public trace feed goes with it (the `trace.json` publish stays available for the local app). |
| 6 | Today's duplicate gemini-3.5-flash-lite(minimal) | **A** — keep the run that got further, unpublish the other, before the guard lands. |
| 7 | History in the local control center | **A** — removed on the public site only; local keeps History (continue, delete) and the Report. |

Defaults taken without asking: faded bars keep the model name and drop the value label (opacity
~0.3); `data/models.json` lists only models with at least one published row.

## 8. Build order

1. Publish side: `models.json` export, row fields `gate_times_s` / `gate_costs_usd` /
   `movement_legs` (projection v8), duplicate guard + tests; resolve the duplicate (6A); refresh
   rows.
2. `board.js`: `modelPageSeries`, `levelRows`, highlight plumbing through `headlineSeries` /
   `secondarySeries` / `battleSeries` (the model's rows always in, `highlight` flag) + JS tests.
3. Components: `BarCard` faded state; lift the gate table and video player out of Report/History
   into shared pieces; `ModelPage`, `LevelList`, `LevelRow`, `LevelDetail`; router + TopBar;
   home bars → model page; History/Report hidden when `STATIC`.
4. Publish, wait for the bundle, render two model pages live (one with every level benchmarked,
   one with greyed levels), check the faded field, the level order, an expanded level's gate table
   and video, and that a sub-Route-1 level is absent from the lower cards but present in the list.
