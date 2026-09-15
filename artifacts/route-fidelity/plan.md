# Route fidelity — every tile a run stood on, drawable on a pixel map

> Plan for the 2026-09-15 step. Andreas (out for the day): "testing and implementing that all of the
> current movement and fight data from the logging system reads in … such good data fidelity that we
> can with a pixel map draw an exact route for all future runs … real experiments with
> gemini-3.5-flash-lite low or gemini-3.8-flash high, which need new runs anyway and are cheap."
> Builds on `artifacts/battle-and-movement-fidelity/plan.md` (§3.2 per-input trace, live since 2026-09-14).

## 0. Decisions (mine, flagged for review — Andreas was out)

| # | Decision | Why |
|---|---|---|
| R1 | The route's source of truth is `events.jsonl` (`turn_input_trace` samples + `referee_position` polls). No new live writer. | Every input's tile is already logged; a second writer would be a second copy of the same state. Derived at projection time like `_gate_clock`. |
| R2 | A multi-tile displacement in one input counts its graph distance as steps, not 1. | Scripted walks (Oak's escort to the lab: 5 samples, ~28 tiles) and the video/between-poll bound both count the tiles moved; the trace counted 1 per sample. Parity with the 25 back-filled runs. |
| R3 | Movement the poll sees after the last sample (door auto-step, a warp still fading) is added to that turn's traced steps. | Audit 2026-09-15: 7 of 140 turns ended on a pre-warp tile; each lost one step. The poll is the settled truth. |
| R4 | Outdoor maps get a `world` tile offset in the walk graph (version 2), stitched from pret's connection offsets, Pallet Town at (0,0). Indoor maps have none and are drawn as insets. | Pixel = (world + tile) × 16. One frame for every future run, no per-run stitching. |
| R5 | Publish writes `data/runs/<run_id>/route.json` per traced run (beside `summary.json`, so unpublish removes it) and two row fields, `route_points` and `route_coverage`. Old runs (no trace) get neither. `--refresh-rows` writes missing/stale route files too. | The board can draw later from a static file; rows stay small. |
| R6 | Two official first-badge runs queued now: gemini-3.5-flash-lite(low) then gemini-3.8-flash(high). The old rows for both aliases are replaced at publish (`board_clash` guard → unpublish first). | Andreas: they need new runs anyway and are cheap. |

## 1. What the audit found (2026-09-15, the two traced runs of 2026-09-14)

- 140 turns, 308 tile transitions, 0 off-graph tiles, 0 blind turns. Every transition is a walk-graph edge
  except one scripted escort (turn 24 of gemini-3.8-flash(low): Oak walks the player to the lab, jumps of
  5/8/5/3/7 tiles between samples).
- 7 turns where the trace's last tile ≠ the poll: the door tile vs the tile after the auto-step out
  ((6,7) vs (6,8) in Pallet Town, 4×), and the pre-warp tile vs the destination map (stairs, lab door, house
  door). The sample is taken at the end of the input's gap; a warp fade outlasts it. The poll a second later
  has the settled tile. → R3.

## 1b. Results (2026-09-15, same day)
- Built and committed (8d5163e); 1041 Python + 67 JS tests green.
- Control: today's gemini-3.5-flash-lite(low) official run (T40, current code) re-derived under R2+R3 changed only the door
  auto-step (+1 on one leg, movement efficiency 0.477 → 0.467). The 2026-09-14 runs moved more because their stored
  figures came from the derive of that afternoon (the battle-start rule landed mid-run): gemini-3.8-flash(low)'s lab
  leg 19 → 38 steps (Oak's escort), efficiency 0.556 → 0.515. Back-fill applied to 4 runs, tracker oracle OK on 4/4.
- Live: flash-lite(low) published with `route.json` (46 visits, coverage 1.0, 43 steps + 2 warps) and every row
  re-projected to v11. gemini-3.8-flash(high) queued behind it (playing on the pre-R2/R3 daemon; the back-fill script
  covers it at publish).

## 2. Build

1. `scripts/build_walkgraph.py` — `world` per connected outdoor map; graph version 2; regenerate (offline,
   nodes/adj byte-identical). `WalkGraph.world_xy(g, m, x, y)`.
2. `src/referee/trace.py` — `derive(..., distance=)`; `scripted_tiles` in the derived dict.
   `src/referee/referee.py` — pass the graph's distance; remember the trace's end tile.
   `src/referee/progress.py` — `traced_end` per turn; `_fold` adds the poll-vs-end-tile steps; persisted.
3. `src/app/route.py` — `build_route(events, graph)`: visits `[turn, i, g, m, x, y, b]` (i −1 = poll,
   b = in battle), transitions step/warp/jump/break, shortest-path `fills` for jumps, coverage. `load_route(run_dir)`.
4. `src/app/projection.py` v11 — `route_points`, `route_coverage`. `src/app/publish.py` — `route.json` on
   publish and on `--refresh-rows`.
5. `scripts/render_route.py` — PNG proof: passable tiles in the world frame, route coloured by turn, insets
   for indoor maps.
6. `scripts/backfill_trace_steps.py` — re-derive traced steps (R2 + R3) for stored traced runs and rewrite
   their legs through the real tracker; the two casual runs now, the two official runs when they end.
7. Live: watch the queued runs; audit each with the same script; publish; verify the route file live.

## 3. Not in this step
- Drawing the route on the site. The data and a static file are the deliverable; the component comes when
  Andreas has looked at a rendered PNG and said what he wants on the page.
- The rival leg's scoring (left as is, 2026-09-14 night).
