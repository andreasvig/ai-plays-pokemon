# Granular progress between gates — research and proposal

> Status: BUILT 2026-09-09 (same day; see §8b and §9). Written from Andreas's question:
> *"can we get more granularity in the benchmark, between checkpoints? I am
> thinking of using grid position to give a more nuanced % score between 0 and
> 1 — ideally something like: what was the furthest grid position to the goal
> gotten? We could use a pathfinding algorithm to see if the model had fewer
> steps, or coming from the position of the last checkpoint."*
>
> Companion: `docs/benchmark.md` (the locked "farthest, fastest" metric),
> `configs/checkpoints-firered-firstbadge.yaml` (the 12-gate ladder),
> `src/referee/referee.py` (the only out-of-band memory reader).

## 0. Verdict in three lines

1. **Feasible and cheap at the read layer.** The referee already decodes the
   player's x/y on every poll (`src/referee/referee.py:646-652`) and throws
   them away. Recording them is a ten-line change.
2. **The score needs a walkable map graph, and the repo has none** — but
   pret's `pokefirered` decompilation has every layout, collision attribute,
   warp and map connection as data files (verified 2026-09-09: 367 layouts,
   `map.bin` + `metatile_attributes.bin` + `map.json` per map). Build the graph
   offline, commit it as JSON.
3. **This is the only thing that can break the ties the board has today.** Two
   official runs terminated at the same gate carry the *same* turn count (the
   missed gate's deadline), so the "then fewest turns" tiebreak is vacuous
   between them. The three glm runs online: 20 / 50 / 100 turns — each exactly
   the deadline of the gate it missed.

## 1. What exists (verified 2026-09-09)

| Layer | State | Where |
|---|---|---|
| Position read | `player_x`/`player_y` (s16, SaveBlock1 +0x0/+0x2) and `map_group`/`map_num` (+0x4/+0x5) decoded every poll | `referee.py:48-51`, `:646-652` |
| Position recorded | **Nowhere.** Not in `events.jsonl`, `state.json`, `referee_state.json` or `run_summary.json`. 232 local runs have zero position data | census on the 2026-09-09 gemini run |
| Poll cadence | Once per turn after the screen settles, plus TaskMaster-handoff turns | `src/agent/turn.py:1869-1945` |
| Memory access | `READMEM:<addr>:<len>` is unrestricted, already used on IWRAM, EWRAM and cartridge ROM (`0x080000AC`) | `src/emulator/emulator.py:144-171`, `src/app/supervisor.py:290` |
| Map data | **None** — no tilemaps, collision, warps, connections, names, graph | grep across the repo |
| Scoring shape | `gates_reached: int` is the primary key everywhere: `RunSummary`, leaderboard sort `(-gates, turns)`, history sort, frontend `gates.js` | `src/app/models.py:139`, `src/app/derivations.py:39-47`, `src/app/projection.py:175-181` |
| Agent blindness | Referee is module-isolated from the agent; the agent never sees memory | `src/agent/turn.py:1013-1017` |

The first-badge ladder has 12 gates. Six are **map** gates (bedroom → Pallet →
Oak's Lab → Route 1 → Viridian → Viridian Forest → Pewter) and six are
**flag/var** gates (starter, rival, parcel, Pokédex, Brock). A flag gate has no
map signature, but every one of them still has a *locus*: the tile where the
event fires (the Pokéball table in Oak's Lab, the Mart counter in Viridian,
Brock's tile in Pewter Gym). Position scoring needs that locus per gate, added
to the ladder YAML as an optional `locus: {map_group, map_num, x, y}`.

## 2. Why raw position is not a score

- **Coordinates are map-local.** (x, y) on Route 1 and (x, y) in Viridian are
  incomparable. There is no world-space transform; distance must be *path*
  distance over a graph whose edges include warps (doors, stairs) and map
  connections (Pallet ↔ Route 1 with an x-offset).
- **Euclidean and Manhattan lie.** Pallet → Route 1 is straight north, but
  Viridian → Pewter walks *around* through Route 2 and the Forest; a
  straight-line metric would reward standing at the wrong fence.
- **Progress is non-monotone within a leg.** Gate 9 (Pokédex) is back in
  Oak's Lab in Pallet; gate 10 is the Forest entrance north of Viridian. The
  optimal route walks south then north. Any metric must take the *best* point
  reached, not the *last*.
- **Backtracking is normal play.** A model that reaches the Viridian Mart, gets
  the parcel, and walks back to Pallet is doing the right thing. Min-distance-
  ever-reached handles it; final position does not.

## 3. Metric options

### A. Path-distance progress (recommended)

For the leg from gate *k* (reached) to gate *k+1* (not reached):

```
D      = shortest_steps(locus_k, locus_{k+1})                 # constant per leg, from the graph
d_min  = min over every polled position p of shortest_steps(p, locus_{k+1})
frac   = clamp(1 - d_min / D, 0, 1)
score  = gates_reached + frac                                  # in [0, 12)
completion% = score / total_gates
```

- **The leg window.** `d_min` ranges only over positions polled *after* gate
  *k* was stamped. Earlier positions never count. On the Pokédex leg (parcel
  delivered → Oak's Lab) the model stood in Oak's Lab at turn ~30, but that
  was before the task existed; only lab visits after the parcel stamp score.
  Without this rule every leg that revisits a place would start near 100%.
  (Andreas, 2026-09-09.)
- **Barriers are real.** Steps are counted over the walkable graph, so trees,
  walls, water and buildings are impassable and ledges are one-way edges. In
  Viridian Forest a tile five squares from the north exit as the crow flies
  can be sixty steps away through the maze, and scores as sixty. This is the
  whole reason for path distance over Euclidean/Manhattan. NPC trainers are
  not barriers (you walk around them); tall grass is not a barrier.
- **One graph across map borders.** Warps (doors, stairs) and map connections
  are edges like any other, so the distance field for "Route 1" extends into
  Oak's Lab: from the Pokéball table it is roughly steps-to-the-lab-door + the
  door + steps across Pallet to the north edge. Every step toward the lab door
  lowers `d_min` and counts. The Route 1 leg opens when the rival gate stamps,
  which happens *inside the lab*, so this case is the rule, not an edge case;
  the bedroom → 1F → outside legs work the same way through the stairs.
  (Andreas, 2026-09-09.)
- **Off-graph maps.** A position on a map the graph does not contain (the
  model wandered into a house the builder skipped) is not an error: `d_min`
  keeps its previous value and the turn is logged as off-graph. Cheap
  insurance: include every indoor map of Pallet, Viridian, Route 2 and Pewter
  in the build, not just the ones on the ideal route.
- Per-position BFS is cheap: the graph for Pallet → Pewter is a few tens of
  thousands of nodes; precompute one distance field per gate locus (a dict
  `node → steps`) and the per-turn cost is a lookup.
- `d_min` is the literal answer to "furthest grid position to the goal gotten".
- Robust to the route taken: any walkable path counts, not a golden one.
- Known weakness: a model that wanders *past* a closed door it cannot enter
  (e.g. stands beside the Mart before it opens) scores nearly full for that leg
  yet has not triggered the event. Acceptable — it *was* one step away — and
  the flag gate itself still decides completion.

### B. Golden-path projection

Precompute the shortest path *P* from `locus_k` to `locus_{k+1}`; progress is
the furthest index along *P* the player has come within *r* tiles of. Simpler
to explain ("62% along the route") but penalises legitimate alternative routes
(the Forest has several lanes; Mt. Moon later has three floors). Use only as a
cross-check on A, or drop.

### C. Exploration coverage

Distinct tiles visited, or new-tile rate per turn. Not goal-directed — rewards
wandering — so **not** a score. Worth logging as a diagnostic (it quantifies
the "re-hypothesising where the warp tile is every turn" flailing seen in the
ten-turn samples).

### D. Log position only, keep the integer score

Zero benchmark change: add the telemetry, decide the metric later against real
traces. This is the "remove the capability" option and is also step 1 of every
other option, so it is not really a choice against A — it is the first phase.

## 4. The efficiency idea ("did the model take fewer steps?")

`efficiency_k = D_k / steps_walked_k`, per completed leg, in (0, 1].

The catch is *steps walked*. Position is sampled once per turn, and a turn
presses up to ~10 buttons, so:

- **Lower bound:** path distance between consecutive turn samples (the model
  walked at least that far).
- **Upper bound:** count of directional presses in the turn's `button_sequence`
  (some are blocked by walls and move nothing — those are exactly the waste
  worth measuring, but they are indistinguishable from real steps in the log).
- **Exact:** would need position after every press. That means either Lua
  recording position per queued button (a small change, but it breaks the
  "Lua stays dumb" rule in `referee.py:31-33`) or Python sending presses one at
  a time with a `READMEM` between (slows every turn). Not worth it for v1.

Recommendation: report the lower-bound efficiency as a secondary stat on the
report page, not on the board. Ranking stays gates → fraction → turns.

## 5. Building the walk graph from pret

All from `pret/pokefirered` (public, MIT-style licence on the decompiled data
files; the ROM itself is never needed for this):

| File | Gives | Encoding |
|---|---|---|
| `data/layouts/<Layout>/map.bin` | the metatile grid | u16 per cell: bits 0–9 metatile id, **bits 10–11 collision** (0 = passable), bits 12–15 elevation |
| `data/layouts/layouts.json` | width, height, primary/secondary tileset per layout | JSON |
| `data/tilesets/{primary,secondary}/<name>/metatile_attributes.bin` | per-metatile **behaviour** (ledges `MB_JUMP_*` = one-way edges, water, doors, tall grass) | u32 per metatile (FRLG: behaviour bits 0–8, layer bits 29–30) |
| `data/maps/<Map>/map.json` | **warps** (x, y → dest map + warp id), **connections** (direction + offset), object_events (NPC start tiles) | JSON |
| `data/maps/map_groups.json` | map name → (group, num) — the same table the referee's gates use | JSON |

Build script (offline, run once, output committed):

1. For each map on the first-badge route (Pallet, Route 1, Viridian, Route 2,
   Viridian Forest, Pewter, and the indoor maps the ladder's loci need): decode
   the grid, mark passable cells, add 4-neighbour edges, make ledge cells
   one-way, connect warp cells to their destination warp, stitch connected maps
   by offset.
2. Elevation: treat cells of different elevation as non-adjacent unless a
   behaviour says otherwise (bridges are rare on this route; verify on Route 2).
3. **NPCs: ignore as blockers.** They wander, and the scripted story blockers
   (Oak stopping you north of Pallet, the old man in Viridian) sit on legs the
   ladder order already sequences correctly, so the graph never needs to know.
4. Emit `data/firered-walkgraph.json` (nodes as `"g:n:x:y"`, edges, gate loci)
   and, per gate locus, a precomputed distance field.
5. **Verification tests** (the graph is data, so test it like data): BFS from
   the bedroom reaches Pewter Gym; the path passes the six map gates in ladder
   order; `D_k` per leg is within a sane band of a human speedrun's step counts
   (write the expected numbers down once, by hand, from a walkthrough); a
   spot-check of ten known coordinates (the bed, the lab door, the Mart
   counter) read live from the emulator via `scripts/probe_memory.py` lands on
   passable nodes.

## 5a. Built 2026-09-09 — `scripts/build_walkgraph.py` → `data/firered-walkgraph.json`

32 maps, 8,473 passable tiles, 29,069 directed edges, 0.26 MB. Steps from the
bed: 1F 10 · Pallet 22 · Route 1 38 · Oak's Lab 41 · Viridian 97 · Mart 135 ·
Route 2 140 · Forest 195 · Pewter 362 · Gym 419 · Brock's stand tile 427. The
Forest maze alone is 139 steps south warp → north warp.

Two things the first build got wrong, both now pinned by `tests/test_walkgraph.py`:

1. **GitHub's contents API mangles binary files.** `gh api …/contents/<map.bin>`
   returned "base64" that decoded to 1,419 bytes of UTF-8 mojibake for a 960-byte
   grid, so every layout was noise (5,057 nodes, 4,533 edges, degree-0 tiles
   everywhere). Fetch `raw.githubusercontent.com` instead. The
   `_1F`/`_2F` map names also broke the CamelCase → `MAP_` rule (a digit-to-letter
   boundary is not a word boundary), which silently dropped every house and
   Pokémon Center.
2. **Cut trees are objects, not tiles.** With only tile collision the shortest
   Viridian → Pewter walk took Route 2's east side through two cuttable trees
   and used zero Forest tiles (Pewter at 246 steps instead of 362). Object
   events whose graphics are CUT_TREE / ROCK_SMASH_ROCK / PUSHABLE_BOULDER are
   now barriers; the verifier asserts the path crosses the Forest.

## 5b. Gate loci — estimated 2026-09-09 from pret `map.json` object/coord events

Rule for gates without a map signature: **the locus is the set of passable
tiles adjacent to the NPC or object that fires the event**, because that is
where the player must stand. A *set* rather than one tile: the scorer takes the
nearest member, so which side the model approaches from never costs steps. The
graph builder derives the passable neighbours automatically from the object's
start tile; the numbers below are the object tiles, straight from pret.
Andreas accepted that the starter locus is somewhat arbitrary.

| # | Gate | Type | Locus (map (group,num) → object tile) | Source |
|---|---|---|---|---|
| 4 | `starter_chosen` | flag | Oak's Lab (4,3): the three Pokéballs at (8,4) (9,4) (10,4) → stand tiles south of them | `object_events` ITEM_BALL ×3 |
| 5 | `rival1_done` | flag | Oak's Lab (4,3): the battle trigger row (5,8) (6,8) (7,8) — the rival stops you there on the way out | `coord_events` RivalBattleTrigger{Left,Mid,Right} |
| 8 | `parcel_delivered` (= Mart pickup, var ≥ 1) | var | Viridian Mart (5,3): clerk at (2,3) → tiles adjacent | `object_events` CLERK |
| 9 | `pokedex_received` | flag | Oak's Lab (4,3): Oak at (6,3) → tile south (6,4) | `object_events` PROF_OAK |
| 12 | `brock_defeated` | flag | Pewter Gym (6,2): Brock at (6,5) → tile south (6,6) | `object_events` BROCK |

For **map gates** no estimate is needed: the target is *the set of entry tiles
of the target map* — the warp destination for indoor maps (Oak's Lab door at
Pallet (16,13) → lab warps (5..7,12); Forest south entrance via Route 2 warps
(5..6,51); Pewter Gym door at Pewter (15,16)) or the connection edge for
outdoor maps (Pallet↔Route 1 offset 0; Route 1↔Viridian offset −12/+12;
Viridian↔Route 2 offset +12/−12; Route 2↔Pewter −12/+12). The builder reads
these from `connections` and `warp_events`; nothing is hand-typed.

Two things to verify live with `scripts/probe_memory.py` before trusting the
table: (a) that the referee's x/y are the same map-local coordinates pret uses
(stand on the bed and on the lab door; compare), and (b) the exact stand tile
for the Mart clerk, since mart counters differ in which side is open.

## 6. What changes downstream if A is adopted

- `RunSummary` gains `progress: float` (gates + fraction) and the row gains
  `leg_progress` for display ("3 gates + 62% to Viridian"). `gates_reached`
  stays as is — every existing reader keeps working.
- Leaderboard ranking becomes `(-progress, turns)`. Completed runs are
  unaffected (fraction is 0 after the last gate). Terminated runs at the same
  gate finally separate.
- **Existing rows cannot be rescored** — no telemetry was recorded. Partial
  backfill is possible from savepoints (`emulator.state` every 10 turns and at
  handoffs: reload each, `READMEM` position, take the min), but at that
  resolution `d_min` is only an upper bound. Decide whether the four online
  rows keep `progress = gates_reached` (honest, slightly disadvantaged) or get
  the savepoint backfill.
- Ladder YAML gains an optional `locus` per gate; `benchmark_version` need not
  change because deadlines and signatures are untouched — but the *ranking*
  changes, so the public page's wording ("graded on fewest agent turns") stays
  true and gains "progress toward the next milestone" as the tiebreak.
- HUD: the live dashboard can show "d = 41 steps to Viridian" next to the
  countdown. The agent never sees it (referee isolation holds).

## 7. Phased build, with estimates

| Phase | What | Size |
|---|---|---|
| P0 | `referee_position` event per poll (`turn, map_group, map_num, x, y`), written in `Referee.poll` after the snapshot read; a compact `positions` list in `run_summary.json` (private, stripped on publish like `turns`). Tests: event shape, continue/seal untouched. | half a day |
| P1 | Walk-graph builder from pret data + committed JSON + verification tests in §5. Most of the time is data wrangling (elevation, ledges, connection offsets) and the hand-written expected step counts. | 1–2 days |
| P2 | Scorer: distance fields, `progress` in projection/models/derivations/API/Svelte, ranking change, report leg line, HUD distance. | 1 day |
| P3 | Efficiency stat (lower bound) on the report; exploration coverage as a diagnostic. | half a day |

P0 is worth shipping **now** regardless of the metric decision: every run from
today on carries the data, and the metric can be chosen later against real
traces instead of intuition.

## 8. Decisions — taken 2026-09-09

1. **Ranking:** the fraction enters the order. Leaderboard = `(-progress, turns)` where `progress = gates_reached + fraction`.
2. **Closest-ever** (`d_min` over the leg window), not final position.
3. **The four rows online:** unpublish when the scorer ships; re-run later with telemetry. No savepoint backfill.
4. **Phasing:** all at once — telemetry, graph, scorer, HUD distance, efficiency stat and coverage diagnostic land together.
5. **Loci:** the §5b table; verify the two live checks before freezing.

## 8b. Build order (authorised 2026-09-09)

1. `src/referee/walkgraph.py` — graph interface (`WalkGraph.load`, `node_id`, `distance_to(target, node)`, `steps_between`) + `scripts/build_walkgraph.py` that fetches pret data into `local/pret-cache/` and writes `data/firered-walkgraph.json` (nodes, edges, per-gate target sets, distance fields). Tests: reachability bedroom → Brock, gate order along the path, spot-check tiles.
2. `Referee` gains a `ProgressTracker`: per poll records `(turn, g, n, x, y)`, updates per-leg `d_min`, steps walked (lower bound) and tiles seen; persisted in `referee_state.json` (so continue/seal carry it, capped at the savepoint turn like stamps); emits `referee_position`; `scorecard()` gains `progress` (float), `legs[]` and `next_gate_distance`.
3. Projection → `RunSummary.progress` + `leg` display fields; `derivations` sort + `_better` on progress; `static.js` `rankBoard` the same; Leaderboard/History/Report show "3 gates · 62% to Viridian"; Report gets per-leg efficiency + coverage; HUD shows steps to next gate.
4. Unpublish the four rows; publish wording: "ties broken by progress toward the next milestone".

## 8c. Open questions (were §8)

1. **Ranking or display?** Does the fraction enter the leaderboard order (recommended: yes, between gates and turns) or stay a report-page number?
2. **Closest-ever vs final.** Recommended: closest-ever (`d_min`), which is what "furthest position gotten" means.
3. **The four rows online.** Keep them at integer progress, or backfill from savepoints?
4. **Flag-gate loci.** Confirm the tile choice per flag gate (Pokéball table, Mart counter, Brock's tile) once the graph exists — each is a judgement call written into the ladder file.

## 9. Shipped 2026-09-09

- `src/referee/walkgraph.py` + `scripts/build_walkgraph.py` + `data/firered-walkgraph.json` (§5a).
- `src/referee/progress.py` `ProgressTracker`, wired into `Referee.poll` (a
  `referee_position` event every poll: turn, map, x, y, on_graph, distance,
  next_gate), persisted as `positions` in `referee_state.json` (capped on
  continue like stamps), summarised under `scorecard()["progress"]`. Deviations
  the builder made and I accepted: current leg = first *incomplete* rung (a
  partial multigate is reached but not complete); multigate targets are the
  *unstamped* members' loci; skipped rungs get a `status: "skipped"` leg so
  `legs[]` stays ladder-aligned; `tiles_seen` counts raw coordinates so
  coverage works without a graph.
- `RunSummary.progress` + `leg_*` fields, `rank_score`; leaderboard and history
  rank on it; `static.js` `rankBoard` the same; completion % is progress-based;
  Leaderboard/History sub-line "NN% to <gate>"; Report "Between gates" table
  (path, walked ≥, efficiency, tiles); Spectate HUD "· N steps away".
- Live: a 6-turn casual `--stop-at` run polled 6/6 positions on-graph; turn 1
  sat on the 2F stairs warp tile (10,2) exactly as pret has it, so the
  referee's x/y and the graph's coordinates agree with no offset. An official
  glm-5.3-flash(low) run showed the HUD line "7 steps away" mid-run.
- Tests: 861 pass (+ the 4 known order-dependent). New: `test_walkgraph.py`
  (7), `test_progress.py` (30), `test_progress_projection.py` (3), derivations
  (+3), `static.test.mjs` (+1 case).
- Leg-0 yardstick (found 2026-09-09 while the first official run played): the
  first poll used to happen after turn 1's buttons, so `d_open` for "Left the
  bedroom" was whatever tile turn 1 ended on (10 in one run, 1 in another).
  `TurnManager._run_loop_async` now polls once at total-turn 0 before any
  action, so every leg's D is measured from where the leg actually opens.
  Runs that started before the fix loaded (incl. the published
  `2026-09-09_11-58-07` low run, D=10 on leg 0) keep the old yardstick; the
  effect is bounded to that one leg's fraction.
- Site state after shipping: the three pre-telemetry glm rows (low/high/max,
  all stopped mid-leg) were unpublished per §8 (no backfill). The gemini
  3.8-flash(medium) run stays: it finished 12/12, so its score is exact with or
  without positions (Andreas, 2026-09-09: "we can still use the gemini run
  since it completed"); its report simply has no Between-gates block.
  `2026-09-09_11-58-07_config-5.0__glm-5-3-flash-low`
  (terminated at the T100 Viridian deadline, 6/12 gates, progress 6.76 = 56%,
  14 steps short) is the first published run scored on the fraction. Verified
  live by `pokebench-static-site.mjs` (18 pass; the video assertions cannot run
  until a recorded run is published) and locally by `pokebench-progress-ui.mjs`
  (14/14).
